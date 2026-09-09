import os, re, shutil, subprocess, threading, uuid, zipfile, json
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import trimesh

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / "jobs"
JOBS.mkdir(exist_ok=True)
STATE = {}

app = FastAPI(title="3D Scanner Reconstruction API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/files", StaticFiles(directory=JOBS), name="files")

def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stdout[-12000:])
    return p.stdout

def safe_extract(zip_path: Path, out_dir: Path):
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            target = (out_dir / member.filename).resolve()
            if not str(target).startswith(str(out_dir.resolve())):
                raise RuntimeError("Unsafe ZIP path.")
        z.extractall(out_dir)

def collect_images(extracted: Path, image_dir: Path):
    image_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for ext in ("*.jpg","*.jpeg","*.png","*.JPG","*.JPEG","*.PNG"):
        files += list(extracted.rglob(ext))
    files = sorted(set(files))
    if len(files) < 12:
        raise RuntimeError(f"Too few images: {len(files)}. At least 12 are recommended.")
    for i, src in enumerate(files, 1):
        shutil.copy2(src, image_dir / f"img_{i:03d}{src.suffix.lower()}")
    return len(files)

def parse_registered_images(sparse_dir: Path):
    try:
        txt = run(["colmap","model_converter",
                   "--input_path", str(sparse_dir),
                   "--output_path", str(sparse_dir),
                   "--output_type","TXT"])
        images_txt = sparse_dir / "images.txt"
        if not images_txt.exists(): return None
        count=0
        for line in images_txt.read_text(errors="ignore").splitlines():
            line=line.strip()
            if line and not line.startswith("#"):
                # Every image has one pose line followed by one points line.
                parts=line.split()
                if len(parts)>=10 and parts[0].isdigit():
                    count += 1
        return count
    except Exception:
        return None

def reconstruct(job_id: str, zip_path: Path):
    st=STATE[job_id]
    work=JOBS/job_id
    try:
        extracted=work/"extracted"
        images=work/"images"
        workspace=work/"colmap"
        extracted.mkdir(exist_ok=True)
        workspace.mkdir(exist_ok=True)

        st.update(status="running",stage="UNPACK",progress=12,message="ZIPを展開しています。")
        safe_extract(zip_path, extracted)
        n=collect_images(extracted, images)
        st["input_images"]=n

        st.update(stage="COLMAP",progress=20,message="特徴点抽出・マッチング・SfM・MVSを実行しています。")
        # Automatic reconstructor provides an end-to-end sparse+dense pipeline.
        # MEDIUM is chosen to keep this usable for classroom-scale jobs.
        run([
            "colmap","automatic_reconstructor",
            "--workspace_path",str(workspace),
            "--image_path",str(images),
            "--quality","MEDIUM",
            "--data_type","INDIVIDUAL",
            "--mesher","POISSON"
        ])

        st.update(stage="MESH",progress=82,message="生成メッシュを確認しています。")
        dense0=workspace/"dense"/"0"
        ply_candidates=[
            dense0/"meshed-poisson.ply",
            dense0/"meshed-delaunay.ply",
            dense0/"meshed-advancing-front.ply",
            dense0/"fused.ply",
        ]
        ply=next((p for p in ply_candidates if p.exists()),None)
        if not ply:
            # Search other dense components if 0 was not the successful one.
            found=[]
            for p in (workspace/"dense").rglob("*.ply"):
                found.append(p)
            if not found:
                raise RuntimeError("COLMAP finished but no dense PLY/mesh was produced.")
            ply=max(found,key=lambda p:p.stat().st_size)

        out_ply=work/"model.ply"
        shutil.copy2(ply,out_ply)

        sparse0=workspace/"sparse"/"0"
        registered=parse_registered_images(sparse0) if sparse0.exists() else None
        st["registered_images"]=registered

        st.update(stage="GLB",progress=91,message="GLBへ変換しています。")
        mesh=trimesh.load(out_ply, force="mesh", process=False)
        if mesh.is_empty:
            raise RuntimeError("Generated PLY is empty.")
        # Normalize around origin without changing relative shape.
        mesh.apply_translation(-mesh.bounding_box.centroid)
        extent=max(mesh.extents) if len(mesh.extents) else 1.0
        if extent and extent>0:
            mesh.apply_scale(1.0/extent)
        out_glb=work/"model.glb"
        mesh.export(out_glb)

        st.update(
            status="done",stage="COMPLETE",progress=100,
            message="3Dモデルを生成しました。",
            glb_url=f"/files/{job_id}/model.glb",
            ply_url=f"/files/{job_id}/model.ply"
        )
    except Exception as e:
        st.update(status="error",stage="ERROR",message=str(e),progress=st.get("progress",0))

@app.post("/api/reconstruct")
async def create_job(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "ZIP file required.")
    job_id=uuid.uuid4().hex[:12]
    work=JOBS/job_id
    work.mkdir(parents=True,exist_ok=True)
    zip_path=work/"capture.zip"
    with zip_path.open("wb") as f:
        shutil.copyfileobj(file.file,f)
    STATE[job_id]={
        "job_id":job_id,"status":"queued","stage":"QUEUED","progress":5,
        "message":"再構成待機中","input_images":None,"registered_images":None
    }
    threading.Thread(target=reconstruct,args=(job_id,zip_path),daemon=True).start()
    return {"job_id":job_id}

@app.get("/api/status/{job_id}")
def status(job_id: str):
    if job_id not in STATE:
        raise HTTPException(404,"Unknown job.")
    return STATE[job_id]

@app.get("/api/health")
def health():
    try:
        out=run(["colmap","-h"])
        colmap=True
    except Exception:
        colmap=False
    return {"ok":True,"colmap":colmap}
