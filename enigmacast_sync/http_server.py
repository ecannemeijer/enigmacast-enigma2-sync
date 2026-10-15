"""HTTPS entrypoint. Plain HTTP is refused."""

from __future__ import annotations

import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from enigmacast_sync.service import (
    ApiError, assign_profiles, authenticate, health, issue_pairing, join, list_devices, list_profiles,
    make_backup, pair, push, put_playlist, recover, require_admin, revoke_device, server_info, sync_pull, upsert_profile,
)

PORT = 8740


def make_handler(db):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def log_message(self, fmt, *args):
            return

        def _route(self, method: str):
            parsed = urlparse(self.path)
            path = parsed.path
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                with lock:
                    return self._dispatch(method, parsed, raw)
            except ApiError as exc:
                self._json(exc.status, {"error": exc.message})
            except Exception:
                self._json(500, {"error": "server error"})

        def _dispatch(self, method, parsed, raw):
            path = parsed.path
            signed_path = path if not parsed.query else path + "?" + parsed.query
            if path == "/api/v1/health" and method == "GET":
                return self._json(200, health())
            if path == "/api/v1/server" and method == "GET":
                return self._json(200, server_info(db))
            if path == "/api/v1/pair" and method == "POST":
                return self._json(200, pair(db, _body(raw)))
            if path == "/api/v1/join" and method == "POST":
                return self._json(200, join(db, _body(raw)))
            if path == "/api/v1/recover" and method == "POST":
                return self._json(200, recover(db, str(_body(raw).get("code", ""))))
            device = authenticate(db, {k: v for k, v in self.headers.items()}, method, signed_path, raw)
            body = _body(raw) if raw else {}
            if path == "/api/v1/pairing" and method == "POST":
                return self._json(200, issue_pairing(db, device))
            if path == "/api/v1/devices" and method == "GET":
                return self._json(200, list_devices(db, device))
            if path == "/api/v1/devices/revoke" and method == "POST":
                return self._json(200, revoke_device(db, device, str(body.get("deviceId", ""))))
            if path == "/api/v1/devices/profiles" and method == "POST":
                return self._json(200, assign_profiles(db, device, str(body.get("deviceId", "")), list(body.get("profiles") or [])))
            if path == "/api/v1/profiles" and method == "GET":
                return self._json(200, list_profiles(db, device))
            if path == "/api/v1/profiles" and method == "POST":
                return self._json(200, upsert_profile(db, device, body))
            if path == "/api/v1/playlists" and method == "POST":
                return self._json(200, put_playlist(db, device, body))
            if path == "/api/v1/sync/push" and method == "POST":
                return self._json(200, push(db, device, body))
            if path == "/api/v1/sync/pull" and method == "GET":
                query = parse_qs(parsed.query)
                profile_id = (query.get("profileId") or [""])[0]
                cursor = int((query.get("cursor") or ["0"])[0])
                return self._json(200, sync_pull(db, device, profile_id, cursor))
            if path == "/api/v1/backups" and method == "POST":
                return self._json(200, make_backup(db, device))
            if path == "/api/v1/status" and method == "GET":
                require_admin(device)
                return self._json(200, server_info(db))
            self._json(404, {"error": "not found"})

        def _json(self, status: int, payload: dict):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve(data_dir: Path, cert: Path, key: Path, host: str = "0.0.0.0", port: int = PORT):
    from enigmacast_sync.service import open_db
    db = open_db(data_dir)
    httpd = ThreadingHTTPServer((host, port), make_handler(db))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    httpd.serve_forever()


def _body(raw: bytes) -> dict:
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))
