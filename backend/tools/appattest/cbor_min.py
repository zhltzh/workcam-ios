"""Minimal CBOR decoder (RFC 8949 subset) - dependency-free.

Supports the major types App Attest objects actually use:
  0 uint, 1 negint, 2 bytes, 3 text, 4 array, 5 map, 6 tags (unwrap),
  7 simple/float (false/true/null + float16/32/64).
Indefinite-length items are NOT supported - Apple's App Attest
attestation/assertion objects are always definite-length; rejecting
indefinite forms doubles as an input-hardening measure.

Returns plain Python objects. Byte-accurate round-trip is NOT a goal
here (decode-only verifier path); maps preserve insertion order so
field-position assumptions in Apple's format keep working.
"""

from __future__ import annotations

from typing import Any


class CBORDecodeError(ValueError):
    pass


def _read_head(data: bytes, pos: int) -> tuple[int, int, int]:
    """Returns (major, arg_value, new_pos)."""
    if pos >= len(data):
        raise CBORDecodeError("truncated: no initial byte")
    ib = data[pos]
    major = ib >> 5
    info = ib & 0x1F
    pos += 1
    if info < 24:
        return major, info, pos
    if info == 24:
        if pos >= len(data):
            raise CBORDecodeError("truncated uint8")
        return major, data[pos], pos + 1
    if info == 25:
        if pos + 2 > len(data):
            raise CBORDecodeError("truncated uint16")
        return major, int.from_bytes(data[pos:pos + 2], "big"), pos + 2
    if info == 26:
        if pos + 4 > len(data):
            raise CBORDecodeError("truncated uint32")
        return major, int.from_bytes(data[pos:pos + 4], "big"), pos + 4
    if info == 27:
        if pos + 8 > len(data):
            raise CBORDecodeError("truncated uint64")
        return major, int.from_bytes(data[pos:pos + 8], "big"), pos + 8
    if info == 31:
        raise CBORDecodeError(
            "indefinite-length items are not supported (and must not "
            "appear in Apple App Attest objects)")
    raise CBORDecodeError(f"reserved additional info {info}")


def _decode_one(data: bytes, pos: int) -> tuple[Any, int]:
    major, arg, pos = _read_head(data, pos)
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major == 2:
        if pos + arg > len(data):
            raise CBORDecodeError("truncated byte string")
        return data[pos:pos + arg], pos + arg
    if major == 3:
        if pos + arg > len(data):
            raise CBORDecodeError("truncated text string")
        try:
            return data[pos:pos + arg].decode("utf-8"), pos + arg
        except UnicodeDecodeError as e:
            raise CBORDecodeError(f"invalid utf-8 text: {e}") from e
    if major == 4:
        out = []
        for _ in range(arg):
            item, pos = _decode_one(data, pos)
            out.append(item)
        return out, pos
    if major == 5:
        out: dict = {}
        for _ in range(arg):
            key, pos = _decode_one(data, pos)
            if not isinstance(key, (str, int)):
                raise CBORDecodeError("map keys must be text/int")
            if key in out:
                raise CBORDecodeError(f"duplicate map key: {key!r}")
            value, pos = _decode_one(data, pos)
            out[key] = value
        return out, pos
    if major == 6:
        # tag: decode and return the INNER value with the tag attached
        # so callers can distinguish tagged data when needed
        inner, pos = _decode_one(data, pos)
        return ("tag", arg, inner), pos
    if major == 7:
        if arg == 20:
            return False, pos
        if arg == 21:
            return True, pos
        if arg == 22 or arg == 23:
            return None, pos
        if arg == 25:      # float16
            if pos + 2 > len(data):
                raise CBORDecodeError("truncated float16")
            import struct
            raw = data[pos:pos + 2]
            pos += 2
            h = int.from_bytes(raw, "big")
            sign = -1.0 if h >> 15 else 1.0
            exp = (h >> 10) & 0x1F
            frac = h & 0x3FF
            if exp == 0:
                val = sign * frac * 2.0 ** -24
            elif exp == 31:
                val = sign * float("inf") if frac == 0 else float("nan")
            else:
                val = sign * (1 + frac / 1024.0) * 2.0 ** (exp - 15)
            return val, pos
        if arg == 26:      # float32
            import struct
            if pos + 4 > len(data):
                raise CBORDecodeError("truncated float32")
            val = struct.unpack(">f", data[pos:pos + 4])[0]
            return val, pos + 4
        if arg == 27:      # float64
            import struct
            if pos + 8 > len(data):
                raise CBORDecodeError("truncated float64")
            val = struct.unpack(">d", data[pos:pos + 8])[0]
            return val, pos + 8
        raise CBORDecodeError(f"unsupported simple value {arg}")
    raise CBORDecodeError(f"unknown major type {major}")


def loads(data: bytes) -> Any:
    """Decode ONE CBOR item from the head of the buffer; trailing
    garbage is rejected (strict - attestation objects must be exact)."""
    value, pos = _decode_one(data, 0)
    if pos != len(data):
        raise CBORDecodeError(
            f"trailing bytes after item ({len(data) - pos})")
    return value


def dumps(value: Any) -> bytes:
    """Minimal ENCODER for tests + fixture building (canonical-ish:
    shortest-form heads, maps in given order). Supports the types the
    verifier ever needs to construct."""
    out = bytearray()

    def head(major: int, arg: int) -> None:
        if arg < 24:
            out.append((major << 5) | arg)
        elif arg < 0x100:
            out.append((major << 5) | 24)
            out.append(arg)
        elif arg < 0x10000:
            out.append((major << 5) | 25)
            out.extend(arg.to_bytes(2, "big"))
        elif arg < 0x100000000:
            out.append((major << 5) | 26)
            out.extend(arg.to_bytes(4, "big"))
        else:
            out.append((major << 5) | 27)
            out.extend(arg.to_bytes(8, "big"))

    def enc(v: Any) -> None:
        if v is False:
            out.append(0xF4)
        elif v is True:
            out.append(0xF5)
        elif v is None:
            out.append(0xF6)
        elif isinstance(v, int):
            if v >= 0:
                head(0, v)
            else:
                head(1, -1 - v)
        elif isinstance(v, bytes):
            head(2, len(v))
            out.extend(v)
        elif isinstance(v, str):
            b = v.encode("utf-8")
            head(3, len(b))
            out.extend(b)
        elif isinstance(v, float):
            import math
            if not math.isfinite(v):
                raise TypeError("cannot encode non-finite floats")
            # float64 (0xfb) for everything - tests only need
            # round-trip fidelity, not minimal size
            head(7, 27)
            import struct as _s
            out.extend(_s.pack(">d", v))
        elif isinstance(v, list):
            head(4, len(v))
            for item in v:
                enc(item)
        elif isinstance(v, dict):
            head(5, len(v))
            for k, item in v.items():
                enc(k)
                enc(item)
        else:
            raise TypeError(f"cannot encode {type(v).__name__}")

    enc(value)
    return bytes(out)
