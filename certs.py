"""Local TLS certificates so iPhones can reach the server over HTTPS.

iOS only allows camera access (getUserMedia) on secure origins, so the
phone app must talk HTTPS. We mint our own tiny certificate authority
("FaceVision Local CA") once, then a server certificate signed by it that
covers this Mac's .local hostname and current LAN IPs. The phone installs
+ trusts the CA one time (see /pair), after which everything just works —
fully offline, nothing leaves the network.

Uses the openssl CLI that ships with macOS/Linux. All files in certs/
(gitignored).
"""

import re
import socket
import subprocess
from pathlib import Path

CERTS_DIR = Path(__file__).parent / "certs"
CA_KEY = CERTS_DIR / "ca.key"
CA_CERT = CERTS_DIR / "ca.crt"
SERVER_KEY = CERTS_DIR / "server.key"
SERVER_CERT = CERTS_DIR / "server.crt"
SAN_FILE = CERTS_DIR / "server.san"  # SAN list the current cert was made with


def _run(*args: str):
    subprocess.run(args, check=True, capture_output=True)


def primary_lan_ip() -> str | None:
    """The IP other devices on this network reach us at.

    Prefers real broadcast LAN interfaces (Wi-Fi/Ethernet, en0...) over VPN
    tunnels — with a VPN up, the default route points into the tunnel and
    that address is unreachable from the phone."""
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True,
                             timeout=5).stdout
        current = None
        candidates: dict[str, str] = {}  # interface -> ip
        for line in out.splitlines():
            if line and not line[0].isspace():
                current = line.split(":")[0]
            m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+).*broadcast", line)
            if (m and current and not m.group(1).startswith("127.")
                    and not current.startswith(("lo", "utun", "awdl", "llw",
                                                "bridge", "gif", "stf"))):
                candidates.setdefault(current, m.group(1))
        if candidates:
            return candidates[sorted(candidates)[0]]  # en0 before en1, ...
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        # fallback: default-route trick (may pick a VPN tunnel address)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))  # TEST-NET address; nothing is sent
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def lan_hostnames_and_ips() -> tuple[list[str], list[str]]:
    """(dns_names, ips) this machine is reachable as on the local network."""
    host = socket.gethostname().split(".")[0]
    names = [f"{host}.local", "localhost"]
    ips = {"127.0.0.1"}
    if primary := primary_lan_ip():
        ips.add(primary)
    try:  # every assigned IPv4, e.g. both Wi-Fi and Ethernet
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    return names, sorted(ips)


def _san_string() -> str:
    names, ips = lan_hostnames_and_ips()
    return ",".join([f"DNS:{n}" for n in names] + [f"IP:{i}" for i in ips])


def ensure_certs() -> tuple[Path, Path, Path]:
    """Return (server_cert, server_key, ca_cert), creating them on first run.
    The server cert is re-issued automatically when the LAN IPs change."""
    CERTS_DIR.mkdir(exist_ok=True)
    san = _san_string()

    if not (CA_KEY.exists() and CA_CERT.exists()):
        print("Creating local certificate authority (one time)...")
        _run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(CA_KEY), "-out", str(CA_CERT),
             "-days", "3650", "-subj", "/CN=FaceVision Local CA",
             "-addext", "basicConstraints=critical,CA:TRUE")
        SERVER_CERT.unlink(missing_ok=True)  # any old leaf is now orphaned

    up_to_date = (
        SERVER_CERT.exists() and SERVER_KEY.exists()
        and SAN_FILE.exists() and SAN_FILE.read_text() == san
    )
    if not up_to_date:
        print(f"Issuing HTTPS certificate for: {san}")
        csr = CERTS_DIR / "server.csr"
        _run("openssl", "req", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(SERVER_KEY), "-out", str(csr),
             "-subj", "/CN=FaceVision")
        ext = CERTS_DIR / "server.ext"
        # iOS requires SANs and a validity under 825 days to trust a cert
        ext.write_text(
            f"subjectAltName={san}\n"
            "basicConstraints=CA:FALSE\n"
            "keyUsage=digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
        )
        _run("openssl", "x509", "-req", "-in", str(csr),
             "-CA", str(CA_CERT), "-CAkey", str(CA_KEY), "-CAcreateserial",
             "-out", str(SERVER_CERT), "-days", "820",
             "-extfile", str(ext))
        SAN_FILE.write_text(san)
        csr.unlink(missing_ok=True)
        ext.unlink(missing_ok=True)

    return SERVER_CERT, SERVER_KEY, CA_CERT
