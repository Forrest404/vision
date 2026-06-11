# Precise Live Segmentation

Real-time instance segmentation from your camera: every object gets a pixel-accurate
mask tracing its outline (not just a box), with class name and confidence.

## Run

```bash
.venv/bin/python app.py
```

First run downloads model weights automatically (~20-45 MB each). macOS will ask
once for camera permission for your terminal — click Allow, then rerun if needed.

## Controls

| Key | Action |
|-----|--------|
| `q` / `Esc` | Quit |
| `m` | Toggle YOLO11-seg (80 labeled classes) ↔ Segment Everything (FastSAM, masks anything) |
| `1` / `2` / `3` | YOLO model size: nano / small / medium |
| `[` / `]` | Lower / raise confidence threshold |
| `b` | Toggle bounding boxes |
