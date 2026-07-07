"""Phone companion endpoints: pairing page, CA download, live-stream WS.

The iPhone PWA (web/pages/phone.js) connects over HTTPS on the LAN and
streams camera frames through /ws/phone. Each connection gets its own
FaceTracker running on the shared upload engine, so phone recognition —
including numbered auto-capture — behaves exactly like the Mac camera
without ever stalling it.
"""

import io
import socket

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

import pipeline as pl
import routes_faces
from certs import CA_CERT, primary_lan_ip

router = APIRouter()

HTTP_PORT = 8000
HTTPS_PORT = 8443


def phone_enabled() -> bool:
    with pl.state_lock:
        return bool(pl.state.get("phone_enabled"))


def _require_enabled():
    if not phone_enabled():
        raise HTTPException(
            403, "iPhone pairing is turned off — enable it in Settings on the Mac")


def pair_url() -> str:
    host = primary_lan_ip() or f"{socket.gethostname().split('.')[0]}.local"
    return f"http://{host}:{HTTP_PORT}/pair"


# ------------------------------- pairing ----------------------------------

@router.get("/api/pair/qr.png")
def pairing_qr():
    """QR of the pairing URL — scan it with the iPhone camera."""
    _require_enabled()
    import qrcode
    img = qrcode.make(pair_url(), border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.get("/api/pair/info")
def pairing_info():
    return {"enabled": phone_enabled(), "url": pair_url()}


@router.get("/ca.crt")
def ca_certificate():
    """The local CA the iPhone installs + trusts (one time)."""
    _require_enabled()
    return FileResponse(CA_CERT, media_type="application/x-x509-ca-cert",
                        filename="FaceVision-CA.crt")


@router.get("/pair")
def pair_page():
    if not phone_enabled():
        return HTMLResponse(
            "<body style='background:#0b0c0e;color:#e9ecef;font-family:system-ui;"
            "padding:40px;text-align:center'><h2>Pairing is off</h2>"
            "<p>Turn on “iPhone camera” in Settings on the Mac, then scan the QR again.</p></body>",
            status_code=403)
    host = primary_lan_ip() or f"{socket.gethostname().split('.')[0]}.local"
    app_url = f"https://{host}:{HTTPS_PORT}/#/phone"
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect your iPhone — FaceVision</title>
<style>
  body {{ background:#0b0c0e; color:#e9ecef; font-family:-apple-system,system-ui,sans-serif;
         margin:0; padding:28px 20px; line-height:1.55; }}
  main {{ max-width:560px; margin:0 auto; }}
  h1 {{ font-size:1.4rem; }} h2 {{ font-size:1rem; margin:26px 0 6px; color:#38bdf8; }}
  a.button {{ display:inline-block; background:#38bdf8; color:#0b0c0e; font-weight:600;
              padding:12px 18px; border-radius:10px; text-decoration:none; margin:8px 0; }}
  code {{ background:#1c1f24; padding:2px 7px; border-radius:6px; font-size:.9em; }}
  p, li {{ color:#c8cdd3; }} .dim {{ color:#9aa3ad; font-size:.88em; }}
</style></head><body><main>
<h1>Connect your iPhone</h1>
<p>Three one-time steps. Everything stays on your Wi-Fi — no internet involved.</p>

<h2>1 &nbsp;Trust this Mac</h2>
<p><a class="button" href="/ca.crt">Download certificate</a></p>
<ol>
  <li>Tap <b>Allow</b> when Safari asks to download a configuration profile.</li>
  <li>Open <b>Settings</b> → <b>Profile Downloaded</b> → <b>Install</b>.</li>
  <li>Then <b>Settings → General → About → Certificate Trust Settings</b> →
      turn <b>ON</b> “FaceVision Local CA”.</li>
</ol>

<h2>2 &nbsp;Open the app</h2>
<p><a class="button" href="{app_url}">Open FaceVision</a><br>
<span class="dim">or visit <code>{app_url}</code></span></p>

<h2>3 &nbsp;Install it</h2>
<p>In Safari tap <b>Share</b> <span class="dim">(the square with an arrow)</span> →
<b>Add to Home Screen</b>. FaceVision appears as a real app with camera access.</p>

<p class="dim">Phone and Mac must be on the same Wi-Fi network. If the Mac's
address changes, revisit this page — the certificate covers the new address
automatically after a server restart.</p>
</main></body></html>""")


# ----------------------------- live stream WS ------------------------------

def _analyze(tracker: "pl.FaceTracker", jpeg: bytes) -> dict:
    """Decode a frame, track + recognize (+ auto-capture) — worker thread."""
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return {"error": "bad frame"}
    engine = routes_faces.runtime["engine"]
    if engine is None:
        return {"error": "face models not loaded"}

    with pl.state_lock:
        face_cfg = dict(pl.state["face"])

    dets = engine.detect(frame)
    tracks = tracker.update(dets, frame, face_cfg)
    h, w = frame.shape[:2]
    return {
        "w": w, "h": h,
        "faces": [
            {
                "bbox": {"x": float(t["bbox"][0]), "y": float(t["bbox"][1]),
                         "w": float(t["bbox"][2]), "h": float(t["bbox"][3])},
                "name": t["name"],
                "person_id": t["person_id"],
                "score": round(t["score"], 3),
            }
            for t in tracks
        ],
    }


@router.websocket("/ws/phone")
async def phone_stream(ws: WebSocket):
    """Ping-pong frame protocol: binary JPEG in -> JSON detections out.
    The client waits for each reply before sending the next frame, which
    self-adjusts the frame rate to what this Mac can process."""
    await ws.accept()
    if not phone_enabled():
        await ws.send_json({"error": "pairing is turned off in Settings"})
        await ws.close(code=1008)
        return
    engine = routes_faces.runtime["engine"]
    tracker = pl.FaceTracker(engine=engine)
    try:
        while True:
            jpeg = await ws.receive_bytes()
            if not phone_enabled():  # toggled off mid-stream
                await ws.close(code=1008)
                return
            result = await run_in_threadpool(_analyze, tracker, jpeg)
            await ws.send_json(result)
    except WebSocketDisconnect:
        pass
