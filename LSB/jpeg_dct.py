#!/usr/bin/env python3
"""
jpeg_dct.py - JPEG mode for embed_imei.py / extract_imei.py: LSB embedding
              in the quantized DCT coefficients of a JPEG file.

Why a separate mode is needed
-----------------------------
A JPEG file does not store pixels. It stores, for every 8 x 8 pixel block,
64 quantized DCT coefficients (integers describing how much of each spatial
frequency the block contains). Writing bits into pixel LSBs and then saving
as JPEG fails, because the JPEG encoder quantizes the data and throws those
bits away before the file even exists.

So in JPEG mode we skip the pixels and work directly on the coefficients:
read them from the file (lossless, no decoding), change their LSBs, and
write them back (lossless, no re-encoding). The resulting file is a normal
JPEG that any viewer opens, and the hidden bits survive any transfer that
copies the file byte-for-byte: USB, WhatsApp "document" mode, e-mail
attachments, cloud drives, etc.

What it does NOT survive: anything that re-encodes the JPEG, e.g. WhatsApp
"photo" mode, social-media uploads, screenshots, or opening the file in an
editor and saving it again.

Layout of the payload
---------------------
* Only the luminance (Y) channel is used.
* In every 8 x 8 block we use K = 16 low-frequency AC coefficients
  (zig-zag positions 1..16). The DC coefficient (average brightness) and the
  high frequencies (usually zero) are left alone.
* A coefficient is only used if |c| >= 2. We replace the LSB of its
  MAGNITUDE and keep its sign:  |c| = 2 <-> 3, 4 <-> 5, ...
  Its magnitude therefore never drops below 2, so the extractor sees exactly
  the same set of usable coefficients. Coefficients 0 and +/-1 are skipped
  because changing them would be visible and would make a "used" coefficient
  look "unused" (or the other way round).
* Blocks are grouped in a T x T = 8 x 8 block "tile" (64 x 64 pixels), giving
  T * T * K = 1024 bit slots. Slot s carries frame bit (s mod frame_bits), so
  every bit is stored several times, at different frequencies, in every tile.
  The tile repeats over the whole image, like the pixel mode.
* Extraction counts votes for every bit across all copies and takes the
  majority, then checks the "#END" delimiter.
"""

import math
import os
import sys
import tempfile

import cv2
import numpy as np

try:
    import jpeglib
except ImportError:  # pragma: no cover - only hit when the dependency is missing
    jpeglib = None

# ---------------------------------------------------------------------------
# Format constants. Shared by embedding and extraction in this file, and the
# DELIMITER must match the one in embed_imei.py / extract_imei.py.
# ---------------------------------------------------------------------------
DELIMITER = "#END"

# Tile size in 8x8 blocks (T x T blocks = 64 x 64 pixels per tile).
TILE_BLOCKS = 8

# Zig-zag positions 1..16 of an 8x8 DCT block as (row, col). These low
# frequencies are the ones most often non-zero, so they give the most votes.
FREQ_POSITIONS = [
    (0, 1), (1, 0), (2, 0), (1, 1), (0, 2), (0, 3), (1, 2), (2, 1),
    (3, 0), (4, 0), (3, 1), (2, 2), (1, 3), (0, 4), (0, 5), (1, 4),
]
ROWS = np.array([p[0] for p in FREQ_POSITIONS])
COLS = np.array([p[1] for p in FREQ_POSITIONS])
K = len(FREQ_POSITIONS)

# Smallest coefficient magnitude we touch (see "Layout" above).
MIN_MAGNITUDE = 2

JPEG_EXTENSIONS = {".jpg", ".jpeg"}


def _require_jpeglib() -> None:
    if jpeglib is None:
        sys.exit("[!] JPEG mode needs the 'jpeglib' package: "
                 "pip install -r requirements.txt")


def is_jpeg_file(path: str) -> bool:
    """True if the file starts with the JPEG SOI marker (FF D8 FF)."""
    try:
        with open(path, "rb") as f:
            return f.read(3) == b"\xff\xd8\xff"
    except OSError:
        return False


def _text_to_bits(text: str) -> np.ndarray:
    return np.unpackbits(np.frombuffer(text.encode("ascii"), dtype=np.uint8))


def _bits_to_text(bits: np.ndarray) -> str:
    data = np.packbits(bits.astype(np.uint8))
    return "".join(chr(b) if b < 128 else "?" for b in data)


def _slot_to_bit(frame_bits: int, t: int = TILE_BLOCKS) -> np.ndarray:
    """
    (T, T, K) array: which frame bit each slot of a tile carries.

    Slots are numbered frequency-major, s = k*T*T + r*T + c, and slot s holds
    bit s mod frame_bits. Consecutive copies of the frame therefore land on
    different frequencies, so a bit never depends on one frequency only.
    """
    k, r, c = np.meshgrid(np.arange(K), np.arange(t), np.arange(t), indexing="ij")
    s = k * t * t + r * t + c                       # shape (K, T, T)
    return (s % frame_bits).transpose(1, 2, 0)      # shape (T, T, K)


