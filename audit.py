"""Audit-trail helper: record every privacy-relevant action.

Every mutating request should call log(request, action, ...) so a store can
prove who did what and when — a baseline requirement for biometric systems.
"""

from fastapi import Request

import auth
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
    user = auth.current_user(request)
    db.add_audit(user["username"] if user else None,
                 action, target, detail, client_ip(request))
