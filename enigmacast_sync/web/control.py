"""Start, stop and restart the Sync API through a fixed init script. No shell."""

from __future__ import annotations

import subprocess
from pathlib import Path

ACTIONS = frozenset({"start", "stop", "restart"})


def run(script: Path, action: str) -> int:
    if action not in ACTIONS:
        raise ValueError("unsupported action")
    if not script.is_file():
        raise FileNotFoundError(str(script))
    completed = subprocess.run([str(script), action], check=False)
    return completed.returncode
