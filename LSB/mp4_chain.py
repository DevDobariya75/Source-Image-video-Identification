"""
mp4_chain.py - Store the forwarding chain inside an MP4 / MOV / 3GP video.

Why not pixel LSB for video?
----------------------------
Phone videos are lossy H.264/H.265. Pixel LSBs only survive if the video is
re-encoded LOSSLESSLY (see video_lsb.py), which makes the file 10-20x larger
and cannot be done by a phone's hardware encoder. Doing that at every hop of
a Bluetooth chain is not practical.

Instead the chain goes into the MP4 *container*. An MP4 file is a list of
"boxes" (size + type + data): ftyp, moov (the index), mdat (the video and
audio data), ... Players skip box types they don't know. We add one box of
type "uuid" with our own 16-byte ID, holding the COMPLETE chain: the same
2016-byte frame (100 hop slots) as photos use (chain_payload.py), scrambled
with a fixed keystream so it is not readable text.

    [ftyp][moov][mdat] ... [uuid: CHAIN_UUID | scrambled 2016-byte frame]

* No re-encoding: the video and audio bytes are untouched, and the file
  grows by exactly 2040 bytes (24-byte box header + frame).
* The box is appended at the END of the file, so the absolute offsets
  stored in moov (stco/co64 tables) stay valid.
* Later hops overwrite the box in place (it always has the same size).
  A box in the old 8-hop format (200 bytes) is renamed to "free" (a
  standard "ignore me" box) and a new box is appended.
* Image ID = hash of the moov box, which we never change: every copy of
  the same original video gets the same ID.

Survives: byte-for-byte transfers (Bluetooth, USB, e-mail, cloud drives).
Does NOT survive: re-encoding (WhatsApp video mode, editing, screen
recording) or re-muxing tools that drop unknown boxes.
"""

import hashlib
import os
import shutil
import struct
from typing import List, Optional

import chain_payload as cp

# Our box's 16-byte "extended type" (randomly generated once, never change).
CHAIN_UUID = bytes.fromhex("6b1f8e3a52c94d07a3e1d9c46f20b8a5")
BOX_HEADER = 8 + 16                        # size + "uuid" + extended type
BOX_SIZE = BOX_HEADER + cp.FRAME_BYTES     # 2040 bytes

VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".3gp", ".3g2"}


def _keystream(n: int) -> bytes:
    out = b""
    i = 0
    while len(out) < n:
        out += hashlib.sha256(b"imei-chain-mp4/" + i.to_bytes(4, "big")).digest()
        i += 1
    return out[:n]


_KEY = _keystream(cp.FRAME_BYTES)


def _scramble(frame: bytes) -> bytes:
    """XOR with a fixed keystream. Applying it twice gives the frame back."""
    return bytes(a ^ b for a, b in zip(frame, _KEY))


def is_mp4_file(path: str) -> bool:
    """True if the file starts with an ISO-BMFF 'ftyp' box (MP4/MOV/3GP)."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return len(head) >= 8 and head[4:8] == b"ftyp"


# ---------------------------------------------------------------------------
# Box parsing (top level only)
# ---------------------------------------------------------------------------

def top_level_boxes(f) -> List[dict]:
    """
    List the top-level boxes: {offset, size, type, header, open_ended, uuid}.
    Raises ValueError if the structure is broken.
    """
    f.seek(0, os.SEEK_END)
    end = f.tell()
    boxes, pos = [], 0
    while pos < end:
        if end - pos < 8:
            raise ValueError("Truncated MP4 box header.")
        f.seek(pos)
        size, btype = struct.unpack(">I4s", f.read(8))
        header = 8
        open_ended = False
        if size == 1:                                   # 64-bit size follows
            size = struct.unpack(">Q", f.read(8))[0]
            header = 16
        elif size == 0:                                 # box runs to end of file
            size = end - pos
            open_ended = True
        if size < header or pos + size > end:
            raise ValueError(f"Invalid MP4 box '{btype.decode('latin-1')}' at {pos}.")
        uuid = f.read(16) if btype == b"uuid" and size >= header + 16 else None
        boxes.append({"offset": pos, "size": size, "type": btype, "header": header,
                      "open_ended": open_ended, "uuid": uuid})
        pos += size
    return boxes


def _our_boxes(boxes: List[dict]) -> List[dict]:
    return [b for b in boxes if b["type"] == b"uuid" and b["uuid"] == CHAIN_UUID]


def _find_chain_box(boxes: List[dict]) -> Optional[dict]:
    """Our box in the current format (old 200-byte boxes are ignored)."""
    return next((b for b in _our_boxes(boxes) if b["size"] == BOX_SIZE), None)


def content_id(path: str) -> bytes:
    """8-byte Image ID: hash of the moov box (the video's index), never modified."""
    with open(path, "rb") as f:
        boxes = top_level_boxes(f)
        moov = next((b for b in boxes if b["type"] == b"moov"), None)
        if moov is None:
            raise ValueError("Not a playable MP4: no 'moov' box.")
        f.seek(moov["offset"])
        h = hashlib.sha256()
        left = moov["size"]
        while left:
            chunk = f.read(min(left, 1 << 20))
            h.update(chunk)
            left -= len(chunk)
    return h.digest()[:cp.IMAGE_ID_BYTES]


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

def read(path: str) -> Optional[cp.Chain]:
    """Return the chain stored in the video, or None if there is none."""
    with open(path, "rb") as f:
        box = _find_chain_box(top_level_boxes(f))
        if box is None:
            return None
        f.seek(box["offset"] + BOX_HEADER)
        frame = _scramble(f.read(cp.FRAME_BYTES))
    try:
        return cp.unpack(frame)
    except cp.ChainError:
        return None


def write(src: str, dst: str, chain: cp.Chain) -> None:
    """Copy src to dst (unless they are the same file) and store the chain in dst."""
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copyfile(src, dst)
    payload = (struct.pack(">I4s", BOX_SIZE, b"uuid") + CHAIN_UUID
               + _scramble(cp.pack(chain)))

    with open(dst, "r+b") as f:
        boxes = top_level_boxes(f)
        box = _find_chain_box(boxes)
        if box is not None:                      # later hop: overwrite in place
            f.seek(box["offset"])
            f.write(payload)
            return
        for old in _our_boxes(boxes):            # old-format box: hide it
            f.seek(old["offset"] + 4)
            f.write(b"free")

        last = boxes[-1]
        if last["open_ended"]:
            # A last box with size 0 means "until end of file". Appending after
            # it would swallow our box, so give it its explicit size first.
            if last["size"] > 0xFFFFFFFF:
                raise ValueError("Unsupported MP4 layout (open-ended box > 4 GB).")
            f.seek(last["offset"])
            f.write(struct.pack(">I", last["size"]))
        f.seek(0, os.SEEK_END)
        f.write(payload)
