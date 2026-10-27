import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

from enigmacast_sync.service import open_db
from enigmacast_sync.web.auth import check_password, ensure_password, new_session, read_session, session_secret
from enigmacast_sync.web.control import run
from enigmacast_sync.web.lan import allowed
from enigmacast_sync.web.queries import devices, profiles, synchronisation, tail
from enigmacast_sync.web.server import make_server


class LanTests(unittest.TestCase):
    def test_private_and_public(self):
        self.assertTrue(allowed("127.0.0.1"))
        self.assertTrue(allowed("192.168.1.20"))
        self.assertTrue(allowed("10.8.0.4"))
        self.assertTrue(allowed("::ffff:192.168.1.20"))
        self.assertTrue(allowed("fe80::1%eth0"))
        self.assertTrue(allowed("fd00::20"))
        self.assertFalse(allowed("8.8.8.8"))
        self.assertFalse(allowed("2001:4860:4860::8888"))
        self.assertFalse(allowed("not-an-ip"))


class AuthTests(unittest.TestCase):
    def test_password_and_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            ensure_password(data)
            password = (data / "web-password-once").read_text(encoding="utf-8").strip()
            self.assertEqual("admin", password)
            self.assertTrue(check_password(data, password))
            self.assertFalse(check_password(data, "wrong"))
            secret = session_secret(data)
            token, csrf = new_session(secret, True)
            session = read_session(secret, token)
            self.assertTrue(session["auth"])
            self.assertEqual(csrf, session["csrf"])
            self.assertIsNone(read_session(secret, token + "x"))


class ControlTests(unittest.TestCase):
    def test_only_fixed_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = _control_script(Path(tmp))
            self.assertEqual(0, run(script, "restart"))
            with self.assertRaises(ValueError):
                run(script, "restart; reboot")


class QueryTests(unittest.TestCase):
    def test_profiles_devices_and_sync_use_stored_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = open_db(Path(tmp))
            db.execute(
                "INSERT INTO profiles (uuid, name, avatar, language, updated_ms, change_id, deleted) VALUES (?,?,?,?,?,?,0)",
                ("p1", "Ada", 0, "nl", 20, "c1"),
            )
            db.execute(
                "INSERT INTO records (profile_id, entity, record_key, payload, updated_ms, change_id, deleted) VALUES (?,?,?,?,?,?,0)",
                ("p1", "favorite", "a", json.dumps({"kind": "m", "title": "Film"}), 20, "f1"),
            )
            db.execute(
                "INSERT INTO records (profile_id, entity, record_key, payload, updated_ms, change_id, deleted) VALUES (?,?,?,?,?,?,0)",
                ("p1", "progress", "b", json.dumps({"kind": "s", "title": "Show"}), 30, "g1"),
            )
            db.execute(
                "INSERT INTO devices (id, name, pubkey, token_hash, role, revoked, created_ms) VALUES (?,?,?,?,?,0,?)",
                ("dev", "Living room", b"k", "h", "admin", 10),
            )
            db.execute(
                "INSERT INTO changes (profile_id, entity, record_key, op, payload, updated_ms, change_id, device_id) VALUES (?,?,?,?,?,?,?,?)",
                ("p1", "progress", "b", "upsert", "{}", 30, "g1", "dev"),
            )
            db.commit()
            found = profiles(db, "ada", "favorites", 1)["profiles"]
            db.execute(
                "INSERT INTO records (profile_id, entity, record_key, payload, updated_ms, change_id, deleted) VALUES (?,?,?,?,?,?,0)",
                ("orphan", "favorite", "c", json.dumps({"title": "Film"}), 40, "g2"),
            )
            db.commit()
            listed = [row["uuid"] for row in profiles(db, "", "name", 1)["profiles"]]
            self.assertIn("orphan", listed)
            self.assertEqual("Ada", found[0]["name"])
            self.assertEqual(1, found[0]["favorites"])
            self.assertEqual(1, found[0]["episodes"])
            self.assertIsNone(found[0]["watchHistory"])
            self.assertIsNone(devices(db)[0]["appVersion"])
            self.assertEqual(30, devices(db)[0]["lastSyncMs"])
            report = synchronisation(db)
            self.assertFalse(report["conflictsTracked"])
            self.assertFalse(report["queueTracked"])
            self.assertEqual("upsert", report["events"][0]["op"])
            log = Path(tmp) / "logs" / "web.log"
            log.parent.mkdir()
            log.write_text("started\npassword=secret\n", encoding="utf-8")
            self.assertEqual(["started"], tail(log))
            db.close()


