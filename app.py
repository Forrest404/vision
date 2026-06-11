"""Real-time precise object segmentation from the live camera.

Two modes, toggled with `m`:
  - YOLO11-seg:  detects 80 everyday object classes and traces a pixel-accurate
                 mask around each, with class name + confidence.
  - FastSAM:     "segment everything" — masks every object in view, including
                 things outside the 80 known classes (no class names).

Controls:
  q / Esc   quit
  m         toggle YOLO-seg <-> Segment-Everything (FastSAM)
  1 / 2 / 3 YOLO model size: nano / small / medium
  [ / ]     lower / raise confidence threshold
  b         toggle bounding boxes on/off
"""

import os
import time

# Some ops aren't implemented on Apple's MPS backend yet; fall back to CPU
# for those instead of crashing. Must be set before torch is imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import cv2
import numpy as np
import torch
from ultralytics import YOLO, FastSAM

YOLO_MODELS = {"1": "yolo11n-seg.pt", "2": "yolo11s-seg.pt", "3": "yolo11m-seg.pt"}
FASTSAM_MODEL = "FastSAM-s.pt"

MASK_ALPHA = 0.45
OUTLINE_THICKNESS = 2
FONT = cv2.FONT_HERSHEY_SIMPLEX


def pick_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def make_palette(n: int = 256) -> np.ndarray:
    """Stable, vivid BGR colors: spread hues around the wheel."""
    hues = (np.arange(n) * 47) % 180  # golden-angle-ish stride for separation
    hsv = np.stack([hues, np.full(n, 200), np.full(n, 255)], axis=1).astype(np.uint8)
    return cv2.cvtColor(hsv[None], cv2.COLOR_HSV2BGR)[0].astype(np.int32)


PALETTE = make_palette()


class ModelBank:
    """Lazy-loads models on first use and keeps them cached."""

    def __init__(self, device: str):
        self.device = device
        self._cache: dict[str, object] = {}

    def has(self, name: str) -> bool:
        return name in self._cache

    def get(self, name: str):
        if name not in self._cache:
            print(f"Loading {name} (downloads on first use) ...")
            model = FastSAM(name) if "FastSAM" in name else YOLO(name)
            # Warm up so the first on-camera frame isn't a multi-second stall.
            dummy = np.zeros((480, 640, 3), dtype=np.uint8)
            model.predict(dummy, device=self.device, verbose=False)
            self._cache[name] = model
        return self._cache[name]


def draw_instances(frame, result, names, conf_per_box, show_boxes, by_class):
    """Blend translucent mask fills, trace contours, and label each instance."""
    if result.masks is None or len(result.masks) == 0:
        return frame

    masks = result.masks.data.cpu().numpy() > 0.5  # (N, H, W) at frame size
    classes = result.boxes.cls.cpu().numpy().astype(int) if result.boxes is not None else None

    h, w = frame.shape[:2]
    color_layer = np.zeros_like(frame)
    covered = np.zeros((h, w), dtype=bool)

    for i, mask in enumerate(masks):
        if mask.shape != (h, w):  # safety net if masks come back model-sized
            mask = cv2.resize(mask.astype(np.uint8), (w, h)).astype(bool)
        color_idx = classes[i] if (by_class and classes is not None) else i
        color_layer[mask] = PALETTE[color_idx % len(PALETTE)]
        covered |= mask

    frame[covered] = (
        frame[covered] * (1 - MASK_ALPHA) + color_layer[covered] * MASK_ALPHA
    ).astype(np.uint8)

    # Crisp outline tracing each object's edge.
    for i, polygon in enumerate(result.masks.xy):
        if len(polygon) < 3:
            continue
        color_idx = classes[i] if (by_class and classes is not None) else i
        color = tuple(int(c) for c in PALETTE[color_idx % len(PALETTE)])
        cv2.polylines(frame, [polygon.astype(np.int32)], True, color, OUTLINE_THICKNESS)

    if result.boxes is not None:
        for i, box in enumerate(result.boxes.xyxy.cpu().numpy().astype(int)):
            color_idx = classes[i] if (by_class and classes is not None) else i
            color = tuple(int(c) for c in PALETTE[color_idx % len(PALETTE)])
            x1, y1, x2, y2 = box
            if show_boxes:
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
            if names is not None and classes is not None:
                label = f"{names[classes[i]]} {conf_per_box[i]:.2f}"
                (tw, th), _ = cv2.getTextSize(label, FONT, 0.55, 2)
                ty = max(y1, th + 8)
                cv2.rectangle(frame, (x1, ty - th - 8), (x1 + tw + 6, ty), color, -1)
                cv2.putText(frame, label, (x1 + 3, ty - 4), FONT, 0.55, (0, 0, 0), 2)

    return frame


def draw_hud(frame, lines):
    pad, line_h = 8, 22
    width = max(cv2.getTextSize(t, FONT, 0.55, 1)[0][0] for t in lines) + 2 * pad
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (width, line_h * len(lines) + pad), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (pad, line_h * (i + 1)), FONT, 0.55, (255, 255, 255), 1)


def main():
    device = pick_device()
    print(f"Inference device: {device}")
    bank = ModelBank(device)

    yolo_name = YOLO_MODELS["3"]  # medium: ~49 FPS on M4 Pro GPU, best mask quality
    sam_mode = False
    conf = 0.35
    show_boxes = False
    fps = 0.0

    cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    if not cap.isOpened():
        raise SystemExit(
            "Could not open the camera. Grant camera access to your terminal in "
            "System Settings > Privacy & Security > Camera, then rerun."
        )

    window = "Precise Segmentation  (q quit, m mode, 1/2/3 size, [/] conf, b boxes)"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    while True:
        ok, frame = cap.read()
        if not ok:
            print("Camera frame grab failed; stopping.")
            break

        start = time.perf_counter()
        if sam_mode:
            model = bank.get(FASTSAM_MODEL)
            result = model.predict(
                frame, device=device, retina_masks=True, conf=0.4, iou=0.9, verbose=False
            )[0]
            names = None
        else:
            model = bank.get(yolo_name)
            result = model.predict(
                frame, device=device, retina_masks=True, conf=conf, verbose=False
            )[0]
            names = result.names

        confs = (
            result.boxes.conf.cpu().numpy() if result.boxes is not None else np.array([])
        )
        frame = draw_instances(
            frame, result, names, confs, show_boxes, by_class=not sam_mode
        )

        # Exponential moving average keeps the FPS readout steady.
        inst_fps = 1.0 / max(time.perf_counter() - start, 1e-6)
        fps = inst_fps if fps == 0 else 0.9 * fps + 0.1 * inst_fps

        count = 0 if result.masks is None else len(result.masks)
        draw_hud(frame, [
            f"FPS {fps:5.1f}   objects {count}",
            f"mode: {'Segment Everything (FastSAM)' if sam_mode else f'YOLO11-seg ({yolo_name})'}",
            f"conf {conf:.2f}   device {device}   boxes {'on' if show_boxes else 'off'}",
        ])
        cv2.imshow(window, frame)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):  # q or Esc
            break
        elif key == ord("m"):
            sam_mode = not sam_mode
        elif key == ord("b"):
            show_boxes = not show_boxes
        elif key == ord("["):
            conf = max(0.05, conf - 0.05)
        elif key == ord("]"):
            conf = min(0.95, conf + 0.05)
        elif key != 255 and chr(key) in YOLO_MODELS:
            yolo_name = YOLO_MODELS[chr(key)]
            sam_mode = False

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
