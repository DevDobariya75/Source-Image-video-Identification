#!/usr/bin/env python3
"""
test_chain.py - End-to-end tests for the forwarding chain (chain_imei.py).

Simulates photos (PNG, RGB pixel LSB, 3 and 6 bits per pixel) and videos
(MP4 box) travelling between phones over Bluetooth (a byte-for-byte copy)
and checks that the chain, and the tree it forms, can be recovered:

        A                 A sends to B and D, B sends to C,
       / \                C and D "claim" the photo on arrival.
      B   D
      |
      C

Usage:  python test_chain.py
"""

import os
import shutil
import sys

import cv2
import numpy as np

import chain_imei as ci
import chain_payload as cp
import pixel_chain

WORK = "test_out/chain"
IMEI = {"A": "356938035643809", "B": "490154203237518",
        "C": "352099001761481", "D": "013467007045768"}
DEV = {k: cp.parse_device_id(f"phone-{k}") for k in IMEI}

failures = 0


def check(name: str, cond: bool) -> None:
    global failures
    print(f"  [{'OK' if cond else 'FAIL'}] {name}")
    if not cond:
        failures += 1


def path(name: str) -> str:
    return os.path.join(WORK, name)


def bluetooth(src: str, dst: str) -> str:
    """Bluetooth (OPP) transfers the file unchanged: a byte-for-byte copy."""
    shutil.copyfile(src, path(dst))
    return path(dst)


def hop(src: str, dst: str, phone: str, role: int = cp.ROLE_SEND, **kw) -> dict:
    return ci.append_hop(src, path(dst), DEV[phone], IMEI[phone], role, **kw)


def imeis(res) -> list:
    return [h.imei for h in res.chain.hops]


