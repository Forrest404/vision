# FaceVision — audit results & backlog

Full-code review performed 2026-07-07 (two independent passes: backend + frontend).
Items marked ✅ are fixed; ⏳ are deliberate deferrals with reasoning.

## Bugs fixed ✅

### Backend
- ✅ **CSRF via side-effect GET endpoints** — any website you visited could hit
  `/set_mode`, `/toggle_boxes`, etc. on localhost. Middleware now rejects any
  request whose `Origin` doesn't match the `Host`.
- ✅ **Pipeline thread could die silently** — one exception in a frame killed the
  camera loop forever. The loop now catches per-frame errors, shows them on the
  feed, and keeps running.
- ✅ **Phone WebSocket engine contention** — if a phone connected before the
  models finished loading, its tracker permanently fell back to the Mac camera's
  private engine. Now binds the upload engine lazily per frame.
- ✅ **Settings snapshot froze future defaults** — `set_settings` persisted the
  entire merged dict, so improved defaults in later versions never applied.
  Now only user-changed keys are persisted.
- ✅ **Stale person avatars** — relabeling a face to another person left it as the
  old person's cover photo.
- ✅ **Upload batch died on one bad file** — per-file try/except; errors are
  reported per file, the rest of the batch proceeds.
- ✅ **Phone WS crashed on a bad frame** — processing errors now return
  `{"error": ...}` instead of killing the stream.
- ✅ **Zero-magnitude embedding edge case** — returns a zero vector (matches
  nothing) instead of unnormalized garbage.
- ✅ **O(V²) name voting** — now `collections.Counter`.

### Frontend
- ✅ **Modal Escape listeners leaked** on every backdrop-close.
- ✅ **Object-URL memory leaks** (identify/search/phone) — the router now revokes
  all blob URLs a page created when navigating away.
- ✅ **Phone review mutated dead DOM** if you left mid-identify/save.
- ✅ **Enroll popover could overflow** the photo edge (hardcoded width).
- ✅ **Double uploads** — dropzone locks during an in-flight batch.
- ✅ **No busy feedback** — spinners on Identify/Search.
- ✅ **Phone name sheet ignored Escape**.
- ✅ **Settings camera list wrote to detached DOM** after navigation.
- ✅ **Avatar initials had no accessible label**.
- ✅ **Service worker cache never invalidated** — versioned (`facevision-v2`);
  bump on release.

## Features added ✅
- ✅ **Gallery page** — browse every photo (newest first, paged), face-count
  badges, names in captions, lightbox with labeled boxes, per-photo delete.
- ✅ **Library stats** in Settings (people / photos / faces / unnamed).
- ✅ **One-click backup** — Settings → "Export backup (.zip)" downloads the
  database + all photos (WAL-checkpointed for consistency).

## Deferred ⏳ (with reasoning)
- ⏳ **PIN/auth for LAN access** — anyone on your Wi-Fi can open the app. The
  Origin guard stops drive-by browser attacks; a real pairing token would be the
  next step if you use untrusted networks.
- ⏳ **`web_search.py` integration** (Google Vision lookup of unknown faces) —
  written and opt-in via `GOOGLE_VISION_KEY`, but wiring it in sends face crops
  to Google, which breaks the fully-offline promise. Wire it only if you accept
  that; it needs ~20 lines in `FaceTracker._embed_and_vote`.
- ⏳ **Bulk photo selection / bulk delete** in Gallery.
- ⏳ **Explicit DB close on shutdown** — WAL mode makes crashes safe already.
- ⏳ **Import backup from the UI** (restore currently = unzip over `data/`).
- ⏳ **ANN index for embeddings** — brute-force cosine is <10 ms up to ~50k faces;
  revisit only past that scale.
- ⏳ **WebSocket Origin check** — HTTP middleware doesn't cover WS upgrades;
  low risk (gated by the pairing toggle), tidy up later.
- ⏳ **merge cover edge case** — cover_face_id could briefly reference a face
  deleted by a concurrent request; self-heals on next label change.
