"""Lightweight action log: record privacy-relevant actions (enrol, delete,
erase, export, settings changes) with a timestamp and client IP. There are no
user accounts, so entries are anonymous — the log is still useful as a record
of what changed and when.
"""

from fastapi import Request

import routes_faces


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def log(request: Request, action: str, target: str = "", detail: str = ""):
    db = routes_faces.runtime.get("db")
    if db is None:
        return
    db.add_audit(None, action, target, detail, client_ip(request))
