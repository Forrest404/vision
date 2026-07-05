"""HTTP API for the on-device face database.

All endpoints are sync `def` on purpose: FastAPI runs them in its worker
threadpool, so SFace/YuNet inference on uploads never blocks the event
loop that streams /video_feed.

server.py injects the shared objects at startup:
    runtime["db"]     FaceDB
    runtime["engine"] upload FaceEngine (internally locked; separate from
                      the pipeline's engine so uploads don't stall the feed)
"""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

import face_db as fdb
import pipeline as pl

router = APIRouter(prefix="/api")

runtime = {"db": None, "engine": None}

MAX_FILES = 50
MAX_BYTES = 20 * 1024 * 1024


def _db() -> fdb.FaceDB:
    db = runtime["db"]
    if db is None:
        raise HTTPException(503, "database not ready")
    return db


def _engine():
    engine = runtime["engine"]
    if engine is None:
        raise HTTPException(
            503,
            "face models not loaded — start the server once with internet "
            "access (or place the ONNX files in models/) and restart",
        )
    return engine


def _threshold(db: fdb.FaceDB) -> float:
    return float(db.get_settings()["rec_threshold"])


def _photo_json(photo: dict) -> dict:
    return {
        "photo_id": photo["id"],
        "url": f"/media/photos/{photo['filename']}",
        "thumb_url": f"/media/thumbs/{photo['filename']}",
        "original_name": photo.get("original_name"),
        "width": photo["width"],
        "height": photo["height"],
        "created_at": photo.get("created_at"),
    }


def _face_json(face: dict) -> dict:
    return {
        "face_id": face["id"],
        "photo_id": face["photo_id"],
        "person_id": face["person_id"],
        "person_name": face.get("person_name"),
        "bbox": {"x": face["x"], "y": face["y"], "w": face["w"], "h": face["h"]},
        "landmarks": face.get("landmarks"),
        "score": face.get("det_score"),
        "crop_url": f"/media/crops/{face['id']}.jpg",
    }


# ------------------------------- uploads ----------------------------------

@router.post("/photos")
def upload_photos(files: list[UploadFile] = File(...)):
    """Batch enroll: store photos, detect + embed faces, suggest matches."""
    db, engine = _db(), _engine()
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"max {MAX_FILES} files per request")

    threshold = _threshold(db)
    results = []
    added_faces = False
    for up in files:
        data = up.file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            results.append({"original_name": up.filename, "error": "file too large (20 MB max)"})
            continue
        photo, bgr, duplicate = db.add_photo(data, up.filename)
        if photo is None:
            results.append({"original_name": up.filename, "error": "could not decode image"})
            continue

        if duplicate:
            faces = [
                {**_face_json(f), "suggestion": None}
                for f in db.faces_of_photo(photo["id"])
            ]
        else:
            faces = []
            for det in engine.detect_and_embed(bgr):
                face_id = db.add_face(
                    photo["id"], det["bbox"], det["landmarks"],
                    det["score"], det["embedding"], image=bgr,
                )
                hit = db.index.match(det["embedding"], threshold)
                faces.append({
                    "face_id": face_id,
                    "photo_id": photo["id"],
                    "person_id": None,
                    "person_name": None,
                    "bbox": dict(zip("xywh", det["bbox"])),
                    "landmarks": det["landmarks"],
                    "score": det["score"],
                    "crop_url": f"/media/crops/{face_id}.jpg",
                    "suggestion": (
                        {"person_id": hit[0], "name": hit[1], "score": round(hit[2], 3)}
                        if hit else None
                    ),
                })
                added_faces = True

        results.append({**_photo_json(photo), "duplicate": duplicate, "faces": faces})

    if added_faces:
        db.index.rebuild()  # make the new (unlabeled) faces searchable
    return {"results": results}


