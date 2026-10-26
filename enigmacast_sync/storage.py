"""Pick permanent storage. A mounted disk wins; otherwise the data stays in /etc."""

from __future__ import annotations

import os
from pathlib import Path

CANDIDATES = (Path("/media/hdd"), Path("/media/usb"), Path("/media/mmc"), Path("/media/cf"))
FALLBACK = Path("/etc/enigmacast-sync")


def choose_data_dir(
    mounts: list[Path] | None = None,
    proc_mounts: str | None = None,
    legacy_roots: list[Path] | None = None,
    fallback: Path = FALLBACK,
) -> Path:
    """Where the database lives. Must match pick_dir() in the init scripts.

    1. a mounted disk that already holds a sync database;
    2. the first writable mounted disk;
    3. a database left by 0.2.4 or older in a /media folder that is not a mount point
       (those versions only checked that the folder existed), unless /etc already has one;
    4. /etc/enigmacast-sync.
    Rule 3 keeps an upgrade from losing its pairings, certificate and history."""
    roots = mounts if mounts is not None else _mounted_candidates(proc_mounts)
    writable = [root for root in roots if root.is_dir() and os.access(root, os.W_OK)]
    for root in writable:
        if (root / "enigmacast-sync" / "sync.db").is_file():
            return root / "enigmacast-sync"
    if writable:
        return writable[0] / "enigmacast-sync"
    if not (fallback / "sync.db").is_file():
        for root in (legacy_roots if legacy_roots is not None else list(CANDIDATES)):
            data = root / "enigmacast-sync"
            if (data / "sync.db").is_file() and os.access(data, os.W_OK):
                return data
    return fallback


def _mounted_candidates(proc_mounts: str | None) -> list[Path]:
    points = _mount_points(proc_mounts)
    return [path for path in CANDIDATES if str(path) in points]


def _mount_points(proc_mounts: str | None) -> set[str]:
    if proc_mounts is None:
        try:
            proc_mounts = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return set()
    points: set[str] = set()
    for line in proc_mounts.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            points.add(parts[1].replace("\\040", " "))
    return points
