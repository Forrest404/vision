"""Authentication + role-based access for the compliance build.

Passwords: PBKDF2-HMAC-SHA256 with a per-user salt (stdlib only).
Sessions: a signed token cookie = base64(user_id|expiry).hmac_sha256, keyed
by a server secret persisted in data/secret.key. No external deps.

Roles (increasing power): viewer < operator < admin.
  viewer   — read/browse only
  operator — enroll, label, assign watchlist, delete photos/people
  admin    — everything, plus user management, settings, exports, erasure
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from fastapi import HTTPException, Request

import face_db as fdb

ROLES = ("viewer", "operator", "admin")
_RANK = {r: i for i, r in enumerate(ROLES)}
COOKIE = "fv_session"
SESSION_TTL = 7 * 24 * 3600  # a week

_SECRET_PATH = fdb.DATA_DIR / "secret.key"
_secret: bytes | None = None


def _server_secret() -> bytes:
    global _secret
    if _secret is None:
        fdb.DATA_DIR.mkdir(parents=True, exist_ok=True)
        if _SECRET_PATH.exists():
            _secret = _SECRET_PATH.read_bytes()
        else:
            _secret = secrets.token_bytes(32)
            _SECRET_PATH.write_bytes(_secret)
            os.chmod(_SECRET_PATH, 0o600)
    return _secret


# ------------------------------ passwords ---------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


# ------------------------------- sessions ---------------------------------

def create_session(user_id: int) -> str:
    payload = f"{user_id}|{int(time.time()) + SESSION_TTL}".encode()
    sig = hmac.new(_server_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload).decode() + "." + \
        base64.urlsafe_b64encode(sig).decode()


def _validate_token(token: str) -> int | None:
    try:
        p_b64, s_b64 = token.split(".")
        payload = base64.urlsafe_b64decode(p_b64)
        sig = base64.urlsafe_b64decode(s_b64)
        good = hmac.new(_server_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, good):
            return None
        user_id, expiry = payload.decode().split("|")
        if int(expiry) < time.time():
            return None
        return int(user_id)
    except (ValueError, TypeError):
        return None


# --------------------------- request helpers ------------------------------

def current_user(request: Request) -> dict | None:
    """The logged-in user dict, or None. Cached on request.state."""
    if getattr(request.state, "_user_cached", False):
        return request.state.user
    user = None
    token = request.cookies.get(COOKIE)
    if token:
        uid = _validate_token(token)
        db = _db()
        if uid is not None and db is not None:
            user = db.get_user_by_id(uid)
    request.state.user = user
    request.state._user_cached = True
    return user


def require(request: Request, role: str = "viewer") -> dict:
    """Raise 401/403 unless the caller is logged in with >= role."""
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "authentication required")
    if _RANK.get(user["role"], -1) < _RANK[role]:
        raise HTTPException(403, f"{role} role required")
    return user


def _db():
    import routes_faces
    return routes_faces.runtime.get("db")


# ------------------------------ bootstrap ---------------------------------

def bootstrap_admin(db: "fdb.FaceDB"):
    """Create the first admin with a random one-time password (printed once)
    if no users exist yet. Returns the plaintext password or None."""
    if db.user_count() > 0:
        return None
    pw = secrets.token_urlsafe(9)
    db.create_user("admin", hash_password(pw), "admin", must_change=True)
    print("\n" + "=" * 56)
    print("  FaceVision first-run: an admin account was created.")
    print("      username: admin")
    print(f"      password: {pw}")
    print("  Log in and change it immediately (Settings > Users).")
    print("=" * 56 + "\n")
    return pw
