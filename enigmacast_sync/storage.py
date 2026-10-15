"""Pick permanent storage. Internal flash is refused when a disk is mounted."""

from __future__ import annotations

import os
from pathlib import Path

CANDIDATES = (Path("/media/hdd"), Path("/media/usb"), Path("/media/mmc"), Path("/media/cf"))


def choose_data_dir(mounts: list[Path] | None = None) -> Path | None:
    roots = mounts if mounts is not None else [path for path in CANDIDATES if path.is_dir() and os.access(path, os.W_OK)]
    for root in roots:
        if root.is_dir():
            return root / "enigmacast-sync"
    return None
