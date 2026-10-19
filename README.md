<div align="center">

# 🔍 Identification of Source Image / Video

**A digital-forensics toolkit for tracing images and videos back to the device that created them.**

![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-4.x-5C3EE8?logo=opencv&logoColor=white)
![NumPy](https://img.shields.io/badge/NumPy-1.21%2B-013243?logo=numpy&logoColor=white)
![Formats](https://img.shields.io/badge/formats-PNG%20%7C%20JPEG%20%7C%20MP4-2EA44F)
![Status](https://img.shields.io/badge/status-in%20development-orange)

</div>

---

## 📖 About

When an image or video is shared, cropped, or re-posted, its origin is quickly
lost. This project explores techniques that answer one forensic question:

> **"Which device produced this image, and can we still prove it after the image has been modified?"**

Each technique lives in its own folder, with its own scripts, tests and
guide. You can study, run and compare the techniques independently.

---

## 🧰 Techniques

| # | Technique | Approach | Status | Guide |
|---|-----------|----------|--------|-------|
| 1 | **LSB Steganography** | Hides a device ID (IMEI) in least significant bits, with redundancy and majority voting. Works on **images (PNG/BMP/TIFF/PPM), JPEG and MP4 video** | ✅ Implemented | [LSB/LSBGuide.md](LSB/LSBGuide.md) |
| … | *More techniques coming* | | 🚧 Planned | |

### LSB modes

The LSB technique picks a mode automatically from the **output file name**
when embedding, and from the **file contents** when extracting:

| Mode | Output | Where the bits go | File size |
|------|--------|-------------------|-----------|
| **Pixel** | `.png` `.bmp` `.tif` `.tiff` `.ppm` | Pixel LSBs (3 bits per pixel) | Large (lossless) |
| **JPEG** | `.jpg` `.jpeg` | LSBs of the JPEG's DCT coefficients, edited without re-compression | Same as the original JPEG |
| **Video** | `.mp4` | Brightness LSB of every frame, saved as lossless H.264 | 10–20× larger than the original |

Each technique and mode has different strengths. Compare them before choosing:

| Technique / mode | Survives byte-for-byte transfer (USB, WhatsApp *document*) | Survives crop | Survives re-compression / resize (WhatsApp *photo/video*) | Invisible |
|-----------|:---:|:---:|:---:|:---:|
| LSB pixel mode | ✅ | ✅ | ❌ | ✅ |
| LSB JPEG mode | ✅ (tested via WhatsApp) | ❌ | ❌ | ✅ |
| LSB video mode | ✅ | ✅ (if saved losslessly) | ❌ | ✅ |

---

## 🚀 Quick Start

```bash
# 1. Clone the repository
git clone <repo-url>
cd MiniProject

# 2. Pick a technique and install its dependencies
cd LSB
pip install -r requirements.txt

# 3. Follow that technique's guide
#    e.g. LSB/LSBGuide.md
```

**Example with LSB:**

```bash
# Image -> PNG (pixel mode)
python embed_imei.py   --image photo.jpg --payload "356938035643809" --output watermarked.png
python extract_imei.py --image watermarked.png --payload-length 15

# JPEG -> JPEG (JPEG mode)
python embed_imei.py   --image photo.jpeg --payload "356938035643809" --output watermarked.jpeg
python extract_imei.py --image watermarked.jpeg --payload-length 15

# MP4 -> MP4 (video mode)
python embed_imei.py   --image clip.mp4 --payload "356938035643809" --output watermarked.mp4
python extract_imei.py --image watermarked.mp4 --payload-length 15
```

> 📤 To share a watermarked file, send it as a **file** (USB, WhatsApp
> *Document*, e-mail attachment, cloud drive). Sending it as a normal
> photo or video re-compresses it and erases the watermark.

---

## 📁 Project Structure

```
MiniProject/
├── README.md                 ← you are here (project overview)
│
└── LSB/                      ← Technique 1: LSB steganography
    ├── embed_imei.py         ← embed (picks pixel / JPEG / video mode)
    ├── extract_imei.py       ← extract (detects the mode automatically)
    ├── jpeg_dct.py           ← JPEG mode: DCT-coefficient LSB
    ├── video_lsb.py          ← video mode: lossless MP4 frame LSB
    ├── run_test.sh           ← automated tests for all three modes
    ├── requirements.txt      ← opencv-python, numpy, jpeglib, av
    └── LSBGuide.md           ← full explanation, formats, tests, limits
```

---

## ➕ Adding a New Technique

To keep the project consistent, each new technique should:

1. Live in **its own top-level folder** (e.g. `DCT/`, `PRNU/`).
2. Include a **`requirements.txt`** for its dependencies.
3. Include a **guide** (`<Name>Guide.md`) that covers how the technique
   works, supported formats, step-by-step testing, and limitations.
4. Include an **automated test** that proves the technique works.
5. Add a row to the **Techniques** and comparison tables above.

---

## ⚠️ Disclaimer

This project is for **educational and research purposes**. The IMEI numbers
used in the examples are dummy values. Results from these techniques are
**provenance hints, not legal proof**. Read each technique's
*Limitations* section before relying on it.

---

<div align="center">

Made for digital forensics research 🔬

</div>
