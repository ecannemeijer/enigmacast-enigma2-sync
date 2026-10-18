"""Pick permanent storage. A mounted disk wins; otherwise the data stays in /etc."""

from __future__ import annotations

import os
from pathlib import Path

CANDIDATES = (Path("/media/hdd"), Path("/media/usb"), Path("/media/mmc"), Path("/media/cf"))
FALLBACK = Path("/etc/enigmacast-sync")


def choose_data_dir(mounts: list[Path] | None = None, proc_mounts: str | None = None) -> Path:
    roots = mounts if mounts is not None else _mounted_candidates(proc_mounts)
    for root in roots:
        if root.is_dir() and os.access(root, os.W_OK):
            return root / "enigmacast-sync"
    return FALLBACK


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
