"""AES-256-GCM with the standard library only, so an Enigma2 image needs no pip packages."""

from __future__ import annotations

import os
import struct

SBOX = (
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
)
RCON = (0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1) & 0xFF


def _expand(key: bytes) -> list[list[int]]:
    words = [list(key[i:i + 4]) for i in range(0, 32, 4)]
    i = 8
    while len(words) < 60:
        temp = words[-1][:]
        if i % 8 == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[i // 8]
        elif i % 8 == 4:
            temp = [SBOX[b] for b in temp]
        words.append([words[-8][j] ^ temp[j] for j in range(4)])
        i += 1
    return words


def _encrypt_block(block: bytes, words: list[list[int]]) -> bytes:
    state = [list(block[c * 4:(c + 1) * 4]) for c in range(4)]
    state = [[state[c][r] for c in range(4)] for r in range(4)]

    def add(round_i: int):
        for c in range(4):
            word = words[round_i * 4 + c]
            for r in range(4):
                state[r][c] ^= word[r]

    def sub():
        for r in range(4):
            for c in range(4):
                state[r][c] = SBOX[state[r][c]]

    def shift():
        for r in range(1, 4):
            state[r] = state[r][r:] + state[r][:r]

    def mix():
        for c in range(4):
            col = [state[r][c] for r in range(4)]
            state[0][c] = _xtime(col[0]) ^ _xtime(col[1]) ^ col[1] ^ col[2] ^ col[3]
            state[1][c] = col[0] ^ _xtime(col[1]) ^ _xtime(col[2]) ^ col[2] ^ col[3]
            state[2][c] = col[0] ^ col[1] ^ _xtime(col[2]) ^ _xtime(col[3]) ^ col[3]
            state[3][c] = _xtime(col[0]) ^ col[0] ^ col[1] ^ col[2] ^ _xtime(col[3])

    add(0)
    for round_i in range(1, 14):
        sub()
        shift()
        mix()
        add(round_i)
    sub()
    shift()
    add(14)
    return bytes(state[r][c] for c in range(4) for r in range(4))


def _inc(counter: bytes) -> bytes:
    y = b"\x00" * 16
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16].ljust(16, b"\x00")
        y = bytes(a ^ b for a, b in zip(y, block))
        y = _gf_mul(y, h)
    return y


def _gf_mul(x: bytes, y: bytes) -> bytes:
    z = 0
    v = int.from_bytes(y, "big")
    for bit in range(128):
        if (int.from_bytes(x, "big") >> (127 - bit)) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ 0xE1000000000000000000000000000000
        else:
            v >>= 1
    return z.to_bytes(16, "big")


def _inc(counter: bytes) -> bytes:
    prefix, value = counter[:12], int.from_bytes(counter[12:], "big")
    return prefix + ((value + 1) & 0xFFFFFFFF).to_bytes(4, "big")


def encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    if len(key) != 32:
        raise ValueError("key")
    nonce = os.urandom(12)
    return nonce + _seal(key, nonce, plaintext, aad)


def decrypt(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    if len(blob) < 12 + 16:
        raise ValueError("blob")
    nonce, body = blob[:12], blob[12:]
    cipher, tag = body[:-16], body[-16:]
    words = _expand(key)
    h = _encrypt_block(b"\x00" * 16, words)
    expected = _tag(words, h, nonce, cipher, aad)
    if not _const_eq(expected, tag):
        raise ValueError("tag")
    return _xor_keystream(words, nonce, cipher)


def _seal(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
    words = _expand(key)
    h = _encrypt_block(b"\x00" * 16, words)
    cipher = _xor_keystream(words, nonce, plaintext)
    return cipher + _tag(words, h, nonce, cipher, aad)


def _xor_keystream(words, nonce: bytes, data: bytes) -> bytes:
    counter = nonce + b"\x00\x00\x00\x02"
    out = bytearray()
    for offset in range(0, len(data), 16):
        stream = _encrypt_block(counter, words)
        block = data[offset:offset + 16]
        out.extend(a ^ b for a, b in zip(block, stream))
        counter = _inc(counter)
    return bytes(out)


def _inc(counter: bytes) -> bytes:
    prefix, value = counter[:12], int.from_bytes(counter[12:], "big")
    return prefix + ((value + 1) & 0xFFFFFFFF).to_bytes(4, "big")


def _ghash(h: bytes, data: bytes) -> bytes:
    y = b"\x00" * 16
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16]
        y = bytes(a ^ b for a, b in zip(y, block))
        y = _gf_mul(y, h)
    return y


def _gf_mul(x: bytes, y: bytes) -> bytes:
    z = 0
    v = int.from_bytes(y, "big")
    xv = int.from_bytes(x, "big")
    for bit in range(128):
        if (xv >> (127 - bit)) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ 0xE1000000000000000000000000000000
        else:
            v >>= 1
    return z.to_bytes(16, "big")


def _pad16(data: bytes) -> bytes:
    extra = (-len(data)) % 16
    return data + b"\x00" * extra


def _tag(words, h: bytes, nonce: bytes, cipher: bytes, aad: bytes) -> bytes:
    j0 = nonce + b"\x00\x00\x00\x01"
    lengths = struct.pack(">QQ", len(aad) * 8, len(cipher) * 8)
    s = _ghash(h, _pad16(aad) + _pad16(cipher) + lengths)
    return bytes(a ^ b for a, b in zip(_encrypt_block(j0, words), s))


def _const_eq(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= x ^ y
    return diff == 0
