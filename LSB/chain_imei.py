#!/usr/bin/env python3
"""
chain_imei.py - Record the forwarding chain of a photo or video inside the file.

Every device that forwards the file (e.g. over Bluetooth) appends one "hop"
(its device ID, IMEI, time and role) to a chain hidden in the file. Reading
the file later gives the full path, e.g. A -> B -> C.

Usage
-----
    # Phone A forwards the photo (first hop: a new chain is started,
    # the JPEG is converted once to a lossless PNG)
    python chain_imei.py append --input photo.jpg --output photo_A.png \
        --device-id phone-A --imei 356938035643809

    # Phone B forwards it on, using 6 bits per pixel instead of 3
    python chain_imei.py append --input photo_A.png --output photo_AB.png \
        --device-id phone-B --imei 490154203237518 --bpp 6

    # Phone C, the final receiver, adds itself
    python chain_imei.py append --input photo_AB.png --output photo_ABC.png \
        --device-id phone-C --imei 012345678901237 --role claim

    # Read the chain (add --json for machine-readable output)
    python chain_imei.py read --input photo_ABC.png

    # Videos work the same way
    python chain_imei.py append --input clip.mp4 --output clip_A.mp4 --device-id phone-A

--device-id takes a 16-hex-character Android ANDROID_ID as-is; any other text
(like "phone-A") is hashed to 8 bytes, which is handy for testing on a PC.
(--image is accepted as an alias of --input.)

How it is stored
----------------
Both media types carry the same fixed 2016-byte frame with room for 100
hops (chain_payload.py):

* Photos: written into the RGB pixel LSBs (pixel_chain.py), 3 or 6 bits per
  pixel (--bpp), as a 74 x 74 or 52 x 52 px tile repeated over the whole
  photo and read by majority vote. Photos are saved as PNG (lossless); a JPEG
  or other format is converted once, at the first hop.
* Videos (MP4 / MOV / 3GP): the complete frame is stored in one small hidden
  box at the end of the file (mp4_chain.py); the video is never re-encoded.

Both survive byte-for-byte transfers (Bluetooth, USB, e-mail attachment) but
NOT re-compression or resizing (WhatsApp photo/video mode, editing,
screenshots).
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Optional

import chain_payload as cp
import mp4_chain
import pixel_chain

IMAGE_OUTPUT_EXTENSIONS = pixel_chain.OUTPUT_EXTENSIONS

MEDIA_IMAGE = "image"
MEDIA_VIDEO = "video"


@dataclass
class ReadResult:
    chain: cp.Chain
    media: str                 # "image" | "video"
    bits_per_pixel: Optional[int] = None   # 3 or 6 (images only)
    dy: int = 0                # grid offset of the tile (images only)
    dx: int = 0
    confidence: float = 1.0    # vote confidence (always 1.0 for video)
    votes_min: int = 1         # fewest copies any bit was read from


def media_type(path: str) -> Optional[str]:
    """'image' for a PNG, 'video' for an MP4/MOV/3GP, else None (by content)."""
    if pixel_chain.is_png_file(path):
        return MEDIA_IMAGE
    if mp4_chain.is_mp4_file(path):
        return MEDIA_VIDEO
    return None


def _decode_image(rgb) -> Optional[ReadResult]:
    r = pixel_chain.decode(rgb)
    if r is None:
        return None
    return ReadResult(r.chain, MEDIA_IMAGE, r.bits_per_pixel, r.dy, r.dx,
                      r.confidence, r.votes_min)


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
    """
    Read the chain from a PNG photo or an MP4 video. Returns None if there is
    no valid chain (other image formats, e.g. JPEG, can't hold one).
    Raises ValueError for a damaged PNG / MP4.
    """
    kind = media_type(path)
    if kind == MEDIA_IMAGE:
        return _decode_image(pixel_chain.load_rgb(path))
    if kind == MEDIA_VIDEO:
        chain = mp4_chain.read(path)
        return ReadResult(chain, MEDIA_VIDEO) if chain else None
    return None


def append_hop(input_path: str, output_path: str, device_id: bytes,
               imei: Optional[str], role: int = cp.ROLE_SEND,
               timestamp: Optional[int] = None, allow_repeat: bool = False,
               bits_per_pixel: Optional[int] = None) -> dict:
    """
    Append one hop to the file's chain (starting a new chain if it has none)
    and save the result. Photos are saved as PNG, videos keep their format.
    Returns a summary dict.

    bits_per_pixel (photos only): 3 or 6. None keeps the mode the photo was
    already stamped with, or uses 3 for a new chain. The whole chain is
    re-written in the chosen mode, so the mode may change from hop to hop.

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
                "converted": False, "media": MEDIA_VIDEO, "stats": None}

    if out_ext not in IMAGE_OUTPUT_EXTENSIONS:
        raise ValueError("A photo must be saved as .png (lossless).")
    if bits_per_pixel is not None and bits_per_pixel not in pixel_chain.MODES:
        raise ValueError(f"Bits per pixel must be one of {sorted(pixel_chain.MODES)}.")
    converted = not pixel_chain.is_png_file(input_path)
    rgb = pixel_chain.load_rgb(input_path)
    existing = None if converted else _decode_image(rgb)
    chain, added = _next_chain(existing.chain if existing else None,
                               pixel_chain.content_id(rgb),
                               device_id, imei, role, timestamp, allow_repeat)
    bpp = bits_per_pixel or (existing.bits_per_pixel if existing else pixel_chain.DEFAULT_BPP)
    stats = pixel_chain.write_frame(rgb, cp.pack(chain), bpp)
    pixel_chain.save_png(output_path, rgb)
    return {"chain": chain, "added": added, "new_chain": existing is None,
            "converted": converted, "media": MEDIA_IMAGE, "stats": stats}


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
                         args.timestamp, args.allow_repeat, args.bpp)
    except (ValueError, OSError, cp.ChainError) as e:
        sys.exit(f"[!] {e}")

    s = res["stats"]
    where = ("hidden MP4 box, video not re-encoded" if res["media"] == MEDIA_VIDEO
             else f"RGB pixel LSBs, {s['bits_per_pixel']} bits/pixel "
                  f"({pixel_chain.MODES[s['bits_per_pixel']]} per channel), PNG")
    if res["converted"]:
        print("[+] Input             : not a PNG, converted once to lossless PNG; "
              "new chain started")
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
    if s:
        print(f"[+] Tile              : {s['tile']} x {s['tile']} px, repeated "
              f"{s['copies']} times (full copies) over {s['size'][0]} x {s['size'][1]} px")
        if "changed" in s:
            print(f"[+] Values changed    : {s['changed']} / {s['values']} "
                  f"({100 * s['changed'] / s['values']:.1f} %)")
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
                       bits_per_pixel=res.bits_per_pixel,
                       confidence=round(res.confidence, 4),
                       alignment=[res.dy, res.dx], votes_min=res.votes_min)
        print(json.dumps(out, indent=2))
        return 0 if res else 1

    if res is None:
        print("[-] No forwarding chain found.")
        print("[-] The file was never stamped, is not a PNG/MP4, or was "
              "re-compressed or resized (WhatsApp photo/video, editing, screenshot).")
        return 1
    print(f"[+] Media             : {res.media}")
    if res.media == MEDIA_IMAGE:
        print(f"[+] Mode              : RGB LSB, {res.bits_per_pixel} bits/pixel")
        print(f"[+] Grid alignment    : dy={res.dy}, dx={res.dx} px")
        print(f"[+] Copies per bit    : min {res.votes_min}")
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
                   help="Output path: .png for photos, .mp4/.mov/.3gp for videos")
    a.add_argument("--device-id", required=True,
                   help="16-hex ANDROID_ID, or any text (hashed to 8 bytes)")
    a.add_argument("--imei", default=None, help="15-digit IMEI (optional)")
    a.add_argument("--role", choices=["send", "claim"], default="send",
                   help="send = forwarding it (default); claim = final receiver")
    a.add_argument("--timestamp", type=int, default=None,
                   help="Unix time of the hop (default: now)")
    a.add_argument("--allow-repeat", action="store_true",
                   help="Add a hop even if this device is already the last one")
    a.add_argument("--bpp", type=int, choices=sorted(pixel_chain.MODES), default=None,
                   help="Photos: 3 = 1 bit per R/G/B value, 6 = 2 bits per value "
                        "(default: keep the photo's current mode, 3 for a new chain)")
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
