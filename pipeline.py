"""Camera pipeline: capture frames, run the selected model, publish JPEGs.

Owns all shared mutable state:
  state / state_lock — settings the UI can change (mode, size, conf, ...)
  latest / out_cond  — the freshest annotated JPEG + stats for streaming

Modes: "yolo" (YOLO11-seg + ByteTrack), "sam" (FastSAM segment-everything),
and "faces" (YuNet detection + SFace recognition against the local DB).
"""

import colorsys
import os
import threading
import time
from collections import Counter

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import cv2
import numpy as np
from pathlib import Path

from app import FASTSAM_MODEL, ModelBank, open_camera, pick_device
import web_search

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
    "hair drier", "pen",
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
    "mode": "yolo",   # "yolo" | "sam" | "faces"
    "size": "s",      # n | s | m — small tracks at 40+ FPS; medium is finer but ~25 FPS
    "conf": 0.35,
    "boxes": False,
    "classes": None,  # sorted list of class ids, or None for all
    "face": {},       # recognition/overlay settings, loaded from the DB at startup
    "camera": {"width": 1280, "height": 720, "index": 0},
    "camera_restart": False,  # set True after changing camera size/device
    "camera_on": True,   # the user's switch — the camera never opens while False
    "viewers": 0,        # open /video_feed connections; camera idles at zero
    "phone_enabled": False,  # iPhone pairing/streaming gate (Settings toggle)
}
state_lock = threading.Lock()

out_cond = threading.Condition()
latest = {
    "jpeg": None, "seq": 0,
    "fps": 0.0, "objects": 0, "model": YOLO_SIZES["s"], "error": None,
}

device = pick_device()
bank = ModelBank(device)

# Set by server startup (needs face models + DB); None until then.
face_runtime = {"engine": None, "index": None, "db": None}


def viewer_delta(n: int):
    """Track open /video_feed connections (server calls this per stream)."""
    with state_lock:
        state["viewers"] = max(0, state["viewers"] + n)


def current_state() -> dict:
    with state_lock:
        snap = {k: v for k, v in state.items() if k != "camera_restart"}
        snap["face"] = dict(snap["face"])
        snap["camera"] = dict(snap["camera"])
    snap["classes"] = snap["classes"] or []
    with out_cond:
        snap.update(
            fps=latest["fps"], objects=latest["objects"],
            model=latest["model"], error=latest["error"],
        )
    snap["device"] = device
    return snap


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


# --------------------------- face mode helpers ----------------------------

def hex_to_bgr(value: str, fallback=(248, 189, 56)) -> tuple:
    try:
        v = value.lstrip("#")
        r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
        return (b, g, r)
    except Exception:
        return fallback


def face_is_sharp(frame: np.ndarray, row, blur_threshold: float = 25.0) -> bool:
    """Variance-of-Laplacian focus check on the face region. Deliberately
    lenient: it only rejects heavy motion blur — a slightly soft face is
    still worth capturing."""
    x, y, w, h = (int(v) for v in row[:4])
    ih, iw = frame.shape[:2]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(iw, x + w), min(ih, y + h)
    if x2 <= x1 or y2 <= y1:
        return False
    gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var() >= blur_threshold


