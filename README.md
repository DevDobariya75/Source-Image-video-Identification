<div align="center">

# 🔍 Identification of Source Image / Video

**A digital-forensics toolkit for tracing images and videos back to the device that created them.**

![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-4.x-5C3EE8?logo=opencv&logoColor=white)
![NumPy](https://img.shields.io/badge/NumPy-1.21%2B-013243?logo=numpy&logoColor=white)
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
| 1 | **LSB Steganography** | Hides a device ID (IMEI) in the least significant bits of the pixels, with redundancy and majority voting to survive cropping | ✅ Implemented | [LSB/LSBGuide.md](LSB/LSBGuide.md) |
| … | *More techniques coming* | | 🚧 Planned | |

Each technique has different strengths. Compare them before choosing one:

| Technique | Survives crop | Survives JPEG / resize | Invisible | Needs embedding |
|-----------|:---:|:---:|:---:|:---:|
| LSB Steganography | ✅ | ❌ | ✅ | ✅ |

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
python embed_imei.py   --image photo.jpg --payload "356938035643809" --output watermarked.png
python extract_imei.py --image watermarked.png --payload-length 15
```

---

## 📁 Project Structure

```
MiniProject/
├── README.md                 ← you are here (project overview)
│
└── LSB/                      ← Technique 1: LSB steganography
    ├── embed_imei.py
    ├── extract_imei.py
    ├── run_test.sh
    ├── requirements.txt
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
