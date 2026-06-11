"""Web UI backend for real-time precise segmentation.

The server owns the camera: a background thread captures frames, runs
YOLO11-seg (with ByteTrack tracking for smooth, stable detections) or
FastSAM, draws pixel-accurate masks, and publishes JPEG frames. The
browser shows them as an MJPEG stream (/video_feed) and adjusts settings
with plain fire-and-forget GET requests.

Run:  python server.py                       (opens http://localhost:8000)
      python server.py --source clip.mp4     (a video/image file instead of the camera)

Works on macOS (Apple GPU via MPS), Windows and Linux (NVIDIA GPU via CUDA
if available, otherwise CPU).
"""

import argparse
import colorsys
import os
import threading
import time
import webbrowser
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import FASTSAM_MODEL, ModelBank, open_camera, pick_device

WEB_DIR = Path(__file__).parent / "web"
PORT = 8000

YOLO_SIZES = {"n": "yolo11n-seg.pt", "s": "yolo11s-seg.pt", "m": "yolo11m-seg.pt"}

MASK_ALPHA = 0.45
OUTLINE_THICKNESS = 2
JPEG_QUALITY = 82
FONT = cv2.FONT_HERSHEY_SIMPLEX

# Standard COCO class list (what the YOLO models are trained on), hardcoded so
# the UI can build its class chips without waiting for a model to load.
COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon",
    "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
    "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]


def wheel_color(idx: int):
    """Golden-angle hue wheel — the UI chips use the same formula in CSS,
    so a chip's colour always matches its class's mask colour."""
    hue = (idx * 137.508) % 360
    r, g, b = colorsys.hls_to_rgb(hue / 360, 0.55, 0.85)
    return int(b * 255), int(g * 255), int(r * 255)  # BGR for OpenCV


CLASS_COLORS = [wheel_color(i) for i in range(len(COCO_CLASSES))]
INSTANCE_COLORS = [wheel_color(i) for i in range(256)]

# ----------------------------- shared state ------------------------------

state = {
    "mode": "yolo",   # "yolo" | "sam"
    "size": "s",      # n | s | m — small tracks at 40+ FPS; medium is finer but ~25 FPS
    "conf": 0.35,
    "boxes": False,
    "classes": None,  # sorted list of class ids, or None for all
}
state_lock = threading.Lock()

out_cond = threading.Condition()
latest = {
    "jpeg": None, "seq": 0,
    "fps": 0.0, "objects": 0, "model": YOLO_SIZES["s"], "error": None,
}

device = pick_device()
bank = ModelBank(device)
app = FastAPI(title="Precise Segmentation")


# ------------------------------ pipeline ---------------------------------

def message_frame(text: str) -> np.ndarray:
    frame = np.full((720, 1280, 3), 18, dtype=np.uint8)
    for i, line in enumerate(text.split("\n")):
        cv2.putText(frame, line, (60, 320 + i * 44), FONT, 1.0, (200, 200, 205), 2)
    return frame


def publish(frame: np.ndarray, fps: float, objects: int, model: str, error=None):
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        return
    with out_cond:
        latest["jpeg"] = jpg.tobytes()
        latest["seq"] += 1
        latest["fps"] = round(fps, 1)
        latest["objects"] = objects
        latest["model"] = model
        latest["error"] = error
        out_cond.notify_all()


def draw(frame: np.ndarray, result, cfg: dict) -> int:
    """Blend mask fills, trace outlines, draw labels/boxes. Returns count.

    Fills are rasterized from the mask polygons with fillPoly (one cheap call
    per instance + a single full-frame blend), which keeps FastSAM's dozens of
    masks real-time where per-mask boolean compositing was not.
    """
    if result.masks is None or result.boxes is None or len(result.masks) == 0:
        return 0

    sam = cfg["mode"] == "sam"
    classes = result.boxes.cls.int().tolist()
    confs = result.boxes.conf.tolist()
    boxes = result.boxes.xyxy.int().tolist()
    polys = [p.astype(np.int32) for p in result.masks.xy]

    def color_of(i):
        if sam:
            return INSTANCE_COLORS[i % len(INSTANCE_COLORS)]
        return CLASS_COLORS[classes[i] % len(CLASS_COLORS)]

    # All-C++ blend: rasterize fills + coverage, blend the whole frame with
    # SIMD addWeighted, then copy only covered pixels back. ~6x faster than
    # NumPy boolean-index blending.
    color_layer = np.zeros_like(frame)
    covered = np.zeros(frame.shape[:2], dtype=np.uint8)
    for i, poly in enumerate(polys):
        if len(poly) >= 3:
            cv2.fillPoly(color_layer, [poly], color_of(i))
            cv2.fillPoly(covered, [poly], 255)
    blended = cv2.addWeighted(frame, 1 - MASK_ALPHA, color_layer, MASK_ALPHA, 0)
    cv2.copyTo(blended, covered, frame)

    for i, poly in enumerate(polys):
        if len(poly) >= 3:
            cv2.polylines(frame, [poly], True, color_of(i), OUTLINE_THICKNESS)

    for i, (x1, y1, x2, y2) in enumerate(boxes):
        color = color_of(i)
        if cfg["boxes"]:
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
        if not sam:
            label = f"{result.names[classes[i]]} {confs[i]:.2f}"
            (tw, th), _ = cv2.getTextSize(label, FONT, 0.55, 2)
            ty = max(y1, th + 8)
            cv2.rectangle(frame, (x1, ty - th - 8), (x1 + tw + 6, ty), color, -1)
            cv2.putText(frame, label, (x1 + 3, ty - 4), FONT, 0.55, (10, 12, 16), 2)

    return len(polys)


