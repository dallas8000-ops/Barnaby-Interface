"""Tablet access: a paired tablet on the home network drives this PC's Barnaby.

Off by default. When on, an HTTPS + WSS server on the LAN serves the same UI to
the tablet and admits only paired devices. See
docs/superpowers/specs/2026-09-28-barnaby-tablet-access-design.md.

Building blocks (pure logic, unit-tested):

* :class:`TlsMaterial`: Barnaby's own certificate authority plus a server
  certificate for the PC's current LAN addresses. The CA is **name
  constrained** (critical) to private address ranges, ``.local``,
  ``localhost`` and this PC's hostname. The tablet trusts it once, and even a
  leaked CA key couldn't be used to impersonate public websites to that tablet.
  The server certificate is reissued from the same CA when the LAN address
  changes, so the trust step isn't repeated.
* :class:`DeviceRegistry`: paired devices. Only SHA-256 hashes of their
  256-bit tokens are stored.
* :class:`Pairing`: one live single-use code at a time, 5-minute expiry.
* :class:`Lockout`: per-IP failure counting. Five failures in 10 minutes block
  that address for 10 minutes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

PRIVATE_NETS = [ipaddress.ip_network(n) for n in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16")]


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _write_private(path: Path, data: bytes) -> None:
    """Atomic write, owner-only on POSIX. On Windows the data dir is already per-user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def lan_addresses() -> list[str]:
    """This PC's private IPv4 addresses a tablet on the same network could reach."""
    found: set[str] = set()
    route: str | None = None
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:  # the address of the default route's interface; UDP connect sends no packets
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            route = s.getsockname()[0]
    except OSError:
        pass
    return order_addresses(route, found)


def order_addresses(route: str | None, found: set[str]) -> list[str]:
    """LAN addresses, most likely reachable first: the default route's interface, then
    192.168.x, then the rest. Virtual adapters (VirtualBox 192.168.56.1, Hyper-V, WSL)
    must not win over the real LAN, since the QR code uses the first one."""
    lan = {ip for ip in found | ({route} if route else set()) if _lan_ip(ip)}
    first = [route] if route in lan else []
    rest = sorted(lan - set(first), key=lambda a: (not a.startswith("192.168."), a))
    return first + rest


# RFC 1918 only. Python's is_private also covers documentation, benchmarking and
# other reserved ranges; a server certificate naming one of those would fall
# outside the CA's name constraints and Chrome would reject the whole chain.
LAN_NETS = PRIVATE_NETS[:3]


def _lan_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return isinstance(addr, ipaddress.IPv4Address) and any(addr in net for net in LAN_NETS)


