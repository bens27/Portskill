"""Minimal NIST P-256 ECDSA (stdlib-only). Used for WebAuthn ES256 verify."""
from __future__ import annotations

import hashlib
import os

# NIST P-256 / secp256r1
P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = -3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
G = (GX, GY)


def _inv(x: int, m: int) -> int:
    return pow(x % m, -1, m)


def _on_curve(pt) -> bool:
    if pt is None:
        return True
    x, y = pt
    return (y * y - (x * x * x + A * x + B)) % P == 0


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        lam = (3 * x1 * x1 + A) * _inv(2 * y1, P) % P
    else:
        lam = (y2 - y1) * _inv(x2 - x1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    y3 = (lam * (x1 - x3) - y1) % P
    return x3, y3


def _mul(k: int, pt):
    if k % N == 0 or pt is None:
        return None
    if k < 0:
        k = k % N
        pt = (pt[0], (-pt[1]) % P)
    acc = None
    cur = pt
    while k:
        if k & 1:
            acc = _add(acc, cur)
        cur = _add(cur, cur)
        k >>= 1
    return acc


def generate_keypair() -> tuple[int, tuple[int, int]]:
    while True:
        d = int.from_bytes(os.urandom(32), "big") % N
        if 1 <= d < N:
            pub = _mul(d, G)
            if pub is not None and _on_curve(pub):
                return d, pub


def _digest_int(message: bytes) -> int:
    h = hashlib.sha256(message).digest()
    return int.from_bytes(h, "big")


def sign(private_key: int, message: bytes, *, k: int | None = None) -> tuple[int, int]:
    e = _digest_int(message)
    while True:
        if k is None:
            kk = int.from_bytes(os.urandom(32), "big") % N
        else:
            kk = k % N
        if kk == 0:
            if k is not None:
                raise ValueError("k must be in 1..n-1")
            continue
        r_pt = _mul(kk, G)
        if r_pt is None:
            if k is not None:
                raise ValueError("k produced infinity")
            continue
        r = r_pt[0] % N
        if r == 0:
            if k is not None:
                raise ValueError("k produced r=0")
            continue
        s = (_inv(kk, N) * (e + r * (private_key % N))) % N
        if s == 0:
            if k is not None:
                raise ValueError("k produced s=0")
            continue
        return r, s


def verify(public_key: tuple[int, int], message: bytes, r: int, s: int) -> bool:
    if not _on_curve(public_key) or public_key is None:
        return False
    if not (1 <= r < N and 1 <= s < N):
        return False
    e = _digest_int(message)
    w = _inv(s, N)
    u1 = (e * w) % N
    u2 = (r * w) % N
    pt = _add(_mul(u1, G), _mul(u2, public_key))
    if pt is None:
        return False
    return pt[0] % N == r


def encode_raw_public(public_key: tuple[int, int]) -> tuple[bytes, bytes]:
    x, y = public_key
    return x.to_bytes(32, "big"), y.to_bytes(32, "big")


def decode_raw_public(x: bytes, y: bytes) -> tuple[int, int]:
    if len(x) != 32 or len(y) != 32:
        raise ValueError("P-256 public key coords must be 32 bytes")
    pt = (int.from_bytes(x, "big"), int.from_bytes(y, "big"))
    if not _on_curve(pt) or pt[0] >= P or pt[1] >= P:
        raise ValueError("point is not on P-256")
    return pt
