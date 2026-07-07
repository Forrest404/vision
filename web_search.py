"""Async internet face lookup via Google Cloud Vision Web Detection.

Set GOOGLE_VISION_KEY=<key> to enable (free for 1 000 requests/month).
Get a key: console.cloud.google.com → Vision API → Credentials.

When an unknown face stays unrecognised for long enough, a cropped JPEG is
sent to Google Vision's Web Detection endpoint.  The best-scoring web entity
(usually a person's name) is returned and shown on the overlay in amber with
a "?" prefix so it is visually distinct from locally-enrolled faces.

Images are sent to Google's API and are subject to their privacy policy.
The feature is opt-in: it does nothing when GOOGLE_VISION_KEY is not set.
"""

import base64
import hashlib
import os
import queue
import threading
from typing import Callable, Optional

import requests

KEY = os.environ.get("GOOGLE_VISION_KEY", "")

# frames a track must survive before we spend an API call on it
WEB_SEARCH_MIN_AGE = 25

_q: "queue.Queue[tuple]" = queue.Queue()
_cache: dict[str, Optional[str]] = {}
_cache_lock = threading.Lock()


def lookup(
    emb_bytes: bytes,
    face_jpg: bytes,
    on_result: Callable[[Optional[str]], None],
) -> bool:
    """Queue an async web lookup.  Returns False when no key is configured."""
    if not KEY:
        return False
    h = hashlib.sha256(emb_bytes).hexdigest()[:24]
    with _cache_lock:
        if h in _cache:
            on_result(_cache[h])
            return True
    _q.put((h, face_jpg, on_result))
    return True


def _worker() -> None:
    while True:
        h, face_jpg, cb = _q.get()
        with _cache_lock:
            if h in _cache:
                cb(_cache[h])
                _q.task_done()
                continue
        name = _detect(face_jpg)
        with _cache_lock:
            _cache[h] = name
        cb(name)
        _q.task_done()


def _detect(face_jpg: bytes) -> Optional[str]:
    payload = {
        "requests": [{
            "image": {"content": base64.b64encode(face_jpg).decode()},
            "features": [{"type": "WEB_DETECTION", "maxResults": 5}],
        }]
    }
    try:
        r = requests.post(
            f"https://vision.googleapis.com/v1/images:annotate?key={KEY}",
            json=payload, timeout=10,
        )
        r.raise_for_status()
        ann = r.json()["responses"][0].get("webDetection", {})
        for e in ann.get("webEntities", []):
            if e.get("score", 0) >= 0.55 and e.get("description"):
                return e["description"]
        labels = ann.get("bestGuessLabels", [])
        if labels:
            return labels[0].get("label")
    except Exception:
        pass
    return None


threading.Thread(target=_worker, daemon=True, name="web-search").start()