def is_private_peer(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if isinstance(addr, ipaddress.IPv6Address):
        return addr.is_loopback or addr.is_link_local or addr in ipaddress.ip_network("fc00::/7")
    return any(addr in net for net in PRIVATE_NETS)


# ------------------------------------------------------------------ TLS
class TlsMaterial:
    CA_DAYS = 3650
    SERVER_DAYS = 397
    RENEW_BEFORE_DAYS = 30

    def __init__(self, directory: Path, *, hostname: str | None = None,
                 ips: Callable[[], list[str]] = lan_addresses) -> None:
        self.dir = Path(directory)
        self.hostname = (hostname or socket.gethostname()).strip().lower() or "barnaby-pc"
        self._ips = ips
        self._lock = threading.Lock()

    @property
    def cert_path(self) -> Path:
        return self.dir / "server.pem"

    @property
    def key_path(self) -> Path:
        return self.dir / "server.key"

    def _names(self) -> tuple[list[str], list[str]]:
        dns = sorted({self.hostname, f"{self.hostname}.local", "localhost"})
        ips = sorted({ip for ip in self._ips() if _lan_ip(ip)} | {"127.0.0.1"})
        return dns, ips

    def ensure(self) -> tuple[Path, Path]:
        """Create what's missing, reissue the server cert if stale. Returns (cert, key)."""
        with self._lock:
            ca_cert, ca_key = self._ensure_ca()
            if self._server_needs_issue():
                self._issue_server(ca_cert, ca_key)
            return self.cert_path, self.key_path

    def ca_pem(self) -> bytes:
        return (self.dir / "ca.pem").read_bytes()

    def ca_fingerprint(self) -> str:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        cert = x509.load_pem_x509_certificate(self.ca_pem())
        return ":".join(f"{b:02X}" for b in cert.fingerprint(hashes.SHA256()))

    def _ensure_ca(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        cert_p, key_p = self.dir / "ca.pem", self.dir / "ca.key"
        if cert_p.exists() and key_p.exists():
            try:
                cert = x509.load_pem_x509_certificate(cert_p.read_bytes())
                key = serialization.load_pem_private_key(key_p.read_bytes(), password=None)
                if cert.not_valid_after_utc - _now_utc() > dt.timedelta(days=self.RENEW_BEFORE_DAYS):
                    return cert, key
            except (ValueError, TypeError):
                log.warning("Tablet CA files unreadable; creating a new CA (the tablet must trust it again)")
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Barnaby local CA ({self.hostname})"),
                          x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Barnaby")])
        now = _now_utc()
        permitted = [x509.IPAddress(n) for n in PRIVATE_NETS] + \
                    [x509.DNSName("local"), x509.DNSName("localhost"), x509.DNSName(self.hostname)]
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(minutes=5))
                .not_valid_after(now + dt.timedelta(days=self.CA_DAYS))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                             key_encipherment=False, data_encipherment=False,
                                             key_agreement=False, key_cert_sign=True, crl_sign=True,
                                             encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None),
                               critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .sign(key, hashes.SHA256()))
        _write_private(key_p, key.private_bytes(serialization.Encoding.PEM,
                                                serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
        _write_private(cert_p, cert.public_bytes(serialization.Encoding.PEM))
        # A new CA invalidates any server cert signed by the old one.
        for stale in (self.cert_path, self.key_path):
            stale.unlink(missing_ok=True)
        return cert, key

    def _server_needs_issue(self) -> bool:
        from cryptography import x509
        if not (self.cert_path.exists() and self.key_path.exists()):
            return True
        try:
            cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        except ValueError:
            return True
        if cert.not_valid_after_utc - _now_utc() < dt.timedelta(days=self.RENEW_BEFORE_DAYS):
            return True
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have_ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
        have_dns = set(san.get_values_for_type(x509.DNSName))
        dns, ips = self._names()
        return not (set(ips) <= have_ips and set(dns) <= have_dns)

    def _issue_server(self, ca_cert, ca_key) -> None:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

        dns, ips = self._names()
        key = ec.generate_private_key(ec.SECP256R1())
        now = _now_utc()
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, self.hostname)]))
                .issuer_name(ca_cert.subject).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(minutes=5))
                .not_valid_after(now + dt.timedelta(days=self.SERVER_DAYS))
                .add_extension(x509.SubjectAlternativeName(
                    [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]),
                    critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                             key_encipherment=False, data_encipherment=False,
                                             key_agreement=False, key_cert_sign=False, crl_sign=False,
                                             encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                               critical=False)
                .sign(ca_key, hashes.SHA256()))
        _write_private(self.key_path, key.private_bytes(serialization.Encoding.PEM,
                                                        serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption()))
        _write_private(self.cert_path, cert.public_bytes(serialization.Encoding.PEM))
        log.info("Issued tablet TLS certificate for %s", ", ".join(dns + ips))