def frame_source(source):
    """Yield frames forever: camera by default, or a video/image file."""
    if source:
        path = Path(source)
        if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
            img = cv2.imread(str(path))
            if img is None:
                raise SystemExit(f"Could not read image: {source}")
            while True:
                yield img.copy()
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise SystemExit(f"Could not open video: {source}")
        while True:
            ok, frame = cap.read()
            if not ok:  # loop the file
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            yield frame

    while True:
        cap = open_camera()
        if not cap.isOpened():
            publish(
                message_frame("Camera unavailable - retrying...\n"
                              "Close other apps using it. On macOS allow camera\n"
                              "access for your terminal in System Settings."),
                0.0, 0, "-", error="camera unavailable",
            )
            time.sleep(3)
            continue
        while True:
            ok, frame = cap.read()
            if not ok:
                cap.release()
                break  # reopen the camera
            yield frame


def pipeline(source):
    fps = 0.0
    for frame in frame_source(source):
        with state_lock:
            cfg = dict(state)

        name = FASTSAM_MODEL if cfg["mode"] == "sam" else YOLO_SIZES[cfg["size"]]
        model = bank.get(name)

        start = time.perf_counter()
        kwargs = dict(
            device=device, retina_masks=True, conf=cfg["conf"],
            max_det=100, verbose=False,
        )
        if cfg["mode"] == "yolo":
            # always pass classes — the persistent predictor remembers the last
            # value, so omitting the kwarg would keep a stale filter forever
            kwargs["classes"] = cfg["classes"] or None
            # ByteTrack keeps objects through brief misses -> smooth, stable masks
            result = model.track(frame, persist=True, **kwargs)[0]
        else:
            # 60 masks keeps "segment everything" real-time at full-res masks
            kwargs["max_det"] = 60
            result = model.predict(frame, iou=0.9, **kwargs)[0]

        count = draw(frame, result, cfg)

        inst = 1.0 / max(time.perf_counter() - start, 1e-6)
        fps = inst if fps == 0 else fps * 0.9 + inst * 0.1
        publish(frame, fps, count, name)


# ------------------------------ endpoints --------------------------------

@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/video_feed")
def video_feed():
    def gen():
        seen = 0
        while True:
            with out_cond:
                out_cond.wait_for(lambda: latest["seq"] != seen, timeout=1.0)
                if latest["seq"] == seen or latest["jpeg"] is None:
                    continue
                seen = latest["seq"]
                buf = latest["jpeg"]
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(buf)).encode() + b"\r\n\r\n" + buf + b"\r\n"
            )
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


def current_state() -> dict:
    with state_lock:
        snap = dict(state)
    snap["classes"] = snap["classes"] or []
    with out_cond:
        snap.update(
            fps=latest["fps"], objects=latest["objects"],
            model=latest["model"], error=latest["error"],
        )
    snap["device"] = device
    return snap


@app.get("/stats")
async def stats():
    return current_state()


@app.get("/api/info")
async def info():
    return {"classes": COCO_CLASSES, "device": device, "state": current_state()}


@app.get("/set_mode")
async def set_mode(mode: str = ""):
    with state_lock:
        if mode in ("yolo", "sam"):
            state["mode"] = mode
        else:  # no/bad argument: toggle
            state["mode"] = "sam" if state["mode"] == "yolo" else "yolo"
    return current_state()


@app.get("/set_model_size")
async def set_model_size(size: str = "m"):
    with state_lock:
        if size in YOLO_SIZES:
            state["size"] = size
    return current_state()


@app.get("/set_confidence")
async def set_confidence(value: float = 0.35):
    with state_lock:
        state["conf"] = min(0.95, max(0.1, float(value)))
    return current_state()


@app.get("/toggle_boxes")
async def toggle_boxes():
    with state_lock:
        state["boxes"] = not state["boxes"]
    return current_state()


@app.get("/set_classes")
async def set_classes(ids: str = ""):
    parsed = sorted(
        {int(t) for t in ids.split(",") if t.strip().isdigit()}
        & set(range(len(COCO_CLASSES)))
    )
    with state_lock:
        state["classes"] = parsed or None
    return current_state()


# -------------------------------- main ------------------------------------

def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.environ.get("SEG_SOURCE") or None,
                        help="video or image file to stream instead of the camera")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    print(f"Inference device: {device}")
    threading.Thread(target=pipeline, args=(args.source,), daemon=True).start()
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
