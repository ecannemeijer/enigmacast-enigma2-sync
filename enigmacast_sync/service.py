"""REST API logic. HTTP is a thin wrapper around these functions."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path

from enigmacast_sync.db import apply_record, backup, connect, pull, restore, token_hash
from enigmacast_sync.p256 import verify
from enigmacast_sync.secrets import master_key, open_secret, seal

SKEW_MS = 5 * 60 * 1000
PAIR_MS = 10 * 60 * 1000


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def open_db(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    db = connect(data_dir / "sync.db")
    _set_meta(db, "data_dir", str(data_dir))
    db.commit()
    return db


def _dir(db) -> Path:
    return Path(_meta(db, "data_dir") or ".")


def _meta(db, key: str, default: str | None = None) -> str | None:
    row = db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return default if row is None else row["v"]


def _set_meta(db, key: str, value: str) -> None:
    db.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))


def init_server(db) -> dict:
    """Create the first pairing code and a one-time recovery code. Safe to call once."""
    if _meta(db, "initialized") == "1":
        raise ApiError(409, "already initialized")
    pairing = f"{secrets.randbelow(1_000_000):06d}"
    recovery = secrets.token_urlsafe(18)
    now = _now()
    db.execute("INSERT INTO pairing (code_hash, exp_ms, used) VALUES (?, ?, 0)", (_code_hash(pairing), now + PAIR_MS))
    _set_meta(db, "recovery_hash", hashlib.sha256(recovery.encode()).hexdigest())
    _set_meta(db, "initialized", "1")
    db.commit()
    return {"pairingCode": pairing, "recoveryCode": recovery, "pairingExpiresMs": now + PAIR_MS}


def health() -> dict:
    return {"ok": True, "service": "enigmacast-sync"}


def server_info(db) -> dict:
    admins = db.execute("SELECT COUNT(*) AS n FROM devices WHERE role='admin' AND revoked=0").fetchone()["n"]
    return {"api": "v1", "port": 8740, "adminDevices": admins, "initialized": _meta(db, "initialized") == "1"}


def join(db, body: dict) -> dict:
    """A TV running EnigmaCast joins without a pairing code. The first one is the only admin."""
    name = str(body.get("name", "EnigmaCast"))[:64]
    pubkey = _b64(body.get("publicKey", ""))
    if not pubkey:
        raise ApiError(400, "publicKey is required")
    now = _now()
    token = secrets.token_urlsafe(32)
    existing = db.execute("SELECT * FROM devices WHERE pubkey=? AND revoked=0", (pubkey,)).fetchone()
    if existing is not None:
        db.execute("UPDATE devices SET token_hash=?, name=? WHERE id=?", (token_hash(token), name, existing["id"]))
        db.commit()
        return {"deviceId": existing["id"], "token": token, "role": existing["role"]}
    admins = db.execute("SELECT COUNT(*) AS n FROM devices WHERE role='admin' AND revoked=0").fetchone()["n"]
    role = "admin" if admins == 0 else "member"
    device_id = secrets.token_hex(16)
    db.execute(
        "INSERT INTO devices (id, name, pubkey, token_hash, role, revoked, created_ms) VALUES (?,?,?,?,?,0,?)",
        (device_id, name, pubkey, token_hash(token), role, now),
    )
    db.commit()
    return {"deviceId": device_id, "token": token, "role": role}


def pair(db, body: dict) -> dict:
    code = str(body.get("code", ""))
    name = str(body.get("name", "device"))[:64]
    pubkey = _b64(body.get("publicKey", ""))
    if not code or not pubkey:
        raise ApiError(400, "code and publicKey are required")
    now = _now()
    row = db.execute(
        "SELECT code_hash FROM pairing WHERE code_hash=? AND used=0 AND exp_ms>=?",
        (_code_hash(code), now),
    ).fetchone()
    if row is None:
        raise ApiError(401, "pairing code rejected")
    db.execute("UPDATE pairing SET used=1 WHERE code_hash=?", (row["code_hash"],))
    admins = db.execute("SELECT COUNT(*) AS n FROM devices WHERE role='admin' AND revoked=0").fetchone()["n"]
    role = "admin" if admins == 0 else "member"
    device_id = secrets.token_hex(16)
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO devices (id, name, pubkey, token_hash, role, revoked, created_ms) VALUES (?,?,?,?,?,0,?)",
        (device_id, name, pubkey, token_hash(token), role, now),
    )
    db.commit()
    return {"deviceId": device_id, "token": token, "role": role}


def recover(db, code: str) -> dict:
    digest = hashlib.sha256(code.encode()).hexdigest()
    if not secrets.compare_digest(digest, _meta(db, "recovery_hash") or ""):
        raise ApiError(401, "recovery code rejected")
    db.execute("UPDATE devices SET revoked=1")
    pairing = f"{secrets.randbelow(1_000_000):06d}"
    now = _now()
    db.execute("DELETE FROM pairing")
    db.execute("INSERT INTO pairing (code_hash, exp_ms, used) VALUES (?, ?, 0)", (_code_hash(pairing), now + PAIR_MS))
    fresh = secrets.token_urlsafe(18)
    _set_meta(db, "recovery_hash", hashlib.sha256(fresh.encode()).hexdigest())
    db.commit()
    return {"pairingCode": pairing, "recoveryCode": fresh}


def authenticate(db, headers: dict, method: str, path: str, body: bytes) -> dict:
    token = _bearer(headers)
    if not token:
        raise ApiError(401, "missing token")
    device = db.execute("SELECT * FROM devices WHERE token_hash=? AND revoked=0", (token_hash(token),)).fetchone()
    if device is None:
        raise ApiError(401, "unknown device")
    stamp = headers.get("x-timestamp") or headers.get("X-Timestamp")
    nonce = headers.get("x-nonce") or headers.get("X-Nonce")
    signature = headers.get("x-signature") or headers.get("X-Signature")
    if not stamp or not nonce or not signature:
        raise ApiError(401, "missing signature")
    try:
        when = int(stamp)
    except ValueError:
        raise ApiError(401, "bad timestamp")
    if abs(_now() - when) > SKEW_MS:
        raise ApiError(401, "timestamp out of range")
    db.execute("DELETE FROM nonces WHERE exp_ms<?", (_now(),))
    if db.execute("SELECT 1 FROM nonces WHERE nonce=?", (nonce,)).fetchone():
        raise ApiError(401, "replay")
    message = f"{stamp}\n{nonce}\n{method.upper()}\n{path}\n".encode() + hashlib.sha256(body).digest()
    if not verify(device["pubkey"], message, _b64(signature)):
        raise ApiError(401, "bad signature")
    db.execute("INSERT INTO nonces (nonce, exp_ms) VALUES (?, ?)", (nonce, _now() + SKEW_MS))
    db.commit()
    return dict(device)


def require_admin(device: dict) -> None:
    if device.get("role") != "admin" or device.get("revoked"):
        raise ApiError(403, "admin only")


def issue_pairing(db, device: dict) -> dict:
    require_admin(device)
    code = f"{secrets.randbelow(1_000_000):06d}"
    exp = _now() + PAIR_MS
    db.execute("INSERT INTO pairing (code_hash, exp_ms, used) VALUES (?, ?, 0)", (_code_hash(code), exp))
    db.commit()
    return {"pairingCode": code, "expiresMs": exp}


def revoke_device(db, device: dict, device_id: str) -> dict:
    require_admin(device)
    if device_id == device["id"]:
        raise ApiError(400, "the admin device cannot revoke itself; use recovery")
    db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))
    db.commit()
    return {"revoked": device_id}


def list_devices(db, device: dict) -> dict:
    require_admin(device)
    rows = db.execute("SELECT id, name, role, revoked, created_ms FROM devices ORDER BY created_ms").fetchall()
    return {"devices": [dict(row) for row in rows]}


def list_profiles(db, device: dict) -> dict:
    del device
    rows = db.execute(
        "SELECT uuid, name, avatar, language FROM profiles WHERE deleted=0 ORDER BY name"
    ).fetchall()
    return {"profiles": [dict(row) for row in rows]}


def upsert_profile(db, device: dict, body: dict) -> dict:
    uuid = str(body.get("uuid", ""))
    if not uuid:
        raise ApiError(400, "uuid required")
    _allow_profile(device, uuid, db)
    updated = int(body.get("updatedMs") or _now())
    change = str(body.get("changeId") or "")
    if not change:
        raise ApiError(400, "changeId required")
    row = db.execute("SELECT updated_ms, change_id FROM profiles WHERE uuid=?", (uuid,)).fetchone()
    from enigmacast_sync.db import decide
    choice = decide(None if row is None else row["updated_ms"], None if row is None else row["change_id"], updated, change)
    if choice == "accept":
        db.execute(
            """INSERT INTO profiles (uuid, name, avatar, language, updated_ms, change_id, deleted)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(uuid) DO UPDATE SET name=excluded.name, avatar=excluded.avatar, language=excluded.language,
                 updated_ms=excluded.updated_ms, change_id=excluded.change_id, deleted=excluded.deleted""",
            (uuid, str(body.get("name", ""))[:40], int(body.get("avatar") or 0), body.get("language"), updated, change, 1 if body.get("deleted") else 0),
        )
        db.execute(
            "INSERT INTO changes (profile_id, entity, record_key, op, payload, updated_ms, change_id, device_id) VALUES (?,?,?,?,?,?,?,?)",
            (uuid, "profile", uuid, "delete" if body.get("deleted") else "upsert", json.dumps({"name": body.get("name", "")}), updated, change, device["id"]),
        )
    db.commit()
    return {"result": choice}


def put_playlist(db, device: dict, body: dict) -> dict:
    """One IPTV login for every paired device. Not stored per profile, so a profile switch does not reload it."""
    change = str(body["changeId"])
    updated = int(body["updatedMs"])
    key = master_key(_dir(db) / "master.key")
    sealed = seal(key, json.dumps({k: body.get(k, "") for k in ("m3uUrl", "server", "username", "password", "kind")}))
    audience = json.dumps(body.get("audience") or [])
    choice = apply_record(db, "*", "playlist", body.get("id") or "default", "upsert", audience, updated, change, device["id"])
    if choice == "accept":
        db.execute(
            """INSERT INTO playlist (id, audience, secret, updated_ms, change_id, deleted) VALUES (?,?,?,?,?,0)
               ON CONFLICT(id) DO UPDATE SET audience=excluded.audience, secret=excluded.secret,
                 updated_ms=excluded.updated_ms, change_id=excluded.change_id, deleted=0""",
            (body.get("id") or "default", audience, sealed, updated, change),
        )
    db.commit()
    return {"result": choice}


def get_playlist(db, device: dict) -> dict:
    """The device-wide IPTV login, decrypted for any paired device. Null when nobody stored one."""
    del device
    row = db.execute("SELECT * FROM playlist WHERE id=? AND deleted=0", ("default",)).fetchone()
    if row is None:
        return {"playlist": None}
    raw = json.loads(open_secret(master_key(_dir(db) / "master.key"), row["secret"]))
    return {"playlist": {
        "id": row["id"],
        "m3uUrl": raw.get("m3uUrl", ""),
        "server": raw.get("server", ""),
        "username": raw.get("username", ""),
        "password": raw.get("password", ""),
        "kind": raw.get("kind", ""),
        "updatedMs": row["updated_ms"],
        "changeId": row["change_id"],
    }}


def push(db, device: dict, body: dict) -> dict:
    results = []
    for change in body.get("changes") or []:
        profile_id = str(change.get("profileId", ""))
        _allow_profile(device, profile_id, db)
        results.append(apply_record(
            db, profile_id, str(change.get("entity", "")), str(change.get("key", "")),
            str(change.get("op", "upsert")), json.dumps(change.get("payload") or {}),
            int(change.get("updatedMs") or 0), str(change.get("changeId", "")), device["id"],
        ))
    db.commit()
    return {"results": results}


def sync_pull(db, device: dict, profile_id: str, cursor: int) -> dict:
    _allow_profile(device, profile_id, db)
    nxt, changes = pull(db, [profile_id], cursor)
    return {"cursor": nxt, "changes": changes}


def make_backup(db, device: dict) -> dict:
    require_admin(device)
    data = _dir(db)
    target = backup(data / "sync.db", data / "master.key", data / "backups")
    return {"path": str(target)}


def restore_backup(db, device: dict, folder: str) -> dict:
    require_admin(device)
    data = _dir(db)
    restore(Path(folder), data / "sync.db", data / "master.key")
    return {"restored": folder}


def _allow_profile(device: dict, profile_id: str, db) -> None:
    """Any paired EnigmaCast TV may sync every profile. Revoked devices never reach this."""
    del device, profile_id, db


def assign_profiles(db, device: dict, device_id: str, profile_ids: list[str]) -> dict:
    require_admin(device)
    _set_meta(db, f"device-profiles:{device_id}", json.dumps(profile_ids))
    db.commit()
    return {"deviceId": device_id, "profiles": profile_ids}


def _bearer(headers: dict) -> str:
    raw = headers.get("authorization") or headers.get("Authorization") or ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return ""


def _b64(value: str) -> bytes:
    import base64
    try:
        return base64.b64decode(value)
    except Exception as exc:
        raise ApiError(400, "bad base64") from exc


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _now() -> int:
    return int(time.time() * 1000)
