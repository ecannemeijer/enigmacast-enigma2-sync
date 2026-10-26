"""HTTPS entrypoint. Plain HTTP is refused."""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from enigmacast_sync.service import (
    ApiError, assign_profiles, authenticate, health, issue_pairing, join, list_devices, list_profiles,
    get_iptv_providers, get_playlist, make_backup, pair, push, put_iptv_providers, put_playlist,
    maintain, recover, require_admin, revoke_device, server_info, sync_pull, upsert_profile, version_info,
)
from enigmacast_sync import SERVER_VERSION

PORT = 8740
# A client that stops reading (or never finishes its TLS handshake) frees its thread after this.
SOCKET_TIMEOUT_S = 60
HANDSHAKE_TIMEOUT_S = 15
MAINTENANCE_EVERY_S = 6 * 60 * 60


def make_handler(db, lock: threading.Lock | None = None):
    lock = lock or threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 with an explicit "Connection: close": one request per TLS connection, and the
        # response length is always the exact byte count of the encoded body.
        protocol_version = "HTTP/1.0"
        server_version = "enigmacast-sync/" + SERVER_VERSION
        sys_version = ""
        timeout = SOCKET_TIMEOUT_S

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def log_message(self, fmt, *args):
            return

        def _route(self, method: str):
            parsed = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length else b""
                # Only the database work is serialised. Writing the response happens after the
                # lock is released, so one slow TV can no longer stall every other request.
                with lock:
                    status, payload = self._dispatch(method, parsed, raw)
            except ApiError as exc:
                status, payload = exc.status, {"error": exc.message}
            except Exception:
                status, payload = 500, {"error": "server error"}
            self._send(status, payload)

        def _dispatch(self, method, parsed, raw):
            path = parsed.path
            signed_path = path if not parsed.query else path + "?" + parsed.query
            if path == "/api/v1/version" and method == "GET":
                return (200, version_info())
            if path == "/api/v1/health" and method == "GET":
                return (200, health())
            if path == "/api/v1/server" and method == "GET":
                return (200, server_info(db))
            if path == "/api/v1/pair" and method == "POST":
                return (200, pair(db, _body(raw)))
            if path == "/api/v1/join" and method == "POST":
                return (200, join(db, _body(raw)))
            if path == "/api/v1/recover" and method == "POST":
                return (200, recover(db, str(_body(raw).get("code", ""))))
            device = authenticate(db, {k: v for k, v in self.headers.items()}, method, signed_path, raw)
            body = _body(raw) if raw else {}
            if path == "/api/v1/pairing" and method == "POST":
                return (200, issue_pairing(db, device))
            if path == "/api/v1/devices" and method == "GET":
                return (200, list_devices(db, device))
            if path == "/api/v1/devices/revoke" and method == "POST":
                return (200, revoke_device(db, device, str(body.get("deviceId", ""))))
            if path == "/api/v1/devices/profiles" and method == "POST":
                return (200, assign_profiles(db, device, str(body.get("deviceId", "")), list(body.get("profiles") or [])))
            if path == "/api/v1/profiles" and method == "GET":
                return (200, list_profiles(db, device))
            if path == "/api/v1/profiles" and method == "POST":
                return (200, upsert_profile(db, device, body))
            if path == "/api/v1/playlists" and method == "GET":
                return (200, get_playlist(db, device))
            if path == "/api/v1/playlists" and method == "POST":
                return (200, put_playlist(db, device, body))
            if path == "/api/v1/iptv/providers" and method == "GET":
                return (200, get_iptv_providers(db, device))
            if path == "/api/v1/iptv/providers" and method == "POST":
                return (200, put_iptv_providers(db, device, body))
            if path == "/api/v1/sync/push" and method == "POST":
                return (200, push(db, device, body))
            if path == "/api/v1/sync/pull" and method == "GET":
                query = parse_qs(parsed.query)
                profile_id = (query.get("profileId") or [""])[0]
                cursor = int((query.get("cursor") or ["0"])[0])
                limit = int((query.get("limit") or ["500"])[0])
                return (200, sync_pull(db, device, profile_id, cursor, limit))
            if path == "/api/v1/backups" and method == "POST":
                return (200, make_backup(db, device))
            if path == "/api/v1/status" and method == "GET":
                require_admin(device)
                return (200, server_info(db))
            return (404, {"error": "not found"})

        def _send(self, status: int, payload: dict) -> None:
            data = encode(payload)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
            except (OSError, ssl.SSLError):
                # The client went away mid-response. Never write a second (error) response onto
                # the same stream; just drop the connection.
                self.close_connection = True

    return Handler


def encode(payload: dict) -> bytes:
    """Compact ASCII JSON. Content-Length is computed from these bytes, never from str length."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


class TlsThreadingServer(ThreadingHTTPServer):
    """TLS per connection, with the handshake inside the worker thread.

    Wrapping the listening socket made every handshake run in the accept loop: one slow or
    stalled client blocked all others, and a failed handshake could take the loop down."""

    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, handler, context: ssl.SSLContext):
        self.tls = context
        super().__init__(address, handler)

    def get_request(self):
        sock, addr = self.socket.accept()
        sock.settimeout(HANDSHAKE_TIMEOUT_S)
        return sock, addr

    def process_request_thread(self, request, client_address):
        tls = None
        try:
            tls = self.tls.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
            tls.do_handshake()
            tls.settimeout(SOCKET_TIMEOUT_S)
            self.finish_request(tls, client_address)
        except Exception:
            pass
        finally:
            self._close(tls or request)

    @staticmethod
    def _close(sock) -> None:
        # Send TLS close_notify so the client sees a clean end of stream, then close.
        if isinstance(sock, ssl.SSLSocket):
            try:
                sock.settimeout(1)
                sock = sock.unwrap()
            except Exception:
                pass
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def handle_error(self, request, client_address):
        return


def serve(data_dir: Path, cert: Path, key: Path, host: str = "0.0.0.0", port: int = PORT):
    from enigmacast_sync.service import open_db
    db = open_db(data_dir)
    lock = threading.Lock()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    httpd = TlsThreadingServer((host, port), make_handler(db, lock), context)
    threading.Thread(target=_maintenance, args=(db, lock), daemon=True).start()
    httpd.serve_forever()


def _maintenance(db, lock: threading.Lock) -> None:
    """Prune applied history and compact the database outside any request."""
    time.sleep(60)
    while True:
        try:
            with lock:
                maintain(db)
        except Exception:
            pass
        time.sleep(MAINTENANCE_EVERY_S)


def _body(raw: bytes) -> dict:
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))
