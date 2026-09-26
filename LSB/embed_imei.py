#!/usr/bin/env python3
"""
embed_imei.py - Embed an IMEI using Spatially Distributed LSB Steganography
                with Redundancy (companion: extract_imei.py).

Purpose
-------
Embed a short identifier (e.g. a 15-digit IMEI used for image-provenance
tracking) into the Least Significant Bits (LSBs) of an image, repeated as a
small 2-D "tile" across the ENTIRE pixel array. Because every region of the
image carries a full copy of the identifier, the identifier can still be
recovered after heavy cropping. Because there are many copies, per-bit
majority voting (done in extract_imei.py) corrects bits damaged by noise or
local edits.

Usage
-----
    python embed_imei.py --image input.jpg --payload "123456789012345" --output watermarked.png

How the payload is laid out
---------------------------
1.  Frame  = payload text + DELIMITER ("#END").
    e.g. "123456789012345#END"  -> 19 characters -> 19 * 8 = 152 bits.

2.  Each pixel has 3 colour channels (B, G, R in OpenCV), and each channel
    stores exactly ONE bit in its LSB, so each pixel stores 3 bits.
    152 bits / 3 = 51 pixels needed.

3.  Those pixels are arranged in a square tile of side
    ceil(sqrt(51)) = 8  ->  an 8 x 8 pixel tile (64 px = 192 bit slots).
    Unused slots (192 - 152 = 40) are padding (0).

4.  The tile is repeated (tiled) over the whole image. Pixel (y, x) therefore
    carries tile cell (y % 8, x % 8). A 1920x1080 image contains
    240 * 135 = 32,400 copies of the identifier.

Limitations (important for forensic use)
----------------------------------------
* LSB watermarks are FRAGILE against lossy re-compression (JPEG/WebP),
  resizing, rotation, and filtering: those operations rewrite the LSBs.
  Always save the watermarked output as a lossless format (PNG/BMP/TIFF).
* LSB embedding is NOT secret or tamper-proof: anyone who knows the scheme
  can read or overwrite the bits. Use it for provenance *hints*, not proof.
"""

import argparse
import math
import os
import sys

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Format constants. These MUST match the ones in extract_imei.py, otherwise
# the extractor computes a different tile size / delimiter and finds nothing.
# ---------------------------------------------------------------------------

# The delimiter marks the end of the payload. On extraction it doubles as a
# "sync word": only the correct tile alignment decodes to a frame that ends in
# exactly these characters, which is how the grid is recovered after a crop.
DELIMITER = "#END"

# Each colour channel stores 1 bit -> a 3-channel (BGR) pixel stores 3 bits.
BITS_PER_PIXEL = 3

# Formats that preserve every bit of every pixel. Anything else (e.g. JPEG)
# would destroy the LSBs we just wrote. (.pgm is excluded: it is grayscale
# only, so it cannot store the three colour channels.)
LOSSLESS_EXTENSIONS = {".png", ".bmp", ".tif", ".tiff", ".ppm"}


def text_to_bits(text: str) -> np.ndarray:
    """
    Convert an ASCII string to a flat array of bits (MSB first).

    Example: "1" is ASCII 49 = 0b00110001
             -> [0, 0, 1, 1, 0, 0, 0, 1]

    np.unpackbits does exactly this for every byte: it expands each uint8
    into its 8 binary digits, most-significant bit first.
    """
    data = np.frombuffer(text.encode("ascii"), dtype=np.uint8)
    return np.unpackbits(data)


def tile_shape(payload_length: int) -> tuple:
    """
    Compute the (height, width) of the square tile that holds one frame.

    extract_imei.py runs the same calculation from --payload-length, so both
    scripts always agree on the geometry.
    """
    frame_bits = (payload_length + len(DELIMITER)) * 8
    pixels_needed = math.ceil(frame_bits / BITS_PER_PIXEL)
    side = math.ceil(math.sqrt(pixels_needed))
    return side, side


def build_tile(payload: str) -> np.ndarray:
    """
    Build the (th, tw, 3) array of bits that forms one copy of the frame.

    Bit i of the frame is stored at:
        pixel index p = i // 3            (which pixel in the tile)
        row         r = p // tw           (row inside the tile)
        column      c = p %  tw           (column inside the tile)
        channel     k = i %  3            (0 = Blue, 1 = Green, 2 = Red)

    A C-order reshape of the flat bit array to (th, tw, 3) performs exactly
    this mapping for us.
    """
    th, tw = tile_shape(len(payload))
    bits = text_to_bits(payload + DELIMITER)

    capacity = th * tw * BITS_PER_PIXEL
    padded = np.zeros(capacity, dtype=np.uint8)
    padded[: bits.size] = bits  # remaining slots are padding (0)
    return padded.reshape(th, tw, BITS_PER_PIXEL)


