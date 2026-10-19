#!/usr/bin/env python3
"""
extract_imei.py - Extract an IMEI hidden by embed_imei.py, using Majority
                  Voting across all redundant copies.

Usage
-----
    python extract_imei.py --image watermarked.png --payload-length 15
    python extract_imei.py --image watermarked.jpg --payload-length 15

A JPEG file (detected from its first bytes, not its name) is read in JPEG
mode: the bits come from its DCT coefficients (see jpeg_dct.py). Any other
image is read in pixel-LSB mode, described below. An MP4 video is read in
video mode (see video_lsb.py):

    python extract_imei.py --image watermarked.mp4 --payload-length 15

How extraction works
--------------------
embed_imei.py stores the frame (payload + "#END") in a small th x tw pixel
tile (8 x 8 for a 15-digit IMEI) and repeats it over the whole image, so
pixel (y, x) carries tile cell (y % th, x % tw).

1.  Read the LSB of every channel of every pixel.
2.  Group pixels by their position inside the tile grid and count, for each
    of the tile's bit slots, how many copies say "1" and how many copies
    there are in total.
3.  Majority vote: a bit is 1 if more than half of the copies say 1. Copies
    damaged by noise or local edits are simply outvoted.

Why it survives cropping
------------------------
A crop only removes pixels; the pixels that remain still carry the same bit
they carried before. The only thing lost is *where the tile grid starts*
(the crop offset modulo the tile size). The extractor therefore tries every
possible grid alignment (8 x 8 = 64 of them) and keeps the one whose decoded
frame ends in the expected DELIMITER. Any crop of at least one full tile
(8 x 8 px for a 15-digit payload) can in principle be decoded.

--payload-length is required because it determines the tile size; it must
be the same length that was embedded.
"""

import argparse
import math
import sys

import cv2
import numpy as np

import jpeg_dct
import video_lsb

# ---------------------------------------------------------------------------
# Format constants. These MUST match the ones in embed_imei.py.
# ---------------------------------------------------------------------------

# End-of-payload marker, also used as the sync word to find the grid
# alignment after a crop.
DELIMITER = "#END"

# Each colour channel stores 1 bit -> a 3-channel (BGR) pixel stores 3 bits.
BITS_PER_PIXEL = 3


def bits_to_text(bits: np.ndarray) -> str:
    """
    Pack every group of 8 bits (MSB first) back into one character.

    [0, 0, 1, 1, 0, 0, 0, 1] -> 0b00110001 = 49 -> "1"

    Bytes that are not ASCII (>= 128) become "?" so a wrong alignment never
    raises an exception or breaks console output; it simply fails validation.
    """
    data = np.packbits(bits.astype(np.uint8))
    return "".join(chr(b) if b < 128 else "?" for b in data)


def tile_shape(payload_length: int) -> tuple:
    """
    Compute the (height, width) of the square tile that holds one frame.
    Identical to the calculation in embed_imei.py.
    """
    frame_bits = (payload_length + len(DELIMITER)) * 8
    pixels_needed = math.ceil(frame_bits / BITS_PER_PIXEL)
    side = math.ceil(math.sqrt(pixels_needed))
    return side, side


