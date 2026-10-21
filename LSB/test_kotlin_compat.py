#!/usr/bin/env python3
"""
test_kotlin_compat.py - Check that the Android app's Kotlin chain code
(android/core) and this Python code read and write IDENTICAL chains.

1. Makes test photos (4:2:0, 4:2:2, 4:4:4, grayscale, odd sizes, restart
   markers, EXIF, progressive) and videos, and stamps them with Python.
2. Runs the Kotlin tests on them (gradlew :core:test). Kotlin checks it
   computes the same Image IDs, reads Python's chains, and stamps its own
   copies.
3. Checks Kotlin's output with Python: chain readable, and the luminance
   coefficients exactly equal to what Python wrote.

Usage:  python test_kotlin_compat.py        (needs Java; Gradle is downloaded)
"""

import os
import shutil
import subprocess
import sys

import cv2
import numpy as np

import chain_imei as ci
import chain_payload as cp
import jpeg_dct

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


def add_exif(path):
    b = open(path, "rb").read()
    exif = b"Exif\x00\x00MM\x00\x2a\x00\x00\x00\x08\x00\x01\x01\x12\x00\x03\x00\x00\x00\x01\x00\x06\x00\x00\x00\x00\x00\x00"
    seg = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    open(path, "wb").write(b[:2] + seg + b[2:])


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
    S = cv2.IMWRITE_JPEG_SAMPLING_FACTOR
    cases = {
        "s420.jpg": (img, [cv2.IMWRITE_JPEG_QUALITY, 90, S, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_420]),
        "s422.jpg": (img, [cv2.IMWRITE_JPEG_QUALITY, 90, S, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_422]),
        "s444.jpg": (img, [cv2.IMWRITE_JPEG_QUALITY, 90, S, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]),
        "gray.jpg": (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), [cv2.IMWRITE_JPEG_QUALITY, 90]),
        "odd.jpg": (textured(301, 437, 2), [cv2.IMWRITE_JPEG_QUALITY, 85]),
        "restart.jpg": (img, [cv2.IMWRITE_JPEG_QUALITY, 90, cv2.IMWRITE_JPEG_RST_INTERVAL, 7]),
        "exif.jpg": (img, [cv2.IMWRITE_JPEG_QUALITY, 95]),
        "large_12mp.jpg": (cv2.resize(textured(750, 1000, 4), (4000, 3000), interpolation=cv2.INTER_CUBIC), [cv2.IMWRITE_JPEG_QUALITY, 92]),
        "progressive.jpg": (img, [cv2.IMWRITE_JPEG_QUALITY, 90, cv2.IMWRITE_JPEG_PROGRESSIVE, 1]),
        "progressive_gray.jpg": (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), [cv2.IMWRITE_JPEG_QUALITY, 80, cv2.IMWRITE_JPEG_PROGRESSIVE, 1]),
        "progressive_rst.jpg": (textured(301, 437, 3), [cv2.IMWRITE_JPEG_QUALITY, 92, cv2.IMWRITE_JPEG_PROGRESSIVE, 1, cv2.IMWRITE_JPEG_RST_INTERVAL, 5]),
    }
    lines = []
    for name, (im, params) in cases.items():
        cv2.imwrite(p(name), im, params)
        if name == "exif.jpg":
            add_exif(p(name))
        lines.append(stamp_entry(name, "image"))

    real = os.path.join(HERE, "testimage.jpeg")
    if os.path.exists(real):
        shutil.copyfile(real, p("real_photo.jpg"))
        lines.append(stamp_entry("real_photo.jpg", "image"))

    make_video(p("clip.mp4"))
    lines.append(stamp_entry("clip.mp4", "video"))
    real_video = os.path.join(HERE, "test_video.mp4")
    if os.path.exists(real_video):
        shutil.copyfile(real_video, p("real_video.mp4"))
        lines.append(stamp_entry("real_video.mp4", "video"))

    with open(p("manifest.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return lines


def stamp_entry(name, kind, unsupported=False):
    if unsupported:
        return f"{name}|{kind}|{name}|||1|{IMEI}|{TS}"
    if kind == "image":
        cid = ci.content_id(jpeg_dct.load_dct(p(name))[0]).hex()
    else:
        import mp4_chain
        cid = mp4_chain.content_id(p(name)).hex()
    ci.append_hop(p(name), p("py_" + name), DEV_A, IMEI, cp.ROLE_SEND, TS)
    return f"{name}|{kind}|{name}|py_{name}|{cid}|0|{IMEI}|{TS}"


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
        name, kind, _, _, _, unsupported, _, _ = line.split("|")
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
        a, b, o = (jpeg_dct.load_dct(x)[0] for x in (kt, py, p(name)))
        check(f"{name}: luminance coefficients identical to Python's",
              np.array_equal(a.Y, b.Y))
        same_chroma = all(
            (getattr(a, c) is None and getattr(o, c) is None)
            or np.array_equal(getattr(a, c), getattr(o, c)) for c in ("Cb", "Cr"))
        check(f"{name}: chroma untouched", same_chroma)
        if name == "exif.jpg":
            check(f"{name}: EXIF kept", b"Exif\x00\x00MM" in open(kt, "rb").read())
        size_in, size_out = os.path.getsize(p(name)), os.path.getsize(kt)
        check(f"{name}: size change {100 * (size_out - size_in) / size_in:+.2f}% (< 2%)",
              abs(size_out - size_in) < 0.02 * size_in)


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
