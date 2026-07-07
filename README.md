# FaceVision

Offline, on-device **face recognition** with a full web UI — plus the
original real-time object segmentation as a separate mode. Nothing ever
leaves your machine: detection, embeddings, and the face database all live
in this folder.

- **Live** — camera feed with a name next to every face it knows
  ("Unknown" otherwise), powered by OpenCV YuNet + SFace
- **Auto-capture** — clear unknown faces on the live feed are saved
  automatically as numbered people (1000, 2000, …) with a snapshot;
  rename them on the People page (toggle in Live/Settings)
- **Enroll** — drag-drop photos (batch supported), click each detected
  face, type a name; the face joins your on-device database
- **Identify** — upload a photo, see who's in it (nothing is stored)
- **Search** — upload a face and get every stored photo containing that
  person, with the matching face highlighted and a link to the original
- **People** — browse, search, rename, merge and delete people and photos
- **Objects** — the original YOLO11-seg / FastSAM segmentation tool
- **Settings** — recognition threshold, detector confidence, overlay
  colors/landmarks/label size, camera resolution — all persisted locally

Works on **macOS** (Apple GPU), **Windows** and **Linux** (NVIDIA GPU if
available, otherwise CPU).

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

Model weights download automatically on first use (YOLO models ~20–45 MB
each; the two face models are ~37 MB total). **After that everything runs
fully offline.** If the machine is offline on first run, download the two
face models manually and drop them into `models/`:

- <https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx>
- <https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx>

## Run

**macOS / Linux**
```bash
.venv/bin/python server.py
```

**Windows**
```powershell
.venv\Scripts\python server.py
```

Your browser opens http://localhost:8000. First launch may ask for camera
permission — allow it (on macOS the permission goes to your terminal app).

## Use your iPhone as a camera

FaceVision installs on your iPhone as a real app (PWA) and streams the
phone camera to your Mac for live recognition — entirely over your own
Wi-Fi, no internet, nothing leaves the network.

**One-time setup** (phone and Mac on the same Wi-Fi):

1. Start the server on the Mac. It prints a pairing address like
   `http://your-mac.local:8000/pair` — open that on the iPhone.
2. Follow the page: download the certificate → install the profile
   (Settings → Profile Downloaded → Install) → trust it (Settings →
   General → About → Certificate Trust Settings). This is what lets the
   phone camera work, since iOS requires HTTPS.
3. Tap "Open FaceVision", then Share → **Add to Home Screen**.

From then on, tap the icon: fullscreen viewfinder with live name overlays,
auto-capture of new faces (same numbered system and toggle as the Mac
camera), a flip-camera button, and a shutter that offers **Identify**
(nothing stored) or **Add to library** (stored + tap faces to name them).

Notes: the server now listens on your local network (`--local-only`
restores the old localhost-only behavior). If the Mac's network address
changes, restart the server (the certificate re-issues itself) and reload
the app.

## The face database

Everything is stored in `data/`:

| Path | Contents |
|---|---|
| `data/faces.db` | SQLite: people, photos, face embeddings |
| `data/media/photos/` | Full uploaded photos |
| `data/media/thumbs/` | Gallery thumbnails |
| `data/media/crops/` | One crop per detected face |

Delete `data/` to wipe the library. Back it up by copying the folder.

**Recognition quality tips**

- Enroll 3–5 photos per person (different angles/lighting) for reliable
  matches.
- If strangers get named, raise the match threshold in Settings; if known
  people show as Unknown, lower it slightly. Default is 0.36.
- Face search also digs through *unlabeled* faces, so you can find "who
  else appears with this person" before naming anyone.

## Objects mode

The original segmentation tool lives in the **Objects** page:

| Control | What it does | Shortcut |
|---|---|---|
| Mode | YOLO Seg (named objects) ↔ FastSAM (mask everything) | `M` |
| Model size | Nano / Small / Medium — speed vs precision | `1` `2` `3` |
| Confidence | How sure the model must be before masking something | `[` `]` |
| Bounding boxes | Classic boxes in addition to masks | `B` |
| Class filter | Tap chips to only detect those classes | |
| Snapshot | Saves the current annotated frame as a PNG | |

## Tips

- **Small** is the best model-size balance (40+ FPS on an Apple M-series
  GPU); **Nano** for CPU-only machines.
- On Windows/Linux with an NVIDIA card, install the CUDA build of PyTorch
  first: `pip install torch --index-url https://download.pytorch.org/whl/cu124`
- No camera, or want to test with a file? `python server.py --source video.mp4`
- Port already in use? `python server.py --port 8080`
- Set `FACEVISION_NO_DOWNLOAD=1` to forbid all network access (the server
  then requires the model files to already exist).

## Troubleshooting

- **"Camera unavailable" in the feed** — close other apps using the camera
  (Zoom, FaceTime…). macOS: System Settings → Privacy & Security → Camera →
  allow your terminal. The server keeps retrying every few seconds.
- **"Face models loading" banner stays** — the one-time download needs
  internet; or place the ONNX files in `models/` manually (links above) and
  restart.
- **Slow / laggy** — in Objects press `1` for Nano; in Live lower the
  camera resolution in Settings.
- **Browser didn't open** — go to http://localhost:8000 manually.

## Desktop window (alternative, no browser)

```bash
.venv/bin/python app.py        # Windows: .venv\Scripts\python app.py
```
The original OpenCV-window segmentation tool. Keys: `q` quit, `m` mode,
`1/2/3` size, `[` `]` confidence, `b` boxes.