# ------------------------------------------------------------------ devices
def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class DeviceRegistry:
    MAX_DEVICES = 10
    TOUCH_EVERY_S = 60

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._devices: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            devices = data.get("devices", []) if isinstance(data, dict) else []
            return [d for d in devices if isinstance(d, dict) and {"id", "name", "token_sha256"} <= set(d)]
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            log.warning("Paired tablet list unreadable; starting empty (tablets must pair again)")
            return []

    def _save(self) -> None:
        _write_private(self.path, json.dumps({"devices": self._devices}, indent=2).encode())

    def pair(self, name: str) -> tuple[str, str]:
        with self._lock:
            if len(self._devices) >= self.MAX_DEVICES:
                raise ValueError(f"At most {self.MAX_DEVICES} paired tablets. Remove one first.")
            token = secrets.token_urlsafe(32)
            device_id = "dev_" + secrets.token_urlsafe(8).replace("-", "a").replace("_", "b")
            now = self._clock()
            self._devices.append({"id": device_id, "name": name[:60], "token_sha256": _sha256(token),
                                  "created": now, "last_seen": now})
            self._save()
            return device_id, token

    def authenticate(self, token: str) -> str | None:
        if not token:
            return None
        digest = _sha256(token)
        match = None
        with self._lock:
            for d in self._devices:  # compare against every entry: no early exit timing signal
                if hmac.compare_digest(d["token_sha256"], digest):
                    match = d["id"]
        return match

    def touch(self, device_id: str) -> None:
        with self._lock:
            for d in self._devices:
                if d["id"] == device_id:
                    now = self._clock()
                    if now - float(d.get("last_seen") or 0) >= self.TOUCH_EVERY_S:
                        d["last_seen"] = now
                        self._save()

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            before = len(self._devices)
            self._devices = [d for d in self._devices if d["id"] != device_id]
            if len(self._devices) == before:
                return False
            self._save()
            return True

    def has(self, device_id: str) -> bool:
        with self._lock:
            return any(d["id"] == device_id for d in self._devices)

    def full(self) -> bool:
        with self._lock:
            return len(self._devices) >= self.MAX_DEVICES

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"id": d["id"], "name": d["name"], "created": d.get("created"),
                     "last_seen": d.get("last_seen")} for d in self._devices]


# ------------------------------------------------------------------ pairing & lockout
class Pairing:
    TTL_S = 300

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._code: str | None = None
        self._expires = 0.0

    def start(self) -> tuple[str, float]:
        self._code = secrets.token_urlsafe(12)  # 96 bits
        self._expires = self._clock() + self.TTL_S
        return self._code, self._expires

    def valid(self, code: str) -> bool:
        """Check without using the code up."""
        live = self._code
        return bool(live) and self._clock() <= self._expires and hmac.compare_digest(code.encode(), live.encode())

    def consume(self, code: str) -> bool:
        live = self._code
        if live is None or self._clock() > self._expires:
            self._code = None
            return False
        if not hmac.compare_digest(code.encode(), live.encode()):
            return False
        self._code = None
        return True

    @property
    def expires_at(self) -> float | None:
        return self._expires if self._code and self._clock() <= self._expires else None


class Lockout:
    LIMIT = 5
    WINDOW_S = 600
    BLOCK_S = 600

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._fails: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}

    def blocked(self, ip: str) -> bool:
        until = self._blocked_until.get(ip)
        if until is None:
            return False
        if self._clock() >= until:
            del self._blocked_until[ip]
            return False
        return True

    def fail(self, ip: str) -> None:
        now = self._clock()
        recent = [t for t in self._fails.get(ip, []) if now - t < self.WINDOW_S] + [now]
        self._fails[ip] = recent
        if len(recent) >= self.LIMIT:
            self._blocked_until[ip] = now + self.BLOCK_S
            self._fails[ip] = []
            log.warning("Tablet access: blocked %s for %d minutes after repeated failures", ip, self.BLOCK_S // 60)
        if len(self._fails) > 1024:  # bound memory against address spraying
            oldest = sorted(self._fails, key=lambda k: max(self._fails[k] or [0]))[:512]
            for k in oldest:
                self._fails.pop(k, None)

    def clear(self, ip: str) -> None:
        self._fails.pop(ip, None)


# ------------------------------------------------------------------ network profile
def windows_network_categories() -> set[str] | None:
    """Windows' category for each connected network (Public / Private / DomainAuthenticated).
    None if it can't be determined."""
    if sys.platform != "win32":
        return None
    import subprocess
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-NetConnectionProfile | ForEach-Object { $_.NetworkCategory.ToString() }) -join ','"],
            capture_output=True, text=True, timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    cats = {c.strip() for c in out.stdout.split(",") if c.strip()}
    return cats or None


