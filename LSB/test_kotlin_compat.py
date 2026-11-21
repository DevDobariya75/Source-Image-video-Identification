#!/usr/bin/env python3
"""
test_kotlin_compat.py - Check that the Android app's Kotlin chain code
(android/core) and this Python code read and write IDENTICAL chains.

1. Makes test photos (PNG: RGB, RGBA, grayscale, palette, odd sizes, a
   12 MP photo) and videos, and stamps them with Python in both photo modes
   (3 and 6 bits per pixel). Also adds files Kotlin must refuse (16-bit and
   interlaced PNG, JPEG), which the app converts first.
2. Runs the Kotlin tests on them (gradlew :core:test). Kotlin checks it
   computes the same Image IDs, reads Python's chains, and stamps its own
   copies.
3. Checks Kotlin's output with Python: chain readable, and the stamped
   pixels exactly equal to the ones Python wrote.

Usage:  python test_kotlin_compat.py        (needs Java; Gradle is downloaded)
"""

import os
import shutil
import subprocess
import sys
import zlib

import cv2
import numpy as np

import chain_imei as ci
import chain_payload as cp
import pixel_chain

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "test_out", "kotlin_compat")
ANDROID = os.path.join(HERE, "..", "android")
TS = 1792500000
IMEI = "356938035643809"
DEV_A = cp.parse_device_id("phone-A")
DEV_B = cp.parse_device_id("phone-B")
failures = 0


def check(name, cond):
    global failures
    print(f"  [{'OK' if cond else 'FAIL'}] {name}")
    if not cond:
        failures += 1


def p(name):
    return os.path.join(OUT, name)


def textured(h, w, seed=1):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    img = np.dstack([x * 255 // w, y * 255 // h, (x + y) * 255 // (w + h)]).astype(np.int16)
    img += rng.integers(-40, 40, (h, w, 3))
    return cv2.GaussianBlur(np.clip(img, 0, 255).astype(np.uint8), (3, 3), 0)


def png_chunk(kind, data):
    return (len(data).to_bytes(4, "big") + kind + data
            + zlib.crc32(kind + data).to_bytes(4, "big"))


def write_palette_png(path, h, w, seed=7):
    """8-bit palette PNG (OpenCV can't write one)."""
    rng = np.random.default_rng(seed)
    pal = rng.integers(0, 256, (256, 3), dtype=np.uint8)
    idx = rng.integers(0, 256, (h, w), dtype=np.uint8)
    raw = b"".join(b"\x00" + idx[y].tobytes() for y in range(h))
    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 3, 0, 0, 0])
    with open(path, "wb") as f:
        f.write(pixel_chain.PNG_SIGNATURE + png_chunk(b"IHDR", ihdr)
                + png_chunk(b"PLTE", pal.tobytes()) + png_chunk(b"IDAT", zlib.compress(raw))
                + png_chunk(b"IEND", b""))


def write_interlaced_png(path, h, w):
    """Header says Adam7-interlaced: Kotlin must refuse it (the app converts it)."""
    raw = b"".join(b"\x00" + bytes(3 * w) for _ in range(h))
    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 2, 0, 0, 1])
    with open(path, "wb") as f:
        f.write(pixel_chain.PNG_SIGNATURE + png_chunk(b"IHDR", ihdr)
                + png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b""))


def make_video(path):
    import av
    rng = np.random.default_rng(5)
    out = av.open(path, "w")
    s = out.add_stream("libx264", rate=25)
    s.width, s.height, s.pix_fmt = 320, 240, "yuv420p"
    base = rng.integers(0, 256, (240, 320, 3), dtype=np.uint8)
    for i in range(20):
        out.mux(s.encode(av.VideoFrame.from_ndarray(np.roll(base, i * 3, axis=1), format="rgb24")))
    out.mux(s.encode())
    out.close()


