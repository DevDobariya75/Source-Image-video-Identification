#!/usr/bin/env python3
"""
video_lsb.py - Video mode for embed_imei.py / extract_imei.py: the same
               tiled, redundant LSB technique, applied to every frame of a
               video and saved as a LOSSLESS H.264 MP4.

Why the output must be lossless
-------------------------------
Normal MP4 (H.264) compression is lossy like JPEG: it rewrites the pixel
LSBs, and tests show the hidden bits come out as random noise (50 % match).
H.264 also has a lossless mode (x264 with qp = 0) that gives back every
pixel exactly, so pixel LSBs survive. The file is still a standard .mp4.

Trade-offs of lossless H.264:
* Files are several times larger than a normal phone video.
* It uses the "High 4:4:4 Predictive" profile. VLC, ffmpeg-based players and
  most desktop browsers play it. Some phone galleries and built-in Windows
  players may not.

Where the bits go
-----------------
Videos are stored as YUV (brightness Y + two colour planes U, V), not as
BGR pixels. Converting to BGR and back is not exact, so we work on the
YUV 4:2:0 frames directly and use only the LSB of the brightness plane Y:
1 bit per pixel.

* Frame = payload + "#END" (152 bits for a 15-digit IMEI).
* Tile  = ceil(sqrt(152)) = 13 x 13 Y pixels, repeated over every frame.
* Every frame carries the same tile, so votes are summed over ALL pixels of
  ALL frames. A 10 s 1080p clip gives millions of votes per bit.
* Like the image pixel mode, the extractor tries all 13 x 13 grid
  alignments, so a crop (re-saved losslessly) is still recoverable.

What it survives / does not survive
-----------------------------------
Survives: byte-for-byte transfers (USB, WhatsApp document mode, e-mail,
cloud drives), lossless crops/trims.
Does NOT survive: any lossy re-encode, e.g. WhatsApp "video" mode, social-
media uploads, screen recording, or editing and exporting the video normally.

The audio track, if any, is copied unchanged.
"""

import math
import sys

import numpy as np

try:
    import av
except ImportError:  # pragma: no cover - only hit when the dependency is missing
    av = None

# Must match DELIMITER in embed_imei.py / extract_imei.py.
DELIMITER = "#END"

VIDEO_EXTENSIONS = {".mp4"}

# x264 settings: qp = 0 is mathematically lossless. The preset only trades
# encoding speed for file size; the output is lossless for every preset.
X264_OPTIONS = {"qp": "0", "preset": "medium"}


def _require_av() -> None:
    if av is None:
        sys.exit("[!] Video mode needs the 'av' package: "
                 "pip install -r requirements.txt")


