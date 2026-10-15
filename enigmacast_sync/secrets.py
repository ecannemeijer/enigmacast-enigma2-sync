"""Field encryption for playlist secrets. The key file travels with backups."""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

from enigmacast_sync.aes import decrypt, encrypt


def master_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        key = path.read_bytes()
        if len(key) == 32:
            return key
    key = os.urandom(32)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def seal(key: bytes, plaintext: str) -> str:
    return encrypt(key, plaintext.encode("utf-8")).hex()


def open_secret(key: bytes, stored: str) -> str:
    return decrypt(key, bytes.fromhex(stored)).decode("utf-8")


def fingerprint(material: bytes) -> str:
    return sha256(material).hexdigest()
