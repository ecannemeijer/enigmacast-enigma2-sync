"""P-256 ECDSA (SHA-256) using only the standard library."""

from __future__ import annotations

import hashlib
import secrets

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5


def _inv(value: int, mod: int) -> int:
    return pow(value, -1, mod)


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        slope = (3 * x1 * x1 + A) * _inv(2 * y1, P) % P
    else:
        slope = (y2 - y1) * _inv(x2 - x1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    y3 = (slope * (x1 - x3) - y1) % P
    return x3, y3


def _mul(k: int, point):
    result = None
    addend = point
    while k:
        if k & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        k >>= 1
    return result


def sign(private: int, message: bytes) -> bytes:
    digest = int.from_bytes(hashlib.sha256(message).digest(), "big") % N
    while True:
        k = secrets.randbelow(N - 1) + 1
        point = _mul(k, (GX, GY))
        r = point[0] % N
        if r == 0:
            continue
        s = _inv(k, N) * (digest + r * private) % N
        if s == 0:
            continue
        return _der(r, s)


def verify(public_spki: bytes, message: bytes, signature: bytes) -> bool:
    point = uncompressed_point(public_spki)
    if point is None:
        return False
    parsed = _parse_der(signature)
    if parsed is None:
        return False
    r, s = parsed
    if not (1 <= r < N and 1 <= s < N):
        return False
    digest = int.from_bytes(hashlib.sha256(message).digest(), "big") % N
    w = _inv(s, N)
    u1 = digest * w % N
    u2 = r * w % N
    point = _add(_mul(u1, (GX, GY)), _mul(u2, point))
    return point is not None and point[0] % N == r


def uncompressed_point(spki_or_point: bytes):
    if len(spki_or_point) == 65 and spki_or_point[0] == 4:
        raw = spki_or_point
    else:
        marker = spki_or_point.find(b"\x03\x42\x00\x04")
        if marker < 0:
            return None
        raw = spki_or_point[marker + 3:marker + 68]
    if len(raw) != 65 or raw[0] != 4:
        return None
    x = int.from_bytes(raw[1:33], "big")
    y = int.from_bytes(raw[33:65], "big")
    if x >= P or y >= P or (y * y - (x * x * x + A * x + B)) % P != 0:
        return None
    return x, y


def generate():
    private = secrets.randbelow(N - 1) + 1
    x, y = _mul(private, (GX, GY))
    point = b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")
    return private, point


def _der(r: int, s: int) -> bytes:
    def part(value: int) -> bytes:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        if raw[0] & 0x80:
            raw = b"\x00" + raw
        return b"\x02" + bytes([len(raw)]) + raw

    body = part(r) + part(s)
    return b"\x30" + bytes([len(body)]) + body


def _parse_der(signature: bytes):
    if len(signature) < 8 or signature[0] != 0x30:
        return None
    index = 2
    if signature[1] & 0x80:
        return None

    def read():
        nonlocal index
        if signature[index] != 0x02:
            return None
        length = signature[index + 1]
        index += 2
        chunk = signature[index:index + length]
        index += length
        return int.from_bytes(chunk, "big")

    r = read()
    s = read()
    if r is None or s is None or index != len(signature):
        return None
    return r, s
