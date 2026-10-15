"""SQLite schema, conflict handling and backups for the sync server."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    pubkey BLOB NOT NULL,
    token_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, exp_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pairing (
    code_hash TEXT PRIMARY KEY,
    exp_ms INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS profiles (
    uuid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    avatar INTEGER NOT NULL,
    language TEXT,
    updated_ms INTEGER NOT NULL,
    change_id TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS records (
    profile_id TEXT NOT NULL,
    entity TEXT NOT NULL,
    record_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_ms INTEGER NOT NULL,
    change_id TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_id, entity, record_key)
);
CREATE TABLE IF NOT EXISTS playlist (
    id TEXT PRIMARY KEY,
    audience TEXT NOT NULL,
    secret TEXT NOT NULL,
    updated_ms INTEGER NOT NULL,
    change_id TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS changes (
    rev INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    entity TEXT NOT NULL,
    record_key TEXT NOT NULL,
    op TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_ms INTEGER NOT NULL,
    change_id TEXT NOT NULL UNIQUE,
    device_id TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def decide(stored_ms, stored_change, incoming_ms: int, incoming_change: str) -> str:
    """Same rule as the Android SyncConflict object."""
    if stored_change is not None and stored_change == incoming_change:
        return "replay"
    if stored_ms is None or incoming_ms > stored_ms:
        return "accept"
    if incoming_ms == stored_ms and incoming_change > (stored_change or ""):
        return "accept"
    return "reject"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def apply_record(db: sqlite3.Connection, profile_id: str, entity: str, record_key: str, op: str,
                 payload: str, updated_ms: int, change_id: str, device_id: str) -> str:
    row = db.execute(
        "SELECT updated_ms, change_id FROM records WHERE profile_id=? AND entity=? AND record_key=?",
        (profile_id, entity, record_key),
    ).fetchone()
    choice = decide(None if row is None else row["updated_ms"], None if row is None else row["change_id"], updated_ms, change_id)
    if choice != "accept":
        return choice
    deleted = 1 if op == "delete" else 0
    db.execute(
        """INSERT INTO records (profile_id, entity, record_key, payload, updated_ms, change_id, deleted)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(profile_id, entity, record_key) DO UPDATE SET
             payload=excluded.payload, updated_ms=excluded.updated_ms, change_id=excluded.change_id, deleted=excluded.deleted""",
        (profile_id, entity, record_key, payload, updated_ms, change_id, deleted),
    )
    db.execute(
        """INSERT INTO changes (profile_id, entity, record_key, op, payload, updated_ms, change_id, device_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (profile_id, entity, record_key, op, payload if not deleted else "{}", updated_ms, change_id, device_id),
    )
    return "accept"


def pull(db: sqlite3.Connection, profile_ids: list[str], cursor: int) -> tuple[int, list[dict]]:
    if not profile_ids:
        return cursor, []
    marks = ",".join("?" for _ in profile_ids)
    rows = db.execute(
        f"SELECT * FROM changes WHERE rev>? AND profile_id IN ({marks}) ORDER BY rev",
        (cursor, *profile_ids),
    ).fetchall()
    changes = [dict(row) for row in rows]
    nxt = changes[-1]["rev"] if changes else cursor
    return nxt, changes


def backup(db_path: Path, key_path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = dest_dir / f"enigmacast-sync-{stamp}"
    target.mkdir()
    snapshot = target / "sync.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(snapshot)
    src.backup(dst)
    dst.close()
    src.close()
    if key_path.is_file():
        shutil.copy2(key_path, target / "master.key")
    (target / "manifest.json").write_text(json.dumps({"created": stamp}), encoding="utf-8")
    return target


def restore(bundle: Path, db_path: Path, key_path: Path) -> None:
    snapshot = bundle / "sync.db"
    if not snapshot.is_file():
        raise FileNotFoundError("backup has no database")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snapshot, db_path)
    key = bundle / "master.key"
    if key.is_file():
        shutil.copy2(key, key_path)
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