def make_cover() -> str:
    """A 1024x768 textured photo-like JPEG (quality 90), like a camera photo."""
    rng = np.random.default_rng(3)
    h, w = 768, 1024
    y, x = np.mgrid[0:h, 0:w]
    img = np.dstack([x * 255 // w, y * 255 // h, (x + y) * 255 // (w + h)])
    img = img.astype(np.int16) + rng.integers(-30, 30, (h, w, 3))
    img = cv2.GaussianBlur(np.clip(img, 0, 255).astype(np.uint8), (3, 3), 0)
    cv2.imwrite(path("cover.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return path("cover.jpg")


def make_video(name: str, faststart: bool) -> str:
    """A 2 s, 320x240 lossy H.264 MP4 with an AAC audio track, like a phone clip."""
    import av
    rng = np.random.default_rng(5)
    out = av.open(path(name), "w", options={"movflags": "faststart"} if faststart else {})
    v = out.add_stream("libx264", rate=25)
    v.width, v.height, v.pix_fmt = 320, 240, "yuv420p"
    a = out.add_stream("aac", rate=44100)
    base = rng.integers(0, 256, (240, 320, 3), dtype=np.uint8)
    for i in range(50):
        frame = av.VideoFrame.from_ndarray(np.roll(base, i * 3, axis=1), format="rgb24")
        out.mux(v.encode(frame))
    samples = np.zeros((1, 1024), dtype=np.float32)
    for i in range(86):
        af = av.AudioFrame.from_ndarray(samples, format="fltp", layout="mono")
        af.sample_rate, af.pts = 44100, i * 1024
        out.mux(a.encode(af))
    out.mux(v.encode())
    out.mux(a.encode())
    out.close()
    return path(name)


def decode_video(p: str):
    """(number of video frames, hash of all decoded pixels, audio packet count)."""
    import av
    import hashlib
    h = hashlib.sha256()
    n = 0
    with av.open(p) as c:
        for f in c.decode(video=0):
            h.update(f.to_ndarray(format="yuv420p").tobytes())
            n += 1
    with av.open(p) as c:
        audio = sum(1 for _ in c.demux(audio=0))
    return n, h.hexdigest(), audio


def test_video() -> None:
    import mp4_chain
    for faststart in (False, True):
        layout = "moov first (faststart)" if faststart else "moov last (phone default)"
        print(f"8. Video chain, {layout}")
        src = make_video(f"clip_{int(faststart)}.mp4", faststart)
        before = decode_video(src)
        check("plain video has no chain", ci.read_chain(src) is None)

        hop(src, f"vA_{int(faststart)}.mp4", "A")
        b_in = bluetooth(path(f"vA_{int(faststart)}.mp4"), f"vB_in_{int(faststart)}.mp4")
        hop(b_in, f"vAB_{int(faststart)}.mp4", "B")
        hop(path(f"vAB_{int(faststart)}.mp4"), f"vABC_{int(faststart)}.mp4", "C", cp.ROLE_CLAIM)
        final = path(f"vABC_{int(faststart)}.mp4")
        res = ci.read_chain(final)
        check("chain A -> B -> C read back",
              res is not None and res.media == "video"
              and imeis(res) == [IMEI["A"], IMEI["B"], IMEI["C"]])
        check("file grew by exactly one 2040-byte box (complete chain)",
              os.path.getsize(final) - os.path.getsize(src) == mp4_chain.BOX_SIZE)
        check("video and audio decode identically (no re-encoding)",
              decode_video(final) == before)
        with open(src, "rb") as f_src, open(final, "rb") as f_out:
            data_src, data_out = f_src.read(), f_out.read()
        check("original bytes untouched (only appended to)",
              data_out[:len(data_src)] == data_src)
        check("chain is not stored as readable text",
              IMEI["A"].encode() not in data_out)

        hop(src, f"vA_again_{int(faststart)}.mp4", "A")
        hop(path(f"vA_again_{int(faststart)}.mp4"), f"vAD_{int(faststart)}.mp4", "D", cp.ROLE_CLAIM)
        res_d = ci.read_chain(path(f"vAD_{int(faststart)}.mp4"))
        check("branch A -> D has the same image ID",
              res_d is not None and res_d.chain.image_id == res.chain.image_id)

    print("9. Video edge cases")
    # A last box with size 0 means "runs to end of file"; appending must fix it.
    src = path("clip_1.mp4")                    # faststart: mdat is the last box
    with open(src, "rb") as f:
        boxes = mp4_chain.top_level_boxes(f)
    open_ended = path("clip_open_ended.mp4")
    shutil.copyfile(src, open_ended)
    with open(open_ended, "r+b") as f:
        f.seek(boxes[-1]["offset"])
        f.write(b"\x00\x00\x00\x00")
    before = decode_video(open_ended)
    hop(open_ended, "v_open_ended_A.mp4", "A")
    out = path("v_open_ended_A.mp4")
    check("open-ended last box: chain readable",
          ci.read_chain(out) is not None and imeis(ci.read_chain(out)) == [IMEI["A"]])
    check("open-ended last box: video still decodes identically",
          decode_video(out) == before)

    import av
    with av.open(path("vABC_0.mp4")) as i, av.open(path("v_reencoded.mp4"), "w") as o:
        s = o.add_stream("libx264", rate=25)
        s.width, s.height, s.pix_fmt = 320, 240, "yuv420p"
        for fr in i.decode(video=0):
            o.mux(s.encode(av.VideoFrame.from_ndarray(fr.to_ndarray(format="yuv420p"),
                                                      format="yuv420p")))
        o.mux(s.encode())
    check("control: re-encoding (e.g. WhatsApp video) loses the chain",
          ci.read_chain(path("v_reencoded.mp4")) is None)

    try:
        ci.append_hop(path("vABC_0.mp4"), path("wrong.jpg"), DEV["D"], None)
        check("a video can't be saved as .jpg", False)
    except ValueError:
        check("a video can't be saved as .jpg", True)
    with open(path("not_media.bin"), "wb") as f:
        f.write(b"hello world" * 100)
    check("unknown file type: no chain", ci.read_chain(path("not_media.bin")) is None)

    src = path("clip_0.mp4")
    for i in range(cp.MAX_HOPS):
        ci.append_hop(src, path("v_full.mp4"), cp.parse_device_id(f"dev{i}"), None)
        src = path("v_full.mp4")
    res = ci.read_chain(src)
    check(f"video: {cp.MAX_HOPS} hops fit in the one box",
          res is not None and len(res.chain.hops) == cp.MAX_HOPS)
    check("video: file still grew by one box only",
          os.path.getsize(src) - os.path.getsize(path("clip_0.mp4")) == mp4_chain.BOX_SIZE)

    # A video stamped with the old 8-hop format (200-byte box): the old box is
    # turned into a "free" box and a new one is appended.
    old = path("v_old_format.mp4")
    shutil.copyfile(path("clip_0.mp4"), old)
    with open(old, "ab") as f:
        f.write((200).to_bytes(4, "big") + b"uuid" + mp4_chain.CHAIN_UUID + bytes(176))
    hop(old, "v_old_format_A.mp4", "A")
    with open(path("v_old_format_A.mp4"), "rb") as f:
        types = [b["type"] for b in mp4_chain.top_level_boxes(f)]
    check("old-format box replaced (renamed to 'free', new box appended)",
          types[-2:] == [b"free", b"uuid"]
          and imeis(ci.read_chain(path("v_old_format_A.mp4"))) == [IMEI["A"]])


def main() -> int:
    os.makedirs(WORK, exist_ok=True)
    cover = make_cover()

    print("1. Payload format")
    chain = cp.Chain.new()
    chain.hops.append(cp.make_hop(DEV["A"], IMEI["A"], timestamp=1700000000))
    chain.hops.append(cp.make_hop(DEV["B"], None, cp.ROLE_CLAIM, 1700000100))
    frame = cp.pack(chain)
    back = cp.unpack(frame)
    check(f"frame is {cp.FRAME_BYTES} bytes with room for {cp.MAX_HOPS} hops",
          len(frame) == cp.FRAME_BYTES == 2016 and cp.MAX_HOPS == 100)
    check("pack/unpack round trip", back == chain)
    damaged = bytearray(frame)
    damaged[20] ^= 0x01
    try:
        cp.unpack(bytes(damaged))
        check("CRC rejects a flipped bit", False)
    except cp.ChainError:
        check("CRC rejects a flipped bit", True)
    check("ANDROID_ID hex is used as-is",
          cp.parse_device_id("9774d56d682e549c") == bytes.fromhex("9774d56d682e549c"))
    check("IMEI Luhn check", cp.luhn_ok(IMEI["A"]) and not cp.luhn_ok("356938035643808"))
    check("tile sides: 74 px (3 bits/pixel), 52 px (6 bits/pixel)",
          pixel_chain.tile_side(3) == 74 and pixel_chain.tile_side(6) == 52)

    print("2. Unstamped photo")
    check("no chain in a plain JPEG", ci.read_chain(cover) is None)
    png_cover = path("cover.png")
    pixel_chain.save_png(png_cover, pixel_chain.load_rgb(cover))
    check("no chain in a plain PNG", ci.read_chain(png_cover) is None)

    original = pixel_chain.load_rgb(cover)
    for bpp, max_delta in ((3, 1), (6, 3)):
        m = f"_{bpp}"
        print(f"3. Chain A -> B -> C over Bluetooth, {bpp} bits/pixel")
        r = hop(cover, f"A_out{m}.png", "A", bits_per_pixel=bpp)
        check("JPEG input converted once to PNG", r["converted"])
        at_b = bluetooth(path(f"A_out{m}.png"), f"B_in{m}.png")
        hop(at_b, f"B_out{m}.png", "B")                   # keeps the photo's mode
        at_c = bluetooth(path(f"B_out{m}.png"), f"C_in{m}.png")
        hop(at_c, f"C_final{m}.png", "C", cp.ROLE_CLAIM)
        res_c = ci.read_chain(path(f"C_final{m}.png"))
        check("chain found", res_c is not None)
        check("order is A, B, C",
              res_c is not None and imeis(res_c) == [IMEI["A"], IMEI["B"], IMEI["C"]])
        check("device IDs match",
              res_c is not None and [h.device_id for h in res_c.chain.hops]
              == [DEV["A"], DEV["B"], DEV["C"]])
        check("roles are send, send, claim",
              res_c is not None and [h.role for h in res_c.chain.hops]
              == [cp.ROLE_SEND, cp.ROLE_SEND, cp.ROLE_CLAIM])
        check(f"mode detected: {bpp} bits/pixel",
              res_c is not None and res_c.bits_per_pixel == bpp)
        image_id = ci.read_chain(path(f"A_out{m}.png")).chain.image_id
        check("image ID stays the same along the chain",
              res_c is not None and res_c.chain.image_id == image_id)
        out = pixel_chain.load_rgb(path(f"C_final{m}.png"))
        diff = np.abs(out.astype(int) - original.astype(int))
        check(f"every R/G/B value changed by at most {max_delta}", int(diff.max()) <= max_delta)
        check(f"only the lowest {bpp // 3} bit(s) differ",
              np.array_equal(out >> (bpp // 3), original >> (bpp // 3)))
        mse = float((diff ** 2).mean())
        print(f"       PSNR {10 * np.log10(255 ** 2 / mse):.1f} dB")

        if bpp == 3:
            id_3 = image_id
        else:
            check("same original gives the same image ID in both modes", image_id == id_3)

    m = "_3"
    image_id = id_3
    res_c = ci.read_chain(path(f"C_final{m}.png"))

    print("4. Branch: A also sends its copy to D")
    r = hop(path(f"A_out{m}.png"), "A_out_again.png", "A")
    check("A is not added twice when it sends again", not r["added"])
    at_d = bluetooth(path("A_out_again.png"), "D_in.png")
    hop(at_d, "D_final.png", "D", cp.ROLE_CLAIM)
    res_d = ci.read_chain(path("D_final.png"))
    check("D's copy reads A -> D",
          res_d is not None and imeis(res_d) == [IMEI["A"], IMEI["D"]])
    check("D's copy has the same image ID as C's (same tree)",
          res_d is not None and res_d.chain.image_id == image_id)
    hop(cover, "A_fresh.png", "A")
    check("stamping the original again gives the same image ID",
          ci.read_chain(path("A_fresh.png")).chain.image_id == image_id)
    r = hop(path(f"A_out{m}.png"), "A_repeat.png", "A", allow_repeat=True)
    check("allow_repeat adds the same device again", r["added"]
          and len(r["chain"].hops) == 2)

    print("5. Switching mode between hops")
    hop(path("C_final_3.png"), "C_to_6.png", "D", bits_per_pixel=6)
    res = ci.read_chain(path("C_to_6.png"))
    check("3-bit photo re-stamped in 6-bit mode: whole chain kept",
          res is not None and res.bits_per_pixel == 6
          and imeis(res) == [IMEI["A"], IMEI["B"], IMEI["C"], IMEI["D"]])
    hop(path("C_final_6.png"), "C_to_3.png", "D", bits_per_pixel=3)
    res = ci.read_chain(path("C_to_3.png"))
    check("6-bit photo re-stamped in 3-bit mode: whole chain kept",
          res is not None and res.bits_per_pixel == 3 and len(res.chain.hops) == 4)

    print("6. Damage")
    for bpp in (3, 6):
        rgb = pixel_chain.load_rgb(path(f"C_final_{bpp}.png"))
        side = pixel_chain.tile_side(bpp)
        crop = rgb[37:37 + side + 3, 91:91 + 2 * side]
        res = pixel_chain.decode(crop)
        check(f"{bpp} bits/pixel: survives a {crop.shape[1]}x{crop.shape[0]} crop "
              "at a tile-misaligned offset",
              res is not None and [h.imei for h in res.chain.hops] == imeis(res_c))
        check(f"{bpp} bits/pixel: crop smaller than one tile can't be read",
              pixel_chain.decode(rgb[:side - 1, :side - 1]) is None)
        rng = np.random.default_rng(9)
        noisy = rgb ^ (rng.random(rgb.shape) < 0.10).astype(np.uint8)
        res = pixel_chain.decode(noisy)
        check(f"{bpp} bits/pixel: survives 10% of LSBs flipped (majority vote)",
              res is not None and [h.imei for h in res.chain.hops] == imeis(res_c))
    img = cv2.imread(path("C_final_3.png"))
    cv2.imwrite(path("recompressed.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    pixel_chain.save_png(path("recompressed.png"), pixel_chain.load_rgb(path("recompressed.jpg")))
    check("control: saving as JPEG (e.g. WhatsApp photo) destroys the chain",
          ci.read_chain(path("recompressed.png")) is None)
    h, w = img.shape[:2]
    small = cv2.resize(img, (w * 9 // 10, h * 9 // 10), interpolation=cv2.INTER_AREA)
    cv2.imwrite(path("resized.png"), small)
    check("control: resizing to 90% destroys the chain (LSBs are recomputed)",
          ci.read_chain(path("resized.png")) is None)

    print("7. Limits")
    src = cover
    for i in range(cp.MAX_HOPS):
        ci.append_hop(src, path("full.png"), cp.parse_device_id(f"dev{i}"), None,
                      bits_per_pixel=3 if i % 2 else 6)
        src = path("full.png")
    full = ci.read_chain(src)
    check(f"{cp.MAX_HOPS} hops fit in a photo",
          full is not None and len(full.chain.hops) == cp.MAX_HOPS)
    try:
        ci.append_hop(src, path("overfull.png"), cp.parse_device_id("dev-x"), None)
        check(f"hop {cp.MAX_HOPS + 1} is refused", False)
    except cp.ChainError:
        check(f"hop {cp.MAX_HOPS + 1} is refused", True)
    try:
        ci.append_hop(cover, path("wrong.jpg"), DEV["A"], None)
        check("a photo can't be saved as .jpg", False)
    except ValueError:
        check("a photo can't be saved as .jpg", True)
    tiny = path("tiny.png")
    cv2.imwrite(tiny, cv2.imread(cover)[:60, :60])
    try:
        ci.append_hop(tiny, path("tiny_out.png"), DEV["A"], None)
        check("a photo smaller than one tile is refused", False)
    except ValueError:
        check("a photo smaller than one tile is refused", True)

    test_video()

    print()
    print("ALL CHAIN TESTS PASSED" if failures == 0
          else f"{failures} CHAIN TEST(S) FAILED")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