@router.post("/identify")
def identify(file: UploadFile = File(...)):
    """Detect + match faces in an uploaded image. Persists nothing."""
    db, engine = _db(), _engine()
    data = file.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (20 MB max)")
    bgr = fdb.decode_image(data)
    if bgr is None:
        raise HTTPException(400, "could not decode image")

    threshold = _threshold(db)
    faces = []
    for det in engine.detect_and_embed(bgr):
        hit = db.index.match(det["embedding"], threshold)
        faces.append({
            "bbox": dict(zip("xywh", det["bbox"])),
            "landmarks": det["landmarks"],
            "score": det["score"],
            "match": (
                {"person_id": hit[0], "name": hit[1], "score": round(hit[2], 3)}
                if hit else None
            ),
        })
    h, w = bgr.shape[:2]
    return {"width": w, "height": h, "faces": faces}


# ------------------------------- labeling ---------------------------------

class LabelItem(BaseModel):
    face_id: int
    person_id: int | None = None
    name: str | None = None


class LabelBody(BaseModel):
    labels: list[LabelItem]


@router.post("/faces/label")
def label_faces(body: LabelBody):
    """Batch face naming; one index rebuild at the end."""
    db = _db()
    updated = []
    for item in body.labels:
        face = db.label_face(item.face_id, person_id=item.person_id,
                             name=(item.name or "").strip() or None)
        if face:
            updated.append(_face_json(face))
    db.index.rebuild()
    return {"faces": updated}


@router.delete("/faces/{face_id}")
def delete_face(face_id: int):
    if not _db().delete_face(face_id):
        raise HTTPException(404, "face not found")
    return {"ok": True}


# -------------------------------- persons ---------------------------------

class RenameBody(BaseModel):
    name: str


class MergeBody(BaseModel):
    target_id: int


@router.get("/persons")
def list_persons(q: str = ""):
    persons = _db().list_persons(q)
    return [
        {
            "id": p["id"], "name": p["name"],
            "face_count": p["face_count"], "photo_count": p["photo_count"],
            "cover_url": (f"/media/crops/{p['cover_face_id']}.jpg"
                          if p["cover_face_id"] else None),
        }
        for p in persons
    ]


@router.get("/persons/{person_id}")
def get_person(person_id: int):
    db = _db()
    person = db.get_person(person_id)
    if not person:
        raise HTTPException(404, "person not found")
    photos = db.list_photos(person_id=person_id, page=1, page_size=500)
    return {
        "id": person["id"],
        "name": person["name"],
        "faces": [
            {
                "face_id": f["id"], "photo_id": f["photo_id"],
                "bbox": {"x": f["x"], "y": f["y"], "w": f["w"], "h": f["h"]},
                "score": f["det_score"],
                "crop_url": f"/media/crops/{f['id']}.jpg",
            }
            for f in person["faces"]
        ],
        "photos": [
            {**_photo_json(p), "faces": [_face_json(f) for f in db.faces_of_photo(p["id"])]}
            for p in photos["items"]
        ],
    }


@router.patch("/persons/{person_id}")
def rename_person(person_id: int, body: RenameBody):
    if not body.name.strip():
        raise HTTPException(400, "name required")
    if not _db().rename_person(person_id, body.name):
        raise HTTPException(404, "person not found")
    return {"ok": True, "id": person_id, "name": body.name.strip()}


@router.post("/persons/{person_id}/merge")
def merge_person(person_id: int, body: MergeBody):
    if not _db().merge_person(person_id, body.target_id):
        raise HTTPException(400, "merge failed (person missing or same id)")
    return {"ok": True, "target_id": body.target_id}


@router.delete("/persons/{person_id}")
def delete_person(person_id: int):
    if not _db().delete_person(person_id):
        raise HTTPException(404, "person not found")
    return {"ok": True}


# -------------------------------- photos ----------------------------------

@router.get("/photos")
def list_photos(person_id: int | None = None, page: int = 1, page_size: int = 48):
    db = _db()
    page_size = min(max(page_size, 1), 200)
    listing = db.list_photos(person_id=person_id, page=page, page_size=page_size)
    return {
        "total": listing["total"],
        "page": page,
        "items": [
            {**_photo_json(p), "faces": [_face_json(f) for f in db.faces_of_photo(p["id"])]}
            for p in listing["items"]
        ],
    }


