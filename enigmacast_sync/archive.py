"""Complete EnigmaCast data archive: database, secrets and TLS identity.

A database snapshot alone is not enough to survive a firmware update: the
master key decrypts IPTV passwords, the certificate/key keep the paired apps
trusting the same server, and the dashboard hash keeps its password. This
module creates and validates one portable tar.gz with those files.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import ssl
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from enigmacast_sync import __version__

FORMAT = 1
MANIFEST = "manifest.json"
ALLOWED = frozenset({"sync.db", "master.key", "cert.pem", "key.pem", "web-auth.json"})
MAX_MEMBERS = 16
MAX_UNCOMPRESSED = 64 * 1024 * 1024


class ArchiveError(ValueError):
    """The uploaded archive is not a safe, complete EnigmaCast backup."""


@dataclass(frozen=True)
class Bundle:
    root: Path
    files: frozenset[str]
    manifest: dict


def create_full_backup(data: Path, dest: Path | None = None) -> Path:
    """Write a complete backup and return its path.

    The database is copied with SQLite's online backup API, so this is safe
    while the Sync API is running. The archive includes the master key and,
    when present, the TLS certificate/key and dashboard password hash.
    """
    data = Path(data)
    database = data / "sync.db"
    if not database.is_file():
        raise FileNotFoundError("database missing")
    master = data / "master.key"
    if not master.is_file():
        raise FileNotFoundError("master key missing")

    backups = data / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".full-", dir=backups))
    try:
        source = sqlite3.connect(database)
        snapshot = sqlite3.connect(staging / "sync.db")
        try:
            source.backup(snapshot)
        finally:
            snapshot.close()
            source.close()

        files = ["sync.db"]
        shutil.copy2(master, staging / "master.key")
        files.append("master.key")
        cert, key = data / "cert.pem", data / "key.pem"
        if cert.is_file() and key.is_file():
            shutil.copy2(cert, staging / "cert.pem")
            shutil.copy2(key, staging / "key.pem")
            files.extend(("cert.pem", "key.pem"))
        elif cert.is_file() or key.is_file():
            raise ArchiveError("certificate and key must both exist")
        auth = data / "web-auth.json"
        if auth.is_file():
            shutil.copy2(auth, staging / "web-auth.json")
            files.append("web-auth.json")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        manifest = {
            "format": FORMAT,
            "service": "enigmacast-sync",
            "serverVersion": __version__,
            "created": stamp,
            "files": files,
        }
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        target = Path(dest) if dest is not None else backups / f"enigmacast-full-{stamp}.tar.gz"
        target.parent.mkdir(parents=True, exist_ok=True)
        suffix = 1
        while target.exists():
            target = target.with_name(f"{target.stem}-{suffix}{target.suffix}")
            suffix += 1
        with tarfile.open(target, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            for name in files + [MANIFEST]:
                archive.add(staging / name, arcname=name, recursive=False)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return target
    finally:
        shutil.rmtree(staging, ignore_errors=True)


@contextmanager
def open_bundle(path: Path) -> Iterator[Bundle]:
    """Validate an archive without trusting any member name or path."""
    path = Path(path)
    stage = Path(tempfile.mkdtemp(prefix="enigmacast-restore-"))
    try:
        try:
            archive = tarfile.open(path, "r:gz")
        except (OSError, tarfile.TarError) as exc:
            raise ArchiveError("geen geldige .tar.gz") from exc
        with archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_MEMBERS:
                raise ArchiveError("ongeldige inhoud")
            names: set[str] = set()
            total = 0
            for member in members:
                name = _member_name(member.name)
                if name is None:
                    raise ArchiveError(f"ongeldig bestand: {member.name}")
                if name in names:
                    raise ArchiveError(f"dubbel bestand: {name}")
                if not member.isfile():
                    raise ArchiveError(f"geen gewoon bestand: {name}")
                if name != MANIFEST and name not in ALLOWED:
                    raise ArchiveError(f"onbekend bestand: {name}")
                if member.size < 0 or member.size > MAX_UNCOMPRESSED:
                    raise ArchiveError(f"bestand te groot: {name}")
                total += member.size
                if total > MAX_UNCOMPRESSED:
                    raise ArchiveError("back-up is te groot")
                names.add(name)
                payload = archive.extractfile(member)
                if payload is None:
                    raise ArchiveError(f"bestand ontbreekt: {name}")
                with open(stage / name, "wb") as output:
                    shutil.copyfileobj(payload, output)
        if "sync.db" not in names or "master.key" not in names:
            raise ArchiveError("database of master.key ontbreekt")
        _check_database(stage / "sync.db")
        _check_master(stage / "master.key")
        if ("cert.pem" in names) != ("key.pem" in names):
            raise ArchiveError("certificaat en sleutel horen samen")
        if "cert.pem" in names:
            _check_certificate(stage)
        if "web-auth.json" in names:
            _check_auth(stage / "web-auth.json")
        manifest = {}
        if MANIFEST in names:
            try:
                manifest = json.loads((stage / MANIFEST).read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("manifest is not an object")
                if int(manifest.get("format") or 0) > FORMAT:
                    raise ArchiveError("nieuwere back-upindeling")
            except ArchiveError:
                raise
            except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ArchiveError("ongeldig manifest") from exc
        yield Bundle(stage, frozenset(names), manifest)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def restore_full_backup(archive: Path, data: Path) -> dict:
    """Validate, make a safety backup, then atomically replace the data files.

    The caller must stop the Sync API first. The web process should be
    restarted afterwards because the certificate and dashboard hash change.
    """
    data = Path(data)
    with open_bundle(archive) as bundle:
        safety = create_full_backup(data)
        restored: list[str] = []
        for name in bundle.files:
            if name == MANIFEST:
                continue
            target = data / name
            temporary = data / f".{name}.restore"
            shutil.copyfile(bundle.root / name, temporary)
            if name in ("master.key", "key.pem", "web-auth.json"):
                temporary.chmod(0o600)
            elif name == "cert.pem":
                temporary.chmod(0o644)
            else:
                temporary.chmod(0o600)
            os.replace(temporary, target)
            restored.append(name)
        # Old WAL data belongs to the replaced database and must not be replayed.
        for suffix in ("-wal", "-shm"):
            (data / f"sync.db{suffix}").unlink(missing_ok=True)
        # The restored dashboard password must invalidate the current browser session.
        (data / "web.secret").unlink(missing_ok=True)
        (data / "web-password-once").unlink(missing_ok=True)
        _chown_service(data)
        return {"safety": str(safety), "files": sorted(restored)}


def _member_name(name: str) -> str | None:
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    if not name or "/" in name or name in (".", "..") or name.startswith("."):
        return None
    return name


def _check_database(path: Path) -> None:
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        result = db.execute("PRAGMA quick_check").fetchone()
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.close()
    except sqlite3.Error as exc:
        raise ArchiveError("database is beschadigd") from exc
    if not result or result[0] != "ok":
        raise ArchiveError("database is beschadigd")
    if not {"profiles", "devices", "records"}.issubset(tables):
        raise ArchiveError("database hoort niet bij EnigmaCast")


def _check_master(path: Path) -> None:
    if path.stat().st_size != 32:
        raise ArchiveError("master.key heeft de verkeerde lengte")


def _check_certificate(root: Path) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(str(root / "cert.pem"), str(root / "key.pem"))
    except (OSError, ssl.SSLError) as exc:
        raise ArchiveError("certificaat en sleutel passen niet bij elkaar") from exc


def _check_auth(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not all(key in payload for key in ("salt", "hash", "iterations")):
            raise ValueError("missing field")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ArchiveError("ongeldig dashboardwachtwoord") from exc


def _chown_service(data: Path) -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    import pwd
    try:
        user = pwd.getpwnam("enigmacast")
    except KeyError:
        return
    for path in [data, *data.iterdir()]:
        try:
            os.chown(path, user.pw_uid, user.pw_gid)
        except OSError:
            pass
