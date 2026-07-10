"""Compliance endpoints: audit/event logs, right-to-erasure, and per-person
data export. No authentication — open like the rest of the API.
"""

import io
import json
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import audit
import face_db as fdb
import routes_faces

router = APIRouter(prefix="/api")


def _db() -> fdb.FaceDB:
    db = routes_faces.runtime.get("db")
    if db is None:
        raise HTTPException(503, "database not ready")
    return db


# --------------------------- audit + events -------------------------------

@router.get("/audit")
def get_audit(request: Request, limit: int = 200, action: str = ""):
    return _db().query_audit(limit, action)


@router.get("/events")
def get_events(request: Request, limit: int = 100, person_id: int | None = None):
    return _db().query_events(limit, person_id)


# ------------------------- erasure + export -------------------------------

@router.delete("/persons/{person_id}/erase")
def erase_person(request: Request, person_id: int):
    """Right-to-erasure: remove the person, their photos/faces, and events."""
    removed = _db().erase_person(person_id)
    if not removed:
        raise HTTPException(404, "person not found")
    audit.log(request, "erase_person", str(person_id), json.dumps(removed))
    return {"ok": True, "removed": removed}


@router.get("/persons/{person_id}/export")
def export_person(request: Request, person_id: int):
    """Download everything stored about one person (photos + JSON record)."""
    db = _db()
    person = db.get_person(person_id)
    if not person:
        raise HTTPException(404, "person not found")
    photos = db.list_photos(person_id=person_id, page=1, page_size=1000)["items"]
    record = {
        "person": {"id": person["id"], "name": person["name"],
                   "category": person.get("category")},
        "faces": person["faces"],
        "photos": [{"id": p["id"], "filename": p["filename"],
                    "created_at": p["created_at"]} for p in photos],
        "events": db.query_events(1000, person_id),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("record.json", json.dumps(record, indent=2))
        for p in photos:
            fp = fdb.PHOTOS_DIR / p["filename"]
            if fp.exists():
                zf.write(fp, f"photos/{p['filename']}")
    audit.log(request, "export_person", str(person_id))
    safe = "".join(c for c in person["name"] if c.isalnum()) or str(person_id)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="person-{safe}.zip"'})
