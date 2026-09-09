# 3D SCANNER — STEP 4

This package adds server-side photogrammetry to the 48-view capture workflow.

## Architecture

Smartphone / browser:
1. Select the ZIP exported from STEP 2/3.
2. Upload it to the reconstruction API.
3. Poll job progress.
4. Preview and download GLB / PLY.

PC / server:
1. Unpack images.
2. Run COLMAP automatic reconstruction.
3. Locate the generated dense mesh / point cloud.
4. Convert PLY to GLB with trimesh.

## Requirements

- Python 3.10+
- COLMAP in PATH
- FastAPI dependencies from `requirements.txt`
- For practical dense reconstruction, a computer with a discrete GPU is strongly recommended.

## macOS example

Install COLMAP:

    brew install colmap

Create Python environment:

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt

Start API:

    uvicorn server:app --host 0.0.0.0 --port 8000

Then open `index.html`.

If the web page runs on the same computer:
    http://localhost:8000

If the page runs on a smartphone on the same Wi-Fi:
    use the computer's LAN address, e.g.
    http://192.168.1.20:8000

Important:
- Browser camera access still requires HTTPS when using the capture app.
- Mixed Content rules can block an HTTPS webpage from calling an HTTP API.
  For phone deployment, expose the API via HTTPS (reverse proxy / tunnel / hosted server).

## Output

Each job creates:

    jobs/<job_id>/model.glb
    jobs/<job_id>/model.ply

The GLB produced in this first STEP 4 implementation is converted from COLMAP's generated PLY.
It is intended as a practical preview / XR handoff model. Full high-quality texture baking is a later enhancement.

## Reconstruction limitations

Photogrammetry works poorly with:
- transparent objects
- mirrors / glossy surfaces
- uniformly white or textureless objects
- changing light
- moving objects
- too little overlap between neighboring views

Use a textured matte surface and constant lighting whenever possible.