class DashboardTests(unittest.TestCase):
    def test_login_csrf_and_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            db = open_db(data)
            db.execute(
                "INSERT INTO profiles (uuid, name, avatar, language, updated_ms, change_id, deleted) VALUES (?,?,?,?,?,?,0)",
                ("p1", "Ada", 0, None, 5, "c"),
            )
            db.commit()
            db.close()
            script = _control_script(data)
            httpd = make_server(data, "127.0.0.1", 0, script)
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
                denied = urllib.request.Request("http://127.0.0.1:%s/api/overview" % port)
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    urllib.request.urlopen(denied, timeout=3)
                self.assertEqual(401, blocked.exception.code)
                login = opener.open("http://127.0.0.1:%s/login" % port, timeout=3)
                html = login.read().decode("utf-8")
                csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
                password = (data / "web-password-once").read_text(encoding="utf-8").strip()
                bad = urllib.parse.urlencode({"csrf": csrf, "password": "nope"}).encode()
                try:
                    opener.open("http://127.0.0.1:%s/login" % port, data=bad, timeout=3)
                    self.fail("wrong password was accepted")
                except urllib.error.HTTPError as exc:
                    self.assertEqual(401, exc.code)
                login = opener.open("http://127.0.0.1:%s/login" % port, timeout=3)
                csrf = re.search(r'name="csrf" value="([^"]+)"', login.read().decode("utf-8")).group(1)
                good = urllib.parse.urlencode({"csrf": csrf, "password": password}).encode()
                opener.open("http://127.0.0.1:%s/login" % port, data=good, timeout=3)
                overview = json.loads(opener.open("http://127.0.0.1:%s/api/overview" % port, timeout=3).read())
                self.assertEqual(1, overview["profiles"])
                server_page = opener.open("http://127.0.0.1:%s/server" % port, timeout=3).read().decode("utf-8")
                token = re.search(r'csrf-token" content="([^"]+)"', server_page).group(1)
                changed = json.dumps({"current": password, "password": "nieuw", "confirm": "nieuw"}).encode()
                opener.open(urllib.request.Request(
                    "http://127.0.0.1:%s/api/password" % port,
                    data=changed,
                    headers={"Content-Type": "application/json", "X-CSRF-Token": token},
                ), timeout=3)
                self.assertTrue(check_password(data, "nieuw"))
                self.assertTrue(overview["web"])
                page = opener.open("http://127.0.0.1:%s/profiles" % port, timeout=3).read().decode("utf-8")
                self.assertIn('data-page="profiles"', page)
                fresh = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
                form = fresh.open("http://127.0.0.1:%s/login" % port, timeout=3).read().decode("utf-8")
                token = re.search(r'name="csrf" value="([^"]+)"', form).group(1)
                bare = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
                posted = urllib.parse.urlencode({"csrf": token, "password": "nieuw"}).encode()
                bare.open("http://127.0.0.1:%s/login" % port, data=posted, timeout=3)
                again = json.loads(bare.open("http://127.0.0.1:%s/api/overview" % port, timeout=3).read())
                self.assertEqual(1, again["profiles"])
                action = json.dumps({"action": "start"}).encode()
                request = urllib.request.Request(
                    "http://127.0.0.1:%s/api/server" % port,
                    data=action,
                    headers={"Content-Type": "application/json"},
                )
                try:
                    opener.open(request, timeout=3)
                    self.fail("missing csrf was accepted")
                except urllib.error.HTTPError as exc:
                    self.assertEqual(403, exc.code)
            finally:
                httpd.shutdown()
                httpd.server_close()


