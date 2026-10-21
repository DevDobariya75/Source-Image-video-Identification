#!/usr/bin/env python3
"""
chain_imei.py - Record the forwarding chain of a photo or video inside the file.

Every device that forwards the file (e.g. over Bluetooth) appends one "hop"
(its device ID, IMEI, time and role) to a chain hidden in the file. Reading
the file later gives the full path, e.g. A -> B -> C.

Usage
-----
    # Phone A forwards the photo (first hop: a new chain is started)
    python chain_imei.py append --input photo.jpg --output photo_A.jpg \
        --device-id phone-A --imei 356938035643809

    # Phone B forwards it on
    python chain_imei.py append --input photo_A.jpg --output photo_AB.jpg \
        --device-id phone-B --imei 490154203237518

    # Phone C, the final receiver, adds itself
    python chain_imei.py append --input photo_AB.jpg --output photo_ABC.jpg \
        --device-id phone-C --imei 012345678901237 --role claim

    # Read the chain (add --json for machine-readable output)
    python chain_imei.py read --input photo_ABC.jpg

    # Videos work the same way
    python chain_imei.py append --input clip.mp4 --output clip_A.mp4 --device-id phone-A

--device-id takes a 16-hex-character Android ANDROID_ID as-is; any other text
(like "phone-A") is hashed to 8 bytes, which is handy for testing on a PC.
(--image is accepted as an alias of --input.)

How it is stored
----------------
Both media types carry the same fixed 176-byte frame (chain_payload.py):

* Photos (JPEG): written into the LSBs of the JPEG's DCT coefficients, with
  the same scheme as jpeg_dct.py but a larger tile of 16 x 16 blocks
  (128 x 128 px), repeated over the whole photo and read by majority vote.
  Each hop rewrites it in place without re-compressing the JPEG.
  Other image formats (PNG, ...) are converted to JPEG once, at the first hop.
* Videos (MP4 / MOV / 3GP): stored in a small hidden box at the end of the
  file (mp4_chain.py); the video itself is never re-encoded.

Both survive byte-for-byte transfers (Bluetooth, USB, e-mail attachment) but
NOT re-compression (WhatsApp photo/video mode, editing, screenshots).
"""

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Optional

import numpy as np

import chain_payload as cp
import jpeg_dct
import mp4_chain

# Tile size in 8x8 blocks for the chain frame: 16 x 16 blocks x 16 freqs
# = 4096 slots, enough for ~3 copies of the 1408-bit frame per tile.
CHAIN_TILE_BLOCKS = 16

IMAGE_OUTPUT_EXTENSIONS = {".jpg", ".jpeg"}

MEDIA_IMAGE = "image"
MEDIA_VIDEO = "video"


@dataclass
class ReadResult:
    chain: cp.Chain
    media: str                 # "image" | "video"
    dy: int = 0                # grid alignment (images only)
    dx: int = 0
    confidence: float = 1.0    # vote confidence (always 1.0 for video)
    votes_min: int = 1


def media_type(path: str) -> Optional[str]:
    """'image' for a JPEG, 'video' for an MP4/MOV/3GP, else None (by content)."""
    if jpeg_dct.is_jpeg_file(path):
        return MEDIA_IMAGE
    if mp4_chain.is_mp4_file(path):
        return MEDIA_VIDEO
    return None


# ---------------------------------------------------------------------------
# Images (JPEG DCT)
# ---------------------------------------------------------------------------

def _decode(Y: np.ndarray) -> Optional[ReadResult]:
    """Try every grid offset; return the best one whose frame passes the CRC."""
    best = None
    for dy, dx, bits, confidence, missing, bn in jpeg_dct.vote_alignments(
            Y, cp.FRAME_BITS, CHAIN_TILE_BLOCKS):
        if missing:
            continue
        try:
            chain = cp.unpack(np.packbits(bits).tobytes())
        except cp.ChainError:
            continue
        r = ReadResult(chain, MEDIA_IMAGE, dy, dx, confidence, int(bn.min()))
        if best is None or r.confidence > best.confidence:
            best = r
    return best