def extract(image_path: str, payload_length: int, max_frames: int = 0) -> int:
    if video_lsb.is_video_file(image_path):
        return video_lsb.extract(image_path, payload_length, max_frames)
    if jpeg_dct.is_jpeg_file(image_path):
        return jpeg_dct.extract(image_path, payload_length)

    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        sys.exit(f"[!] Could not read image: {image_path}")

    h, w = img.shape[:2]
    th, tw = tile_shape(payload_length)
    frame_bits = (payload_length + len(DELIMITER)) * 8
    if h < th or w < tw:
        sys.exit(f"[!] Image ({w}x{h}) is smaller than one tile ({tw}x{th}); "
                 "not enough data to recover the payload.")

    # ------------------------------------------------------------------
    # Read the LSB of every channel:
    #
    #       1 0 1 1 0 1 0 1   (181)
    #     & 0 0 0 0 0 0 0 1   (1)
    #     = 0 0 0 0 0 0 0 1   -> bit 1
    #
    # AND with 1 zeros the upper 7 bits, leaving only the LSB (0 or 1).
    # ------------------------------------------------------------------
    lsb = (img & 1).astype(np.uint32)

    # ------------------------------------------------------------------
    # Count votes per tile cell.
    #
    # Pixel (y, x) of this (possibly cropped) image belongs to "phase"
    # (y % th, x % tw). After a crop by (cy, cx), every pixel with the same
    # phase still carries the same frame bit - the phase is just shifted by
    # (cy % th, cx % tw). So we can sum all pixels that share a phase first
    # and worry about the unknown shift afterwards.
    #
    # To do that with one reshape, pad the image up to a multiple of the
    # tile size, and track which entries are real pixels with `mask` so that
    # padding never counts as a vote (this lets partial tiles at the edges
    # contribute too).
    # ------------------------------------------------------------------
    ph = math.ceil(h / th) * th
    pw = math.ceil(w / tw) * tw
    lsb_padded = np.zeros((ph, pw, BITS_PER_PIXEL), dtype=np.uint32)
    mask = np.zeros((ph, pw, BITS_PER_PIXEL), dtype=np.uint32)
    lsb_padded[:h, :w] = lsb
    mask[:h, :w] = 1

    # (ph, pw, 3) -> (tiles_y, th, tiles_x, tw, 3), then sum over the
    # tile-index axes -> (th, tw, 3).
    #   ones[r, c, k]  = how many copies voted "1" for cell (r, c, k)
    #   total[r, c, k] = how many copies voted at all
    shape5 = (ph // th, th, pw // tw, tw, BITS_PER_PIXEL)
    ones = lsb_padded.reshape(shape5).sum(axis=(0, 2))
    total = mask.reshape(shape5).sum(axis=(0, 2))

    # ------------------------------------------------------------------
    # Try every possible grid alignment and keep the valid ones.
    # np.roll cyclically shifts the vote table so that cell (0, 0) lines up
    # with the start of the frame for the guessed crop offset (dy, dx).
    # ------------------------------------------------------------------
    candidates = []
    for dy in range(th):
        for dx in range(tw):
            v1 = np.roll(ones, shift=(-dy, -dx), axis=(0, 1)).reshape(-1)
            vn = np.roll(total, shift=(-dy, -dx), axis=(0, 1)).reshape(-1)
            v1, vn = v1[:frame_bits], vn[:frame_bits]

            # MAJORITY VOTE: bit = 1 if more than half of the copies say 1.
            # Written as 2*ones > total to stay in integer arithmetic.
            # Example: 900 copies, 610 say "1"  -> 1220 > 900 -> bit 1.
            #          900 copies, 280 say "1"  ->  560 < 900 -> bit 0.
            bits = (2 * v1 > vn).astype(np.uint8)

            # Confidence = average agreement margin, 1.0 = unanimous copies,
            # 0.0 = coin toss. |2*ones/total - 1| is 1 when all copies agree
            # and 0 when they split 50/50.
            margin = np.abs(2.0 * v1 / vn - 1.0)
            confidence = float(margin.mean())
            ties = int(np.count_nonzero(2 * v1 == vn))

            text = bits_to_text(bits)
            payload, tail = text[:payload_length], text[payload_length:]
            valid = tail == DELIMITER and payload.isprintable()
            candidates.append((valid, confidence, payload, dy, dx, ties, margin))

    # Prefer valid frames; among those, the highest confidence.
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    valid, confidence, payload, dy, dx, ties, margin = candidates[0]

    copies = (h // th) * (w // tw)
    weak_bits = int(np.count_nonzero(margin < 0.2))
    print(f"[+] Image size        : {w} x {h}")
    print(f"[+] Tile size         : {tw} x {th} px, full copies: {copies}")
    print(f"[+] Grid alignment    : dy={dy}, dx={dx} "
          f"(crop offset mod tile size)")
    print(f"[+] Vote confidence   : {confidence * 100:.1f} % "
          f"(weak bits: {weak_bits}, tied bits: {ties})")

    if not valid:
        print(f"[-] No alignment produced the delimiter {DELIMITER!r}.")
        print(f"[-] Best guess (unreliable): {payload!r}")
        return 1

    print(f"[+] Extracted payload : {payload}")
    if payload.isdigit() and len(payload) == 15:
        print("[+] Format            : valid 15-digit IMEI format")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract an IMEI embedded by embed_imei.py, using "
                    "majority voting across all copies (crop-resilient).")
    parser.add_argument("--image", required=True,
                        help="Watermarked (possibly cropped) image or .mp4 video")
    parser.add_argument("--payload-length", type=int, default=15,
                        help="Payload length in characters (default 15)")
    parser.add_argument("--max-frames", type=int, default=0,
                        help="Video only: read at most this many frames "
                             "(default 0 = all). Faster on long videos.")
    args = parser.parse_args()

    if args.payload_length <= 0:
        sys.exit("[!] --payload-length must be positive.")
    return extract(args.image, args.payload_length, args.max_frames)


if __name__ == "__main__":
    sys.exit(main())
