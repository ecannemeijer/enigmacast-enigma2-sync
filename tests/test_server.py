import base64
import json
import tempfile
import unittest
from pathlib import Path

from enigmacast_sync.aes import decrypt, encrypt
from enigmacast_sync.db import decide
from enigmacast_sync.p256 import generate, sign, verify
from enigmacast_sync.service import (
    ApiError, authenticate, get_playlist, init_server, issue_pairing, join, open_db, pair, push, put_playlist, recover, revoke_device,
)
from enigmacast_sync.storage import choose_data_dir


def _signed(private, token, method, path, body: bytes):
    import time
    stamp = str(int(time.time() * 1000))
    nonce = "n1"
    message = f"{stamp}\n{nonce}\n{method}\n{path}\n".encode() + __import__("hashlib").sha256(body).digest()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Timestamp": stamp,
        "X-Nonce": nonce,
        "X-Signature": base64.b64encode(sign(private, message)).decode(),
    }
    return headers


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = open_db(Path(self.tmp.name))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_first_device_is_only_admin(self):
        created = init_server(self.db)
        private, public = generate()
        first = pair(self.db, {"code": created["pairingCode"], "name": "tv", "publicKey": base64.b64encode(public).decode()})
        self.assertEqual("admin", first["role"])
        issued = issue_pairing(self.db, {"id": first["deviceId"], "role": "admin", "revoked": 0})
        second_key = generate()
        second = pair(self.db, {"code": issued["pairingCode"], "name": "phone", "publicKey": base64.b64encode(second_key[1]).decode()})
        self.assertEqual("member", second["role"])
        with self.assertRaises(ApiError):
            issue_pairing(self.db, {"id": second["deviceId"], "role": "member", "revoked": 0})

    def test_join_needs_no_pairing_code(self):
        init_server(self.db)
        _, public = generate()
        first = join(self.db, {"name": "living-room", "publicKey": base64.b64encode(public).decode()})
        self.assertEqual("admin", first["role"])
        _, public2 = generate()
        second = join(self.db, {"name": "bedroom", "publicKey": base64.b64encode(public2).decode()})
        self.assertEqual("member", second["role"])
        again = join(self.db, {"name": "living-room", "publicKey": base64.b64encode(public).decode()})
        self.assertEqual(first["deviceId"], again["deviceId"])
        self.assertEqual("admin", again["role"])

    def test_recovery_revokes_without_a_second_admin(self):
        created = init_server(self.db)
        _, public = generate()
        pair(self.db, {"code": created["pairingCode"], "name": "tv", "publicKey": base64.b64encode(public).decode()})
        again = recover(self.db, created["recoveryCode"])
        self.assertEqual(0, self.db.execute("SELECT COUNT(*) AS n FROM devices WHERE revoked=0").fetchone()["n"])
        fresh = pair(self.db, {"code": again["pairingCode"], "name": "replacement", "publicKey": base64.b64encode(public).decode()})
        self.assertEqual("admin", fresh["role"])
        self.assertEqual(1, self.db.execute("SELECT COUNT(*) AS n FROM devices WHERE role='admin' AND revoked=0").fetchone()["n"])

    def test_older_progress_does_not_overwrite(self):
        created = init_server(self.db)
        private, public = generate()
        device = pair(self.db, {"code": created["pairingCode"], "name": "tv", "publicKey": base64.b64encode(public).decode()})
        auth = {"id": device["deviceId"], "role": "admin", "revoked": 0}
        body = {"changes": [{"profileId": "p", "entity": "progress", "key": "film", "op": "upsert",
                             "payload": {"positionMs": 50}, "updatedMs": 2000, "changeId": "new"}]}
        self.assertEqual(["accept"], push(self.db, auth, body)["results"])
        older = {"changes": [{"profileId": "p", "entity": "progress", "key": "film", "op": "upsert",
                              "payload": {"positionMs": 1}, "updatedMs": 1000, "changeId": "old"}]}
        self.assertEqual(["reject"], push(self.db, auth, older)["results"])
        rewind = {"changes": [{"profileId": "p", "entity": "progress", "key": "film", "op": "upsert",
                               "payload": {"positionMs": 0}, "updatedMs": 3000, "changeId": "rewind"}]}
        self.assertEqual(["accept"], push(self.db, auth, rewind)["results"])
        replay = {"changes": [{"profileId": "p", "entity": "progress", "key": "film", "op": "upsert",
                               "payload": {"positionMs": 0}, "updatedMs": 3000, "changeId": "rewind"}]}
        self.assertEqual(["replay"], push(self.db, auth, replay)["results"])
        tomb = {"changes": [{"profileId": "p", "entity": "favorite", "key": "film", "op": "delete",
                             "payload": {}, "updatedMs": 4000, "changeId": "gone"}]}
        self.assertEqual(["accept"], push(self.db, auth, tomb)["results"])
        row = self.db.execute("SELECT deleted FROM records WHERE entity='favorite'").fetchone()
        self.assertEqual(1, row["deleted"])

    def test_signature_and_replay(self):
        created = init_server(self.db)
        private, public = generate()
        device = pair(self.db, {"code": created["pairingCode"], "name": "tv", "publicKey": base64.b64encode(public).decode()})
        body = b""
        headers = _signed(private, device["token"], "GET", "/api/v1/devices", body)
        auth = authenticate(self.db, headers, "GET", "/api/v1/devices", body)
        self.assertEqual("admin", auth["role"])
        with self.assertRaises(ApiError):
            authenticate(self.db, headers, "GET", "/api/v1/devices", body)

    def test_member_cannot_revoke(self):
        created = init_server(self.db)
        _, public = generate()
        admin = pair(self.db, {"code": created["pairingCode"], "name": "tv", "publicKey": base64.b64encode(public).decode()})
        issued = issue_pairing(self.db, {"id": admin["deviceId"], "role": "admin", "revoked": 0})
        member = pair(self.db, {"code": issued["pairingCode"], "name": "other", "publicKey": base64.b64encode(public).decode()})
        with self.assertRaises(ApiError):
            revoke_device(self.db, {"id": member["deviceId"], "role": "member", "revoked": 0}, admin["deviceId"])

    def test_conflict_matches_app_rule(self):
        self.assertEqual("accept", decide(1000, "a", 2000, "b"))
        self.assertEqual("reject", decide(2000, "b", 1000, "a"))
        self.assertEqual("replay", decide(1000, "a", 9000, "a"))

    def test_secrets_roundtrip_and_storage_choice(self):
        key = b"k" * 32
        blob = encrypt(key, b"secret-pass")
        self.assertEqual(b"secret-pass", decrypt(key, blob))
        self.assertEqual(Path("/etc/enigmacast-sync"), choose_data_dir([]))
        root = Path(self.tmp.name) / "hdd"
        root.mkdir()
        self.assertEqual(root / "enigmacast-sync", choose_data_dir([root]))

    def test_playlist_is_one_login_for_every_device(self):
        created = init_server(self.db)
        _, public = generate()
        admin = pair(self.db, {"code": created["pairingCode"], "name": "tv", "publicKey": base64.b64encode(public).decode()})
        auth = {"id": admin["deviceId"], "role": "admin", "revoked": 0}
        self.assertIsNone(get_playlist(self.db, auth)["playlist"])
        stored = put_playlist(self.db, auth, {
            "changeId": "c1", "updatedMs": 10, "server": "http://tv", "username": "u", "password": "p", "kind": "XTREAM",
        })
        self.assertEqual("accept", stored["result"])
        got = get_playlist(self.db, auth)["playlist"]
        self.assertEqual("p", got["password"])
        self.assertEqual("XTREAM", got["kind"])
        self.assertNotIn("profile", got)
        older = put_playlist(self.db, auth, {
            "changeId": "c0", "updatedMs": 5, "server": "http://tv", "username": "u", "password": "old", "kind": "XTREAM",
        })
        self.assertEqual("reject", older["result"])
        self.assertEqual("p", get_playlist(self.db, auth)["playlist"]["password"])
        issued = issue_pairing(self.db, auth)
        _, other = generate()
        member = pair(self.db, {"code": issued["pairingCode"], "name": "bedroom", "publicKey": base64.b64encode(other).decode()})
        member_auth = {"id": member["deviceId"], "role": "member", "revoked": 0}
        newer = put_playlist(self.db, member_auth, {
            "changeId": "c2", "updatedMs": 20, "m3uUrl": "https://list.example/tv.m3u", "kind": "M3U",
        })
        self.assertEqual("accept", newer["result"])
        again = get_playlist(self.db, auth)["playlist"]
        self.assertEqual("https://list.example/tv.m3u", again["m3uUrl"])
        self.assertEqual("M3U", again["kind"])

    def test_p256_roundtrip(self):
        private, public = generate()
        signature = sign(private, b"hello")
        self.assertTrue(verify(public, b"hello", signature))
        self.assertFalse(verify(public, b"nope", signature))


if __name__ == "__main__":
    unittest.main()