def embed(image_path: str, payload: str, output_path: str) -> None:
    if not payload.isascii():
        sys.exit("[!] Payload must be ASCII text.")
    if not (payload.isdigit() and len(payload) == 15):
        print("[!] Warning: payload is not a 15-digit IMEI; embedding anyway.")
    if DELIMITER in payload:
        sys.exit(f"[!] Payload must not contain the delimiter {DELIMITER!r}.")

    ext = os.path.splitext(output_path)[1].lower()
    if ext not in LOSSLESS_EXTENSIONS:
        sys.exit(
            f"[!] Output format {ext!r} is lossy or unsupported. "
            "Use .png/.bmp/.tif - lossy compression destroys LSBs."
        )

    # IMREAD_COLOR always gives an (H, W, 3) uint8 BGR array. Grayscale input
    # is expanded to 3 channels; an alpha channel, if any, is dropped.
    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        sys.exit(f"[!] Could not read image: {image_path}")

    h, w = img.shape[:2]
    tile = build_tile(payload)
    th, tw = tile.shape[:2]
    if h < th or w < tw:
        sys.exit(f"[!] Image ({w}x{h}) is smaller than one tile ({tw}x{th}).")

    # Repeat the tile across the whole image, then trim to the exact size.
    # After this, pattern[y, x, k] == tile[y % th, x % tw, k] for every pixel,
    # i.e. every th x tw window of the image holds a complete frame copy.
    reps_y = math.ceil(h / th)
    reps_x = math.ceil(w / tw)
    pattern = np.tile(tile, (reps_y, reps_x, 1))[:h, :w, :]

    # ------------------------------------------------------------------
    # THE LSB REPLACEMENT (the core bitwise math)
    # ------------------------------------------------------------------
    # A channel value is an 8-bit unsigned integer, e.g. 181:
    #
    #       181 = 1 0 1 1 0 1 0 1
    #             ^             ^
    #            MSB (128)     LSB (1)
    #
    # Step 1 - CLEAR the LSB with a bitwise AND against 0xFE:
    #
    #       0xFE = 1 1 1 1 1 1 1 0
    #
    #       1 0 1 1 0 1 0 1   (181)
    #     & 1 1 1 1 1 1 1 0   (0xFE)
    #     = 1 0 1 1 0 1 0 0   (180)
    #
    #   AND with 1 keeps a bit unchanged, AND with 0 forces it to 0, so the
    #   upper 7 bits survive and only the last bit becomes 0.
    #
    # Step 2 - SET the LSB to our payload bit with a bitwise OR:
    #
    #   payload bit = 1:   1 0 1 1 0 1 0 0 | 0 0 0 0 0 0 0 1 = 181
    #   payload bit = 0:   1 0 1 1 0 1 0 0 | 0 0 0 0 0 0 0 0 = 180
    #
    #   The LSB is now 0 after step 1, so OR-ing in 0 or 1 writes exactly
    #   that value and leaves the other 7 bits alone.
    #
    # Net effect: each channel changes by at most +/-1 out of 255 (~0.4 %),
    # which is invisible to the human eye (PSNR is typically ~51 dB).
    #
    # NumPy applies both operations to every channel of every pixel at once.
    stego = (img & 0xFE) | pattern

    if not cv2.imwrite(output_path, stego):
        sys.exit(f"[!] Could not write image: {output_path}")

    copies = (h // th) * (w // tw)
    changed = np.count_nonzero(stego != img)
    print(f"[+] Embedded payload  : {payload}{DELIMITER}")
    print(f"[+] Image size        : {w} x {h}")
    print(f"[+] Tile size         : {tw} x {th} px "
          f"({(len(payload) + len(DELIMITER)) * 8} bits used of "
          f"{th * tw * BITS_PER_PIXEL})")
    print(f"[+] Full copies       : {copies}")
    print(f"[+] Channels modified : {changed} / {img.size} "
          f"({100 * changed / img.size:.1f} %, each by exactly 1)")
    print(f"[+] Saved             : {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Embed an IMEI into an image with spatially distributed, "
                    "redundant LSB steganography (crop-resilient).")
    parser.add_argument("--image", required=True, help="Cover image path")
    parser.add_argument("--payload", required=True,
                        help="Payload text, e.g. a 15-digit IMEI")
    parser.add_argument("--output", required=True,
                        help="Output path (must be lossless, e.g. .png)")
    args = parser.parse_args()

    embed(args.image, args.payload, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
