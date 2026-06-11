# Precise Live Segmentation

Real-time object detection from your camera where every object gets a
**pixel-accurate mask tracing its outline** — not just a box. Runs fully on
your own machine (no cloud), with a clean web control panel.

- **YOLO Seg mode** — recognizes 80 everyday objects (people, pets, phones,
  cups, cars…) with name + confidence, tracked across frames for smooth,
  stable masks
- **FastSAM mode** — masks *everything* in view, even objects it can't name
- Works on **macOS** (Apple GPU), **Windows** and **Linux** (NVIDIA GPU if
  available, otherwise CPU)

## Install (one time, ~5 minutes)

You need [Python 3.10+](https://www.python.org/downloads/) installed.
Then open a terminal in this folder and run:

**macOS / Linux**
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**Windows** (PowerShell)
```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

That's it. Model weights (~20–45 MB each) download automatically the first
time each model is used.

## Run

**macOS / Linux**
```bash
.venv/bin/python server.py
```

**Windows**
```powershell
.venv\Scripts\python server.py
```

Your browser opens http://localhost:8000 with the live feed. First launch
may ask for camera permission — allow it (on macOS the permission goes to
your terminal app) and the feed appears.

## Using the control panel

| Control | What it does | Shortcut |
|---|---|---|
| Segmentation mode | YOLO Seg (named objects) ↔ FastSAM (mask everything) | `M` |
| Model size | Nano / Small / Medium — speed vs precision | `1` `2` `3` |
| Confidence | How sure the model must be before masking something | `[` `]` |
| Bounding Boxes | Draw classic boxes in addition to masks | `B` |
| Class filter | Tap chips (person, car, dog…) to only detect those; chip colour = mask colour | |
| Snapshot | Saves the current annotated frame as a PNG | |

The LIVE badge shows the active model and real FPS; "Objects detected"
updates every second.

## Tips

- **Small** is the best balance (40+ FPS on an Apple M-series GPU).
  **Medium** gives the finest masks, **Nano** is for slower machines — on a
  CPU-only machine start with Nano.
- On Windows/Linux with an NVIDIA card, install the CUDA build of PyTorch
  first for GPU speed: `pip install torch --index-url https://download.pytorch.org/whl/cu124`
- No camera, or want to test with a file? `python server.py --source video.mp4`
  (an image works too).
- Port already in use? `python server.py --port 8080`

## Troubleshooting

- **"Camera unavailable" in the feed** — close other apps using the camera
  (Zoom, FaceTime…). macOS: System Settings → Privacy & Security → Camera →
  allow your terminal. The server keeps retrying every few seconds.
- **Slow / laggy** — press `1` for the Nano model, or raise confidence with `]`.
- **Browser didn't open** — go to http://localhost:8000 manually.

## Desktop window (alternative, no browser)

```bash
.venv/bin/python app.py        # Windows: .venv\Scripts\python app.py
```
Same models in a plain OpenCV window. Keys: `q` quit, `m` mode, `1/2/3`
size, `[` `]` confidence, `b` boxes.
