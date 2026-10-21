#!/usr/bin/env python3
"""
test_chain.py - End-to-end tests for the forwarding chain (chain_imei.py).

Simulates photos travelling between phones over Bluetooth (a byte-for-byte
copy) and checks that the chain, and the tree it forms, can be recovered:

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
import jpeg_dct

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
    """A 1024x768 textured photo-like JPEG (quality 90)."""
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
        print(f"7. Video chain, {layout}")
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
        check("file grew by exactly one 200-byte box",
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

    print("8. Video edge cases")
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


def main() -> int:
    os.makedirs(WORK, exist_ok=True)
    cover = make_cover()

    print("1. Payload format")
    chain = cp.Chain.new()
    chain.hops.append(cp.make_hop(DEV["A"], IMEI["A"], timestamp=1700000000))
    chain.hops.append(cp.make_hop(DEV["B"], None, cp.ROLE_CLAIM, 1700000100))
    frame = cp.pack(chain)
    back = cp.unpack(frame)
    check(f"frame is {cp.FRAME_BYTES} bytes", len(frame) == cp.FRAME_BYTES)
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

    print("2. Unstamped photo")
    check("no chain in a plain JPEG", ci.read_chain(cover) is None)

    print("3. Chain A -> B -> C over Bluetooth")
    hop(cover, "A_out.jpg", "A")
    at_b = bluetooth(path("A_out.jpg"), "B_in.jpg")
    hop(at_b, "B_out.jpg", "B")
    at_c = bluetooth(path("B_out.jpg"), "C_in.jpg")
    hop(at_c, "C_final.jpg", "C", cp.ROLE_CLAIM)
    res_c = ci.read_chain(path("C_final.jpg"))
    check("chain found", res_c is not None)
    check("order is A, B, C",
          res_c is not None and imeis(res_c) == [IMEI["A"], IMEI["B"], IMEI["C"]])
    check("device IDs match",
          res_c is not None and [h.device_id for h in res_c.chain.hops]
          == [DEV["A"], DEV["B"], DEV["C"]])
    check("roles are send, send, claim",
          res_c is not None and [h.role for h in res_c.chain.hops]
          == [cp.ROLE_SEND, cp.ROLE_SEND, cp.ROLE_CLAIM])
    image_id = ci.read_chain(path("A_out.jpg")).chain.image_id
    check("image ID stays the same along the chain",
          res_c is not None and res_c.chain.image_id == image_id)

    jpeg = jpeg_dct.load_dct(cover)[0]
    out = jpeg_dct.load_dct(path("C_final.jpg"))[0]
    check("no re-compression (DC coefficients and chroma untouched)",
          np.array_equal(jpeg.Y[:, :, 0, 0], out.Y[:, :, 0, 0])
          and np.array_equal(jpeg.Cb, out.Cb) and np.array_equal(jpeg.Cr, out.Cr))
    diff = np.abs(jpeg.Y.astype(int) - out.Y.astype(int))
    check("every Y coefficient changed by at most 1", int(diff.max()) <= 1)

    print("4. Branch: A also sends its copy to D")
    r = hop(path("A_out.jpg"), "A_out_again.jpg", "A")
    check("A is not added twice when it sends again", not r["added"])
    at_d = bluetooth(path("A_out_again.jpg"), "D_in.jpg")
    hop(at_d, "D_final.jpg", "D", cp.ROLE_CLAIM)
    res_d = ci.read_chain(path("D_final.jpg"))
    check("D's copy reads A -> D",
          res_d is not None and imeis(res_d) == [IMEI["A"], IMEI["D"]])
    check("D's copy has the same image ID as C's (same tree)",
          res_d is not None and res_d.chain.image_id == image_id)
    hop(cover, "A_fresh.jpg", "A")
    check("stamping the original again gives the same image ID",
          ci.read_chain(path("A_fresh.jpg")).chain.image_id == image_id)
    r = hop(path("A_out.jpg"), "A_repeat.jpg", "A", allow_repeat=True)
    check("allow_repeat adds the same device again", r["added"]
          and len(r["chain"].hops) == 2)

    print("5. Damage")
    Y = jpeg_dct.load_dct(path("C_final.jpg"))[0].Y
    res = ci._decode(Y[5:5 + 40, 3:3 + 50])      # 400x320 px crop, off-grid
    check("survives a 400x320 crop at a tile-misaligned offset",
          res is not None and imeis(res) == imeis(res_c))
    rng = np.random.default_rng(9)
    noisy = Y.copy()
    flip = (rng.random(noisy.shape) < 0.10) & (np.abs(noisy) >= 2)
    noisy[flip] = np.sign(noisy[flip]) * (np.abs(noisy[flip]) ^ 1)
    res = ci._decode(noisy)
    check("survives 10% of coefficient LSBs flipped (majority vote)",
          res is not None and imeis(res) == imeis(res_c))
    img = cv2.imread(path("C_final.jpg"))
    cv2.imwrite(path("recompressed.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    check("control: re-compression (e.g. WhatsApp photo) destroys the chain",
          ci.read_chain(path("recompressed.jpg")) is None)

    print("6. Limits")
    src = cover
    for i in range(cp.MAX_HOPS):
        ci.append_hop(src, path("full.jpg"), cp.parse_device_id(f"dev{i}"), None)
        src = path("full.jpg")
    check(f"{cp.MAX_HOPS} hops fit",
          len(ci.read_chain(src).chain.hops) == cp.MAX_HOPS)
    try:
        ci.append_hop(src, path("overfull.jpg"), cp.parse_device_id("dev-x"), None)
        check("a 9th hop is refused", False)
    except cp.ChainError:
        check("a 9th hop is refused", True)
    png = path("cover.png")
    cv2.imwrite(png, cv2.imread(cover))
    r = ci.append_hop(png, path("from_png.jpg"), DEV["A"], IMEI["A"])
    check("a non-JPEG input is converted once and stamped",
          r["recompressed"] and ci.read_chain(path("from_png.jpg")) is not None)

    test_video()

    print()
    print("ALL CHAIN TESTS PASSED" if failures == 0
          else f"{failures} CHAIN TEST(S) FAILED")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
