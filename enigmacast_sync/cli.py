"""Command line: init, serve, recover."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from enigmacast_sync.service import init_server, open_db, recover
from enigmacast_sync.storage import choose_data_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="enigmacast-sync")
    parser.add_argument("--data-dir", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("serve")
    sub.add_parser("serve-web")
    rec = sub.add_parser("recover")
    rec.add_argument("--code", required=True)
    args = parser.parse_args(argv)
    data = Path(args.data_dir) if args.data_dir else choose_data_dir()
    db = open_db(data)
    if args.cmd == "init":
        _ensure_cert(data)
        print(json.dumps(init_server(db)))
        return 0
    if args.cmd == "recover":
        print(json.dumps(recover(db, args.code)))
        return 0
    if args.cmd == "serve-web":
        db.close()
        _ensure_cert(data)
        _become_service_user(data)
        from enigmacast_sync.web.server import serve as serve_web
        serve_web(data)
        return 0
    cert, key = _ensure_cert(data)
    (data / "logs").mkdir(parents=True, exist_ok=True)
    _become_service_user(data)
    (data / "enigmacast-sync.pid").write_text(str(os.getpid()), encoding="utf-8")
    _append_log(data / "logs" / "sync.log")
    from enigmacast_sync.http_server import serve
    serve(data, cert, key)
    return 0


def _become_service_user(data: Path) -> None:
    """Leave root after files are in place. The init script starts Python; this process does not stay root."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    import pwd
    try:
        user = pwd.getpwnam("enigmacast")
    except KeyError:
        print("user enigmacast is missing", file=sys.stderr)
        raise SystemExit(1)
    for path in [data, *data.rglob("*")]:
        try:
            os.chown(path, user.pw_uid, user.pw_gid)
        except OSError:
            pass
    try:
        os.setgroups([])
    except OSError:
        pass
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)


def _append_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a", encoding="utf-8")
    os.dup2(handle.fileno(), 1)
    os.dup2(handle.fileno(), 2)


def _ensure_cert(data: Path) -> tuple[Path, Path]:
    cert, key = data / "cert.pem", data / "key.pem"
    if cert.is_file() and key.is_file():
        return cert, key
    data.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key), "-out", str(cert),
         "-days", "3650", "-nodes", "-subj", "/CN=enigmacast-sync"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        key.chmod(0o600)
    except OSError:
        pass
    return cert, key


if __name__ == "__main__":
    raise SystemExit(main())