class FaceTracker:
    """IoU tracker so we embed each on-screen face rarely, not every frame.

    A new track is embedded + matched immediately; established tracks
    re-embed every EMBED_INTERVAL frames (more often while "Unknown").
    Displayed names come from a rolling majority vote, so one noisy
    embedding can't flicker the label.
    """

    IOU_MATCH = 0.3
    MAX_MISSES = 10
    EMBED_INTERVAL = 15       # ~0.5 s at 30 FPS
    EMBED_INTERVAL_UNKNOWN = 6
    BOX_SMOOTH = 0.6          # EMA weight of the newest box
    VOTE_SLOTS = 5

    # Auto-capture confirmation: an unknown face is only saved after the
    # track has lived this many frames AND two embeddings taken on separate
    # frames agree — random objects never manage both. Tuned toward
    # CAPTURING (a slightly soft frame is fine; missing a person is not).
    ENROLL_MIN_AGE = 4
    ENROLL_CONSISTENCY = 0.5  # cosine between the two confirmation samples

    def __init__(self, engine=None, camera="mac"):
        # engine override lets phone streams use the upload engine so they
        # never contend with the Mac-camera pipeline's private engine
        self._engine = engine
        self._camera = camera  # labels events with their source camera
        self._tracks: list[dict] = []
        self._next_id = 1

    @staticmethod
    def _iou(a, b) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        x1, y1 = max(ax, bx), max(ay, by)
        x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0.0

    def update(self, dets: np.ndarray, frame: np.ndarray, face_cfg: dict) -> list[dict]:
        """dets: YuNet (N, 15) rows. Returns live tracks with display names."""
        engine = self._engine or face_runtime["engine"]
        index = face_runtime["index"]
        threshold = float(face_cfg.get("rec_threshold", 0.363))
        auto = face_cfg.get("auto_enroll") or {}
        # Watchlist mode: recognise-only, never store new faces (lawful retail).
        if (face_cfg.get("watchlist") or {}).get("enabled"):
            auto = {"enabled": False}

        # Greedy IoU association: best-overlap pairs first.
        pairs = []
        for d, row in enumerate(dets):
            for t, track in enumerate(self._tracks):
                iou = self._iou(row[:4], track["bbox"])
                if iou >= self.IOU_MATCH:
                    pairs.append((iou, d, t))
        pairs.sort(reverse=True)
        used_d, used_t = set(), set()
        matches = []
        for iou, d, t in pairs:
            if d in used_d or t in used_t:
                continue
            used_d.add(d); used_t.add(t)
            matches.append((d, t))

        # 1. matched tracks: smooth the box, occasionally refresh the embedding
        for d, t in matches:
            row, track = dets[d], self._tracks[t]
            a = self.BOX_SMOOTH
            track["bbox"] = [a * n + (1 - a) * o for n, o in zip(row[:4], track["bbox"])]
            track["row"] = row
            track["misses"] = 0
            track["age"] += 1
            track["frames_since_embed"] += 1
            interval = (self.EMBED_INTERVAL if any(track["votes"])
                        else self.EMBED_INTERVAL_UNKNOWN)
            if track["frames_since_embed"] >= interval:
                self._embed_and_vote(track, frame, engine, index, threshold, auto)

        # 2. unmatched tracks: age them out after MAX_MISSES frames
        for t, track in enumerate(self._tracks):
            if t not in used_t:
                track["misses"] += 1
        self._tracks = [t for t in self._tracks if t["misses"] <= self.MAX_MISSES]

        # 3. unmatched detections: new tracks, embedded + matched right away
        for d, row in enumerate(dets):
            if d in used_d:
                continue
            track = {
                "id": self._next_id, "bbox": list(row[:4]), "row": row,
                "misses": 0, "age": 1, "frames_since_embed": 0, "votes": [],
                "web_name": None, "web_queued": False,
            }
            self._next_id += 1
            self._embed_and_vote(track, frame, engine, index, threshold, auto)
            self._tracks.append(track)

        # visible tracks = seen this frame
        cats = getattr(index.snapshot, "categories", None) or {}
        out = []
        for track in self._tracks:
            if track["misses"] == 0:
                votes = [v for v in track["votes"] if v]
                name, pid, score = None, None, 0.0
                if votes:
                    counts = Counter((v[0], v[1]) for v in votes)
                    (pid, name), _n = counts.most_common(1)[0]
                    score = max(v[2] for v in votes if (v[0], v[1]) == (pid, name))
                category = cats.get(pid, "none") if pid is not None else "none"
                if category != "none" and not track.get("event_logged"):
                    self._log_event(track, pid, name, category, score, frame)
                    track["event_logged"] = True
                out.append({**track, "person_id": pid, "name": name,
                            "score": score, "category": category})
        return out

    def _log_event(self, track, pid, name, category, score, frame):
        """Record one watchlist sighting per track, with a snapshot crop."""
        db = face_runtime["db"]
        if db is None:
            return
        snap = ""
        try:
            x, y, w, h = (int(v) for v in track["bbox"])
            fh, fw = frame.shape[:2]
            crop = frame[max(0, y):min(fh, y + h), max(0, x):min(fw, x + w)]
            if crop.size:
                import face_db as _fdb
                eid = db.add_event(pid, name, category, self._camera, score, "")
                fn = f"event_{eid}.jpg"
                cv2.imwrite(str(_fdb.CROPS_DIR / fn), crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
                snap = f"/media/crops/{fn}"
                with db._lock:
                    db._conn.execute("UPDATE events SET snapshot=? WHERE id=?", (snap, eid))
                    db._conn.commit()
        except Exception:
            pass

    def _embed_and_vote(self, track: dict, frame: np.ndarray, engine, index,
                        threshold: float, auto: dict):
        track["frames_since_embed"] = 0
        if engine is None or index is None:
            return
        try:
            emb = engine.embed(frame, track["row"])
        except cv2.error:
            return
        hit = index.match(emb, threshold)
        if hit is None and auto.get("enabled") and not track.get("enrolled"):
            hit = self._auto_enroll(track, frame, emb, auto, threshold)
        if hit is None and not track.get("web_queued") and track["age"] >= web_search.WEB_SEARCH_MIN_AGE:
            # face isn't in the local DB — try the internet as a fallback
            x, y, w, h = (int(v) for v in track["bbox"])
            pad = int(min(w, h) * 0.25)
            fh, fw = frame.shape[:2]
            crop = frame[max(0, y - pad):min(fh, y + h + pad),
                         max(0, x - pad):min(fw, x + w + pad)]
            ok, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if ok:
                def _cb(name, t=track):
                    t["web_name"] = name
                track["web_queued"] = web_search.lookup(emb.tobytes(), jpg.tobytes(), _cb)
        track["votes"].append(hit)  # None counts as an "Unknown" vote
        if len(track["votes"]) > self.VOTE_SLOTS:
            track["votes"].pop(0)

    def _auto_enroll(self, track: dict, frame: np.ndarray,
                     emb: np.ndarray, auto: dict, threshold: float):
        """Store a clear unknown face as person 0001/0002/... Returns the
        vote tuple for the new identity, or None if quality gates fail
        (they retry on the next scheduled embed).

        Every gate must pass on TWO separate frames, and the two embeddings
        must agree, before anything is written — a face-shaped object can't
        get enrolled from one lucky frame."""
        db = face_runtime["db"]
        row = track["row"]
        if db is None:
            return None
        if float(row[14]) < float(auto.get("min_score", 0.6)):
            track["pending_emb"] = None  # confirmation restarts after any failure
            return None
        w, h = float(row[2]), float(row[3])
        if min(w, h) < float(auto.get("min_size", 60)):
            track["pending_emb"] = None
            return None
        if not 0.4 <= w / max(h, 1.0) <= 1.5:  # real faces have face-like proportions
            track["pending_emb"] = None
            return None
        if not face_is_sharp(frame, row):
            track["pending_emb"] = None
            return None

        # two-sighting confirmation across separate frames
        pending = track.get("pending_emb")
        if pending is None or track["age"] < self.ENROLL_MIN_AGE:
            track["pending_emb"] = emb  # first clear sample; confirm on the next embed
            return None
        if float(np.dot(pending, emb)) < self.ENROLL_CONSISTENCY:
            track["pending_emb"] = emb  # inconsistent — not a stable face; start over
            return None

        # Already stored? A face this similar may exist UNLABELED (a library
        # upload nobody named, or a deleted person's old face). Give that
        # existing face the new number instead of storing a duplicate.
        for hit in db.index.match_all(emb, threshold):
            if hit["person_id"] is None:
                name = db.next_auto_name()
                face = db.label_face(hit["face_id"], name=name)
                db.index.rebuild()
                track["enrolled"] = True
                if face and face.get("person_id") is not None:
                    return (face["person_id"], name, 1.0)
                return None

        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return None
        # sha256 dedupe reuses the photo when two strangers share one frame
        photo, _bgr, _dup = db.add_photo(
            jpg.tobytes(), f"Auto capture {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        if photo is None:
            return None
        landmarks = [[float(row[4 + i * 2]), float(row[5 + i * 2])] for i in range(5)]
        face_id = db.add_face(
            photo["id"], tuple(float(v) for v in row[:4]),
            landmarks, float(row[14]), emb, image=frame,
        )
        name = db.next_auto_name()
        face = db.label_face(face_id, name=name)
        db.index.rebuild()  # next sighting matches this number instead of minting a new one
        track["enrolled"] = True
        if not face or face.get("person_id") is None:
            return None
        return (face["person_id"], name, 1.0)

    def reset(self):
        self._tracks.clear()


WEB_COLOR = hex_to_bgr("#f59e0b")  # amber — visually distinct from known/unknown
# watchlist category -> box colour (alert reds for 'watch', accents otherwise)
CATEGORY_COLORS = {
    "watch": hex_to_bgr("#ef4444"),   # red alert
    "staff": hex_to_bgr("#34d399"),   # green
    "vip": hex_to_bgr("#a78bfa"),     # purple
}


def draw_faces(frame: np.ndarray, tracks: list[dict], face_cfg: dict) -> int:
    overlay = face_cfg.get("overlay", {})
    known_color = hex_to_bgr(overlay.get("box_color", "#38bdf8"))
    unknown_color = hex_to_bgr(overlay.get("unknown_color", "#ef4444"), (68, 68, 239))
    thickness = int(overlay.get("box_thickness", 2))
    label_scale = float(overlay.get("label_scale", 0.55))
    show_score = overlay.get("show_score", True)
    show_landmarks = overlay.get("show_landmarks", False)

    for track in tracks:
        x, y, w, h = (int(v) for v in track["bbox"])
        known = track["name"] is not None
        web_name = track.get("web_name")

        category = track.get("category", "none")
        if known:
            color = CATEGORY_COLORS.get(category, known_color)
            tag = f"[{category.upper()}] " if category != "none" else ""
            label = f"{tag}{track['name']}"
            if show_score:
                label += f" {track['score']:.2f}"
        elif web_name:
            color = WEB_COLOR
            label = f"? {web_name}"
        else:
            color = unknown_color
            label = "Unknown"

        cv2.rectangle(frame, (x, y), (x + w, y + h), color, thickness)

        if show_landmarks:
            row = track["row"]
            for i in range(5):
                cx, cy = int(row[4 + i * 2]), int(row[5 + i * 2])
                cv2.circle(frame, (cx, cy), 2, color, -1)

        (tw, th), _ = cv2.getTextSize(label, FONT, label_scale, 2)
        ty = max(y, th + 8)
        cv2.rectangle(frame, (x, ty - th - 8), (x + tw + 6, ty), color, -1)
        cv2.putText(frame, label, (x + 3, ty - 4), FONT, label_scale, (10, 12, 16), 2)

    return len(tracks)


# ------------------------------ frame loop --------------------------------

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
        with state_lock:
            cam = dict(state["camera"])
            state["camera_restart"] = False
            wanted = state["camera_on"] and state["viewers"] > 0
        if not wanted:
            # switch off, or nobody watching the feed: keep the camera closed
            publish(message_frame("Camera is off"), 0.0, 0, "-", error="camera off")
            time.sleep(0.3)
            continue
        cap = open_camera(index=cam.get("index", 0),
                          width=cam["width"], height=cam["height"])
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
            with state_lock:
                if (state["camera_restart"]
                        or not (state["camera_on"] and state["viewers"] > 0)):
                    cap.release()
                    break  # reopen at the new size, or park until wanted again
            ok, frame = cap.read()
            if not ok:
                cap.release()
                break  # reopen the camera
            yield frame


def pipeline(source):
    fps = 0.0
    tracker = FaceTracker()
    last_mode = None
    for frame in frame_source(source):
        with state_lock:
            cfg = {k: v for k, v in state.items() if k != "camera"}
            cfg["face"] = dict(state["face"])

        if cfg["mode"] != last_mode:
            tracker.reset()
            last_mode = cfg["mode"]

        start = time.perf_counter()

        try:
            count, name = _process_frame(frame, cfg, tracker)
        except Exception as exc:  # never let one bad frame kill the feed
            print(f"pipeline error: {exc!r}")
            publish(message_frame(f"Processing error:\n{exc}"), fps, 0, "-",
                    error=str(exc))
            time.sleep(0.5)
            continue
        if count is None:  # face models still loading
            time.sleep(0.25)
            continue

        inst = 1.0 / max(time.perf_counter() - start, 1e-6)
        fps = inst if fps == 0 else fps * 0.9 + inst * 0.1
        publish(frame, fps, count, name)


def _process_frame(frame, cfg: dict, tracker: "FaceTracker"):
    """Run one frame through the selected model; returns (count, model_name),
    or (None, name) when the face models are still downloading."""
    if cfg["mode"] == "faces":
        engine = face_runtime["engine"]
        if engine is None:
            publish(message_frame("Face models loading..."), 0.0, 0, "faces",
                    error="face models loading")
            return None, "faces"
        dets = engine.detect(frame)
        tracks = tracker.update(dets, frame, cfg["face"])
        count = draw_faces(frame, tracks, cfg["face"])
        name = "yunet+sface"
    else:
        name = FASTSAM_MODEL if cfg["mode"] == "sam" else YOLO_SIZES[cfg["size"]]
        model = bank.get(name)

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

    return count, name


def start(source=None) -> threading.Thread:
    thread = threading.Thread(target=pipeline, args=(source,), daemon=True)
    thread.start()
    return thread


# ---------------------------- camera discovery -----------------------------

def _macos_camera_names() -> list[str]:
    """Camera names from system_profiler; order matches AVFoundation indices."""
    import json as _json
    import subprocess
    try:
        out = subprocess.run(
            ["system_profiler", "-json", "SPCameraDataType"],
            capture_output=True, timeout=10,
        ).stdout
        return [c.get("_name", "")
                for c in _json.loads(out).get("SPCameraDataType", [])]
    except Exception:
        return []


def list_cameras(max_index: int = 4) -> list[dict]:
    """Probe camera indices. The index the pipeline is using is reported
    without probing (it is obviously available, and re-opening an in-use
    device can glitch the capture on some platforms)."""
    import sys
    names = _macos_camera_names() if sys.platform == "darwin" else []
    with state_lock:
        active = state["camera"].get("index", 0)

    found = []
    for i in range(max_index + 1):
        if i == active:
            ok = True
        else:
            cap = open_camera(index=i, width=640, height=480)
            ok = cap.isOpened()
            cap.release()
        if ok:
            found.append({
                "index": i,
                "name": names[i] if i < len(names) else f"Camera {i}",
                "active": i == active,
            })
    return found