@router.get("/photos/{photo_id}")
def get_photo(photo_id: int):
    db = _db()
    photo = db.get_photo(photo_id)
    if not photo:
        raise HTTPException(404, "photo not found")
    return {**_photo_json(photo),
            "faces": [_face_json(f) for f in db.faces_of_photo(photo_id)]}


@router.delete("/photos/{photo_id}")
def delete_photo(photo_id: int):
    if not _db().delete_photo(photo_id):
        raise HTTPException(404, "photo not found")
    return {"ok": True}


# --------------------------------- search ---------------------------------

@router.post("/search/face")
def search_face(file: UploadFile = File(...), face_index: int | None = Form(None)):
    """Find every stored photo containing the uploaded face."""
    db, engine = _db(), _engine()
    data = file.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (20 MB max)")
    bgr = fdb.decode_image(data)
    if bgr is None:
        raise HTTPException(400, "could not decode image")

    dets = engine.detect_and_embed(bgr)
    h, w = bgr.shape[:2]
    query_faces = [
        {"bbox": dict(zip("xywh", d["bbox"])), "score": d["score"]} for d in dets
    ]
    if not dets:
        return {"width": w, "height": h, "query_faces": [], "matches": []}
    if len(dets) > 1 and face_index is None:
        return {"width": w, "height": h, "query_faces": query_faces,
                "needs_selection": True, "matches": []}

    idx = face_index or 0
    if not 0 <= idx < len(dets):
        raise HTTPException(400, "face_index out of range")
    emb = dets[idx]["embedding"]

    threshold = _threshold(db)
    hits = db.index.match_all(emb, threshold)
    face_map = db.faces_by_ids([hit["face_id"] for hit in hits])

    # who is this? (best labeled hit)
    person = None
    for hit in hits:
        if hit["person_id"] is not None:
            face = face_map.get(hit["face_id"])
            person = {"id": hit["person_id"],
                      "name": face.get("person_name") if face else None,
                      "score": round(hit["score"], 3)}
            break

    # group hits by photo, best score first
    by_photo: dict[int, dict] = {}
    for hit in hits:
        face = face_map.get(hit["face_id"])
        if not face:
            continue
        entry = by_photo.setdefault(face["photo_id"], {"best": 0.0, "faces": []})
        entry["best"] = max(entry["best"], hit["score"])
        entry["faces"].append({**_face_json(face), "match_score": round(hit["score"], 3)})

    matches = []
    for photo_id, entry in sorted(by_photo.items(), key=lambda kv: -kv[1]["best"]):
        photo = db.get_photo(photo_id)
        if photo:
            matches.append({**_photo_json(photo), "best_score": round(entry["best"], 3),
                            "faces": entry["faces"]})

    return {"width": w, "height": h, "query_faces": query_faces,
            "selected_index": idx, "person": person, "matches": matches}


# -------------------------------- settings --------------------------------

@router.get("/cameras")
def list_cameras():
    """Probe attached cameras (with device names on macOS)."""
    return {"cameras": pl.list_cameras()}


@router.get("/settings")
def get_settings():
    return _db().get_settings()


@router.post("/settings")
def set_settings(partial: dict):
    """Merge, persist, and push into the live pipeline immediately."""
    merged = _db().set_settings(partial)
    apply_settings(merged)
    return merged


def apply_settings(settings: dict):
    """Push persisted settings into the running pipeline + engines."""
    with pl.state_lock:
        pl.state["face"] = {
            "rec_threshold": settings["rec_threshold"],
            "overlay": dict(settings["overlay"]),
        }
        cam = settings.get("camera") or {}
        new_cam = {
            "width": int(cam.get("width", 1280)),
            "height": int(cam.get("height", 720)),
            "index": int(cam.get("index", 0)),
        }
        if new_cam != pl.state["camera"]:
            pl.state["camera"] = new_cam
            pl.state["camera_restart"] = True
    for engine in (pl.face_runtime["engine"], runtime["engine"]):
        if engine is not None:
            engine.set_score_threshold(settings["det_score"])