def network_problem(categories: Callable[[], set[str] | None] = windows_network_categories) -> str | None:
    """Refuse tablet access when every connected network is Public (a café, a hotel).
    Private-address checks can't tell those apart from home: they use 10.x / 192.168.x too."""
    cats = categories()
    if cats is None:
        return None  # couldn't tell; the firewall rule (Private profile only) still applies
    if cats & {"Private", "DomainAuthenticated"}:
        return None
    return ("Your network is set to Public in Windows, so tablet access stays off. On your home network, "
            "set it to Private: Settings → Network & internet → your connection → Private network.")


# ------------------------------------------------------------------ server
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self' data:; connect-src 'self'; media-src 'self' blob:; worker-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json",
          ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".webmanifest": "application/manifest+json",
          ".txt": "text/plain; charset=utf-8", ".map": "application/json"}
_NOT_BUILT = (b"<!doctype html><meta charset=utf-8><title>Barnaby</title>"
              b"<p>Barnaby's tablet page isn't built on this PC. In the app folder run <code>npm run build</code>.</p>")
FIRST_FRAME_TIMEOUT_S = 10
# websockets logs every plain-HTTP response (each page file) as "connection rejected" at INFO.
_WS_LOG = logging.getLogger("barnaby_engine.remote.ws")
_WS_LOG.setLevel(logging.WARNING)
REMOTE_MAX_FRAME = 3 * 1024 * 1024  # after auth: a 60 s clip in base64 is ~2.6 MB
FIRST_FRAME_MAX = 4096               # before auth: an auth or pair frame is a few hundred bytes
MAX_PENDING_PER_IP = 4               # unauthenticated sockets waiting on their first frame
MAX_PENDING = 16


def _default_static_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "app" / "dist"


