"""FaceVision — offline face recognition + object segmentation web app.

The server owns the camera: a background thread (pipeline.py) captures
frames, runs YOLO11-seg / FastSAM / YuNet+SFace face recognition, draws
overlays, and publishes JPEG frames. The browser shows them as an MJPEG
stream (/video_feed); the SPA in web/ drives everything else through the
JSON API in routes_faces.py.

Run:  python server.py                       (opens http://localhost:8000)
      python server.py --source clip.mp4     (a video/image file instead of the camera)

Fully offline after the one-time face-model download. Works on macOS
(Apple GPU via MPS), Windows and Linux (NVIDIA GPU via CUDA, else CPU).
"""

import argparse
import os
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import face_db as fdb
import pipeline as pl
import routes_faces
import routes_phone
from certs import ensure_certs
from face_engine import FaceEngine, ensure_models

WEB_DIR = Path(__file__).parent / "web"
PORT = 8000

app = FastAPI(title="FaceVision")


@app.middleware("http")
async def cache_headers(request, call_next):
    """The UI must always revalidate (the old app's files got heuristically
    cached by browsers); stored photos are immutable so they may cache."""
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/media"):
        resp.headers.setdefault("Cache-Control", "max-age=86400")
    return resp


# ----------------------------- static + pages -----------------------------

@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/manifest.json")
async def manifest():
    return FileResponse(WEB_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    # served from the root so its scope covers the whole app
    return FileResponse(WEB_DIR / "sw.js", media_type="text/javascript")


fdb.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
app.mount("/media", StaticFiles(directory=fdb.MEDIA_DIR), name="media")
app.include_router(routes_faces.router)
app.include_router(routes_phone.router)


# ------------------------------- streaming --------------------------------

@app.get("/video_feed")
def video_feed():
    def gen():
        # The camera only runs while someone is actually watching: each open
        # stream counts as a viewer, and the pipeline parks the camera at zero.
        pl.viewer_delta(+1)
        try:
            seen = 0
            while True:
                with pl.out_cond:
                    pl.out_cond.wait_for(lambda: pl.latest["seq"] != seen, timeout=1.0)
                    if pl.latest["seq"] == seen or pl.latest["jpeg"] is None:
                        continue
                    seen = pl.latest["seq"]
                    buf = pl.latest["jpeg"]
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(buf)).encode() + b"\r\n\r\n" + buf + b"\r\n"
                )
        finally:
            pl.viewer_delta(-1)
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


# --------------------------- live mode control -----------------------------

@app.get("/stats")
async def stats():
    return pl.current_state()


@app.get("/api/info")
async def info():
    return {
        "classes": pl.COCO_CLASSES,
        "device": pl.device,
        "face_ready": pl.face_runtime["engine"] is not None,
        "state": pl.current_state(),
    }


@app.get("/set_camera")
async def set_camera(on: bool = True):
    with pl.state_lock:
        pl.state["camera_on"] = bool(on)
    return pl.current_state()


@app.get("/set_mode")
async def set_mode(mode: str = ""):
    with pl.state_lock:
        if mode in ("yolo", "sam", "faces"):
            pl.state["mode"] = mode
        else:  # no/bad argument: toggle between the segmentation modes
            pl.state["mode"] = "sam" if pl.state["mode"] == "yolo" else "yolo"
    return pl.current_state()


@app.get("/set_model_size")
async def set_model_size(size: str = "m"):
    with pl.state_lock:
        if size in pl.YOLO_SIZES:
            pl.state["size"] = size
    return pl.current_state()


@app.get("/set_confidence")
async def set_confidence(value: float = 0.35):
    with pl.state_lock:
        pl.state["conf"] = min(0.95, max(0.1, float(value)))
    return pl.current_state()


@app.get("/toggle_boxes")
async def toggle_boxes():
    with pl.state_lock:
        pl.state["boxes"] = not pl.state["boxes"]
    return pl.current_state()


@app.get("/set_classes")
async def set_classes(ids: str = ""):
    parsed = sorted(
        {int(t) for t in ids.split(",") if t.strip().isdigit()}
        & set(range(len(pl.COCO_CLASSES)))
    )
    with pl.state_lock:
        pl.state["classes"] = parsed or None
    return pl.current_state()


# --------------------------------- startup --------------------------------

def init_face_stack():
    """Open the DB now; fetch/load the face models in the background so a
    first-run download (or a broken network) never blocks server startup."""
    db = fdb.FaceDB()
    routes_faces.runtime["db"] = db
    pl.face_runtime["db"] = db  # auto-capture writes through this
    routes_faces.apply_settings(db.get_settings())

    def load():
        try:
            yunet, sface = ensure_models()
            det_score = float(db.get_settings()["det_score"])
            # Two engines: the pipeline thread owns one, upload request
            # threads share the other (behind its internal lock) — uploads
            # never stall the feed.
            pl.face_runtime["engine"] = FaceEngine(yunet, sface, score_thresh=det_score)
            pl.face_runtime["index"] = db.index
            routes_faces.runtime["engine"] = FaceEngine(yunet, sface, score_thresh=det_score)
            print("Face models loaded — recognition ready.")
        except SystemExit as exc:
            print(f"WARNING: {exc}\nFace features disabled until the models are available.")
        except Exception as exc:  # e.g. corrupt model file
            print(f"WARNING: could not load face models ({exc}). Face features disabled.")

    threading.Thread(target=load, daemon=True).start()


def main():
    import socket

    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.environ.get("SEG_SOURCE") or None,
                        help="video or image file to stream instead of the camera")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--https-port", type=int, default=routes_phone.HTTPS_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--local-only", action="store_true",
                        help="bind 127.0.0.1 only (no phone access)")
    args = parser.parse_args()

    print(f"Inference device: {pl.device}")
    init_face_stack()
    pl.start(args.source)
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    host = "127.0.0.1" if args.local_only else "0.0.0.0"
    if args.local_only:
        uvicorn.run(app, host=host, port=args.port, log_level="warning")
        return

    # Two listeners over the same app (shared state, one process):
    #   HTTP  :8000 — laptop UI + the phone pairing page
    #   HTTPS :8443 — the iPhone app (camera needs a secure origin)
    cert, key, _ca = ensure_certs()
    routes_phone.HTTPS_PORT = args.https_port
    hostname = socket.gethostname().split(".")[0]
    print(f"Laptop UI : http://localhost:{args.port}")
    print(f"iPhone    : open http://{hostname}.local:{args.port}/pair "
          f"on the phone to set up (same Wi-Fi)")

    http_server = uvicorn.Server(uvicorn.Config(
        app, host=host, port=args.port, log_level="warning"))
    threading.Thread(target=http_server.run, daemon=True).start()
    uvicorn.run(app, host=host, port=args.https_port, log_level="warning",
                ssl_certfile=str(cert), ssl_keyfile=str(key))


if __name__ == "__main__":
    main()
