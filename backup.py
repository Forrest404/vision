"""Automatic, self-contained backups so face data is never lost.

On every server startup we snapshot the whole library (database + all photo,
thumbnail and crop images) into data/backups/ as one timestamped .zip, then
keep the most recent few. Restoring is just unzipping over data/. This is the
safety net: even if something deletes the live data, the last session's
images are still on disk.
"""

import shutil
import zipfile
from pathlib import Path

import face_db as fdb

KEEP = 8  # how many recent backups to retain


def _stamp() -> str:
    # Date.now()/new Date() are fine here (plain runtime, not a workflow)
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def startup_backup() -> Path | None:
    """Zip the current DB + media into data/backups/. Returns the zip path,
    or None if there is nothing to back up yet."""
    if not fdb.DB_PATH.exists():
        return None
    # nothing enrolled yet -> skip (avoids a pile of empty backups)
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{fdb.DB_PATH}?mode=ro", uri=True)
        n = con.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        con.close()
    except Exception:
        n = 1  # if we can't tell, back up anyway
    if not n:
        return None

    backups = fdb.DATA_DIR / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    dest = backups / f"backup-{_stamp()}.zip"
    if dest.exists():
        return dest

    tmp = dest.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:  # JPEGs already compressed
            for extra in ("", "-wal", "-shm"):  # include WAL so recent writes are captured
                p = Path(str(fdb.DB_PATH) + extra)
                if p.exists():
                    zf.write(p, f"data/{p.name}")
            for d in (fdb.PHOTOS_DIR, fdb.THUMBS_DIR, fdb.CROPS_DIR):
                for f in d.glob("*"):
                    if f.is_file():
                        zf.write(f, f"data/media/{d.name}/{f.name}")
        tmp.rename(dest)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        print(f"backup failed: {exc!r}")
        return None

    _prune(backups)
    size_mb = dest.stat().st_size / 1e6
    print(f"Backup saved: {dest.name} ({size_mb:.1f} MB)")
    return dest


def _prune(backups: Path):
    zips = sorted(backups.glob("backup-*.zip"))
    for old in zips[:-KEEP]:
        old.unlink(missing_ok=True)


def restore(zip_path: str | Path):
    """Restore a backup zip over data/ (used from the CLI, not the server)."""
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(fdb.DATA_DIR.parent)
    print(f"Restored {zip_path} into {fdb.DATA_DIR}")


if __name__ == "__main__":  # `python backup.py [restore <zip>]`
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "restore":
        restore(sys.argv[2])
    else:
        print(startup_backup() or "nothing to back up")