class _Conn:
    """One raw HTTP/1.1 keep-alive connection, so tests see exactly what a browser sees."""

    def __init__(self, port: int, tls: bool = False):
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        if tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            sock = context.wrap_socket(sock)
        self.sock = sock
        self.file = sock.makefile("rb")
        self.cookie = ""

    def close(self):
        self.file.close()
        self.sock.close()

    def send_raw(self, raw: bytes):
        self.sock.sendall(raw)

    def request(self, method: str, path: str, body: bytes = b"", headers: dict | None = None):
        lines = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1", "Connection: keep-alive"]
        if self.cookie:
            lines.append(f"Cookie: ec_web={self.cookie}")
        if method != "GET":
            lines.append("Content-Type: application/x-www-form-urlencoded")
            lines.append(f"Content-Length: {len(body)}")
        for key, value in (headers or {}).items():
            lines.append(f"{key}: {value}")
        self.send_raw(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        return self.response()

    def response(self):
        status = self.file.readline().decode("latin-1")
        if not status:
            return None, {}, b""
        headers: dict[str, list[str]] = {}
        while True:
            line = self.file.readline().decode("latin-1")
            if line in ("\r\n", "\n", ""):
                break
            key, _, value = line.partition(":")
            headers.setdefault(key.strip().lower(), []).append(value.strip())
        payload = self.file.read(int((headers.get("content-length") or ["0"])[0]))
        for cookie in headers.get("set-cookie", []):
            match = re.match(r"ec_web=([^;]*)", cookie)
            if match:
                self.cookie = match.group(1)
        return int(status.split()[1]), headers, payload

    def login_form_token(self) -> str:
        _, _, page = self.request("GET", "/login")
        return re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()

    def login(self, password: str):
        token = self.login_form_token()
        body = urllib.parse.urlencode({"csrf": token, "password": password}).encode()
        return self.request("POST", "/login", body)

    def page_csrf(self) -> str:
        _, _, page = self.request("GET", "/overview")
        return re.search(rb'name="csrf-token" content="([^"]+)"', page).group(1).decode()


class KeepAliveTests(unittest.TestCase):
    """0.3.1 cached the parsed body per connection: the second POST on a keep-alive connection
    reused the first body and left its own in the socket, which became the next request line
    (501 "Unsupported method ('csrf=...&password=...GET')" for app.css)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.httpd = None

    def tearDown(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.tmp.cleanup()

    def _start(self):
        self.httpd = make_server(self.data, "127.0.0.1", 0, _control_script(self.data))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.password = (self.data / "web-password-once").read_text(encoding="utf-8").strip()
        return self.httpd.server_address[1]

    def _connect(self, tls: bool = False) -> _Conn:
        conn = _Conn(self.httpd.server_address[1], tls)
        self.addCleanup(conn.close)
        return conn

    def _assert_login_then_css(self, conn: _Conn):
        status, headers, _ = conn.login(self.password)
        self.assertEqual(303, status)
        self.assertEqual(["/overview"], headers.get("location"))
        self.assertTrue(any(c.startswith("ec_web=") for c in headers.get("set-cookie", [])))
        status, headers, css = conn.request("GET", "/static/app.css")
        self.assertEqual(200, status)
        self.assertTrue(headers["content-type"][0].startswith("text/css"))
        self.assertIn(b":root", css)
        status, _, page = conn.request("GET", "/overview")
        self.assertEqual(200, status)

    def test_login_post_then_static_css_on_same_connection(self):
        self._start()
        self._assert_login_then_css(self._connect())

    def test_wrong_then_right_password_on_same_connection(self):
        self._start()
        conn = self._connect()
        status, _, _ = conn.login("not-the-password")
        self.assertEqual(401, status)
        self._assert_login_then_css(conn)

    def test_logout_then_login_on_same_connection(self):
        self._start()
        conn = self._connect()
        self.assertEqual(303, conn.login(self.password)[0])
        csrf = conn.page_csrf()
        status, headers, _ = conn.request("POST", "/logout", urllib.parse.urlencode({"csrf": csrf}).encode())
        self.assertEqual(303, status)
        self.assertEqual(["/login"], headers.get("location"))
        self._assert_login_then_css(conn)

    def test_each_api_post_uses_its_own_body(self):
        self._start()
        conn = self._connect()
        self.assertEqual(303, conn.login(self.password)[0])
        csrf = conn.page_csrf()
        first = json.dumps({"action": "restart"}).encode()
        conn.send_raw((
            "POST /api/server HTTP/1.1\r\nHost: x\r\nCookie: ec_web=%s\r\nX-CSRF-Token: %s\r\n"
            "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % (conn.cookie, csrf, len(first))
        ).encode() + first)
        self.assertEqual(200, conn.response()[0])
        second = json.dumps({"action": "restart; reboot"}).encode()
        conn.send_raw((
            "POST /api/server HTTP/1.1\r\nHost: x\r\nCookie: ec_web=%s\r\nX-CSRF-Token: %s\r\n"
            "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % (conn.cookie, csrf, len(second))
        ).encode() + second)
        status, _, payload = conn.response()
        self.assertEqual(400, status, payload)
        self.assertEqual(200, conn.request("GET", "/static/app.css")[0])

    def test_refused_posts_still_consume_their_body(self):
        self._start()
        conn = self._connect()
        # No session: 401 for the API and a CSRF failure for logout, both with a body.
        self.assertEqual(401, conn.request("POST", "/api/backup", b"csrf=x&junk=1")[0])
        self.assertEqual(200, conn.request("GET", "/static/app.css")[0])
        self.assertEqual(303, conn.login(self.password)[0])
        self.assertEqual(403, conn.request("POST", "/logout", b"csrf=wrong")[0])
        self.assertEqual(405, conn.request("POST", "/static/app.css", b"csrf=x")[0])
        self.assertEqual(405, conn.request("PUT", "/api/server", b"x=1")[0])
        self.assertEqual(405, conn.request("DELETE", "/api/server", b"x=1")[0])
        self.assertEqual(200, conn.request("GET", "/static/app.css")[0])

    def test_oversized_and_bad_bodies_close_the_connection(self):
        self._start()
        conn = self._connect()
        conn.send_raw(b"POST /login HTTP/1.1\r\nHost: x\r\nContent-Length: 10000000\r\n\r\n")
        status, headers, _ = conn.response()
        self.assertEqual(413, status)
        self.assertEqual(["close"], headers.get("connection"))
        self.assertEqual(b"", conn.file.read())
        for bad in (b"Content-Length: -5", b"Content-Length: abc", b"Transfer-Encoding: chunked"):
            conn = self._connect()
            conn.send_raw(b"POST /login HTTP/1.1\r\nHost: x\r\n" + bad + b"\r\n\r\n")
            status, headers, _ = conn.response()
            self.assertIn(status, (400, 411), bad)
            self.assertEqual(["close"], headers.get("connection"))

    def test_log_never_holds_a_body_or_password(self):
        self._start()
        conn = self._connect()
        conn.login(self.password)
        # A garbled request line (what the leftover body used to become) is logged without content.
        conn.send_raw(b"csrf=abc&password=hunter2GET /static/app.css HTTP/1.1\r\nHost: x\r\n\r\n")
        conn.response()
        log = (self.data / "logs" / "web.log").read_text(encoding="utf-8")
        self.assertNotIn("hunter2", log)
        self.assertNotIn("password", log)
        self.assertNotIn("csrf", log)
        self.assertIn("POST /login 303", log)

    @unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
    def test_https_keep_alive_and_http_redirect(self):
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(self.data / "key.pem"),
             "-out", str(self.data / "cert.pem"), "-days", "1", "-nodes", "-subj", "/CN=test"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        port = self._start()
        # A silent client must not block the accept loop (the TLS sniff runs in the worker).
        idle = socket.create_connection(("127.0.0.1", port))
        self.addCleanup(idle.close)
        self._assert_login_then_css(self._connect(tls=True))
        plain = self._connect()
        status, headers, _ = plain.request("GET", "/login")
        self.assertEqual(301, status)
        self.assertTrue(headers["location"][0].startswith("https://"))


def _control_script(directory: Path) -> Path:
    if os.name == "nt":
        path = directory / "ctl.cmd"
        path.write_text("@exit /b 0\r\n", encoding="utf-8")
        return path
    path = directory / "ctl"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


if __name__ == "__main__":
    unittest.main()
