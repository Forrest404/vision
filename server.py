"""Web UI backend for real-time precise segmentation.

The browser captures the camera and streams JPEG frames over a WebSocket;
this server runs YOLO11-seg / FastSAM on the Apple GPU and replies with
mask polygons + labels as JSON, which the browser renders on a canvas.

Run:  .venv/bin/python server.py   (opens http://localhost:8000)
"""

import asyncio
import json
import threading
import time
import webbrowser
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import FASTSAM_MODEL, ModelBank, pick_device

WEB_DIR = Path(__file__).parent / "web"
PORT = 8000

YOLO_SIZES = {
    "n": "yolo11n-seg.pt",
    "s": "yolo11s-seg.pt",
    "m": "yolo11m-seg.pt",
    "l": "yolo11l-seg.pt",
    "x": "yolo11x-seg.pt",
}

# Standard COCO class list (what the YOLO models are trained on), hardcoded so
# the UI can build its class filter without waiting for a model to load.
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

DEFAULT_CONFIG = {
    "mode": "yolo",      # "yolo" | "sam"
    "size": "m",         # YOLO model size key
    "conf": 0.35,
    "iou": 0.7,
    "imgsz": 640,
    "max_det": 100,
    "classes": None,     # list of class ids, or None for all
}

device = pick_device()
bank = ModelBank(device)
app = FastAPI(title="Precise Segmentation")


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/api/info")
async def info():
    return {
        "device": device,
        "classes": COCO_CLASSES,
        "sizes": list(YOLO_SIZES),
        "defaults": DEFAULT_CONFIG,
    }


def model_for(cfg: dict) -> str:
    if cfg["mode"] == "sam":
        return FASTSAM_MODEL
    return YOLO_SIZES.get(cfg["size"], YOLO_SIZES["m"])


def run_inference(frame: np.ndarray, name: str, cfg: dict):
    model = bank.get(name)
    kwargs = dict(
        device=device,
        retina_masks=True,
        conf=float(cfg["conf"]),
        iou=float(cfg["iou"]),
        imgsz=int(cfg["imgsz"]),
        max_det=int(cfg["max_det"]),
        verbose=False,
    )
    if cfg["mode"] == "yolo" and cfg.get("classes"):
        kwargs["classes"] = [int(c) for c in cfg["classes"]]
    return model.predict(frame, **kwargs)[0]


def serialize(result, infer_ms: float) -> dict:
    instances = []
    if result.masks is not None and result.boxes is not None:
        classes = result.boxes.cls.int().tolist()
        confs = result.boxes.conf.tolist()
        boxes = result.boxes.xyxy.int().tolist()
        for i, poly in enumerate(result.masks.xy):
            if len(poly) < 3:
                continue
            instances.append({
                "cls": classes[i],
                "name": result.names.get(classes[i], "object"),
                "conf": round(confs[i], 3),
                "box": boxes[i],
                # flat [x0, y0, x1, y1, ...] pixel coords — compact over the wire
                "poly": poly.astype(int).flatten().tolist(),
            })
    h, w = result.orig_shape
    return {
        "type": "result",
        "w": w,
        "h": h,
        "infer_ms": round(infer_ms, 1),
        "instances": instances,
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    await ws.send_json({"type": "hello", "device": device})
    cfg = dict(DEFAULT_CONFIG)
    loop = asyncio.get_event_loop()

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break

            if msg.get("text"):
                data = json.loads(msg["text"])
                if data.get("type") == "config":
                    cfg.update({k: v for k, v in data.items() if k in cfg})
                continue

            if not msg.get("bytes"):
                continue

            frame = cv2.imdecode(np.frombuffer(msg["bytes"], np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                await ws.send_json({"type": "error", "detail": "bad frame"})
                continue

            name = model_for(cfg)
            if not bank.has(name):
                # First use of this model: tell the UI so it can show a
                # loading state (large models also download here).
                await ws.send_json({"type": "status", "loading": True, "model": name})
                await loop.run_in_executor(None, bank.get, name)
                await ws.send_json({"type": "status", "loading": False, "model": name})

            start = time.perf_counter()
            result = await loop.run_in_executor(None, run_inference, frame, name, cfg)
            infer_ms = (time.perf_counter() - start) * 1000
            await ws.send_json(serialize(result, infer_ms))
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    print(f"Inference device: {device}")
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