def content_id(jpeg) -> bytes:
    """
    8-byte fingerprint of the photo, used as the Image ID of a new chain.

    It hashes only data the chain never modifies: the DC coefficient of
    every luminance block and all chroma coefficients. So the same original
    photo always gets the same Image ID, even if it is stamped separately
    for every receiver, and every copy lands in the same forwarding tree.
    """
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(jpeg.Y[:, :, 0, 0], dtype="<i2").tobytes())
    for ch in (jpeg.Cb, jpeg.Cr):
        if ch is not None:
            h.update(np.ascontiguousarray(ch, dtype="<i2").tobytes())
    return h.digest()[:cp.IMAGE_ID_BYTES]


def _next_chain(existing: Optional[cp.Chain], new_id: bytes, device_id: bytes,
                imei: Optional[str], role: int, timestamp: Optional[int],
                allow_repeat: bool):
    """Return (chain, added) with this device's hop appended."""
    chain = existing or cp.Chain(new_id)
    if chain.hops and chain.hops[-1].device_id == device_id and not allow_repeat:
        return chain, False
    if len(chain.hops) >= cp.MAX_HOPS:
        raise cp.ChainError(f"Chain is full ({cp.MAX_HOPS} hops). "
                            "No more devices can be added.")
    chain.hops.append(cp.make_hop(device_id, imei, role, timestamp))
    return chain, True


# ---------------------------------------------------------------------------
# Public API (used by the CLI and by backend/main.py)
# ---------------------------------------------------------------------------

def read_chain(path: str) -> Optional[ReadResult]:
    """Read the chain from a JPEG or MP4. Returns None if there is no valid chain."""
    kind = media_type(path)
    if kind == MEDIA_IMAGE:
        jpeg, _ = jpeg_dct.load_dct(path)
        return _decode(jpeg.Y)
    if kind == MEDIA_VIDEO:
        chain = mp4_chain.read(path)
        return ReadResult(chain, MEDIA_VIDEO) if chain else None
    return None


def append_hop(input_path: str, output_path: str, device_id: bytes,
               imei: Optional[str], role: int = cp.ROLE_SEND,
               timestamp: Optional[int] = None, allow_repeat: bool = False,
               quality: int = 95) -> dict:
    """
    Append one hop to the file's chain (starting a new chain if it has none)
    and save the result. Photos are saved as JPEG, videos keep their format.
    Returns a summary dict.

    If the last hop already belongs to this device, nothing is added unless
    allow_repeat is True. This covers a phone that sends the same file to
    several people: it should appear once, and each receiver's copy then
    branches from it.
    """
    out_ext = os.path.splitext(output_path)[1].lower()

    if mp4_chain.is_mp4_file(input_path):
        if out_ext not in mp4_chain.VIDEO_EXTENSIONS:
            raise ValueError(f"A video must be saved as one of "
                             f"{sorted(mp4_chain.VIDEO_EXTENSIONS)}.")
        existing = mp4_chain.read(input_path)
        chain, added = _next_chain(existing, mp4_chain.content_id(input_path),
                                   device_id, imei, role, timestamp, allow_repeat)
        mp4_chain.write(input_path, output_path, chain)
        return {"chain": chain, "added": added, "new_chain": existing is None,
                "recompressed": False, "media": MEDIA_VIDEO, "stats": None}

    if out_ext not in IMAGE_OUTPUT_EXTENSIONS:
        raise ValueError("A photo must be saved as .jpg or .jpeg.")
    jpeg, recompressed = jpeg_dct.load_dct(input_path, quality)
    existing = None if recompressed else _decode(jpeg.Y)
    chain, added = _next_chain(existing.chain if existing else None, content_id(jpeg),
                               device_id, imei, role, timestamp, allow_repeat)
    bits = np.unpackbits(np.frombuffer(cp.pack(chain), dtype=np.uint8))
    stats = jpeg_dct.write_bits(jpeg, bits, CHAIN_TILE_BLOCKS)
    jpeg.write_dct(output_path)
    return {"chain": chain, "added": added, "new_chain": existing is None,
            "recompressed": recompressed, "media": MEDIA_IMAGE, "stats": stats}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_chain(chain: cp.Chain) -> None:
    print(f"[+] Image ID          : {chain.image_id.hex()}")
    print(f"[+] Hops              : {len(chain.hops)} / {cp.MAX_HOPS}")
    for i, h in enumerate(chain.hops, 1):
        d = h.to_dict()
        print(f"    {i}. device {d['device_id']}  IMEI {d['imei'] or '(unknown)'}"
              f"  {d['time']}  [{d['role']}]")
    if chain.hops:
        path = " -> ".join(h.imei or h.device_id.hex()[:8] for h in chain.hops)
        print(f"[+] Path              : {path}")


