"""Paged pulls, version reporting, data-dir upgrades and the TLS server end to end."""

import base64
import hashlib
import http.client
import json
import os
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from enigmacast_sync import SERVER_VERSION, __version__
from enigmacast_sync.http_server import TlsThreadingServer, encode, make_handler
from enigmacast_sync.p256 import generate, sign
from enigmacast_sync.service import health, init_server, join, open_db, push, sync_pull, version_info
from enigmacast_sync.storage import choose_data_dir


def _change(i: int, profile: str = "p") -> dict:
    return {"profileId": profile, "entity": "progress", "key": f"film-{i}", "op": "upsert",
            "payload": {"positionMs": i, "title": "Amélie – 東京 ✓ " + "x" * 200}, "updatedMs": 1000 + i,
            "changeId": f"c-{profile}-{i}"}


class PullTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = open_db(Path(self.tmp.name))
        init_server(self.db)
        _, public = generate()
        self.device = join(self.db, {"name": "tv", "publicKey": base64.b64encode(public).decode()})
        self.auth = {"id": self.device["deviceId"], "role": "admin", "revoked": 0}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_limit_is_honoured_and_pages_cover_everything(self):
        push(self.db, self.auth, {"changes": [_change(i) for i in range(57)]})
        push(self.db, self.auth, {"changes": [_change(i, "other") for i in range(5)]})
        cursor, seen, pages = 0, [], 0
        while True:
            page = sync_pull(self.db, self.auth, "p", cursor, 10)
            pages += 1
            self.assertLessEqual(len(page["changes"]), 10)
            self.assertEqual(10, page["limit"])
            seen += [c["record_key"] for c in page["changes"]]
            if page["changes"]:
                self.assertEqual(page["changes"][-1]["rev"], page["cursor"])
            cursor = page["cursor"]
            if not page["more"]:
                break
        self.assertEqual(6, pages)
        self.assertEqual([f"film-{i}" for i in range(57)], seen)
        self.assertEqual({"cursor": cursor, "changes": [], "limit": 10, "more": False}, sync_pull(self.db, self.auth, "p", cursor, 10))

    def test_exact_page_has_no_more(self):
        push(self.db, self.auth, {"changes": [_change(i) for i in range(10)]})
        page = sync_pull(self.db, self.auth, "p", 0, 10)
        self.assertEqual(10, len(page["changes"]))
        self.assertFalse(page["more"])

    def test_limit_is_clamped(self):
        push(self.db, self.auth, {"changes": [_change(i) for i in range(3)]})
        self.assertEqual(1, sync_pull(self.db, self.auth, "p", 0, 0)["limit"])
        self.assertEqual(1, len(sync_pull(self.db, self.auth, "p", 0, -5)["changes"]))
        self.assertEqual(1000, sync_pull(self.db, self.auth, "p", 0, 99999)["limit"])

    def test_pull_does_not_leak_device_ids(self):
        push(self.db, self.auth, {"changes": [_change(1)]})
        change = sync_pull(self.db, self.auth, "p", 0, 10)["changes"][0]
        self.assertNotIn("device_id", change)
        self.assertEqual({"rev", "profile_id", "entity", "record_key", "op", "payload", "updated_ms", "change_id"}, set(change))

    def test_version_is_reported(self):
        self.assertEqual(__version__, SERVER_VERSION)
        self.assertEqual(SERVER_VERSION, health()["version"])
        self.assertEqual(SERVER_VERSION, version_info()["version"])
        self.assertTrue(version_info()["paging"])

    def test_body_bytes_are_ascii_and_length_is_byte_length(self):
        payload = {"t": "Amélie – 東京 ✓ 😀"}
        data = encode(payload)
        data.decode("ascii")
        self.assertEqual(payload, json.loads(data))


class UpgradeTests(unittest.TestCase):
    def test_database_from_024_opens_and_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sync.db"
            old = sqlite3.connect(path)
            # 0.2.4 schema had no cursors table.
            old.executescript("""
                CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
                CREATE TABLE changes (rev INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL,
                    entity TEXT NOT NULL, record_key TEXT NOT NULL, op TEXT NOT NULL, payload TEXT NOT NULL,
                    updated_ms INTEGER NOT NULL, change_id TEXT NOT NULL UNIQUE, device_id TEXT NOT NULL);
            """)
            old.executemany("INSERT INTO changes (profile_id, entity, record_key, op, payload, updated_ms, change_id, device_id) VALUES ('p','progress',?, 'upsert', '{}', 1, ?, 'd')",
                            [(f"k{i}", f"c{i}") for i in range(30)])
            old.commit()
            old.close()
            db = open_db(Path(tmp))
            page = sync_pull(db, {"id": "d", "role": "admin", "revoked": 0}, "p", 0, 10)
            self.assertEqual(10, len(page["changes"]))
            self.assertTrue(page["more"])
            db.close()


