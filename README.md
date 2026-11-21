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

### 🔗 Forwarding chain (photos and videos)

`chain_imei.py` records **every device a photo or video passes through**,
not just the first one. Each device that forwards the file (over Bluetooth
or USB) appends a *hop* (device ID, IMEI, time, and role) to a chain hidden
in the file. The chain has room for **100 hops**.

| Media | Where the chain is hidden | Size change |
|-------|---------------------------|-------------|
| Photo | **RGB pixel LSBs** (`pixel_chain.py`): 3 or 6 bits per pixel, as a small tile **repeated over the whole photo**. Saved as a lossless **PNG**. | JPEG → PNG (a camera JPEG becomes a few times larger) |
| Video (MP4 / MOV / 3GP) | The **complete chain** in one small hidden, scrambled box at the end of the file (`mp4_chain.py`). The video is never re-encoded. | +2,040 bytes |

**Photo modes** (chosen with `--bpp`, or in the app):

| Mode | Bits used | Change per R/G/B value | Tile (one full copy of the chain) | Copies in a 12 MP photo |
|------|-----------|-----------------------:|-----------------------------------|------------------------:|
| **3 bits/pixel** (default) | bit 0 of R, G, B | ±1 | 74 × 74 px | ≈ 2,255 |
| **6 bits/pixel** | bits 1 and 0 of R, G, B | ±3 | 52 × 52 px | ≈ 4,524 |

Every 74 × 74 (or 52 × 52) window of the photo holds a full copy of the
chain, so **any crop at least one tile wide and high still reads the whole
chain**. All copies vote bit by bit, so a few percent of damaged bits are
corrected. The reader tries both modes and every crop offset by itself.

```bash
python chain_imei.py append --input photo.jpg    --output photo_A.png   --device-id phone-A --imei 356938035643809
python chain_imei.py append --input photo_A.png  --output photo_AB.png  --device-id phone-B --imei 490154203237518 --bpp 6
python chain_imei.py append --input photo_AB.png --output photo_ABC.png --device-id phone-C --imei 352099001761481 --role claim
python chain_imei.py read   --input photo_ABC.png          # add --json for machine-readable output
#  -> Path: 356938035643809 -> 490154203237518 -> 352099001761481

python chain_imei.py append --input clip.mp4 --output clip_A.mp4 --device-id phone-A   # videos: same commands
```

> 📄 Bit-by-bit description of the format (frame, pixel layout, tiles, MP4
> box): [ChainStorageFormat.md](ChainStorageFormat.md)

> Why photos are PNG: pixel LSBs only survive lossless storage. Saving as
> JPEG, resizing, rotating or filtering recalculates every pixel and erases
> the chain, so share stamped photos only as files (USB, Bluetooth).

> Why videos don't use pixel LSBs: a phone video is lossy H.264/H.265, so
> pixel LSBs only survive a *lossless* re-encode (the `video_lsb.py` mode).
> That makes files 10–20× larger, and phones can't do it. Storing the chain
> in the container keeps the video byte-for-byte intact.

- **Image ID**: calculated from the original file's content (photo: every
  pixel with its 2 lowest bits cleared; video: the `moov` index), which
  stamping never changes. All copies of the same original get the same ID,
  so several copies can be merged into a forwarding tree.
- **`send` / `claim`**: a device adds itself when it forwards the photo
  (`send`). The final receiver adds itself with `claim`.
- If a device is already the last hop (e.g. it sends the same photo to two
  people), it is not added again.
- Format: a fixed 2016-byte frame (header, 100 hop slots of 20 bytes,
  CRC-32), the same for photos and videos (see `chain_payload.py`).
- The mode can change from hop to hop: each hop rewrites the whole chain.

### 🌐 Backend API (forwarding tree)

`backend/` is a FastAPI server that decodes uploaded photos and videos and
merges every copy of the same original (same Image ID) into a **forwarding tree**.

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload          # http://127.0.0.1:8000/docs to try it
python test_api.py                 # automated API tests (temporary database)
```

| Endpoint | What it does |
|----------|--------------|
| `POST /api/upload` | Upload a PNG photo or MP4/MOV/3GP video (`file` field, up to 1 GB). Returns its chain, the photo mode (3 or 6 bits/pixel) and the merged tree. JPEGs are accepted but can't hold a chain. |
| `GET /api/images` | One row per photo: copies, devices, origin |
| `GET /api/images/{image_id}/tree` | The tree, nested (`root`) and flat (`nodes` + `edges`) |
| `GET /api/uploads`, `GET /api/uploads/{id}`, `…/file`, `DELETE` | Uploaded copies |
| `GET /api/devices`, `GET/PUT /api/devices/{device_id}` | Devices seen, with a label you can set |

Data (SQLite database and uploaded photos) is stored in `backend/data/`, or in
the folder set by `CHAIN_DATA_DIR`.

### 📊 Dashboard (React)

`dashboard/` is a React app (Vite) on top of the backend:

- **Analyze**: drop one or more photos or videos and see each one's chain
  (A → B → C, with the time between hops).
- **Media**: one card per original photo or video. Open a card to see its
  **forwarding tree**, with every uploaded copy merged in. Click a device for
  its details.
- **Devices**: every phone seen. Give each a name, like "Dev's phone", and it
  shows up with that name in every tree.

```bash
# terminal 1: backend
cd backend && uvicorn main:app --reload

