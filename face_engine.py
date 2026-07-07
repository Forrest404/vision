"""Offline face detection + recognition built on OpenCV's YuNet and SFace.

YuNet  (cv2.FaceDetectorYN)   — face boxes + 5 landmarks, ~230 KB ONNX model.
SFace  (cv2.FaceRecognizerSF) — 128-d face embeddings,   ~37 MB ONNX model.

Both models are downloaded once from the opencv_zoo repo (see ensure_models);
after that everything runs fully offline on-device.
"""

import os
import ssl
import threading
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

MODELS_DIR = Path(__file__).parent / "models"

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"

_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
YUNET_URL = f"{_ZOO}/face_detection_yunet/{YUNET_FILE}"
SFACE_URL = f"{_ZOO}/face_recognition_sface/{SFACE_FILE}"

# SFace's documented cosine-similarity threshold: same person if score >= this.
COSINE_THRESHOLD = 0.363

# Uploads can be huge; YuNet works fine on a downscaled copy and SFace crops
# to 112x112 anyway, so cap the detection width and scale coordinates back.
MAX_DETECT_WIDTH = 1600


def _ssl_context() -> ssl.SSLContext:
    # macOS system Pythons often lack usable CA certs; use certifi's bundle
    # (already installed via ultralytics -> requests) when available.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _download(url: str, dest: Path, min_bytes: int):
    part = dest.with_suffix(dest.suffix + ".part")
    print(f"Downloading {dest.name} (one-time, ~{max(min_bytes // 1_000_000, 1)} MB)...")
    with urllib.request.urlopen(url, context=_ssl_context(), timeout=30) as resp, \
            open(part, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    if part.stat().st_size < min_bytes:
        part.unlink(missing_ok=True)
        raise OSError(f"{dest.name} download looks truncated")
    part.rename(dest)


def ensure_models(models_dir: Path = MODELS_DIR) -> tuple[Path, Path]:
    """Return (yunet_path, sface_path), downloading them on first run."""
    models_dir.mkdir(parents=True, exist_ok=True)
    needed = [
        (models_dir / YUNET_FILE, YUNET_URL, 200_000),      # ~230 KB
        (models_dir / SFACE_FILE, SFACE_URL, 30_000_000),   # ~37 MB
    ]
    for dest, url, min_bytes in needed:
        if dest.exists():
            if dest.stat().st_size >= min_bytes:
                continue
            dest.unlink()  # partial/corrupt leftover from an aborted download
        try:
            if os.environ.get("FACEVISION_NO_DOWNLOAD"):
                raise OSError("downloads disabled (FACEVISION_NO_DOWNLOAD)")
            _download(url, dest, min_bytes)
        except (urllib.error.URLError, OSError) as exc:
            raise SystemExit(
                f"Could not download {dest.name} ({exc}).\n"
                "The face models are fetched once and then everything is offline.\n"
                "Either connect to the internet and rerun, or download manually:\n"
                f"  {YUNET_URL}\n  {SFACE_URL}\n"
                f"and place the files in {models_dir}/"
            ) from exc
    return models_dir / YUNET_FILE, models_dir / SFACE_FILE


class FaceEngine:
    """Thread-confined YuNet + SFace wrapper.

    cv2 model objects are not documented as thread-safe, so every public
    method serializes on an internal lock. The server creates two engines:
    one owned by the camera pipeline thread and one shared by upload
    request threads, so uploads never stall the live feed.
    """

    def __init__(self, yunet_path: Path, sface_path: Path,
                 score_thresh: float = 0.7, nms_thresh: float = 0.3):
        self._lock = threading.Lock()
        self._det = cv2.FaceDetectorYN.create(
            str(yunet_path), "", (320, 320), score_thresh, nms_thresh, 50
        )
        self._rec = cv2.FaceRecognizerSF.create(str(sface_path), "")

    def set_score_threshold(self, value: float):
        with self._lock:
            self._det.setScoreThreshold(float(value))

    def detect(self, bgr: np.ndarray) -> np.ndarray:
        """Detect faces; returns (N, 15) float32 rows in ORIGINAL image coords:
        x, y, w, h, then 5 landmark (x, y) pairs, then score. Empty (0, 15) if none.
        """
        with self._lock:
            faces, _scale = self._detect_locked(bgr)
        return faces

    def _detect_locked(self, bgr: np.ndarray):
        h, w = bgr.shape[:2]
        scale = 1.0
        if w > MAX_DETECT_WIDTH:
            scale = MAX_DETECT_WIDTH / w
            bgr = cv2.resize(bgr, (MAX_DETECT_WIDTH, round(h * scale)))
            h, w = bgr.shape[:2]
        # YuNet silently misdetects unless the input size matches exactly.
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(bgr)
        if faces is None:
            return np.empty((0, 15), dtype=np.float32), scale
        faces = faces.astype(np.float32)
        if scale != 1.0:
            faces[:, :14] /= scale  # boxes + landmarks back to original coords
        return faces, scale

    def embed(self, bgr: np.ndarray, face_row: np.ndarray) -> np.ndarray:
        """Aligned 128-d L2-normalized embedding for one detected face row."""
        with self._lock:
            return self._embed_locked(bgr, face_row)

    def _embed_locked(self, bgr: np.ndarray, face_row: np.ndarray) -> np.ndarray:
        crop = self._rec.alignCrop(bgr, face_row.astype(np.float32))
        feat = self._rec.feature(crop).flatten().astype(np.float32)
        norm = float(np.linalg.norm(feat))
        if norm <= 0:  # degenerate output: match nothing rather than garbage
            return np.zeros_like(feat)
        return feat / norm

    def detect_and_embed(self, bgr: np.ndarray) -> list[dict]:
        """Detect all faces and embed each. Embeddings/crops run on the same
        (possibly downscaled) image YuNet saw, so its landmarks line up."""
        with self._lock:
            faces, scale = self._detect_locked(bgr)
            if len(faces) == 0:
                return []
            work = bgr
            if scale != 1.0:
                h, w = bgr.shape[:2]
                work = cv2.resize(bgr, (round(w * scale), round(h * scale)))
            out = []
            for row in faces:
                scaled_row = row.copy()
                scaled_row[:14] *= scale
                emb = self._embed_locked(work, scaled_row)
                out.append({
                    "bbox": [float(v) for v in row[:4]],
                    "landmarks": [[float(row[4 + i * 2]), float(row[5 + i * 2])]
                                  for i in range(5)],
                    "score": float(row[14]),
                    "embedding": emb,
                })
            return out

    @staticmethod
    def cosine(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity of two L2-normalized embeddings."""
        return float(np.dot(a, b))