class StorageTests(unittest.TestCase):
    def test_existing_database_on_a_mounted_disk_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            hdd, usb = Path(tmp, "hdd"), Path(tmp, "usb")
            (usb / "enigmacast-sync").mkdir(parents=True)
            hdd.mkdir()
            (usb / "enigmacast-sync" / "sync.db").write_bytes(b"")
            self.assertEqual(usb / "enigmacast-sync", choose_data_dir([hdd, usb], fallback=Path(tmp, "etc")))

    def test_legacy_unmounted_folder_with_data_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp, "hdd")
            (legacy / "enigmacast-sync").mkdir(parents=True)
            (legacy / "enigmacast-sync" / "sync.db").write_bytes(b"")
            etc = Path(tmp, "etc")
            self.assertEqual(legacy / "enigmacast-sync", choose_data_dir([], legacy_roots=[legacy], fallback=etc))
            etc.mkdir()
            (etc / "sync.db").write_bytes(b"")
            self.assertEqual(etc, choose_data_dir([], legacy_roots=[legacy], fallback=etc))

    def test_empty_unmounted_folder_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp, "hdd")
            legacy.mkdir()
            self.assertEqual(Path(tmp, "etc"), choose_data_dir([], legacy_roots=[legacy], fallback=Path(tmp, "etc")))

    def test_init_script_matches_python(self):
        script = (Path(__file__).resolve().parents[1] / "packaging" / "enigmacast-sync").read_text()
        self.assertIn('[ -f "$d/enigmacast-sync/sync.db" ]', script)
        self.assertIn("if [ ! -f /etc/enigmacast-sync/sync.db ]", script)


def _openssl() -> bool:
    try:
        subprocess.run(["openssl", "version"], check=True, stdout=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


@unittest.skipUnless(_openssl(), "openssl missing")
class TlsServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data = Path(cls.tmp.name)
        cls.cert, cls.key = data / "cert.pem", data / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(cls.key), "-out", str(cls.cert),
                        "-days", "1", "-nodes", "-subj", "/CN=enigmacast-sync"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.db = open_db(data)
        init_server(cls.db)
        cls.private, public = generate()
        cls.device = join(cls.db, {"name": "tv", "publicKey": base64.b64encode(public).decode()})
        auth = {"id": cls.device["deviceId"], "role": "admin", "revoked": 0}
        for start in range(0, 3000, 500):
            push(cls.db, auth, {"changes": [_change(i) for i in range(start, start + 500)]})
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cls.cert), str(cls.key))
        cls.server = TlsThreadingServer(("127.0.0.1", 0), make_handler(cls.db), context)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.db.close()
        cls.tmp.cleanup()

    def _get(self, path: str):
        stamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        message = f"{stamp}\n{nonce}\nGET\n{path}\n".encode() + hashlib.sha256(b"").digest()
        headers = {"Authorization": f"Bearer {self.device['token']}", "X-Timestamp": stamp, "X-Nonce": nonce,
                   "X-Signature": base64.b64encode(sign(self.private, message)).decode()}
        context = ssl._create_unverified_context()
        conn = http.client.HTTPSConnection("127.0.0.1", self.port, context=context, timeout=10)
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response, body

    def test_version_endpoint_needs_no_login(self):
        response, body = self._get("/api/v1/version")
        self.assertEqual(200, response.status)
        self.assertEqual(SERVER_VERSION, json.loads(body)["version"])
        response, body = self._get("/api/v1/health")
        self.assertEqual(SERVER_VERSION, json.loads(body)["version"])
        self.assertEqual("close", response.getheader("Connection"))

    def test_paged_pull_over_tls_is_complete(self):
        cursor, total = 0, 0
        while True:
            response, body = self._get(f"/api/v1/sync/pull?profileId=p&cursor={cursor}&limit=200")
            self.assertEqual(200, response.status)
            self.assertEqual(int(response.getheader("Content-Length")), len(body))
            page = json.loads(body)
            self.assertLessEqual(len(page["changes"]), 200)
            total += len(page["changes"])
            cursor = page["cursor"]
            if not page["more"]:
                break
        self.assertEqual(3000, total)

    def test_stalled_handshake_does_not_block_others(self):
        idle = socket.create_connection(("127.0.0.1", self.port))
        try:
            started = time.time()
            response, _ = self._get("/api/v1/version")
            self.assertEqual(200, response.status)
            self.assertLess(time.time() - started, 5)
        finally:
            idle.close()

    def test_parallel_clients(self):
        errors = []

        def run():
            try:
                response, body = self._get("/api/v1/sync/pull?profileId=p&cursor=0&limit=500")
                self.assertEqual(500, len(json.loads(body)["changes"]))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=run) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
