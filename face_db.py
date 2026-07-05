"""On-device face database: SQLite rows + JPEG files under data/.

Layout:
  data/faces.db            SQLite (persons, photos, faces, settings)
  data/media/photos/       full photos, {photo_id:06d}.jpg
  data/media/thumbs/       gallery thumbnails, max 480px
  data/media/crops/        one face crop per detected face, {face_id}.jpg

Only data/media is served over HTTP (mounted at /media); faces.db is not.

Concurrency: one connection shared by all threads, every access serialized
by FaceDB._lock. The camera pipeline never touches SQLite directly — it
reads EmbeddingIndex.snapshot, an immutable tuple rebound atomically after
each rebuild, so the hot path takes no locks.
"""

import hashlib
import io
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "faces.db"
MEDIA_DIR = DATA_DIR / "media"
PHOTOS_DIR = MEDIA_DIR / "photos"
THUMBS_DIR = MEDIA_DIR / "thumbs"
CROPS_DIR = MEDIA_DIR / "crops"

THUMB_MAX = 480
CROP_MARGIN = 0.25  # extra context around the face box in crops

_SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL COLLATE NOCASE,
  cover_face_id INTEGER,
  created_at    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS photos (
  id            INTEGER PRIMARY KEY,
  filename      TEXT NOT NULL DEFAULT '',
  original_name TEXT,
  width         INTEGER,
  height        INTEGER,
  sha256        TEXT UNIQUE,
  created_at    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS faces (
  id         INTEGER PRIMARY KEY,
  photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
  person_id  INTEGER REFERENCES persons(id) ON DELETE SET NULL,
  x REAL, y REAL, w REAL, h REAL,
  landmarks  TEXT,
  det_score  REAL,
  embedding  BLOB NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);
CREATE INDEX IF NOT EXISTS idx_faces_photo  ON faces(photo_id);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

DEFAULT_SETTINGS = {
    "schema_version": 1,
    "rec_threshold": 0.363,   # SFace cosine: >= means same person
    "det_score": 0.7,         # YuNet detector confidence
    "overlay": {
        "box_color": "#38bdf8",
        "unknown_color": "#ef4444",
        "show_landmarks": False,
        "show_score": True,
        "label_scale": 0.55,
        "box_thickness": 2,
    },
    "camera": {"width": 1280, "height": 720, "index": 0},
}


def decode_image(data: bytes) -> np.ndarray | None:
    """Bytes -> BGR ndarray, honoring EXIF orientation (cv2.imdecode ignores
    it, which would store phone photos sideways and break face geometry)."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        return None
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


@dataclass(frozen=True)
class IndexSnapshot:
    """Immutable matcher state; rebound atomically on every rebuild."""
    matrix: np.ndarray        # (N, 128) labeled embeddings, L2-normalized
    face_ids: np.ndarray      # (N,)
    person_ids: np.ndarray    # (N,)
    names: dict               # person_id -> name
    all_matrix: np.ndarray    # (M, 128) every embedding, incl. unlabeled
    all_face_ids: np.ndarray
    all_person_ids: np.ndarray  # -1 for unlabeled


_EMPTY = np.empty((0, 128), dtype=np.float32)
_NO_IDS = np.empty((0,), dtype=np.int64)


class EmbeddingIndex:
    def __init__(self, db: "FaceDB"):
        self._db = db
        self.snapshot = IndexSnapshot(_EMPTY, _NO_IDS, _NO_IDS, {}, _EMPTY, _NO_IDS, _NO_IDS)
        self.rebuild()

    def rebuild(self):
        rows = self._db.all_embeddings()
        names = self._db.person_names()
        if rows:
            all_matrix = np.stack([r[2] for r in rows])
            all_face_ids = np.array([r[0] for r in rows], dtype=np.int64)
            all_person_ids = np.array(
                [r[1] if r[1] is not None else -1 for r in rows], dtype=np.int64
            )
        else:
            all_matrix, all_face_ids, all_person_ids = _EMPTY, _NO_IDS, _NO_IDS
        labeled = all_person_ids >= 0
        self.snapshot = IndexSnapshot(
            all_matrix[labeled], all_face_ids[labeled], all_person_ids[labeled],
            names, all_matrix, all_face_ids, all_person_ids,
        )

    def match(self, emb: np.ndarray, threshold: float):
        """Best labeled match: (person_id, name, score) or None."""
        s = self.snapshot
        if len(s.matrix) == 0:
            return None
        scores = s.matrix @ emb
        i = int(np.argmax(scores))
        if scores[i] < threshold:
            return None
        pid = int(s.person_ids[i])
        return pid, s.names.get(pid, "?"), float(scores[i])

    def match_all(self, emb: np.ndarray, threshold: float) -> list[dict]:
        """Every stored face (labeled or not) scoring >= threshold, best first."""
        s = self.snapshot
        if len(s.all_matrix) == 0:
            return []
        scores = s.all_matrix @ emb
        order = np.argsort(-scores)
        out = []
        for i in order:
            if scores[i] < threshold:
                break
            pid = int(s.all_person_ids[i])
            out.append({
                "face_id": int(s.all_face_ids[i]),
                "person_id": pid if pid >= 0 else None,
                "score": float(scores[i]),
            })
        return out


class FaceDB:
    def __init__(self, db_path: Path = DB_PATH):
        for d in (PHOTOS_DIR, THUMBS_DIR, CROPS_DIR):
            d.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(
                "PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;" + _SCHEMA
            )
            self._conn.commit()
        self.index = EmbeddingIndex(self)

    # ------------------------------- photos -------------------------------

    def add_photo(self, data: bytes, original_name: str | None):
        """Store the photo file + row. Returns (photo dict, bgr, is_duplicate);
        bgr is None when the image cannot be decoded."""
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM photos WHERE sha256=?", (digest,)
            ).fetchone()
        if row:
            img = cv2.imread(str(PHOTOS_DIR / row["filename"]))
            return dict(row), img, True

        bgr = decode_image(data)
        if bgr is None:
            return None, None, False
        h, w = bgr.shape[:2]
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO photos (original_name, width, height, sha256) VALUES (?,?,?,?)",
                    (original_name, w, h, digest),
                )
            except sqlite3.IntegrityError:
                # same file uploaded concurrently in another request thread
                row = self._conn.execute(
                    "SELECT * FROM photos WHERE sha256=?", (digest,)
                ).fetchone()
                return dict(row), bgr, True
            photo_id = cur.lastrowid
            filename = f"{photo_id:06d}.jpg"
            self._conn.execute(
                "UPDATE photos SET filename=? WHERE id=?", (filename, photo_id)
            )
            self._conn.commit()

        cv2.imwrite(str(PHOTOS_DIR / filename), bgr,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
        scale = THUMB_MAX / max(w, h)
        thumb = bgr if scale >= 1 else cv2.resize(bgr, (round(w * scale), round(h * scale)))
        cv2.imwrite(str(THUMBS_DIR / filename), thumb,
                    [cv2.IMWRITE_JPEG_QUALITY, 85])

        return self.get_photo(photo_id), bgr, False

    def get_photo(self, photo_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM photos WHERE id=?", (photo_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_photos(self, person_id: int | None = None,
                    page: int = 1, page_size: int = 48) -> dict:
        offset = (max(page, 1) - 1) * page_size
        with self._lock:
            if person_id is None:
                total = self._conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
                rows = self._conn.execute(
                    "SELECT * FROM photos ORDER BY id DESC LIMIT ? OFFSET ?",
                    (page_size, offset),
                ).fetchall()
            else:
                total = self._conn.execute(
                    "SELECT COUNT(DISTINCT photo_id) FROM faces WHERE person_id=?",
                    (person_id,),
                ).fetchone()[0]
                rows = self._conn.execute(
                    "SELECT DISTINCT p.* FROM photos p JOIN faces f ON f.photo_id=p.id "
                    "WHERE f.person_id=? ORDER BY p.id DESC LIMIT ? OFFSET ?",
                    (person_id, page_size, offset),
                ).fetchall()
        return {"total": total, "items": [dict(r) for r in rows]}

    def delete_photo(self, photo_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT filename FROM photos WHERE id=?", (photo_id,)
            ).fetchone()
            if not row:
                return False
            face_ids = [r[0] for r in self._conn.execute(
                "SELECT id FROM faces WHERE photo_id=?", (photo_id,)
            ).fetchall()]
            self._conn.execute("DELETE FROM photos WHERE id=?", (photo_id,))
            self._conn.commit()
        (PHOTOS_DIR / row["filename"]).unlink(missing_ok=True)
        (THUMBS_DIR / row["filename"]).unlink(missing_ok=True)
        for fid in face_ids:
            (CROPS_DIR / f"{fid}.jpg").unlink(missing_ok=True)
        self.index.rebuild()
        return True

    # -------------------------------- faces -------------------------------

    def add_face(self, photo_id: int, bbox, landmarks, det_score: float,
                 embedding: np.ndarray, image: np.ndarray | None = None) -> int:
        """Insert a face row; if `image` is given, also save its crop JPEG."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO faces (photo_id, x, y, w, h, landmarks, det_score, embedding) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (photo_id, *[float(v) for v in bbox], json.dumps(landmarks),
                 float(det_score), embedding.astype(np.float32).tobytes()),
            )
            face_id = cur.lastrowid
            self._conn.commit()
        if image is not None:
            self.save_crop(face_id, image, bbox)
        return face_id

    @staticmethod
    def save_crop(face_id: int, image: np.ndarray, bbox):
        x, y, w, h = bbox
        mx, my = w * CROP_MARGIN, h * CROP_MARGIN
        ih, iw = image.shape[:2]
        x1 = max(0, int(x - mx)); y1 = max(0, int(y - my))
        x2 = min(iw, int(x + w + mx)); y2 = min(ih, int(y + h + my))
        if x2 > x1 and y2 > y1:
            cv2.imwrite(str(CROPS_DIR / f"{face_id}.jpg"), image[y1:y2, x1:x2],
                        [cv2.IMWRITE_JPEG_QUALITY, 90])

    def faces_of_photo(self, photo_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.*, p.name AS person_name FROM faces f "
                "LEFT JOIN persons p ON p.id=f.person_id WHERE f.photo_id=? ORDER BY f.id",
                (photo_id,),
            ).fetchall()
        return [self._face_dict(r) for r in rows]

    def get_face(self, face_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT f.*, p.name AS person_name FROM faces f "
                "LEFT JOIN persons p ON p.id=f.person_id WHERE f.id=?",
                (face_id,),
            ).fetchone()
        return self._face_dict(row) if row else None

    @staticmethod
    def _face_dict(row) -> dict:
        d = dict(row)
        d.pop("embedding", None)
        d["landmarks"] = json.loads(d["landmarks"]) if d.get("landmarks") else None
        return d

    def label_face(self, face_id: int, person_id: int | None = None,
                   name: str | None = None) -> dict | None:
        """Assign a face to a person (by id or by created/found name), or
        clear the label with person_id=None and name=None."""
        if name:
            person_id = self.find_or_create_person(name)
        with self._lock:
            self._conn.execute(
                "UPDATE faces SET person_id=? WHERE id=?", (person_id, face_id)
            )
            if person_id is not None:
                self._conn.execute(
                    "UPDATE persons SET cover_face_id=COALESCE(cover_face_id, ?) WHERE id=?",
                    (face_id, person_id),
                )
            self._conn.commit()
        return self.get_face(face_id)

    def delete_face(self, face_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM faces WHERE id=?", (face_id,))
            self._conn.execute(
                "UPDATE persons SET cover_face_id=NULL WHERE cover_face_id=?", (face_id,)
            )
            self._conn.commit()
        (CROPS_DIR / f"{face_id}.jpg").unlink(missing_ok=True)
        if cur.rowcount:
            self.index.rebuild()
        return bool(cur.rowcount)

    def faces_by_ids(self, face_ids: list[int]) -> dict[int, dict]:
        """Fetch many faces at once (for search results); id -> face dict."""
        if not face_ids:
            return {}
        marks = ",".join("?" * len(face_ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT f.*, p.name AS person_name FROM faces f "
                f"LEFT JOIN persons p ON p.id=f.person_id WHERE f.id IN ({marks})",
                face_ids,
            ).fetchall()
        return {r["id"]: self._face_dict(r) for r in rows}

    def all_embeddings(self) -> list[tuple[int, int | None, np.ndarray]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, person_id, embedding FROM faces"
            ).fetchall()
        return [
            (r["id"], r["person_id"],
             np.frombuffer(r["embedding"], dtype=np.float32))
            for r in rows
        ]

    # ------------------------------- persons ------------------------------

    def find_or_create_person(self, name: str) -> int:
        name = name.strip()
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM persons WHERE name=?", (name,)
            ).fetchone()
            if row:
                return row["id"]
            cur = self._conn.execute(
                "INSERT INTO persons (name) VALUES (?)", (name,)
            )
            self._conn.commit()
            return cur.lastrowid

    def person_names(self) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT id, name FROM persons").fetchall()
        return {r["id"]: r["name"] for r in rows}

    def list_persons(self, q: str = "") -> list[dict]:
        sql = (
            "SELECT p.id, p.name, "
            "COALESCE(p.cover_face_id, MIN(f.id)) AS cover_face_id, "
            "COUNT(f.id) AS face_count, COUNT(DISTINCT f.photo_id) AS photo_count "
            "FROM persons p LEFT JOIN faces f ON f.person_id=p.id "
        )
        args: tuple = ()
        if q:
            sql += "WHERE p.name LIKE ? "
            args = (f"%{q}%",)
        sql += "GROUP BY p.id ORDER BY p.name"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def get_person(self, person_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM persons WHERE id=?", (person_id,)
            ).fetchone()
            if not row:
                return None
            faces = self._conn.execute(
                "SELECT id, photo_id, x, y, w, h, det_score FROM faces "
                "WHERE person_id=? ORDER BY id", (person_id,),
            ).fetchall()
        return {**dict(row), "faces": [dict(f) for f in faces]}

    def rename_person(self, person_id: int, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE persons SET name=? WHERE id=?", (name.strip(), person_id)
            )
            self._conn.commit()
        if cur.rowcount:
            self.index.rebuild()  # names live in the snapshot
        return bool(cur.rowcount)

    def merge_person(self, source_id: int, target_id: int) -> bool:
        if source_id == target_id:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM persons WHERE id=?", (target_id,)
            ).fetchone()
            src = self._conn.execute(
                "SELECT id FROM persons WHERE id=?", (source_id,)
            ).fetchone()
            if not row or not src:
                return False
            self._conn.execute(
                "UPDATE faces SET person_id=? WHERE person_id=?",
                (target_id, source_id),
            )
            self._conn.execute("DELETE FROM persons WHERE id=?", (source_id,))
            self._conn.execute(
                "UPDATE persons SET cover_face_id=COALESCE(cover_face_id, "
                "(SELECT id FROM faces WHERE person_id=? LIMIT 1)) WHERE id=?",
                (target_id, target_id),
            )
            self._conn.commit()
        self.index.rebuild()
        return True

    def delete_person(self, person_id: int) -> bool:
        """Remove the person; their faces stay in photos but become unlabeled."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
            self._conn.commit()
        if cur.rowcount:
            self.index.rebuild()
        return bool(cur.rowcount)

    # ------------------------------ settings ------------------------------

    def get_settings(self) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        stored = {r["key"]: json.loads(r["value"]) for r in rows}
        return _deep_merge(DEFAULT_SETTINGS, stored)

    def set_settings(self, partial: dict) -> dict:
        merged = _deep_merge(self.get_settings(), partial)
        with self._lock:
            for key, value in merged.items():
                self._conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )
            self._conn.commit()
        return merged


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
