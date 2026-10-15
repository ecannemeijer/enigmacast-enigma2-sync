import json
import os
import re
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