def build_vectors():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    img = textured(480, 640)
    rgba = np.dstack([img, np.full(img.shape[:2], 200, np.uint8)])
    images = {
        "rgb.png": img,
        "rgba.png": rgba,
        "gray.png": cv2.cvtColor(img, cv2.COLOR_BGR2GRAY),
        "odd.png": textured(301, 437, 2),
        "large_12mp.png": cv2.resize(textured(750, 1000, 4), (4000, 3000),
                                     interpolation=cv2.INTER_CUBIC),
    }
    lines = []
    for name, im in images.items():
        cv2.imwrite(p(name), im)
    write_palette_png(p("palette.png"), 233, 157)
    names = list(images) + ["palette.png"]
    for name in names:
        for bpp in pixel_chain.MODES:
            base, ext = os.path.splitext(name)
            mode_name = f"{base}_{bpp}bpp{ext}"
            shutil.copyfile(p(name), p(mode_name))
            lines.append(stamp_entry(mode_name, "image", bpp=bpp))

    real = os.path.join(HERE, "..", "test_image", "image1.jpeg")
    if os.path.exists(real):
        cv2.imwrite(p("real_photo.png"), cv2.imread(real))
        lines.append(stamp_entry("real_photo.png", "image", bpp=3))

    # Files the Kotlin library must refuse (the app converts them to PNG).
    cv2.imwrite(p("deep16.png"), (textured(80, 90).astype(np.uint16) * 257))
    write_interlaced_png(p("interlaced.png"), 80, 90)
    cv2.imwrite(p("photo.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    for name in ("deep16.png", "interlaced.png", "photo.jpg"):
        lines.append(stamp_entry(name, "image", unsupported=True))

    make_video(p("clip.mp4"))
    lines.append(stamp_entry("clip.mp4", "video"))
    real_video = os.path.join(HERE, "test_video.mp4")
    if os.path.exists(real_video):
        shutil.copyfile(real_video, p("real_video.mp4"))
        lines.append(stamp_entry("real_video.mp4", "video"))

    with open(p("manifest.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return lines


def stamp_entry(name, kind, unsupported=False, bpp=None):
    """Manifest line: name|kind|file|py_stamped|content_id|unsupported|imei|ts|bpp"""
    if unsupported:
        return f"{name}|{kind}|{name}|||1|{IMEI}|{TS}|"
    if kind == "image":
        cid = pixel_chain.content_id(pixel_chain.load_rgb(p(name))).hex()
    else:
        import mp4_chain
        cid = mp4_chain.content_id(p(name)).hex()
    ci.append_hop(p(name), p("py_" + name), DEV_A, IMEI, cp.ROLE_SEND, TS, bits_per_pixel=bpp)
    return f"{name}|{kind}|{name}|py_{name}|{cid}|0|{IMEI}|{TS}|{bpp or ''}"


def run_kotlin():
    gradlew = os.path.join(ANDROID, "gradlew.bat" if os.name == "nt" else "gradlew")
    cmd = [gradlew, "-PcoreOnly", f"-PvectorsDir={os.path.abspath(OUT)}",
           ":core:test", "--rerun-tasks", "--console=plain"]
    r = subprocess.run(cmd, cwd=ANDROID, capture_output=True, text=True)
    out = r.stdout + r.stderr
    shown = [l for l in out.splitlines()
             if any(k in l for k in ("OK", "unsupported", "PASSED", "FAILED", "Error", "error:",
                                     "expected", "BUILD"))]
    print("\n".join("    " + l.strip() for l in shown))
    return r.returncode == 0


def verify(lines):
    for line in lines:
        name, kind, _, _, _, unsupported, _, _, bpp = line.split("|")
        if unsupported == "1":
            continue
        kt, py, kt2 = p("kt_" + name), p("py_" + name), p("kt2_" + name)
        res = ci.read_chain(kt)
        check(f"{name}: Python reads the Kotlin-stamped file",
              res is not None and [h.imei for h in res.chain.hops] == [IMEI]
              and res.chain.hops[0].device_id == DEV_A and res.chain.hops[0].timestamp == TS)
        res2 = ci.read_chain(kt2)
        check(f"{name}: Python reads Kotlin's 2nd hop on a Python file",
              res2 is not None and [h.device_id for h in res2.chain.hops] == [DEV_A, DEV_B]
              and res2.chain.hops[1].role == cp.ROLE_CLAIM)
        if kind == "video":
            check(f"{name}: Kotlin and Python video files are byte-identical",
                  open(kt, "rb").read() == open(py, "rb").read())
            continue
        a, b = pixel_chain.load_rgb(kt), pixel_chain.load_rgb(py)
        check(f"{name}: pixels identical to Python's ({bpp} bits/pixel)", np.array_equal(a, b))
        check(f"{name}: read back in {bpp}-bit mode", res is not None and res.bits_per_pixel == int(bpp))


def main():
    print("1. Making test files and stamping them with Python")
    lines = build_vectors()
    print(f"  {len(lines)} files")
    print("2. Running the Kotlin tests")
    ok = run_kotlin()
    check("Kotlin tests passed", ok)
    if ok:
        print("3. Checking Kotlin's output with Python")
        verify(lines)
    print()
    print("ALL COMPATIBILITY TESTS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
