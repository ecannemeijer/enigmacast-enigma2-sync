import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from enigmacast_sync.archive import ArchiveError, create_full_backup, open_bundle, restore_full_backup
from enigmacast_sync.service import open_db


def _prepared(data: Path, name: str = "before") -> None:
    data.mkdir(parents=True, exist_ok=True)
    db = open_db(data)
    db.execute(
        "INSERT INTO profiles (uuid, name, avatar, language, updated_ms, change_id, deleted) VALUES (?,?,?,?,?,?,0)",
        ("p1", name, 0, "nl", 10, "c1"),
    )
    db.commit()
    db.close()
    (data / "master.key").write_bytes(b"k" * 32)
    (data / "web-auth.json").write_text(json.dumps({"salt": "c2FsdA==", "hash": "aGFzaA==", "iterations": 1}), encoding="utf-8")


class ArchiveTests(unittest.TestCase):
    def test_roundtrip_and_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            target = Path(tmp) / "target"
            _prepared(source, "before")
            _prepared(target, "target-before")
            archive = create_full_backup(source)
            with open_bundle(archive) as bundle:
                self.assertIn("sync.db", bundle.files)
                self.assertIn("master.key", bundle.files)
                self.assertIn("web-auth.json", bundle.files)
                self.assertEqual(1, bundle.manifest["format"])
            result = restore_full_backup(archive, target)
            self.assertTrue(Path(result["safety"]).is_file())
            db = open_db(target)
            name = db.execute("SELECT name FROM profiles WHERE uuid='p1'").fetchone()["name"]
            db.close()
            self.assertEqual("before", name)

    def test_unknown_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                payload = b"not a database"
                info = tarfile.TarInfo("evil.txt")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            with self.assertRaises(ArchiveError):
                with open_bundle(archive):
                    pass

    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo("../sync.db")
                info.size = 0
                tar.addfile(info)
            with self.assertRaises(ArchiveError):
                with open_bundle(archive):
                    pass

    def test_database_without_master_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            db = open_db(data)
            db.close()
            archive = Path(tmp) / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(data / "sync.db", arcname="sync.db")
            with self.assertRaises(ArchiveError):
                with open_bundle(archive):
                    pass


if __name__ == "__main__":
    unittest.main()
