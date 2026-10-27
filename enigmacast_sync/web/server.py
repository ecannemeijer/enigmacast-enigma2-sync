"""HTTPS dashboard on port 8766. It keeps running when the Sync API is stopped."""

from __future__ import annotations

import html
import json
import os
import re
import socket
import sqlite3
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from enigmacast_sync import small_thread_stacks
from enigmacast_sync.archive import ArchiveError, create_full_backup, open_bundle, restore_full_backup
from enigmacast_sync.db import backup
from enigmacast_sync.web import PORT
from enigmacast_sync.web.auth import (
    check_password, clear_cookie, cookie_header, cookie_value, ensure_password, new_session, read_session,
    session_secret, set_password,
)
from enigmacast_sync.web.control import run as run_control
from enigmacast_sync.web.lan import allowed
from enigmacast_sync.web.queries import (
    CpuSampler, api_listening, connect, devices, disk, memory, overview, pid_of, profile_detail, profiles, release_info,
    synchronisation, tail, uptime_seconds,
)

STATIC = Path(__file__).resolve().parent / "static"
ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
PAGES = frozenset({"overview", "server", "profiles", "devices", "sync"})
LOGS = {"sync": "sync.log", "web": "web.log"}
# Forms and JSON actions of the dashboard are a few hundred bytes. Anything larger is refused
# before it is read, so a client cannot make the receiver buffer a big upload.
MAX_BODY = 64 * 1024
# A full archive can be larger than a form, but it is still bounded so a malicious
# client cannot make a low-memory receiver buffer an unlimited upload.
RESTORE_MAX_BODY = 16 * 1024 * 1024
# An idle keep-alive connection (browsers keep several open) frees its thread after this.
IDLE_TIMEOUT_S = 30
# The TLS / plain HTTP sniff and the handshake run in the worker thread with this limit.
HANDSHAKE_TIMEOUT_S = 10
# web.log is rotated to web.log.1 above this size, so it never fills the flash or the RAM of tail().
LOG_MAX_BYTES = 256 * 1024
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"})


def make_server(
    data: Path,
    host: str = "0.0.0.0",
    port: int = PORT,
    control_script: Path | None = None,
    web_control_script: Path | None = None,
) -> ThreadingHTTPServer:
    data.mkdir(parents=True, exist_ok=True)
    ensure_password(data)
    secret = {"value": session_secret(data)}
    script = control_script or Path("/etc/init.d/enigmacast-sync")
    web_script = web_control_script or Path("/etc/init.d/enigmacast-web")
    cert, key = data / "cert.pem", data / "key.pem"
    secure = cert.is_file() and key.is_file()
    failures: dict[str, tuple[int, float]] = {}
    cpu = CpuSampler()
    handler = _handler(data, secret, secure, script, web_script, failures, cpu)
    context = None
    if secure:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    httpd = _DualProtocolServer((host, port), handler, context)
    httpd.daemon_threads = True
    _log(data, f"listening on {port} ({'https + http redirect' if secure else 'http'})")
    (data / "enigmacast-web.pid").write_text(str(os.getpid()), encoding="utf-8")
    return httpd


class _DualProtocolServer(ThreadingHTTPServer):
    """Accept both HTTPS and plain HTTP on 8766; plain HTTP is redirected in the handler."""

    def __init__(self, server_address, handler, ssl_context: ssl.SSLContext | None):
        self.ssl_context = ssl_context
        super().__init__(server_address, handler)

    def finish_request(self, request, client_address):
        # Runs in the worker thread. Sniffing and the TLS handshake used to run in the accept
        # loop (get_request), where one silent or slow client blocked every other browser for
        # up to 10 s.
        if self.ssl_context is None:
            return super().finish_request(request, client_address)
        try:
            request.settimeout(HANDSHAKE_TIMEOUT_S)
            first = request.recv(1, socket.MSG_PEEK)
            if not first:
                return None
            if first == b"\x16":
                request = self.ssl_context.wrap_socket(request, server_side=True)
        except (OSError, ssl.SSLError):
            return None
        try:
            return super().finish_request(request, client_address)
        finally:
            if isinstance(request, ssl.SSLSocket):
                try:
                    request.close()
                except OSError:
                    pass

    def handle_error(self, request, client_address):
        # A browser that drops a connection is normal; never print a traceback per socket.
        return


