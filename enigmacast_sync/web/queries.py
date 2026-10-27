"""Read-only dashboard facts from the existing sync database and the local system."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.request
from pathlib import Path

from enigmacast_sync import __version__

RELEASE_URL = "https://api.github.com/repos/ecannemeijer/enigmacast-enigma2-sync/releases/latest"
PAGE_SIZE = 20
SORTS = frozenset({"name", "updated", "favorites", "vod", "episodes"})


def connect(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def overview(db: sqlite3.Connection | None, data: Path) -> dict:
    profiles = devices = 0
    last = None
    if db is not None:
        profiles = len(_profile_rows(db))
        devices = db.execute("SELECT COUNT(*) AS n FROM devices").fetchone()["n"]
        last = db.execute("SELECT MAX(updated_ms) AS ms FROM changes").fetchone()["ms"]
    database = data / "sync.db"
    return {
        "version": __version__,
        "profiles": profiles,
        "devices": devices,
        "lastSyncMs": last,
        "databaseBytes": database.stat().st_size if database.is_file() else 0,
        "disk": disk(data),
        "memory": memory(),
    }


def profiles(db: sqlite3.Connection | None, query: str, sort: str, page: int) -> dict:
    rows = _profile_rows(db)
    needle = query.strip().lower()
    if needle:
        rows = [row for row in rows if needle in row["name"].lower() or needle in row["uuid"].lower()]
    key = sort if sort in SORTS else "name"

    def order(row: dict):
        value = row[key]
        if key == "name":
            return (0, str(value).lower())
        return (value is None, value)

    rows.sort(key=order)
    page = max(page, 1)
    start = (page - 1) * PAGE_SIZE
    return {"total": len(rows), "page": page, "pageSize": PAGE_SIZE, "profiles": rows[start:start + PAGE_SIZE]}


def profile_detail(db: sqlite3.Connection | None, uuid: str) -> dict | None:
    if db is None:
        return None
    row = db.execute(
        "SELECT uuid, name, avatar, language, updated_ms FROM profiles WHERE uuid=? AND deleted=0",
        (uuid,),
    ).fetchone()
    if row is None:
        removed = db.execute("SELECT 1 FROM profiles WHERE uuid=? AND deleted=1", (uuid,)).fetchone()
        used = db.execute(
            "SELECT 1 FROM records WHERE profile_id=? UNION SELECT 1 FROM changes WHERE profile_id=? LIMIT 1",
            (uuid, uuid),
        ).fetchone()
        if removed is not None or used is None:
            return None
        row = {"uuid": uuid, "name": uuid, "avatar": 0, "language": None, "updated_ms": None}
    items = []
    for record in db.execute(
        "SELECT entity, record_key, payload, updated_ms, deleted FROM records WHERE profile_id=?",
        (uuid,),
    ):
        if record["deleted"] or record["entity"] not in ("favorite", "progress"):
            continue
        payload = _json(record["payload"])
        items.append({
            "entity": record["entity"],
            "kind": payload.get("kind") or "",
            "title": str(payload.get("title") or record["record_key"]),
            "updatedMs": record["updated_ms"],
        })
    found = next((item for item in _profile_rows(db) if item["uuid"] == uuid), None)
    return {
        "uuid": row["uuid"],
        "name": row["name"],
        "avatar": row["avatar"],
        "language": row["language"],
        "updatedMs": row["updated_ms"],
        "favorites": None if found is None else found["favorites"],
        "vod": None if found is None else found["vod"],
        "episodes": None if found is None else found["episodes"],
        "watchHistory": None,
        "items": items,
    }


def devices(db: sqlite3.Connection | None) -> list[dict]:
    if db is None:
        return []
    last: dict[str, int] = {}
    for row in db.execute("SELECT device_id, MAX(updated_ms) AS ms FROM changes GROUP BY device_id"):
        last[row["device_id"]] = row["ms"]
    result = []
    for row in db.execute("SELECT id, name, role, revoked, created_ms FROM devices ORDER BY created_ms"):
        result.append({
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "revoked": bool(row["revoked"]),
            "createdMs": row["created_ms"],
            "lastSyncMs": last.get(row["id"]),
            "lastConnectionMs": None,
            "appVersion": None,
        })
    return result


def synchronisation(db: sqlite3.Connection | None, limit: int = 100) -> dict:
    if db is None:
        events = []
    else:
        names = {row["id"]: row["name"] for row in db.execute("SELECT id, name FROM devices")}
        events = []
        for row in db.execute(
            """SELECT rev, profile_id, entity, record_key, op, updated_ms, device_id
               FROM changes ORDER BY rev DESC LIMIT ?""",
            (limit,),
        ):
            events.append({
                "rev": row["rev"],
                "profileId": row["profile_id"],
                "entity": row["entity"],
                "key": row["record_key"],
                "op": row["op"],
                "updatedMs": row["updated_ms"],
                "device": names.get(row["device_id"]) or row["device_id"],
            })
    return {"events": events, "conflictsTracked": False, "queueTracked": False}


def release_info(opener=None) -> dict:
    current = __version__
    fetch = opener or urllib.request.urlopen
    try:
        request = urllib.request.Request(RELEASE_URL, headers={"User-Agent": "enigmacast-sync", "Accept": "application/vnd.github+json"})
        with fetch(request, timeout=4) as response:
            body = json.loads(response.read().decode("utf-8"))
        latest = str(body.get("tag_name") or "").lstrip("v")
    except Exception:
        return {"version": current, "latest": None, "url": None}
    return {
        "version": current,
        "latest": latest or None,
        "url": body.get("html_url"),
        "update": bool(latest) and _version(latest) > _version(current),
    }


def disk(path: Path) -> dict | None:
    try:
        info = os.statvfs(path if path.is_dir() else path.parent)
    except (AttributeError, OSError):
        return None
    total = info.f_frsize * info.f_blocks
    free = info.f_frsize * info.f_bavail
    return {"total": total, "free": free}


def memory() -> dict | None:
    path = Path("/proc/meminfo")
    if not path.is_file():
        return None
    values: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            values[key] = int(parts[0]) * 1024
    if "MemTotal" not in values:
        return None
    return {"total": values["MemTotal"], "available": values.get("MemAvailable")}


def cpu_jiffies() -> tuple[int, int] | None:
    path = Path("/proc/stat")
    if not path.is_file():
        return None
    first = path.read_text(encoding="utf-8", errors="replace").splitlines()[0].split()
    if not first or first[0] != "cpu":
        return None
    numbers = [int(part) for part in first[1:]]
    idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
    return sum(numbers), idle


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def pid_of(path: Path) -> int | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text.isdigit():
        return None
    pid = int(text)
    return pid if process_alive(pid) else None


def uptime_seconds(pid: int | None) -> float | None:
    if pid is None:
        return None
    stat = Path(f"/proc/{pid}/stat")
    if not stat.is_file():
        return None
    try:
        fields = stat.read_text(encoding="utf-8", errors="replace").rsplit(")", 1)[1].split()
        start = int(fields[19])
        ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        raw = Path("/proc/uptime").read_text(encoding="utf-8").split()[0]
        return max(0.0, float(raw) - (start / ticks))
    except (IndexError, OSError, ValueError):
        return None


def api_listening(port: int = 8740) -> bool:
    import socket
    import ssl
    try:
        sock = socket.create_connection(("127.0.0.1", port), 0.4)
    except OSError:
        return False
    try:
        context = ssl._create_unverified_context()
        tls = context.wrap_socket(sock, server_hostname="127.0.0.1")
        tls.sendall(b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        data = b""
        while len(data) < 512:
            chunk = tls.recv(256)
            if not chunk:
                break
            data += chunk
        return b'"ok": true' in data or b'"ok":true' in data
    except OSError:
        return False
    finally:
        sock.close()


TAIL_BYTES = 64 * 1024


def tail(path: Path, limit: int = 200) -> list[str]:
    """The last lines of a log. Reads at most TAIL_BYTES from the end, not the whole file:
    sync.log and web.log grow for months and the receiver has little free RAM."""
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - TAIL_BYTES)
            handle.seek(start)
            raw = handle.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if start and lines:
        lines = lines[1:]  # the first line is cut off
    return [line for line in lines[-limit:] if not _sensitive(line)]


def _profile_rows(db: sqlite3.Connection | None) -> list[dict]:
    if db is None:
        return []
    counts: dict[str, dict[str, int]] = {}
    for record in db.execute("SELECT profile_id, entity, payload, deleted FROM records"):
        if record["deleted"] or record["entity"] not in ("favorite", "progress"):
            continue
        bucket = counts.setdefault(record["profile_id"], {"favorites": 0, "vod": 0, "episodes": 0})
        if record["entity"] == "favorite":
            bucket["favorites"] += 1
            continue
        kind = str(_json(record["payload"]).get("kind") or "")
        if kind == "m":
            bucket["vod"] += 1
        elif kind in ("s", "e"):
            bucket["episodes"] += 1
    rows = []
    seen: set[str] = set()
    removed: set[str] = set()
    for profile in db.execute("SELECT uuid, name, updated_ms, deleted FROM profiles"):
        if profile["deleted"]:
            removed.add(profile["uuid"])
            continue
        seen.add(profile["uuid"])
        bucket = counts.get(profile["uuid"], {"favorites": 0, "vod": 0, "episodes": 0})
        rows.append({
            "uuid": profile["uuid"],
            "name": profile["name"],
            "updated": profile["updated_ms"],
            "favorites": bucket["favorites"],
            "vod": bucket["vod"],
            "episodes": bucket["episodes"],
            "watchHistory": None,
        })
    extras: set[str] = set()
    for row in db.execute("SELECT DISTINCT profile_id AS id FROM records UNION SELECT DISTINCT profile_id AS id FROM changes"):
        ident = str(row["id"] or "")
        if ident and ident != "*" and ident not in seen and ident not in removed:
            extras.add(ident)
    for ident in sorted(extras):
        bucket = counts.get(ident, {"favorites": 0, "vod": 0, "episodes": 0})
        updated = db.execute(
            "SELECT MAX(updated_ms) AS ms FROM records WHERE profile_id=?",
            (ident,),
        ).fetchone()["ms"]
        rows.append({
            "uuid": ident,
            "name": ident,
            "updated": updated,
            "favorites": bucket["favorites"],
            "vod": bucket["vod"],
            "episodes": bucket["episodes"],
            "watchHistory": None,
        })
    return rows


def _json(payload: str) -> dict:
    try:
        loaded = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _sensitive(line: str) -> bool:
    lowered = line.lower()
    return any(word in lowered for word in ("password", "token", "pairing", "recovery", "secret", "authorization", "bearer"))


def _version(text: str) -> tuple[int, ...]:
    parts = []
    for piece in text.split("."):
        digits = ""
        for char in piece:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


class CpuSampler:
    def __init__(self) -> None:
        self._last: tuple[int, int] | None = None

    def percent(self) -> float | None:
        sample = cpu_jiffies()
        previous = self._last
        self._last = sample
        if sample is None or previous is None:
            return None
        total = sample[0] - previous[0]
        idle = sample[1] - previous[1]
        if total <= 0:
            return None
        return round(100.0 * (total - idle) / total, 1)


def now_ms() -> int:
    return int(time.time() * 1000)
