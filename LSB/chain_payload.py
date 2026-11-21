"""
chain_payload.py - Binary format of the forwarding chain stored in an image.

The chain records every device an image passed through (A -> B -> C ...).
It is packed into a FIXED-size frame, so the extractor always knows how many
bits to look for and needs no --payload-length option. Photos (pixel_chain.py)
and videos (mp4_chain.py) carry exactly the same frame.

Frame layout (2016 bytes = 16128 bits, all integers big-endian)
---------------------------------------------------------------
    offset  size  field
    0       2     magic  b"IC"  ("IMEI Chain")
    2       1     version (2; version 1 was the old 8-hop format)
    3       1     hop count N (0..MAX_HOPS)
    4       8     image ID: set once by the first device, never changed.
                  The dashboard groups copies of the same original photo
                  by this ID to build the forwarding tree.
    12      2000  MAX_HOPS = 100 hop slots of 20 bytes; slots >= N are zero:
                    8  device ID (e.g. the Android ANDROID_ID, 64 bits)
                    7  IMEI as an integer (15 digits < 2^50), 0 = unknown
                    4  Unix timestamp in seconds
                    1  role: 1 = SEND (added when forwarding),
                             2 = CLAIM (added by the final receiver)
    2012    4     CRC-32 of bytes 0..2011

The magic and the CRC together tell the extractor that it found a real chain
at the right grid alignment, not random LSB noise.
"""

import os
import struct
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

MAGIC = b"IC"
VERSION = 2
MAX_HOPS = 100

DEVICE_ID_BYTES = 8
IMEI_BYTES = 7
IMAGE_ID_BYTES = 8

ROLE_SEND = 1
ROLE_CLAIM = 2
ROLE_NAMES = {ROLE_SEND: "send", ROLE_CLAIM: "claim"}

_HOP_STRUCT = struct.Struct(">8s7sIB")          # device, imei, timestamp, role
_HEADER_STRUCT = struct.Struct(">2sBB8s")       # magic, version, count, image id
HOP_BYTES = _HOP_STRUCT.size                    # 20
CRC_BYTES = 4
FRAME_BYTES = _HEADER_STRUCT.size + MAX_HOPS * HOP_BYTES + CRC_BYTES   # 2016
FRAME_BITS = FRAME_BYTES * 8                                           # 16128


class ChainError(ValueError):
    """Raised for an invalid chain or invalid hop data."""


@dataclass
class Hop:
    device_id: bytes            # DEVICE_ID_BYTES bytes
    imei: Optional[str]         # 15-digit string, or None if unknown
    timestamp: int              # Unix seconds
    role: int = ROLE_SEND

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id.hex(),
            "imei": self.imei,
            "timestamp": self.timestamp,
            "time": datetime.fromtimestamp(self.timestamp, timezone.utc)
                            .isoformat().replace("+00:00", "Z"),
            "role": ROLE_NAMES.get(self.role, str(self.role)),
        }


@dataclass
class Chain:
    image_id: bytes                              # IMAGE_ID_BYTES bytes
    hops: List[Hop] = field(default_factory=list)

    @classmethod
    def new(cls) -> "Chain":
        return cls(image_id=os.urandom(IMAGE_ID_BYTES))

    def to_dict(self) -> dict:
        return {
            "image_id": self.image_id.hex(),
            "hop_count": len(self.hops),
            "hops": [dict(index=i + 1, **h.to_dict())
                     for i, h in enumerate(self.hops)],
        }


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

def parse_device_id(text: str) -> bytes:
    """
    16 hex characters (an Android ANDROID_ID) -> those 8 bytes.
    Anything else (e.g. "phone-A" while testing on a PC) is hashed to 8 bytes,
    so the same text always gives the same device ID.
    """
    t = text.strip().lower()
    if len(t) == DEVICE_ID_BYTES * 2:
        try:
            return bytes.fromhex(t)
        except ValueError:
            pass
    if not t:
        raise ChainError("Device ID must not be empty.")
    import hashlib
    return hashlib.sha256(t.encode("utf-8")).digest()[:DEVICE_ID_BYTES]


def luhn_ok(digits: str) -> bool:
    """IMEIs end in a Luhn check digit; a failure usually means a typo."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def validate_imei(imei: Optional[str]) -> Optional[str]:
    if imei is None or imei == "":
        return None
    if not (imei.isdigit() and len(imei) == 15):
        raise ChainError(f"IMEI must be exactly 15 digits, got {imei!r}.")
    return imei


# ---------------------------------------------------------------------------
# Pack / unpack
# ---------------------------------------------------------------------------

def pack(chain: Chain) -> bytes:
    if len(chain.hops) > MAX_HOPS:
        raise ChainError(f"Chain is full ({MAX_HOPS} hops max).")
    if len(chain.image_id) != IMAGE_ID_BYTES:
        raise ChainError("Image ID must be 8 bytes.")

    out = bytearray(_HEADER_STRUCT.pack(MAGIC, VERSION, len(chain.hops),
                                        chain.image_id))
    for h in chain.hops:
        if len(h.device_id) != DEVICE_ID_BYTES:
            raise ChainError("Device ID must be 8 bytes.")
        imei_int = int(validate_imei(h.imei) or 0)
        out += _HOP_STRUCT.pack(h.device_id, imei_int.to_bytes(IMEI_BYTES, "big"),
                                h.timestamp & 0xFFFFFFFF, h.role)
    out += bytes(HOP_BYTES * (MAX_HOPS - len(chain.hops)))
    out += struct.pack(">I", zlib.crc32(out))
    assert len(out) == FRAME_BYTES
    return bytes(out)


def unpack(data: bytes) -> Chain:
    """Parse a frame. Raises ChainError if it is not a valid chain."""
    if len(data) != FRAME_BYTES:
        raise ChainError(f"Frame must be {FRAME_BYTES} bytes.")
    body, crc = data[:-CRC_BYTES], struct.unpack(">I", data[-CRC_BYTES:])[0]
    magic, version, count, image_id = _HEADER_STRUCT.unpack_from(body, 0)
    if magic != MAGIC:
        raise ChainError("No chain marker found.")
    if zlib.crc32(body) != crc:
        raise ChainError("Chain checksum mismatch (data damaged).")
    if version != VERSION:
        raise ChainError(f"Unsupported chain version {version}.")
    if count > MAX_HOPS:
        raise ChainError("Invalid hop count.")

    hops = []
    for i in range(count):
        dev, imei_b, ts, role = _HOP_STRUCT.unpack_from(
            body, _HEADER_STRUCT.size + i * HOP_BYTES)
        imei_int = int.from_bytes(imei_b, "big")
        hops.append(Hop(dev, f"{imei_int:015d}" if imei_int else None, ts, role))
    return Chain(image_id, hops)


def make_hop(device_id: bytes, imei: Optional[str], role: int = ROLE_SEND,
             timestamp: Optional[int] = None) -> Hop:
    if role not in ROLE_NAMES:
        raise ChainError(f"Unknown role {role}.")
    return Hop(device_id, validate_imei(imei),
               int(time.time()) if timestamp is None else int(timestamp), role)
