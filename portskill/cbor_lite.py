"""Minimal CBOR encode/decode for WebAuthn attestation + COSE keys."""
from __future__ import annotations


class CBORError(ValueError):
    pass


def _read_len(buf: bytes, i: int, extra: int) -> tuple[int, int]:
    if extra < 24:
        return extra, i
    if extra == 24:
        if i >= len(buf):
            raise CBORError("truncated")
        return buf[i], i + 1
    if extra == 25:
        if i + 2 > len(buf):
            raise CBORError("truncated")
        return int.from_bytes(buf[i : i + 2], "big"), i + 2
    if extra == 26:
        if i + 4 > len(buf):
            raise CBORError("truncated")
        return int.from_bytes(buf[i : i + 4], "big"), i + 4
    if extra == 27:
        if i + 8 > len(buf):
            raise CBORError("truncated")
        return int.from_bytes(buf[i : i + 8], "big"), i + 8
    raise CBORError(f"unsupported additional info {extra}")


def _decode_at(buf: bytes, i: int):
    if i >= len(buf):
        raise CBORError("truncated")
    b = buf[i]
    i += 1
    major = b >> 5
    extra = b & 0x1F
    if major == 0:
        val, i = _read_len(buf, i, extra)
        return val, i
    if major == 1:
        val, i = _read_len(buf, i, extra)
        return -1 - val, i
    if major == 2:
        n, i = _read_len(buf, i, extra)
        if i + n > len(buf):
            raise CBORError("truncated bytes")
        return buf[i : i + n], i + n
    if major == 3:
        n, i = _read_len(buf, i, extra)
        if i + n > len(buf):
            raise CBORError("truncated text")
        return buf[i : i + n].decode("utf-8"), i + n
    if major == 4:
        n, i = _read_len(buf, i, extra)
        out = []
        for _ in range(n):
            item, i = _decode_at(buf, i)
            out.append(item)
        return out, i
    if major == 5:
        n, i = _read_len(buf, i, extra)
        out = {}
        for _ in range(n):
            key, i = _decode_at(buf, i)
            val, i = _decode_at(buf, i)
            out[key] = val
        return out, i
    if major == 7:
        if extra == 20:
            return False, i
        if extra == 21:
            return True, i
        if extra == 22:
            return None, i
        raise CBORError(f"unsupported simple {extra}")
    raise CBORError(f"unsupported major type {major}")


def loads(buf: bytes):
    if not isinstance(buf, (bytes, bytearray)):
        raise CBORError("expected bytes")
    val, i = _decode_at(bytes(buf), 0)
    return val


def _encode_uint(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([(major << 5) | n])
    if n < 256:
        return bytes([(major << 5) | 24, n])
    if n < 65536:
        return bytes([(major << 5) | 25]) + n.to_bytes(2, "big")
    if n < 2**32:
        return bytes([(major << 5) | 26]) + n.to_bytes(4, "big")
    return bytes([(major << 5) | 27]) + n.to_bytes(8, "big")


def dumps(value) -> bytes:
    if value is False:
        return b"\xf4"
    if value is True:
        return b"\xf5"
    if value is None:
        return b"\xf6"
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if isinstance(value, int):
        if value >= 0:
            return _encode_uint(0, value)
        return _encode_uint(1, -1 - value)
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        return _encode_uint(2, len(raw)) + raw
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return _encode_uint(3, len(raw)) + raw
    if isinstance(value, (list, tuple)):
        out = bytearray(_encode_uint(4, len(value)))
        for item in value:
            out.extend(dumps(item))
        return bytes(out)
    if isinstance(value, dict):
        out = bytearray(_encode_uint(5, len(value)))
        for key, val in value.items():
            out.extend(dumps(key))
            out.extend(dumps(val))
        return bytes(out)
    raise CBORError(f"cannot encode {type(value).__name__}")