def _bit_index_map(by: int, bx: int, frame_bits: int,
                   t: int = TILE_BLOCKS) -> np.ndarray:
    """(blocks_y, blocks_x, K) array of the frame bit each coefficient carries."""
    m = _slot_to_bit(frame_bits, t)
    return np.tile(m, (math.ceil(by / t), math.ceil(bx / t), 1))[:by, :bx]


def load_dct(image_path: str, quality: int = 95):
    """
    Return (jpeg_object, recompressed_flag).

    A JPEG input is opened at the coefficient level, so its quality is kept
    exactly. Any other input (PNG, BMP, ...) is compressed to JPEG once.
    Raises ValueError if the image can't be read.
    """
    _require_jpeglib()
    if is_jpeg_file(image_path):
        try:
            jpeg = jpeglib.read_dct(image_path)
            _ = jpeg.Y
        except Exception as e:  # corrupt / unsupported JPEG
            raise ValueError(f"Could not read JPEG coefficients: {e}") from e
        return jpeg, False

    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        if not cv2.imwrite(tmp, img, [cv2.IMWRITE_JPEG_QUALITY, quality]):
            raise ValueError("Could not compress the cover image to JPEG.")
        jpeg = jpeglib.read_dct(tmp)
        _ = jpeg.Y  # force the coefficients to load before the file is deleted
    finally:
        os.remove(tmp)
    return jpeg, True


def write_bits(jpeg, bits: np.ndarray, t: int = TILE_BLOCKS) -> dict:
    """
    Write a frame of bits into the Y-channel DCT coefficient LSBs of `jpeg`
    (in memory; call jpeg.write_dct() afterwards). The frame is repeated over
    every T x T block tile. Returns statistics for printing.
    Raises ValueError if the frame does not fit or the image is too flat.
    """
    n = bits.size
    if n > t * t * K:
        raise ValueError("Payload too long for the tile size.")

    Y = jpeg.Y                                     # (blocks_y, blocks_x, 8, 8)
    by, bx = Y.shape[:2]
    index = _bit_index_map(by, bx, n, t)

    # Which bit every used coefficient of the image must carry.
    pattern = bits[index].astype(np.int32)

    coeffs = Y[:, :, ROWS, COLS].astype(np.int32)  # (blocks_y, blocks_x, K)
    mag = np.abs(coeffs)
    usable = mag >= MIN_MAGNITUDE

    # Every bit needs at least one usable coefficient, otherwise it can't
    # be recovered.
    votes = np.bincount(index[usable], minlength=n)
    if votes.min() == 0:
        raise ValueError("Image has too little detail (too few non-zero DCT "
                         "coefficients) to hold the payload. Use a larger or "
                         "more textured image.")

    # ------------------------------------------------------------------
    # THE LSB REPLACEMENT, on the coefficient magnitude:
    #     |c| & ~1  clears the LSB, | bit writes the payload bit.
    #     e.g. c = -5 -> |c| = 5 = 101b -> bit 0 -> 100b = 4 -> c = -4
    # The sign is put back afterwards. |c| stays >= 2, so the set of usable
    # coefficients never changes. That is also why a frame can be
    # overwritten again and again (one chain hop after another).
    # ------------------------------------------------------------------
    new_mag = (mag & ~1) | pattern
    new = np.where(usable, np.sign(coeffs) * new_mag, coeffs)
    changed = int(np.count_nonzero(new != coeffs))
    Y[:, :, ROWS, COLS] = new.astype(Y.dtype)
    jpeg.Y = Y
    return {"blocks": (bx, by), "usable": int(usable.sum()),
            "votes_min": int(votes.min()), "votes_median": int(np.median(votes)),
            "changed": changed}