# terminal 2: dashboard (needs Node.js 20+)
cd dashboard
npm install
npm run dev                         # http://localhost:5173
```

In development, the dashboard forwards `/api` calls to `http://127.0.0.1:8000`.
Set `VITE_BACKEND` to use a different backend address.

### 📱 Android app (ChainStamp)

`android/` is the phone app that adds each phone to the chain before the file
is forwarded over Bluetooth. It works for **photos and videos**.

| Module | What it is |
|--------|-----------|
| `android/core` | Pure-Kotlin chain library: a lossless PNG reader/writer, the RGB pixel-LSB chain (3 and 6 bits/pixel) and the MP4 box writer. It produces exactly the same pixels and files as the Python code, which `LSB/test_kotlin_compat.py` checks. |
| `android/app` | The app (Jetpack Compose). |

**Build and install**

1. Install [Android Studio](https://developer.android.com/studio).
2. *File → Open* → choose the `android/` folder and wait for Gradle sync.
3. Either connect a phone with USB debugging enabled and press **Run ▶**,
   or use *Build → Build App Bundle(s) / APK(s) → Build APK(s)* and copy
   `android/app/build/outputs/apk/debug/app-debug.apk` to each phone.

**Demo with three phones (A → B → C)**

1. On each phone, open ChainStamp once and type its IMEI (dial `*#06#` to see it).
2. **Phone A:** *Choose a photo or video* → for a photo, pick **3 or 6 bits / pixel** →
   **Stamp & send** → pick *Bluetooth* → phone B. (A JPEG is converted to PNG once.)
3. **Phone B:** open the received file → *Share → ChainStamp* (or open ChainStamp →
   *Choose a photo or video*). The app shows **A**. Tap **Stamp & send** → Bluetooth → phone C.
4. **Phone C:** open it the same way. The app shows **A → B**. Tap **Save as received**
   to add C and save it to `Pictures/ChainStamp` (or `Movies/ChainStamp` for videos).
5. Upload C's copy to the dashboard to see the chain. Upload copies from other
   branches to see the tree.

> Device ID = Android's `ANDROID_ID`. Since Android 10, normal apps can't read
> the IMEI, so it is typed in once and stored on the phone.

**Tests that don't need the Android SDK**

```bash
cd android && ./gradlew -PcoreOnly :core:test     # Kotlin unit tests
cd LSB && python test_kotlin_compat.py             # Kotlin and Python produce identical chains
```

---

## 📁 Project Structure

```
MiniProject/
├── README.md                 ← you are here (project overview)
├── ChainStorageFormat.md     ← how the chain is stored, bit by bit (photos + videos)
│
├── backend/                  ← FastAPI server for the dashboard
│   ├── main.py               ← REST endpoints (upload, tree, devices)
│   ├── tree.py               ← merges copies' chains into one tree
│   ├── db.py                 ← SQLite storage
│   └── test_api.py           ← automated API tests
│
├── android/                  ← Android app (ChainStamp)
│   ├── core/                 ← Kotlin chain library (PNG pixels + MP4), tested on a PC
│   └── app/                  ← the app (Jetpack Compose)
│
├── dashboard/                ← React dashboard (Vite)
│   └── src/
│       ├── pages/            ← Analyze, Photos, Photo tree, Devices
│       └── components/       ← drop zone, chain strip, tree view
│
└── LSB/                      ← Technique 1: LSB steganography
    ├── embed_imei.py         ← embed (picks pixel / JPEG / video mode)
    ├── extract_imei.py       ← extract (detects the mode automatically)
    ├── jpeg_dct.py           ← JPEG mode: DCT-coefficient LSB
    ├── video_lsb.py          ← video mode: lossless MP4 frame LSB
    ├── chain_imei.py         ← forwarding chain: append / read device hops
    ├── chain_payload.py      ← binary format of the chain (100 hops, CRC-checked)
    ├── pixel_chain.py        ← chain storage for photos (RGB pixel LSB, PNG)
    ├── mp4_chain.py          ← chain storage for videos (hidden MP4 box)
    ├── run_test.sh           ← automated tests for all three modes
    ├── test_chain.py         ← automated tests for the forwarding chain
    ├── test_kotlin_compat.py ← checks the Android code against this Python code
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
