"""Background data-retention: periodically purge old events and old
non-watchlisted captures, per the retention settings. Watchlisted people
are always kept. This is what enforces "don't hoard biometric data".
"""

import threading
import time

_STOP = threading.Event()
INTERVAL = 3600  # re-check hourly


def _run(db):
    while not _STOP.wait(5):  # first pass shortly after startup
        try:
            # Fold the WAL back into the main faces.db file so recent writes
            # are durable and the DB is self-contained between sessions.
            db.checkpoint()
            r = db.get_settings().get("retention", {})
            if r.get("enabled", False):  # OFF by default — never auto-delete
                removed = db.purge_old(
                    int(r.get("events_days", 30)),
                    int(r.get("unmatched_faces_days", 7)))
                if removed["photos"] or removed["events"]:
                    db.add_audit(None, "retention_purge", "",
                                 f"{removed['photos']} photos, {removed['events']} events", "")
                    print(f"retention: purged {removed}")
        except Exception as exc:  # never let the daemon die
            print(f"retention error: {exc!r}")
        _STOP.wait(INTERVAL)


def start(db) -> threading.Thread:
    t = threading.Thread(target=_run, args=(db,), daemon=True)
    t.start()
    return t
