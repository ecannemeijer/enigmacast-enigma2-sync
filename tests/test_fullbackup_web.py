import json
import os
import re
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

from enigmacast_sync.archive import create_full_backup
from enigmacast_sync.service import open_db
from enigmacast_sync.web.server import make_server


def _prepared(data: Path, name: str) -> None:
    data.mkdir(parents=True, exist_ok=True)
    db = open_db(data)
    db.execute(
        "INSERT INTO profiles (uuid, name, avatar, language, updated_ms, change_id, deleted) VALUES (?,?,?,?,?,?,0)",
        ("p1", name, 0, "nl", 10, "c1"),
    )
    db.commit()
    db.close()
    (data / "master.key").write_bytes(b"k" * 32)


def _control(directory: Path) -> Path:
    if os.name == "nt":
        path = directory / "ctl.cmd"
        path.write_text("@exit /b 0\r\n", encoding="utf-8")
        return path
    path = directory / "ctl"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class FullBackupWebTests(unittest.TestCase):
    def test_download_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            data = Path(tmp) / "data"
            _prepared(source, "restored-name")
            _prepared(data, "current-name")
            archive = create_full_backup(source)
            script = _control(Path(tmp))
            httpd = make_server(data, "127.0.0.1", 0, script, script)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
                login = opener.open("http://127.0.0.1:%s/login" % httpd.server_address[1], timeout=3).read().decode()
                csrf = re.search(r'name="csrf" value="([^"]+)"', login).group(1)
                password = (data / "web-password-once").read_text(encoding="utf-8").strip()
                form = urllib.parse.urlencode({"csrf": csrf, "password": password}).encode()
                opener.open("http://127.0.0.1:%s/login" % httpd.server_address[1], data=form, timeout=3)
                download = opener.open("http://127.0.0.1:%s/api/backup/full" % httpd.server_address[1], timeout=10)
                self.assertTrue(download.headers.get("Content-Type", "").startswith("application/gzip"))
                self.assertIn("attachment", download.headers.get("Content-Disposition", ""))
                self.assertTrue(download.read())
                page = opener.open("http://127.0.0.1:%s/server" % httpd.server_address[1], timeout=3).read().decode()
                token = re.search(r'csrf-token" content="([^"]+)"', page).group(1)
                request = urllib.request.Request(
                    "http://127.0.0.1:%s/api/restore" % httpd.server_address[1],
                    data=archive.read_bytes(),
                    headers={"Content-Type": "application/gzip", "X-CSRF-Token": token},
                )
                response = json.loads(opener.open(request, timeout=10).read())
                self.assertTrue(response["restarting"])
                self.assertIn("sync.db", response["restored"])
                db = open_db(data)
                name = db.execute("SELECT name FROM profiles WHERE uuid='p1'").fetchone()["name"]
                db.close()
                self.assertEqual("restored-name", name)
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
