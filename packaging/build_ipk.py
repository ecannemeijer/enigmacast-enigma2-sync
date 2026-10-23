#!/usr/bin/env python3
"""Build enigmacast-sync_<version>_all.ipk without ipkg-build."""

from __future__ import annotations

import io
import os
import stat
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = "enigmacast-sync"


def main() -> None:
    import sys
    version = os.environ.get("VERSION", "0.2.8")
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "dist")
    out.mkdir(parents=True, exist_ok=True)
    stage = _stage(version)
    data = _tar(stage, skip_control=True)
    control = _tar(stage, skip_control=False)
    payload = _ar([
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", control),
        ("data.tar.gz", data),
    ])
    target = out / f"{PKG}_{version}_all.ipk"
    target.write_bytes(payload)
    print(f"Wrote {target}")


def _stage(version: str) -> dict[str, tuple[bytes, int]]:
    files: dict[str, tuple[bytes, int]] = {}
    plugin = ROOT / "enigmacast_sync"
    for path in plugin.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = Path("usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync/enigmacast_sync") / path.relative_to(plugin)
        files[rel.as_posix()] = (path.read_bytes(), 0o644)
    files["usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync/plugin.py"] = (
        (ROOT / "plugin" / "plugin.py").read_bytes(), 0o644,
    )
    for name in ("sync-run", "web-run"):
        files[f"usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync/{name}"] = (
            (ROOT / "packaging" / name).read_bytes().replace(b"\r\n", b"\n"), 0o755,
        )
    files["usr/lib/enigma2/python/Plugins/Extensions/WebInterface/WebChilds/External/enigmacast.py"] = (
        (ROOT / "plugin" / "owif" / "enigmacast.py").read_bytes(), 0o644,
    )
    for name in ("enigmacast-sync", "enigmacast-web"):
        files[f"etc/init.d/{name}"] = ((ROOT / "packaging" / name).read_bytes().replace(b"\r\n", b"\n"), 0o755)
    control = (
        f"Package: {PKG}\nVersion: {version}\nArchitecture: all\n"
        "Section: extra\nMaintainer: ecannemeijer\n"
        "Depends: python3-core, python3-sqlite3, openssl\n"
        "Description: EnigmaCast sync API and web dashboard\n"
    ).encode()
    files["CONTROL/control"] = (control, 0o644)
    for name in ("postinst", "prerm", "postrm"):
        files[f"CONTROL/{name}"] = ((ROOT / "packaging" / name).read_bytes().replace(b"\r\n", b"\n"), 0o755)
    return files


def _tar(files: dict[str, tuple[bytes, int]], skip_control: bool) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name in sorted(files):
            if skip_control and name.startswith("CONTROL/"):
                continue
            if not skip_control and not name.startswith("CONTROL/"):
                continue
            member = name[len("CONTROL/"):] if name.startswith("CONTROL/") else name
            data, mode = files[name]
            info = tarfile.TarInfo(member)
            info.size = len(data)
            info.mode = mode
            info.mtime = int(time.time())
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            if mode & stat.S_IXUSR:
                info.mode = 0o755
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _ar(members: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    out.write(b"!<arch>\n")
    for name, data in members:
        header = name.encode("ascii").ljust(16)
        header += str(int(time.time())).encode().ljust(12)
        header += b"0".ljust(6) + b"0".ljust(6) + b"100644".ljust(8)
        header += str(len(data)).encode().ljust(10)
        header += b"`\n"
        if len(header) != 60:
            raise RuntimeError("bad ar header")
        out.write(header)
        out.write(data)
        if len(data) % 2:
            out.write(b"\n")
    return out.getvalue()


if __name__ == "__main__":
    main()
