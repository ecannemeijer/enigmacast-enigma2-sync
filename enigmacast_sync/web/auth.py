"""Browser login, HMAC sessions and CSRF tokens. No password is stored in source."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

ITERATIONS = 200_000
SESSION_SECONDS = 8 * 60 * 60
COOKIE = "ec_web"
DEFAULT_PASSWORD = "admin"


def ensure_password(data: Path) -> None:
    """Set the password to admin the first time the Web Manager starts."""
    if (data / "web-auth.json").is_file():
        return
    set_password(data, DEFAULT_PASSWORD)
    _write_private(data / "web-password-once", DEFAULT_PASSWORD + "\n")


def set_password(data: Path, password: str) -> None:
    """Store a new password hash and drop the one-time plaintext file."""
    if len(password) < 4:
        raise ValueError("too short")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    _write_private(data / "web-auth.json", json.dumps({
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(digest).decode("ascii"),
        "iterations": ITERATIONS,
    }))
    once = data / "web-password-once"
    if once.is_file():
        once.unlink()


def check_password(data: Path, password: str) -> bool:
    path = data / "web-auth.json"
    if not path.is_file() or not password:
        return False
    raw = json.loads(path.read_text(encoding="utf-8"))
    salt = base64.b64decode(raw["salt"])
    expected = base64.b64decode(raw["hash"])
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(raw["iterations"]))
    return hmac.compare_digest(digest, expected)


def session_secret(data: Path) -> bytes:
    path = data / "web.secret"
    if path.is_file():
        return path.read_bytes()
    secret = secrets.token_bytes(32)
    _write_private(path, secret)
    return secret


def new_session(secret: bytes, authenticated: bool, *, login: bool = False) -> tuple[str, str]:
    """A login token is signed into the form so a browser that drops the cookie can still sign in."""
    csrf = secrets.token_urlsafe(24)
    lifetime = 2 * 60 * 60 if login else SESSION_SECONDS
    payload = {
        "exp": int(time.time()) + lifetime,
        "csrf": csrf,
        "auth": bool(authenticated),
        "login": bool(login),
    }
    return _sign(secret, payload), csrf


def read_session(secret: bytes, token: str) -> dict | None:
    if not token or "." not in token:
        return None
    body, mac = token.rsplit(".", 1)
    expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    if len(expected) != len(mac) or not hmac.compare_digest(expected, mac):
        return None
    try:
        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("exp") or 0) < time.time():
        return None
    if not payload.get("csrf"):
        return None
    return payload


def cookie_header(token: str, secure: bool) -> str:
    parts = [f"{COOKIE}={token}", "HttpOnly", "SameSite=Lax", "Path=/", f"Max-Age={SESSION_SECONDS}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie(secure: bool) -> str:
    parts = [f"{COOKIE}=", "HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=0"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def cookie_value(header: str) -> str:
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return ""


def _sign(secret: bytes, payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    mac = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    return body + "." + mac


def _write_private(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    try:
        path.chmod(0o600)
    except OSError:
        pass