def vote_alignments(Y: np.ndarray, n: int, t: int = TILE_BLOCKS):
    """
    Majority-vote a frame of n bits out of the Y-channel coefficients, once
    for every possible grid offset (dy, dx). Yields
    (dy, dx, bits, confidence, missing, votes_per_bit).

    The offset is (0, 0) for an untouched file. Trying all T*T offsets also
    handles a lossless crop on the 8-pixel grid (e.g. jpegtran -crop).
    """
    by, bx = Y.shape[:2]
    coeffs = Y[:, :, ROWS, COLS].astype(np.int32)
    mag = np.abs(coeffs)
    usable = (mag >= MIN_MAGNITUDE).astype(np.int64)
    lsb = (mag & 1) * usable

    # Sum votes per tile slot, exactly like the pixel mode sums per tile cell.
    # Padding to a multiple of T lets partial tiles at the edges vote too.
    ph, pw = math.ceil(by / t) * t, math.ceil(bx / t) * t
    ones_p = np.zeros((ph, pw, K), np.int64)
    total_p = np.zeros((ph, pw, K), np.int64)
    ones_p[:by, :bx] = lsb
    total_p[:by, :bx] = usable
    shape5 = (ph // t, t, pw // t, t, K)
    ones = ones_p.reshape(shape5).sum(axis=(0, 2))    # (T, T, K)
    total = total_p.reshape(shape5).sum(axis=(0, 2))

    slot_bit = _slot_to_bit(n, t).reshape(-1)

    for dy in range(t):
        for dx in range(t):
            o = np.roll(ones, (-dy, -dx), axis=(0, 1)).reshape(-1)
            v = np.roll(total, (-dy, -dx), axis=(0, 1)).reshape(-1)
            b1 = np.bincount(slot_bit, weights=o, minlength=n)
            bn = np.bincount(slot_bit, weights=v, minlength=n)

            bits = (2 * b1 > bn).astype(np.uint8)
            with np.errstate(invalid="ignore", divide="ignore"):
                margin = np.where(bn > 0, np.abs(2.0 * b1 / bn - 1.0), 0.0)
            yield (dy, dx, bits, float(margin.mean()),
                   int(np.count_nonzero(bn == 0)), bn)


def embed(image_path: str, payload: str, output_path: str, quality: int = 95) -> None:
    _require_jpeglib()
    frame = payload + DELIMITER
    bits = _text_to_bits(frame)
    if bits.size > TILE_BLOCKS * TILE_BLOCKS * K:
        sys.exit(f"[!] Payload too long for JPEG mode (max "
                 f"{TILE_BLOCKS * TILE_BLOCKS * K // 8 - len(DELIMITER)} chars).")

    try:
        jpeg, recompressed = load_dct(image_path, quality)
        stats = write_bits(jpeg, bits)
    except ValueError as e:
        sys.exit(f"[!] {e}")
    jpeg.write_dct(output_path)
    bx, by = stats["blocks"]

    print(f"[+] Mode              : JPEG (DCT coefficient LSB)")
    print(f"[+] Embedded payload  : {frame}")
    print(f"[+] Image size        : {bx * 8} x {by * 8} px ({bx} x {by} blocks)")
    if recompressed:
        print(f"[+] Cover             : not a JPEG, compressed once at quality {quality}")
    else:
        print(f"[+] Cover             : JPEG, coefficients edited in place "
              f"(no re-compression)")
    print(f"[+] Usable coeffs     : {stats['usable']} "
          f"(|c| >= {MIN_MAGNITUDE} in {K} low frequencies)")
    print(f"[+] Copies per bit    : min {stats['votes_min']}, "
          f"median {stats['votes_median']}")
    print(f"[+] Coeffs modified   : {stats['changed']} (each by exactly 1)")
    print(f"[+] Saved             : {output_path}")
    print("[i] Send it as a FILE (USB, WhatsApp document, e-mail attachment).")
    print("[i] Re-compressing it (WhatsApp photo mode, editing) destroys the mark.")


def extract(image_path: str, payload_length: int) -> int:
    _require_jpeglib()
    try:
        Y = load_dct(image_path)[0].Y
    except ValueError as e:
        sys.exit(f"[!] {e}")

    by, bx = Y.shape[:2]
    n = (payload_length + len(DELIMITER)) * 8

    candidates = []
    for dy, dx, bits, confidence, missing, bn in vote_alignments(Y, n):
        text = _bits_to_text(bits)
        payload, tail = text[:payload_length], text[payload_length:]
        valid = tail == DELIMITER and payload.isprintable() and missing == 0
        candidates.append((valid, confidence, payload, dy, dx, missing, bn))

    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    valid, confidence, payload, dy, dx, missing, bn = candidates[0]

    print(f"[+] Mode              : JPEG (DCT coefficient LSB)")
    print(f"[+] Image size        : {bx * 8} x {by * 8} px ({bx} x {by} blocks)")
    print(f"[+] Grid alignment    : dy={dy}, dx={dx} blocks")
    print(f"[+] Copies per bit    : min {int(bn.min())}, median {int(np.median(bn))}")
    print(f"[+] Vote confidence   : {confidence * 100:.1f} %")

    if not valid:
        print(f"[-] No alignment produced the delimiter {DELIMITER!r}.")
        print(f"[-] Best guess (unreliable): {payload!r}")
        print("[-] The file was probably re-compressed (sent as a photo, edited, "
              "screenshotted) or never watermarked.")
        return 1

    print(f"[+] Extracted payload : {payload}")
    if payload.isdigit() and len(payload) == 15:
        print("[+] Format            : valid 15-digit IMEI format")
    return 0