def cmd_append(args) -> int:
    try:
        device_id = cp.parse_device_id(args.device_id)
        imei = cp.validate_imei(args.imei)
        if imei and not cp.luhn_ok(imei):
            print(f"[!] Warning: IMEI {imei} fails the Luhn check (typo?). "
                  "Embedding anyway.")
        role = cp.ROLE_CLAIM if args.role == "claim" else cp.ROLE_SEND
        res = append_hop(args.input, args.output, device_id, imei, role,
                         args.timestamp, args.allow_repeat, args.quality)
    except (ValueError, OSError, cp.ChainError) as e:
        sys.exit(f"[!] {e}")

    where = ("hidden MP4 box, video not re-encoded" if res["media"] == MEDIA_VIDEO
             else "JPEG DCT coefficients, no re-compression")
    if res["recompressed"]:
        print(f"[+] Input             : not a JPEG, compressed once at quality "
              f"{args.quality}; new chain started")
    elif res["new_chain"]:
        print(f"[+] Input             : {res['media']} with no chain; new chain "
              "started (this device is the origin)")
    else:
        print(f"[+] Input             : {res['media']} with an existing chain")
    print(f"[+] Stored in         : {where}")
    if not res["added"]:
        print("[i] This device is already the last hop; nothing added "
              "(use --allow-repeat to force).")
    _print_chain(res["chain"])
    if res["stats"]:
        s = res["stats"]
        print(f"[+] Copies per bit    : min {s['votes_min']}, median {s['votes_median']}")
    print(f"[+] Saved             : {args.output}")
    return 0


def cmd_read(args) -> int:
    try:
        res = read_chain(args.input)
    except (ValueError, OSError) as e:
        sys.exit(f"[!] {e}")

    if args.json:
        out = {"found": res is not None}
        if res:
            out.update(res.chain.to_dict(), media=res.media,
                       confidence=round(res.confidence, 4),
                       alignment=[res.dy, res.dx], votes_min=res.votes_min)
        print(json.dumps(out, indent=2))
        return 0 if res else 1

    if res is None:
        print("[-] No forwarding chain found.")
        print("[-] The file was never stamped, is not a JPEG/MP4, or was "
              "re-compressed (sent as a WhatsApp photo/video, edited, screenshotted).")
        return 1
    print(f"[+] Media             : {res.media}")
    if res.media == MEDIA_IMAGE:
        print(f"[+] Grid alignment    : dy={res.dy}, dx={res.dx} blocks")
        print(f"[+] Vote confidence   : {res.confidence * 100:.1f} %")
    _print_chain(res.chain)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record / read the device forwarding chain hidden in a photo or video.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="Add this device to the file's chain")
    a.add_argument("--input", "--image", required=True, dest="input",
                   help="Input photo or video")
    a.add_argument("--output", required=True,
                   help="Output path: .jpg/.jpeg for photos, .mp4/.mov/.3gp for videos")
    a.add_argument("--device-id", required=True,
                   help="16-hex ANDROID_ID, or any text (hashed to 8 bytes)")
    a.add_argument("--imei", default=None, help="15-digit IMEI (optional)")
    a.add_argument("--role", choices=["send", "claim"], default="send",
                   help="send = forwarding it (default); claim = final receiver")
    a.add_argument("--timestamp", type=int, default=None,
                   help="Unix time of the hop (default: now)")
    a.add_argument("--allow-repeat", action="store_true",
                   help="Add a hop even if this device is already the last one")
    a.add_argument("--quality", type=int, default=95,
                   help="JPEG quality used only if the input photo is not a JPEG")
    a.set_defaults(func=cmd_append)

    r = sub.add_parser("read", help="Show the file's chain")
    r.add_argument("--input", "--image", required=True, dest="input",
                   help="Photo or video to read")
    r.add_argument("--json", action="store_true", help="Print JSON")
    r.set_defaults(func=cmd_read)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
