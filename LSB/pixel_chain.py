"""
pixel_chain.py - Store the forwarding chain in the RGB pixel LSBs of a photo.

The chain frame (chain_payload.py, 2016 bytes = 16128 bits) is written into
the least significant bits of the R, G and B values of the pixels, as a small
square tile that is repeated over the whole photo. The result is saved as PNG
(lossless), so every hidden bit arrives unchanged over USB or Bluetooth.

Two modes, chosen when stamping:

    mode           bits per channel   change per value   tile for the frame
    3 bits/pixel   1 (bit 0)          at most +/-1       74 x 74 px
    6 bits/pixel   2 (bits 1 and 0)   at most +/-3       52 x 52 px

Layout
------
Frame bit i is stored in tile pixel p = i // bpp (row p // side, column
p % side), in carrier slot j = i % bpp of that pixel:

    3 bits/pixel:  j = 0, 1, 2            -> LSB of R, G, B
    6 bits/pixel:  j = 0, 1 | 2, 3 | 4, 5 -> (bit 1, bit 0) of R | G | B

The tile side is the smallest square that holds 16128 bits; the few spare
slots at the end of the tile are 0. Pixel (y, x) of the photo carries tile
pixel (y % side, x % side), so every side x side window of the photo holds a
complete copy of the chain: a 4080 x 3064 phone photo holds ~2,255 copies in
3-bit mode and ~4,524 in 6-bit mode.

Reading
-------
1. Every pixel votes for its tile cell: count the 1s per (cell, slot).
2. A cropped photo starts somewhere inside a tile, so the reader tries every
   grid offset (dy, dx). The 24 bits of magic + version are checked first
   (cheap, all offsets at once); only matching offsets are fully decoded.
3. Majority vote per bit, then the CRC-32 confirms the frame.
Both modes are tried, so the reader needs no setting.

The frame is re-written in full at every hop; nothing is ever re-compressed.

Survives: byte-for-byte transfer (USB, Bluetooth, e-mail attachment), crops
larger than one tile, a few % of damaged bits (majority vote).
Does NOT survive: resizing, rotation, filters, screenshots or saving as JPEG
(all of these recompute every pixel, so the LSBs are lost).
"""

import hashlib
import math
import os
import struct
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

import chain_payload as cp

# bits per pixel -> bits per colour channel
MODES = {3: 1, 6: 2}
DEFAULT_BPP = 3

