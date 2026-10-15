"""Allow the dashboard only from the local network or a private VPN address."""

from __future__ import annotations

import ipaddress


def client_ip(value: str) -> str:
    text = (value or "").strip()
    if text.startswith("::ffff:"):
        text = text[7:]
    if "%" in text:
        text = text.split("%", 1)[0]
    return text


def allowed(value: str) -> bool:
    try:
        address = ipaddress.ip_address(client_ip(value))
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local