def is_video_file(path: str) -> bool:
    """True for MP4/MOV-family files (an 'ftyp' box at byte 4)."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return len(head) >= 8 and head[4:8] == b"ftyp"


def _text_to_bits(text: str) -> np.ndarray:
    return np.unpackbits(np.frombuffer(text.encode("ascii"), dtype=np.uint8))


def _bits_to_text(bits: np.ndarray) -> str:
    data = np.packbits(bits.astype(np.uint8))
    return "".join(chr(b) if b < 128 else "?" for b in data)


def tile_shape(payload_length: int) -> tuple:
    """Square tile holding one frame of bits, 1 bit per Y pixel."""
    side = math.ceil(math.sqrt((payload_length + len(DELIMITER)) * 8))
    return side, side


def _open_input(path: str):
    try:
        container = av.open(path)
    except Exception as e:
        sys.exit(f"[!] Could not open video: {path} ({e})")
    if not container.streams.video:
        sys.exit(f"[!] No video stream in: {path}")
    return container


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed(video_path: str, payload: str, output_path: str) -> None:
    _require_av()
    th, tw = tile_shape(len(payload))
    bits = _text_to_bits(payload + DELIMITER)
    tile = np.zeros(th * tw, dtype=np.uint8)
    tile[: bits.size] = bits
    tile = tile.reshape(th, tw)

    inp = _open_input(video_path)
    vin = inp.streams.video[0]
    ain = inp.streams.audio[0] if inp.streams.audio else None
    w, h = vin.codec_context.width, vin.codec_context.height
    if w % 2 or h % 2:
        sys.exit(f"[!] Video size {w}x{h} must be even for YUV 4:2:0 / H.264.")
    if h < th or w < tw:
        sys.exit(f"[!] Video ({w}x{h}) is smaller than one tile ({tw}x{th}).")

    # Same tile repeated over the whole frame, computed once.
    pattern = np.tile(tile, (math.ceil(h / th), math.ceil(w / tw)))[:h, :w]

    out = av.open(output_path, "w")
    rate = vin.average_rate or vin.guessed_rate or 30
    vout = out.add_stream("libx264", rate=rate)
    vout.width, vout.height, vout.pix_fmt = w, h, "yuv420p"
    vout.time_base = vin.time_base
    vout.options = dict(X264_OPTIONS)
    aout = out.add_stream_from_template(ain) if ain is not None else None

    frames = 0
    header_ready = False

    def write_frame(frame) -> None:
        nonlocal frames, header_ready
        if not header_ready:
            # Keep phone videos upright: copy the rotation tag before the
            # MP4 header is written (it is written by the first mux call).
            rotation = getattr(frame, "rotation", 0) or 0
            if rotation:
                vout.set_display_rotation(rotation)
            header_ready = True

        # (h*3/2, w) array: rows [0, h) are the Y plane, the rest is U and V.
        yuv = frame.to_ndarray(format="yuv420p")
        # THE LSB REPLACEMENT, same bitwise math as the image pixel mode:
        # clear the LSB with & 0xFE, then OR in the payload bit.
        yuv[:h] = (yuv[:h] & 0xFE) | pattern
        new = av.VideoFrame.from_ndarray(yuv, format="yuv420p")
        new.pts, new.time_base = frame.pts, frame.time_base
        out.mux(vout.encode(new))
        frames += 1

    streams = [vin] + ([ain] if ain is not None else [])
    pending_audio = []    # audio that arrives before the header is written
    try:
        for packet in inp.demux(*streams):
            if packet.stream is vin:
                for frame in packet.decode():
                    write_frame(frame)
            elif packet.dts is not None:          # audio: copy unchanged
                packet.stream = aout
                pending_audio.append(packet)
            if header_ready and pending_audio:
                for p in pending_audio:
                    out.mux(p)
                pending_audio.clear()
        out.mux(vout.encode())                    # flush the encoder
        for p in pending_audio:
            out.mux(p)
    finally:
        out.close()
        inp.close()

    if frames == 0:
        sys.exit("[!] The video has no decodable frames.")

    copies = (h // th) * (w // tw)
    print(f"[+] Mode              : Video (Y-plane LSB, lossless H.264 MP4)")
    print(f"[+] Embedded payload  : {payload}{DELIMITER}")
    print(f"[+] Video size        : {w} x {h}, {frames} frames")
    print(f"[+] Tile size         : {tw} x {th} px "
          f"({bits.size} bits used of {th * tw})")
    print(f"[+] Full copies       : {copies} per frame, {copies * frames} total")
    print(f"[+] Audio             : {'copied unchanged' if ain else 'none'}")
    print(f"[+] Saved             : {output_path}")
    print("[i] Send it as a FILE (USB, WhatsApp document, e-mail attachment).")
    print("[i] Re-encoding it (WhatsApp video mode, editing) destroys the mark.")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract(video_path: str, payload_length: int, max_frames: int = 0) -> int:
    _require_av()
    th, tw = tile_shape(payload_length)
    frame_bits = (payload_length + len(DELIMITER)) * 8

    inp = _open_input(video_path)
    vin = inp.streams.video[0]
    w, h = vin.codec_context.width, vin.codec_context.height
    if h < th or w < tw:
        sys.exit(f"[!] Video ({w}x{h}) is smaller than one tile ({tw}x{th}).")

    # Votes per tile cell, summed over every pixel of every frame. Padding to
    # a multiple of the tile lets partial tiles at the edges vote too.
    ph, pw = math.ceil(h / th) * th, math.ceil(w / tw) * tw
    shape4 = (ph // th, th, pw // tw, tw)
    mask = np.zeros((ph, pw), np.int64)
    mask[:h, :w] = 1
    total_one_frame = mask.reshape(shape4).sum(axis=(0, 2))
    ones = np.zeros((th, tw), np.int64)
    lsb = np.zeros((ph, pw), np.uint8)

    frames = 0
    try:
        for frame in inp.decode(vin):
            y = frame.to_ndarray(format="yuv420p")[:h]
            lsb[:h, :w] = y & 1
            ones += lsb.reshape(shape4).sum(axis=(0, 2), dtype=np.int64)
            frames += 1
            if max_frames and frames >= max_frames:
                break
    except Exception as e:
        print(f"[!] Decoding stopped early: {e}")
    finally:
        inp.close()

    if frames == 0:
        sys.exit("[!] The video has no decodable frames.")
    total = total_one_frame * frames

    # Try every grid alignment (handles crops), keep the valid one.
    candidates = []
    for dy in range(th):
        for dx in range(tw):
            v1 = np.roll(ones, (-dy, -dx), axis=(0, 1)).reshape(-1)[:frame_bits]
            vn = np.roll(total, (-dy, -dx), axis=(0, 1)).reshape(-1)[:frame_bits]
            bits = (2 * v1 > vn).astype(np.uint8)       # majority vote
            confidence = float(np.abs(2.0 * v1 / vn - 1.0).mean())
            text = _bits_to_text(bits)
            payload, tail = text[:payload_length], text[payload_length:]
            valid = tail == DELIMITER and payload.isprintable()
            candidates.append((valid, confidence, payload, dy, dx))

    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    valid, confidence, payload, dy, dx = candidates[0]

    print(f"[+] Mode              : Video (Y-plane LSB)")
    print(f"[+] Video size        : {w} x {h}, {frames} frames read")
    print(f"[+] Tile size         : {tw} x {th} px, "
          f"{(h // th) * (w // tw) * frames} full copies")
    print(f"[+] Grid alignment    : dy={dy}, dx={dx}")
    print(f"[+] Vote confidence   : {confidence * 100:.1f} %")

    if not valid:
        print(f"[-] No alignment produced the delimiter {DELIMITER!r}.")
        print(f"[-] Best guess (unreliable): {payload!r}")
        print("[-] The video was probably re-encoded (sent as a normal video, "
              "edited, screen-recorded) or never watermarked.")
        return 1

    print(f"[+] Extracted payload : {payload}")
    if payload.isdigit() and len(payload) == 15:
        print("[+] Format            : valid 15-digit IMEI format")
    return 0