OUTPUT_EXTENSIONS = {".png"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_COMPRESSION = 6

# Magic "IC" + version byte: checked first at every grid offset.
_HEAD = np.unpackbits(np.frombuffer(cp.MAGIC + bytes([cp.VERSION]), dtype=np.uint8))


def tile_side(bpp: int) -> int:
    """Side of the square tile holding one frame: 74 (3 bits/pixel) or 52 (6)."""
    return math.ceil(math.sqrt(math.ceil(cp.FRAME_BITS / bpp)))


def is_png_file(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(8) == PNG_SIGNATURE
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Image I/O (RGB order everywhere, like Android)
# ---------------------------------------------------------------------------

def load_rgb(path: str) -> np.ndarray:
    """Any image OpenCV can read -> (H, W, 3) uint8 RGB. Alpha is dropped."""
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if img is None:
        raise ValueError(f"Could not read image: {path}")
    return np.ascontiguousarray(img[:, :, ::-1])


def save_png(path: str, rgb: np.ndarray) -> None:
    ok, buf = cv2.imencode(".png", np.ascontiguousarray(rgb[:, :, ::-1]),
                           [cv2.IMWRITE_PNG_COMPRESSION, PNG_COMPRESSION])
    if not ok:
        raise ValueError(f"Could not encode PNG: {path}")
    buf.tofile(path)


def content_id(rgb: np.ndarray) -> bytes:
    """
    8-byte Image ID of a new chain: SHA-256 of the width, height and every
    pixel with its two lowest bits cleared. Stamping (in either mode) only
    touches those two bits, so the same original photo always gets the same
    ID and all of its copies land in one forwarding tree.
    """
    h, w = rgb.shape[:2]
    d = hashlib.sha256(struct.pack(">II", w, h))
    d.update(np.ascontiguousarray(rgb & 0xFC).tobytes())
    return d.digest()[:cp.IMAGE_ID_BYTES]


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def _layout(bpp: int):
    """(row, col, slot) inside the tile of every frame bit."""
    side = tile_side(bpp)
    i = np.arange(cp.FRAME_BITS)
    p = i // bpp
    return p // side, p % side, i % bpp


def _carriers(rgb: np.ndarray, bpc: int) -> np.ndarray:
    """(H, W, bpp) array of the carrier bits of every pixel, in slot order."""
    if bpc == 1:
        return rgb & 1
    out = np.empty(rgb.shape[:2] + (6,), np.uint8)
    out[:, :, 0::2] = (rgb >> 1) & 1        # bit 1 of R, G, B
    out[:, :, 1::2] = rgb & 1               # bit 0 of R, G, B
    return out


def _count(n: int, side: int) -> np.ndarray:
    """How many of the indices 0..n-1 fall on each residue r = index % side."""
    r = np.arange(side)
    return np.where(r < n, (n - 1 - r) // side + 1, 0)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_frame(rgb: np.ndarray, frame: bytes, bpp: int = DEFAULT_BPP) -> dict:
    """
    Write the frame into the pixels of `rgb` (in place), repeated over the
    whole image. Returns statistics. Raises ValueError if the photo is
    smaller than one tile.
    """
    if bpp not in MODES:
        raise ValueError(f"Bits per pixel must be one of {sorted(MODES)}.")
    bpc = MODES[bpp]
    side = tile_side(bpp)
    h, w = rgb.shape[:2]
    if h < side or w < side:
        raise ValueError(f"Photo is too small ({w}x{h}): it must be at least "
                         f"{side}x{side} pixels to hold one copy of the chain.")

    bits = np.unpackbits(np.frombuffer(frame, dtype=np.uint8))
    slots = np.zeros(side * side * bpp, np.uint8)
    slots[:bits.size] = bits
    slots = slots.reshape(side, side, bpp)
    # Value of the low bit(s) of each channel of each tile pixel.
    vals = slots if bpc == 1 else (slots[:, :, 0::2] << 1) | slots[:, :, 1::2]

    pattern = np.tile(vals, (math.ceil(h / side), math.ceil(w / side), 1))[:h, :w]
    keep = 0xFF ^ ((1 << bpc) - 1)                     # 0xFE or 0xFC
    before = rgb.copy() if rgb.size <= 50_000_000 else None
    np.bitwise_and(rgb, keep, out=rgb)
    np.bitwise_or(rgb, pattern, out=rgb)

    stats = {"bits_per_pixel": bpp, "tile": side, "size": (w, h),
             "copies": (h // side) * (w // side)}
    if before is not None:
        stats["changed"] = int(np.count_nonzero(rgb != before))
        stats["values"] = int(rgb.size)
    return stats


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@dataclass
class PixelResult:
    chain: cp.Chain
    bits_per_pixel: int
    dy: int                 # grid offset of the tile (0, 0 for an uncropped photo)
    dx: int
    confidence: float       # mean vote margin, 1.0 = every copy agrees
    votes_min: int          # fewest copies any bit was read from


def _decode_mode(rgb: np.ndarray, bpp: int) -> Optional[PixelResult]:
    bpc = MODES[bpp]
    side = tile_side(bpp)
    h, w = rgb.shape[:2]
    if h < side or w < side:
        return None

    # Votes per tile cell: sum the carrier bits of all pixels on that cell.
    ph, pw = math.ceil(h / side) * side, math.ceil(w / side) * side
    car = np.zeros((ph, pw, bpp), np.uint8)
    car[:h, :w] = _carriers(rgb, bpc)
    ones = car.reshape(ph // side, side, pw // side, side, bpp).sum(axis=(0, 2), dtype=np.int64)
    del car
    total = np.outer(_count(h, side), _count(w, side))            # (side, side)

    r, c, j = _layout(bpp)
    offsets = np.arange(side * side)
    dys, dxs = (offsets // side)[:, None], (offsets % side)[:, None]

    # 1. Quick test of the first 24 bits (magic + version) at every offset.
    k = _HEAD.size
    rr, cc = (r[:k] - dys) % side, (c[:k] - dxs) % side
    o, t = ones[rr, cc, j[:k]], total[rr, cc]
    head = (2 * o > t).astype(np.uint8)
    hits = np.flatnonzero((head == _HEAD).all(axis=1))

    # 2. Full majority vote + CRC at the offsets that passed.
    best = None
    for off in hits:
        dy, dx = int(off // side), int(off % side)
        rr, cc = (r - dy) % side, (c - dx) % side
        o, t = ones[rr, cc, j], total[rr, cc]
        bits = (2 * o > t).astype(np.uint8)
        try:
            chain = cp.unpack(np.packbits(bits).tobytes())
        except cp.ChainError:
            continue
        conf = float(np.abs(2.0 * o / t - 1.0).mean())
        res = PixelResult(chain, bpp, dy, dx, conf, int(t.min()))
        if best is None or res.confidence > best.confidence:
            best = res
    return best


def decode(rgb: np.ndarray) -> Optional[PixelResult]:
    """Find the chain in an RGB image (either mode, any crop offset), or None."""
    results = [r for r in (_decode_mode(rgb, bpp) for bpp in MODES) if r is not None]
    return max(results, key=lambda r: r.confidence) if results else None