class RemoteServer:
    """HTTPS + WSS listener for paired tablets. Hands authenticated sockets to the Bridge."""

    def __init__(self, bridge: Any, *, data_dir: Path, port: int = 8766, static_dir: Path | None = None,
                 host: str = "0.0.0.0", ips: Callable[[], list[str]] = lan_addresses,
                 on_change: Callable[[], None] | None = None,
                 peer_allowed: Callable[[str], bool] = is_private_peer,
                 network_check: Callable[[], str | None] = network_problem,
                 clock: Callable[[], float] = time.time) -> None:
        self.bridge = bridge
        self.dir = Path(data_dir)
        self.port_requested = port
        self.port: int | None = None
        self.static_dir = Path(static_dir) if static_dir else _default_static_dir()
        self.host = host
        self._ips = ips
        self._on_change = on_change or (lambda: None)
        self._peer_allowed = peer_allowed
        self.tls = TlsMaterial(self.dir, ips=ips)
        self.registry = DeviceRegistry(self.dir / "devices.json", clock=clock)
        self.pairing = Pairing(clock=clock)
        self.lockout = Lockout(clock=clock)
        self._server: Any = None
        self._network_check = network_check
        self._ssl_ctx: Any = None
        self._cert_bytes = b""
        self._tls_checked = 0.0
        self._clock = clock
        self._pending: dict[str, int] = {}

    @property
    def running(self) -> bool:
        return self._server is not None

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        import ssl
        from websockets.asyncio.server import serve

        problem = await asyncio_to_thread(self._network_check)
        if problem:
            raise RuntimeError(problem)
        cert, key = await asyncio_to_thread(self.tls.ensure)
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(str(cert), str(key))
        self._ssl_ctx, self._cert_bytes, self._tls_checked = ctx, cert.read_bytes(), self._clock()
        self._server = await serve(self._handler, self.host, self.port_requested, ssl=ctx,
                                   process_request=self._process_request, max_size=FIRST_FRAME_MAX,
                                   ping_interval=20, ping_timeout=20, server_header=None,
                                   logger=_WS_LOG)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("Tablet access listening on port %s", self.port)

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        for device_id in list(self.bridge.connected_devices()):
            # 1001 "going away", not 4403 "removed": the tablet keeps its pairing and
            # reconnects when tablet access is back.
            await self.bridge.disconnect_device(device_id, code=1001, reason="tablet access stopped")
        server.close()
        await server.wait_closed()
        log.info("Tablet access stopped")

    # ------------------------------------------------------------ management (desktop only)
    def refresh_tls(self) -> bool:
        """Reissue the server cert if the LAN address changed; new handshakes use it at once."""
        if self._ssl_ctx is None:
            return False
        self._tls_checked = self._clock()
        cert, key = self.tls.ensure()
        data = cert.read_bytes()
        if data == self._cert_bytes:
            return False
        self._ssl_ctx.load_cert_chain(str(cert), str(key))
        self._cert_bytes = data
        return True

    def urls(self) -> list[str]:
        if not self.port:
            return []
        if self._clock() - self._tls_checked > 60:
            self.refresh_tls()
        return [f"https://{ip}:{self.port}/" for ip in self._ips()]

    def start_pairing(self, host: str | None = None) -> tuple[str, float]:
        self.refresh_tls()
        code, expires = self.pairing.start()
        host = host or next(iter(self._ips()), None) or self.tls.hostname
        return f"https://{host}:{self.port}/#pair={code}", expires

    def ca_fingerprint(self) -> str:
        return self.tls.ca_fingerprint()

    def devices(self) -> list[dict[str, Any]]:
        live = self.bridge.connected_devices()
        return [{**d, "connected": d["id"] in live} for d in self.registry.list()]

    async def revoke(self, device_id: str) -> bool:
        removed = self.registry.revoke(device_id)
        await self.bridge.disconnect_device(device_id)
        return removed

    # ------------------------------------------------------------ HTTP
    def _response(self, status: int, body: bytes, content_type: str, *, cache: str = "no-store",
                  extra: list[tuple[str, str]] | None = None):
        import email.utils
        import http
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        headers = Headers([
            ("Date", email.utils.formatdate(usegmt=True)), ("Connection", "close"),
            ("Content-Length", str(len(body))), ("Content-Type", content_type),
            ("Content-Security-Policy", _CSP), ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"),
            ("Cross-Origin-Opener-Policy", "same-origin"), ("Cache-Control", cache),
            ("Permissions-Policy", "microphone=(self), camera=(), geolocation=()"),
            ("Strict-Transport-Security", "max-age=31536000"),
            *(extra or []),
        ])
        return Response(status, http.HTTPStatus(status).phrase, headers, body)

    def _static(self, raw_path: str):
        from urllib.parse import unquote, urlsplit

        path = unquote(urlsplit(raw_path).path)
        parts = [p for p in path.split("/") if p]
        if any(p in (".", "..") or "\\" in p or "\x00" in p or ":" in p for p in parts):
            return self._response(400, b"Bad path\n", "text/plain; charset=utf-8")
        root = self.static_dir.resolve()
        index = root / "index.html"
        if not index.is_file():
            return self._response(200, _NOT_BUILT, _TYPES[".html"])
        target = (root.joinpath(*parts)).resolve() if parts else index
        try:
            target.relative_to(root)
        except ValueError:
            return self._response(400, b"Bad path\n", "text/plain; charset=utf-8")
        if not target.is_file():
            if parts and "." in parts[-1]:
                return self._response(404, b"Not found\n", "text/plain; charset=utf-8")
            target = index  # client-side route
        ctype = _TYPES.get(target.suffix.lower(), "application/octet-stream")
        cache = "public, max-age=31536000, immutable" if parts[:1] == ["assets"] else "no-store"
        return self._response(200, target.read_bytes(), ctype, cache=cache)

    def _process_request(self, conn: Any, request: Any):
        peer = str(conn.remote_address[0]) if conn.remote_address else ""
        if not self._peer_allowed(peer):
            log.warning("Tablet access refused non-private address %s", peer)
            return self._response(403, b"Forbidden\n", "text/plain; charset=utf-8")
        if self.lockout.blocked(peer):
            return self._response(429, b"Too many failed attempts. Try again later.\n", "text/plain; charset=utf-8")
        if request.headers.get("Upgrade", "").lower() == "websocket":
            host = (request.headers.get("Host") or "").lower()
            origin = (request.headers.get("Origin") or "").lower()
            if not host or origin != f"https://{host}":
                log.warning("Tablet access refused socket origin %r for host %r", origin, host)
                return self._response(403, b"Forbidden origin\n", "text/plain; charset=utf-8")
            if request.path.split("?", 1)[0] != "/ws":
                return self._response(404, b"Not found\n", "text/plain; charset=utf-8")
            return None
        if request.path.split("?", 1)[0] == "/barnaby-ca.crt":
            return self._response(200, self.tls.ca_pem(), "application/x-x509-ca-cert",
                                  extra=[("Content-Disposition", 'attachment; filename="barnaby-ca.crt"')])
        return self._static(request.path)

    # ------------------------------------------------------------ sockets
    async def _handler(self, ws: Any) -> None:
        from .server import ClientInfo

        peer = str(ws.remote_address[0]) if ws.remote_address else ""
        if self.lockout.blocked(peer):
            await ws.close(code=4429, reason="too many failed attempts")
            return
        if self._pending.get(peer, 0) >= MAX_PENDING_PER_IP or sum(self._pending.values()) >= MAX_PENDING:
            await ws.close(code=1013, reason="try again later")
            return
        self._pending[peer] = self._pending.get(peer, 0) + 1
        try:
            device_id = await self._first_frame(ws, peer)
        finally:
            left = self._pending.get(peer, 1) - 1
            if left:
                self._pending[peer] = left
            else:
                self._pending.pop(peer, None)
        if device_id is None:
            return
        self.lockout.clear(peer)
        self.registry.touch(device_id)
        protocol = getattr(ws, "protocol", None)
        if protocol is not None:
            protocol.max_size = REMOTE_MAX_FRAME  # authenticated: audio clips allowed now
        self._on_change()
        try:
            await self.bridge.run_client(ws, ClientInfo(role="remote", device_id=device_id),
                                         admit=lambda: self.registry.has(device_id))
        finally:
            self.registry.touch(device_id)
            self._on_change()

    async def _first_frame(self, ws: Any, peer: str) -> str | None:
        """Authenticate or pair from the first frame. Returns the device id, or None
        after closing the socket (and counting a failure where one is due)."""
        import asyncio
        from pydantic import ValidationError
        from websockets.exceptions import ConnectionClosed

        from .protocol import Auth, parse_command, parse_pair

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=FIRST_FRAME_TIMEOUT_S)
        except (asyncio.TimeoutError, ConnectionClosed):
            await ws.close(code=4401, reason="unauthorized")
            return None
        device_id: str | None = None
        try:
            obj = json.loads(raw) if isinstance(raw, str) else None
            kind = obj.get("type") if isinstance(obj, dict) else None
            if kind == "pair":
                frame = parse_pair(raw)
                if self.pairing.valid(frame.code) and self.registry.full():
                    # A real, live code but no room: say why, keep the code, count nothing.
                    await ws.send(json.dumps({"type": "pair_error", "message":
                                              f"This PC already has {DeviceRegistry.MAX_DEVICES} paired tablets. "
                                              "Remove one in Barnaby's Settings on the PC, then scan again."}))
                    await ws.close(code=4409, reason="too many paired tablets")
                    return None
                if self.pairing.consume(frame.code):
                    device_id, token = self.registry.pair(frame.name)
                    try:
                        await ws.send(json.dumps({"type": "paired", "token": token, "device_id": device_id}))
                    except ConnectionClosed:
                        self.registry.revoke(device_id)  # the tablet never got its token: no orphan entry
                        return None
            elif kind == "auth":
                cmd = parse_command(raw)
                if isinstance(cmd, Auth):
                    device_id = self.registry.authenticate(cmd.token)
        except (ValidationError, ValueError, TypeError) as exc:
            log.info("Tablet access: rejected first frame from %s (%s)", peer, type(exc).__name__)
            device_id = None
        if device_id is None:
            self.lockout.fail(peer)
            await ws.close(code=4401, reason="unauthorized")
        return device_id


async def asyncio_to_thread(fn: Callable[[], Any]) -> Any:
    import asyncio
    return await asyncio.to_thread(fn)
