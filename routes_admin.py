"""Admin + compliance endpoints: user management, audit/event logs,
right-to-erasure, and per-person data export. All admin-gated except the
logs which operators may also read.
"""

import io
import json
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

import audit
import auth
import face_db as fdb
import routes_faces

router = APIRouter(prefix="/api")


def _db() -> fdb.FaceDB:
    db = routes_faces.runtime.get("db")
    if db is None:
        raise HTTPException(503, "database not ready")
    return db


# ------------------------------- users ------------------------------------

class NewUser(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class RoleBody(BaseModel):
    role: str


@router.get("/users")
def list_users(request: Request):
    auth.require(request, "admin")
    return _db().list_users()


@router.post("/users")
def create_user(request: Request, body: NewUser):
    auth.require(request, "admin")
    if body.role not in auth.ROLES:
        raise HTTPException(400, "invalid role")
    if len(body.password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    db = _db()
    if db.get_user(body.username):
        raise HTTPException(409, "username already exists")
    uid = db.create_user(body.username, auth.hash_password(body.password), body.role)
    audit.log(request, "create_user", body.username, body.role)
    return {"ok": True, "id": uid}


@router.patch("/users/{user_id}")
def set_role(request: Request, user_id: int, body: RoleBody):
    auth.require(request, "admin")
    if body.role not in auth.ROLES:
        raise HTTPException(400, "invalid role")
    if not _db().set_user_role(user_id, body.role):
        raise HTTPException(404, "user not found")
    audit.log(request, "set_role", str(user_id), body.role)
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(request: Request, user_id: int):
    me = auth.require(request, "admin")
    if me["id"] == user_id:
        raise HTTPException(400, "you cannot delete your own account")
    db = _db()
    admins = [u for u in db.list_users() if u["role"] == "admin"]
    target = db.get_user_by_id(user_id)
    if target and target["role"] == "admin" and len(admins) <= 1:
        raise HTTPException(400, "cannot delete the last admin")
    if not db.delete_user(user_id):
        raise HTTPException(404, "user not found")
    audit.log(request, "delete_user", str(user_id))
    return {"ok": True}


# --------------------------- audit + events -------------------------------

@router.get("/audit")
def get_audit(request: Request, limit: int = 200, action: str = ""):
    auth.require(request, "operator")
    return _db().query_audit(limit, action)


@router.get("/events")
def get_events(request: Request, limit: int = 100, person_id: int | None = None):
    auth.require(request, "viewer")
    return _db().query_events(limit, person_id)


# ------------------------- erasure + export -------------------------------

@router.delete("/persons/{person_id}/erase")
def erase_person(request: Request, person_id: int):
    """Right-to-erasure: remove the person, their photos/faces, and events."""
    auth.require(request, "admin")
    removed = _db().erase_person(person_id)
    if not removed:
        raise HTTPException(404, "person not found")
    audit.log(request, "erase_person", str(person_id), json.dumps(removed))
    return {"ok": True, "removed": removed}


@router.get("/persons/{person_id}/export")
def export_person(request: Request, person_id: int):
    """Download everything stored about one person (photos + JSON record)."""
    auth.require(request, "admin")
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