def serve(data: Path, host: str = "0.0.0.0", port: int = PORT, control_script: Path | None = None) -> None:
    small_thread_stacks()
    try:
        httpd = make_server(data, host, port, control_script)
    except OSError as exc:
        _log(data, f"port {port} is not available")
        print(f"port {port} is not available: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    try:
        httpd.serve_forever()
    finally:
        pid = data / "enigmacast-web.pid"
        if pid.is_file():
            pid.unlink(missing_ok=True)


def _handler(data: Path, secret: dict, secure: bool, script: Path, web_script: Path, failures: dict, cpu: CpuSampler):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = IDLE_TIMEOUT_S

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def do_DELETE(self):
            self._route("DELETE")

        def do_PATCH(self):
            self._route("PATCH")

        def log_request(self, code="-", size="-"):
            self._log_line(str(getattr(code, "value", code)))

        def log_error(self, fmt, *args):
            # send_error() passes the reason, which for a garbled request line holds the raw
            # line itself (it once held a login form body). Only the status is logged.
            code = args[0] if args else "-"
            self._log_line(str(getattr(code, "value", code)))

        def log_message(self, fmt, *args):
            self._log_line("-")

        def _log_line(self, status: str):
            # Never the query string, the body or an unknown method: those may carry secrets.
            method = self.command if self.command in METHODS else "-"
            path = urlparse(self.path or "").path if method != "-" else "-"
            if not re.fullmatch(r"[A-Za-z0-9/._:-]{0,120}", path):
                path = "-"
            _log(data, f"{method} {path} {status}")

        def _route(self, method: str):
            # One handler object serves every request of a keep-alive connection, so the body of
            # this request must be read here, on every path, before anything is answered. A body
            # left in the socket would be parsed as the next request line.
            self._parsed_body = None
            self._raw_body = b""
            if not self._read_body():
                return
            # A browser that types http://<receiver>:8766 must not get a reset. Send it to the
            # HTTPS login instead; the sync API stays TLS-only.
            if secure and not isinstance(self.request, ssl.SSLSocket):
                host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
                self.send_response(301)
                self.send_header("Location", f"https://{host}{self.path}")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                return
            if not allowed(self.client_address[0]):
                return self._text(403, "forbidden")
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith("/static/"):
                if method != "GET":
                    return self._text(405, "method not allowed")
                return self._static(path[len("/static/"):])
            if method not in ("GET", "POST"):
                return self._text(405, "method not allowed")
            session = read_session(secret["value"], cookie_value(self.headers.get("Cookie", "")))
            if path == "/login" and method == "GET":
                return self._login_page(session)
            if path == "/login" and method == "POST":
                return self._login(session)
            if session is None or not session.get("auth"):
                if path.startswith("/api/"):
                    return self._json(401, {"error": "login required"})
                return self._redirect("/login")
            if method == "POST" and not self._csrf(session):
                return self._json(403, {"error": "csrf"})
            if path == "/logout" and method == "POST":
                return self._logout(session)
            if path == "/api/overview" and method == "GET":
                return self._json(200, self._overview())
            if path == "/api/server" and method == "GET":
                return self._json(200, self._server_status())
            if path == "/api/server" and method == "POST":
                return self._server_action()
            if path == "/api/backup" and method == "POST":
                return self._backup()
            if path == "/api/backup/full" and method == "GET":
                return self._backup_full()
            if path == "/api/restore" and method == "POST":
                return self._restore()
            if path == "/api/password" and method == "POST":
                return self._change_password()
            if path == "/api/logs" and method == "GET":
                which = (parse_qs(parsed.query).get("which") or ["web"])[0]
                if which not in LOGS:
                    return self._json(404, {"error": "not found"})
                return self._json(200, {"lines": tail(data / "logs" / LOGS[which])})
            if path == "/api/profiles" and method == "GET":
                query = parse_qs(parsed.query)
                sort = (query.get("sort") or ["name"])[0]
                try:
                    page = int((query.get("page") or ["1"])[0])
                except ValueError:
                    page = 1
                needle = (query.get("q") or [""])[0]
                with self._db() as db:
                    return self._json(200, profiles(db, needle, sort, page))
            if path.startswith("/api/profiles/") and method == "GET":
                uuid = path[len("/api/profiles/"):]
                if not ID_RE.fullmatch(uuid):
                    return self._json(404, {"error": "not found"})
                with self._db() as db:
                    detail = profile_detail(db, uuid)
                if detail is None:
                    return self._json(404, {"error": "not found"})
                return self._json(200, detail)
            if path == "/api/devices" and method == "GET":
                with self._db() as db:
                    return self._json(200, {"devices": devices(db)})
            if path == "/api/sync" and method == "GET":
                with self._db() as db:
                    payload = synchronisation(db)
                payload["log"] = tail(data / "logs" / "sync.log")
                return self._json(200, payload)
            if path == "/api/update" and method == "GET":
                return self._json(200, release_info())
            if path in ("/", "/overview") and method == "GET":
                return self._page("overview", "")
            if path == "/server" and method == "GET":
                return self._page("server", "")
            if path == "/profiles" and method == "GET":
                return self._page("profiles", "")
            if path == "/devices" and method == "GET":
                return self._page("devices", "")
            if path == "/sync" and method == "GET":
                return self._page("sync", "")
            if path.startswith("/profiles/") and method == "GET":
                uuid = path[len("/profiles/"):]
                if not ID_RE.fullmatch(uuid):
                    return self._text(404, "not found")
                return self._page("profile", uuid)
            return self._text(404, "not found")

        def _overview(self) -> dict:
            with self._db() as db:
                payload = overview(db, data)
            api_pid = pid_of(data / "enigmacast-sync.pid")
            web_pid = os.getpid()
            payload.update({
                "api": api_listening() or api_pid is not None,
                "web": True,
                "apiUptime": uptime_seconds(api_pid),
                "webUptime": uptime_seconds(web_pid),
                "cpu": cpu.percent(),
                "memory": payload.get("memory") or memory(),
                "disk": payload.get("disk") or disk(data),
            })
            return payload

        def _server_status(self) -> dict:
            api_pid = pid_of(data / "enigmacast-sync.pid")
            return {
                "api": api_listening() or api_pid is not None,
                "web": True,
                "port": PORT,
                "defaultPassword": check_password(data, "admin"),
            }

        def _server_action(self):
            action = str(self._body().get("action") or "")
            try:
                code = run_control(script, action)
            except (ValueError, FileNotFoundError):
                return self._json(400, {"error": "unsupported action"})
            _log(data, f"sync {action} exit {code}")
            return self._json(200, {"action": action, "exit": code})

        def _change_password(self):
            body = self._body()
            current = str(body.get("current") or "")
            chosen = str(body.get("password") or "")
            confirm = str(body.get("confirm") or "")
            if not check_password(data, current):
                return self._json(400, {"error": "Het huidige wachtwoord klopt niet."})
            if len(chosen) < 4:
                return self._json(400, {"error": "Kies minstens 4 tekens."})
            if chosen != confirm:
                return self._json(400, {"error": "De nieuwe wachtwoorden zijn niet gelijk."})
            set_password(data, chosen)
            renewed = data / "web.secret"
            if renewed.is_file():
                renewed.unlink()
            secret["value"] = session_secret(data)
            token, _csrf = new_session(secret["value"], True)
            _log(data, "dashboard password changed")
            return self._json(200, {"ok": True}, cookie_header(token, secure))

        def _backup(self):
            database = data / "sync.db"
            if not database.is_file():
                return self._json(409, {"error": "database missing"})
            target = backup(database, data / "master.key", data / "backups")
            _log(data, "database backup created")
            return self._json(200, {"path": str(target)})

        def _backup_full(self):
            try:
                target = create_full_backup(data)
            except (FileNotFoundError, OSError, ArchiveError) as exc:
                _log(data, f"full backup failed: {exc}")
                return self._json(409, {"error": "volledige back-up kon niet worden gemaakt"})
            _log(data, "full backup downloaded")
            return self._bytes(200, target.read_bytes(), "application/gzip", None, filename=target.name)

        def _restore(self):
            if not self._raw_body:
                return self._json(400, {"error": "geen back-up ontvangen"})
            upload = data / "backups" / f".restore-{os.getpid()}-{time.time_ns()}.tar.gz"
            upload.parent.mkdir(parents=True, exist_ok=True)
            try:
                upload.write_bytes(self._raw_body)
                # Validate before taking the Sync API down. A typo or the wrong file must
                # not interrupt the running server.
                try:
                    with open_bundle(upload):
                        pass
                except ArchiveError as exc:
                    return self._json(400, {"error": str(exc)})
                stop = run_control(script, "stop")
                if stop != 0:
                    return self._json(500, {"error": f"Sync API stoppen mislukt (code {stop})"})
                result = restore_full_backup(upload, data)
                start = run_control(script, "start")
                if start != 0:
                    _log(data, f"sync start after restore exit {start}")
                _log(data, f"full backup restored: {', '.join(result['files'])}")
                # The current web process still holds the old certificate and session secret.
                # Restart it just after this response has been written.
                timer = threading.Timer(1.5, self._restart_web)
                timer.daemon = True
                timer.start()
                return self._json(200, {
                    "restored": result["files"],
                    "safety": result["safety"],
                    "restarting": True,
                })
            except (ArchiveError, OSError, sqlite3.Error) as exc:
                _log(data, f"full restore failed: {exc}")
                run_control(script, "start")
                return self._json(400, {"error": str(exc)})
            finally:
                upload.unlink(missing_ok=True)

        def _restart_web(self):
            try:
                run_control(web_script, "restart")
            except (OSError, ValueError) as exc:
                _log(data, f"web restart after restore failed: {exc}")

        def _login_page(self, session: dict | None, message: str = "", status: int = 200):
            if session and session.get("auth") and not message:
                return self._redirect("/overview")
            token, _csrf = new_session(secret["value"], False, login=True)
            page = (STATIC / "login.html").read_text(encoding="utf-8")
            page = page.replace("__CSRF__", html.escape(token, quote=True))
            error = f'<p class="error">{html.escape(message)}</p>' if message else ""
            page = page.replace("__ERROR__", error)
            return self._html(status, page, cookie_header(token, secure))

        def _login(self, session: dict | None):
            ip = self.client_address[0]
            if _locked(failures, ip):
                return self._login_page(None, "Te veel pogingen. Wacht een minuut en probeer opnieuw.", 429)
            posted = str(self._body().get("csrf") or "")
            if not self._login_trusted(session, posted):
                return self._login_page(None, "De aanmelding is verlopen. Vernieuw de pagina en probeer opnieuw.", 403)
            if not check_password(data, str(self._body().get("password") or "")):
                _fail(failures, ip)
                return self._login_page(None, "Onjuist wachtwoord.", 401)
            failures.pop(ip, None)
            token, _csrf = new_session(secret["value"], True)
            self.send_response(303)
            self.send_header("Location", "/overview")
            self.send_header("Set-Cookie", cookie_header(token, secure))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _login_trusted(self, session: dict | None, posted: str) -> bool:
            challenge = read_session(secret["value"], posted)
            if challenge is not None and challenge.get("login") and not challenge.get("auth"):
                return True
            site = (self.headers.get("Sec-Fetch-Site") or "").lower()
            if site in ("cross-site", "same-site"):
                return False
            return bool(session) and not session.get("auth") and bool(session.get("csrf"))

        def _logout(self, session: dict | None):
            del session
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", clear_cookie(secure))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _page(self, page: str, identifier: str):
            if page not in PAGES and page != "profile":
                return self._text(404, "not found")
            html = (STATIC / "app.html").read_text(encoding="utf-8")
            html = html.replace("__CSRF__", str(self._session_csrf())).replace("__PAGE__", page).replace("__ID__", identifier)
            return self._html(200, html, None)

        def _session_csrf(self) -> str:
            session = read_session(secret["value"], cookie_value(self.headers.get("Cookie", "")))
            return "" if session is None else str(session.get("csrf") or "")

        def _csrf(self, session: dict) -> bool:
            import hmac
            supplied = self.headers.get("X-CSRF-Token") or str(self._body().get("csrf") or "")
            expected = str(session.get("csrf") or "")
            if not expected or len(expected) != len(supplied):
                return False
            return hmac.compare_digest(expected, supplied)

        def _read_body(self) -> bool:
            """Read exactly Content-Length bytes, or refuse and close the connection."""
            if self.headers.get("Transfer-Encoding"):
                return self._refuse(411, "length required")
            header = (self.headers.get("Content-Length") or "").strip()
            if not header:
                return True
            if not header.isdigit():
                return self._refuse(400, "bad request")
            length = int(header)
            limit = RESTORE_MAX_BODY if urlparse(self.path or "").path == "/api/restore" else MAX_BODY
            if length > limit:
                return self._refuse(413, "request too large")
            raw = self.rfile.read(length) if length else b""
            if len(raw) != length:
                self.close_connection = True
                return False
            self._raw_body = raw
            return True

        def _refuse(self, status: int, message: str) -> bool:
            # The unread body is still in the socket: answer and close, never keep-alive.
            self.close_connection = True
            self._bytes(status, message.encode("utf-8"), "text/plain; charset=utf-8", None, close=True)
            return False

        def _body(self) -> dict:
            if self._parsed_body is not None:
                return self._parsed_body
            raw = self._raw_body
            kind = self.headers.get("Content-Type", "")
            if "application/json" in kind:
                try:
                    loaded = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    loaded = {}
                self._parsed_body = loaded if isinstance(loaded, dict) else {}
            else:
                parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
                self._parsed_body = {key: values[-1] for key, values in parsed.items()}
            return self._parsed_body

        def _db(self):
            return _Database(data / "sync.db")

        def _static(self, name: str):
            if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
                return self._text(404, "not found")
            path = STATIC / name
            if not path.is_file():
                return self._text(404, "not found")
            kind = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(path.suffix, "application/octet-stream")
            return self._bytes(200, path.read_bytes(), kind, None)

        def _redirect(self, location: str):
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _html(self, status: int, page: str, cookie: str | None):
            return self._bytes(status, page.encode("utf-8"), "text/html; charset=utf-8", cookie)

        def _text(self, status: int, message: str):
            return self._bytes(status, message.encode("utf-8"), "text/plain; charset=utf-8", None)

        def _json(self, status: int, payload: dict, cookie: str | None = None):
            return self._bytes(status, json.dumps(payload).encode("utf-8"), "application/json", cookie)

        def _bytes(self, status: int, payload: bytes, content_type: str, cookie: str | None, close: bool = False, filename: str | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            if cookie:
                self.send_header("Set-Cookie", cookie)
            if close:
                self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

    return Handler


class _Database:
    def __init__(self, path: Path):
        self.path = path
        self.db = None

    def __enter__(self):
        self.db = connect(self.path)
        return self.db

    def __exit__(self, exc_type, exc, tb):
        if self.db is not None:
            self.db.close()


def _locked(failures: dict, ip: str) -> bool:
    count, until = failures.get(ip, (0, 0.0))
    return count >= 8 and time.time() < until


def _fail(failures: dict, ip: str) -> None:
    count, until = failures.get(ip, (0, 0.0))
    if time.time() > until:
        count = 0
    count += 1
    failures[ip] = (count, time.time() + 60)


def _log(data: Path, message: str) -> None:
    path = data / "logs" / "web.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size > LOG_MAX_BYTES:
                os.replace(path, path.with_name("web.log.1"))
        except FileNotFoundError:
            pass
        with path.open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except OSError:
        return
