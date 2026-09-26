# LSB Guide

A practical guide to the LSB watermark in this folder. It explains what the
technique is and which file formats it supports. It then walks through
testing it step by step, attacking it with crops, resizes and other edits,
and checking for yourself why it does **not** survive in the real world.

- [1. What is LSB steganography?](#1-what-is-lsb-steganography)
- [2. How this project uses it](#2-how-this-project-uses-it)
- [3. Supported formats](#3-supported-formats)
- [4. Setup](#4-setup)
- [5. Step-by-step basic test](#5-step-by-step-basic-test)
- [6. Robustness tests: crop, resize and more](#6-robustness-tests-crop-resize-and-more)
- [7. Proving it does not survive the real world](#7-proving-it-does-not-survive-the-real-world)
- [8. Results summary](#8-results-summary)

---

## 1. What is LSB steganography?

A colour image is a grid of pixels. Each pixel has three channels (OpenCV
orders them **B**lue, **G**reen, **R**ed), and each channel is an 8-bit number
from 0 to 255:

```
181 = 1 0 1 1 0 1 0 1
      ^             ^
     MSB (128)     LSB (1)
```

The **Least Significant Bit (LSB)** is the right-most bit and is worth only
**1** out of 255. If you change it, the colour moves by at most ±1 (about
0.4%), which no human can see. That spare bit can therefore carry hidden data:
**1 bit per channel, 3 bits per pixel**.

### Writing a bit (in `embed_imei.py`)

```
new = (value & 0xFE) | bit

  1011 0101   (181)
& 1111 1110   (0xFE)  -> clears the LSB, keeps the other 7 bits
= 1011 0100   (180)
| 0000 0001   (bit 1) -> writes the payload bit into the LSB
= 1011 0101   (181)
```

### Reading a bit (in `extract_imei.py`)

```
bit = value & 1

  1011 0101   (181)
& 0000 0001   (1)     -> zeros everything except the LSB
= 0000 0001   -> bit 1
```

---

## 2. How this project uses it

Plain LSB writes a message **once**, starting at the top-left corner, so a
single crop removes it. This project adds three things:

| Idea | What it does |
|---|---|
| **Frame + delimiter** | The payload is `IMEI + "#END"`, e.g. `356938035643809#END`: 19 chars = 152 bits. |
| **Spatial tiling (redundancy)** | Those 152 bits fit in an **8×8-pixel tile** (64 px × 3 = 192 slots). The tile is repeated over the whole image. An 800×600 image holds **7,500 copies**, and any 8×8 piece contains a full copy. |
| **Majority voting + grid search** | The extractor counts, for every bit, how many copies say `1` vs `0` and keeps the majority. It tries all 64 possible grid offsets (a crop shifts the grid) and keeps the one that decodes to text ending in `#END`. |

`--payload-length` must match the length that was embedded, because it
determines the tile size (15 → 8×8).

---

## 3. Supported formats

### Input (cover image for `embed_imei.py`)

Anything OpenCV can read: **JPG/JPEG, PNG, BMP, TIFF, WebP, PPM**, and more.
Grayscale images are converted to 3 channels. An alpha (transparency) channel
is dropped.

### Output (watermarked image)

The output **must be lossless**, or the hidden bits are destroyed on save.
`embed_imei.py` only accepts:

| Extension | Works? | Notes |
|---|---|---|
| `.png` | ✅ | Recommended |
| `.bmp` | ✅ | Uncompressed, large files |
| `.tif` / `.tiff` | ✅ | |
| `.ppm` | ✅ | Uncompressed |
| `.jpg` / `.jpeg` | ❌ refused | Lossy: the script exits with an error |
| `.webp` | ❌ refused | OpenCV saves lossy WebP by default |
| `.pgm` | ❌ refused | Grayscale only, cannot store 3 channels |

### Payload

Any ASCII text is accepted. A 15-digit IMEI is expected, and anything else
prints a warning but still embeds. The text must not contain `#END`.

---

## 4. Setup

```bash
cd LSB
pip install -r requirements.txt       # opencv-python, numpy
```

All commands below are run **from inside the `LSB/` folder**. They work in
**PowerShell, Git Bash, Linux and macOS**, except `bash run_test.sh`, which
needs bash. Replace `python` with `python3` if your system needs it.

Make a work folder:

```shell
python -c "import os; os.makedirs('test_out', exist_ok=True)"
```

---

## 5. Step-by-step basic test

### Option A: automatic

```bash
bash run_test.sh
```

This generates an image and embeds an IMEI. It then crops to 97×61 px at an
off-grid offset, flips 30% of the LSBs, and extracts. It should end with
`ALL TESTS PASSED`.

### Option B: manual, one step at a time

**Step 1: get a cover image.** Copy any photo of your own into `test_out/`
and name it `input.png` (or `input.jpg`, then use that name below). You can
also generate one:

```shell
python -c "import cv2, numpy as np; h, w = 600, 800; y, x = np.mgrid[0:h, 0:w]; img = np.dstack([x*255//w, y*255//h, (x+y)*255//(w+h)]).astype(np.uint8); cv2.imwrite('test_out/input.png', img)"
```

**Step 2: embed the IMEI.**

```bash
python embed_imei.py --image test_out/input.png --payload "356938035643809" --output test_out/watermarked.png
```

Expected output:

```
[+] Embedded payload  : 356938035643809#END
[+] Image size        : 800 x 600
[+] Tile size         : 8 x 8 px (152 bits used of 192)
[+] Full copies       : 7500
...
[+] Saved             : test_out/watermarked.png
```

**Step 3: confirm it is invisible.** Open `input.png` and `watermarked.png`
side by side. They should look identical. To check numerically:

```shell
python -c "import cv2, numpy as np; a=cv2.imread('test_out/input.png'); b=cv2.imread('test_out/watermarked.png'); print('max pixel difference:', np.abs(a.astype(int)-b.astype(int)).max()); print('PSNR (dB):', round(cv2.PSNR(a, b), 1))"
```

Expected: `max pixel difference: 1` and a PSNR of about 51 dB.

**Step 4: extract the IMEI.**

```bash
python extract_imei.py --image test_out/watermarked.png --payload-length 15
```

Expected output:

```
[+] Grid alignment    : dy=0, dx=0 (crop offset mod tile size)
[+] Vote confidence   : 100.0 % (weak bits: 0, tied bits: 0)
[+] Extracted payload : 356938035643809
[+] Format            : valid 15-digit IMEI format
```

### Reading the extractor output

| Field | Meaning |
|---|---|
| **Full copies** | Complete 8×8 tiles in the image. This is the number of votes per bit. |
| **Grid alignment** | The crop offset the extractor detected (mod 8). |
| **Vote confidence** | 100% = every copy agrees. 0% = copies split 50/50 (random noise). |
| **weak bits** | Bits where the vote was close (< 60/40). Many weak bits = the data is dying. |
| **Extracted payload** | Only printed when the delimiter `#END` was found. Otherwise you see `No alignment produced the delimiter`. |

Exit code: `0` on success, `1` on failure (`echo $LASTEXITCODE` in PowerShell, `echo $?` in bash).

---

## 6. Robustness tests: crop, resize and more

### How every test works

Every test below follows the same **three steps**:

| Step | What you do | Result |
|---|---|---|
| **Step 1: attack** | Run the test's `python -c "..."` command | Reads `test_out/watermarked.png`, changes it, and **creates `test_out/attacked.png`** (it prints nothing, which is normal) |
| **Step 2: check** | `dir test_out` (PowerShell) or `ls test_out` (bash) | `attacked.png` should now be listed |
| **Step 3: extract** | `python extract_imei.py --image test_out/attacked.png --payload-length 15` | Shows whether the IMEI survived |

> ⚠️ Always run **Step 1 before Step 3**. If you skip it you get
> `Could not read image: test_out/attacked.png`, because the file has not
> been created yet.
>
> Each test overwrites `attacked.png`, which is fine: every test starts
> again from the untouched `watermarked.png`.

**Before you start**, make sure `test_out/watermarked.png` exists (see
section 5, Step 2). All commands are **single lines** and work in both
PowerShell and bash. Run them from inside the `LSB` folder.

The confidence percentages quoted below were measured on the 800×600 test
image. On your own photo they will differ somewhat. What should match is
**PASS vs FAIL**.

> On Windows, never use `convert` for the optional ImageMagick commands. It
> runs a built-in Windows disk utility, not ImageMagick. Use `magick`.

---

### 6.1 Cropping ✅ survives

**Step 1: create `attacked.png` (a 97×61 crop at an odd, off-grid offset):**

```shell
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', i[217:278, 333:430])"
```

*(ImageMagick alternative: `magick test_out/watermarked.png -crop 97x61+333+217 +repage test_out/attacked.png`)*

**Step 3: extract:**

```shell
python extract_imei.py --image test_out/attacked.png --payload-length 15
```

**Expected:** ✅ PASS, `full copies: 84`, a non-zero grid alignment (e.g.
`dy=7, dx=3`), confidence 100%.

**Find the limit.** Repeat Step 1 with each of these, then Step 3 again:

```shell
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', i[3:11, 5:13])"
```

**Expected:** ✅ PASS. 8×8 is exactly one tile, the smallest crop that can
work.

```shell
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', i[3:10, 5:12])"
```

**Expected:** ❌ FAIL, `Image (7x7) is smaller than one tile (8x8)`.

---

### 6.2 Random LSB damage ✅ survives (majority voting)

**Step 1: create `attacked.png` (flip 30% of all LSBs at random):**

```shell
python -c "import cv2, numpy as np; i=cv2.imread('test_out/watermarked.png'); f=(np.random.default_rng(7).random(i.shape) < 0.30).astype(np.uint8); cv2.imwrite('test_out/attacked.png', i ^ f)"
```

**Step 3: extract:**

```shell
python extract_imei.py --image test_out/attacked.png --payload-length 15
```

**Expected:** ✅ PASS. Confidence drops to about 40%, but the majority of
the copies still wins.

**Push it further.** Change `0.30` in Step 1 to `0.45`, which should still
PASS (about 10% confidence), and then to `0.50`, which should FAIL. At 50%,
every bit is a coin toss.

---

### 6.3 Re-saving as PNG / lossless WebP ✅ survives

**Step 1a: create `attacked.png` (re-save as PNG):**

```shell
python -c "import cv2; cv2.imwrite('test_out/attacked.png', cv2.imread('test_out/watermarked.png'))"
```

**Step 3a: extract:**

```shell
python extract_imei.py --image test_out/attacked.png --payload-length 15
```

**Step 1b: create `attacked.webp` (lossless WebP; quality above 100 means lossless in OpenCV):**

```shell
python -c "import cv2; cv2.imwrite('test_out/attacked.webp', cv2.imread('test_out/watermarked.png'), [cv2.IMWRITE_WEBP_QUALITY, 101])"
```

**Step 3b: extract (note the `.webp` extension):**

```shell
python extract_imei.py --image test_out/attacked.webp --payload-length 15
```

**Expected:** ✅ PASS for both. Lossless formats keep every bit.

---

### 6.4 Brightness change: even ✅ / odd ❌

**Step 1a: create `attacked.png` (brightness +10, an even number):**

```shell
python -c "import cv2, numpy as np; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', cv2.add(i, np.full_like(i, 10)))"
```

**Step 3a:** `python extract_imei.py --image test_out/attacked.png --payload-length 15`
→ **Expected:** ✅ PASS

**Step 1b: create `attacked.png` (brightness +1, an odd number):**

```shell
python -c "import cv2, numpy as np; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', cv2.add(i, np.full_like(i, 1)))"
```

**Step 3b:** `python extract_imei.py --image test_out/attacked.png --payload-length 15`
→ **Expected:** ❌ FAIL

**Why:** adding an even number never changes whether a value is odd or even,
so every LSB is kept. Only pixels clipped at 255 are damaged, and the vote
absorbs those. Adding an odd number flips *every* LSB, so all copies agree on
the **inverted** message (confidence stays high) and `#END` is never found.
Real photo editors use non-uniform curves, which behave like the odd case.

---

### 6.5 Resizing ❌ does not survive

**Step 1a: create `attacked.png` (50% downscale):**

```shell
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', cv2.resize(i, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA))"
```

*(ImageMagick alternative: `magick test_out/watermarked.png -resize 50% test_out/attacked.png`)*

**Step 1b: create `attacked.png` (a tiny 1% shrink):**

```shell
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', cv2.resize(i, None, fx=0.99, fy=0.99))"
```

**Step 1c: create `attacked.png` (2× upscale with nearest-neighbour, no blending):**

```shell
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', cv2.resize(i, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))"
```

**Step 3, after each of 1a, 1b and 1c:**

```shell
python extract_imei.py --image test_out/attacked.png --payload-length 15
```

**Expected:** ❌ FAIL in all three. Interpolation computes new pixel values
by averaging neighbours, which randomises the LSBs (confidence about 2%).
Even the nearest-neighbour upscale fails: each pixel becomes a 2×2 block, so
the tile is now 16×16 while the extractor looks for 8×8.

---

### 6.6 Rotation and flipping ❌ does not survive

**Step 1a: create `attacked.png` (rotate 90°):**

```shell
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', cv2.rotate(i, cv2.ROTATE_90_CLOCKWISE))"
```

**Step 1b: create `attacked.png` (mirror horizontally):**

```shell
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', cv2.flip(i, 1))"
```

**Step 1c: create `attacked.png` (rotate by a small 2°):**

```shell
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); h,w=i.shape[:2]; M=cv2.getRotationMatrix2D((w/2,h/2),2,1); cv2.imwrite('test_out/attacked.png', cv2.warpAffine(i,M,(w,h)))"
```

*(ImageMagick alternatives: `magick test_out/watermarked.png -rotate 90 test_out/attacked.png` and `magick test_out/watermarked.png -flop test_out/attacked.png`)*

**Step 3, after each:**

```shell
python extract_imei.py --image test_out/attacked.png --payload-length 15
```

**Expected:** ❌ FAIL for all three. For 90° and the mirror, notice that
confidence stays at **100%**: the bits are all still there, just in a
different order inside the tile, so the delimiter never lines up. The 2°
rotation resamples the pixels and destroys the bits completely
(confidence about 3%).

---

### 6.7 Blur, noise, grayscale ❌ does not survive

**Step 1a: create `attacked.png` (3×3 Gaussian blur):**

```shell
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', cv2.GaussianBlur(i, (3,3), 0))"
```

**Step 1b: create `attacked.png` (mild sensor-like noise, sigma = 2):**

```shell
python -c "import cv2, numpy as np; i=cv2.imread('test_out/watermarked.png'); n=np.random.default_rng(1).normal(0,2,i.shape); cv2.imwrite('test_out/attacked.png', np.clip(i+n,0,255).astype(np.uint8))"
```

**Step 1c: create `attacked.png` (convert to grayscale):**

```shell
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); g=cv2.cvtColor(i, cv2.COLOR_BGR2GRAY); cv2.imwrite('test_out/attacked.png', cv2.cvtColor(g, cv2.COLOR_GRAY2BGR))"
```

**Step 3, after each:**

```shell
python extract_imei.py --image test_out/attacked.png --payload-length 15
```

**Expected:** ❌ FAIL for all three. Each one recomputes every pixel value,
so the LSBs become random.

---

### 6.8 JPEG compression ❌ does not survive

**Step 1: create `attacked.jpg` (JPEG at the highest quality, 100):**

```shell
python -c "import cv2; cv2.imwrite('test_out/attacked.jpg', cv2.imread('test_out/watermarked.png'), [cv2.IMWRITE_JPEG_QUALITY, 100])"
```

*(ImageMagick alternative: `magick test_out/watermarked.png -quality 100 test_out/attacked.jpg`)*

**Step 3: extract (note the `.jpg` extension):**

```shell
python extract_imei.py --image test_out/attacked.jpg --payload-length 15
```

**Expected:** ❌ FAIL, **even at quality 100.** JPEG converts to YCbCr,
subsamples colour and quantises DCT coefficients. Decoding never restores
the exact original values, so the LSBs are lost. Change `100` to `95` or `75`
in Step 1 and try again. Those fail too.

---

## 7. Proving it does not survive the real world

Section 6 shows *why* the watermark breaks. This section lets you check,
with real apps, that almost every everyday way of sharing an image breaks it.
For each test:

1. Embed into a **real photo** (so the result looks like a normal upload):
   ```bash
   python embed_imei.py --image my_photo.jpg --payload "356938035643809" --output test_out/share_me.png
   python extract_imei.py --image test_out/share_me.png      # must PASS first
   ```
2. Send or share `share_me.png` through the channel you are testing.
3. Download or save the received file as `test_out/received.*`.
4. Run `python extract_imei.py --image test_out/received.<ext>` and record
   PASS or FAIL.

| # | Real-world path | How to test | Expected |
|---|---|---|---|
| 1 | **WhatsApp / Telegram (as photo)** | Send to yourself as a normal photo, then save the received image. | ❌ FAIL: re-encoded as JPEG and usually resized |
| 2 | **WhatsApp / Telegram (as document/file)** | Attach as a *document* / *file*, then download. | ✅ PASS: bytes are sent unchanged |
| 3 | **Instagram / Facebook / X (Twitter)** | Post it, then download the image from the post. | ❌ FAIL: recompressed and resized |
| 4 | **Email attachment** | Attach the PNG to yourself, then download it. | ✅ usually PASS: attachments are not modified |
| 5 | **Email inline / "optimise image" option** | Insert the image into the message body. | ❌ often FAIL: clients may resize |
| 6 | **Google Drive / Dropbox (download original)** | Upload, then use *Download*. | ✅ PASS |
| 7 | **Screenshot** | Open `share_me.png` full screen and press PrtScn / Win+Shift+S / Cmd+Shift+4, then save as PNG. | ❌ FAIL: display scaling and colour management re-render the pixels |
| 8 | **Phone camera photo of the screen** | Photograph the monitor showing the image. | ❌ FAIL: an entirely new image |
| 9 | **Printing and scanning** | Print, then scan to PNG. | ❌ FAIL |
| 10 | **Opening and "Save As" in an editor** | Open in Paint / Photoshop / GIMP / phone gallery, make no change, *Save As* PNG. | ⚠️ Often PASS for PNG. FAIL if saved as JPEG, if an auto-enhance/colour profile is applied, or if the image is exported resized |
| 11 | **Phone gallery "edit" / crop** | Crop in the phone's gallery app and save. | ❌ usually FAIL: the gallery re-saves as JPEG |

### Why this happens

- Most sharing services re-encode images to **JPEG/WebP** and **resize**
  them to save bandwidth (sections 6.5 and 6.8).
- Screens, cameras and printers **redraw** the image instead of copying
  its bytes (sections 6.5–6.7).
- Only paths that copy the **file bytes exactly** (file transfer,
  attachments, cloud storage) keep the watermark.

### Security check: anyone can remove it

The watermark is not secret. This one-liner wipes it without any visible
change:

```bash
python -c "import cv2; i=cv2.imread('test_out/watermarked.png'); cv2.imwrite('test_out/attacked.png', i & 0xFE)"
python extract_imei.py --image test_out/attacked.png   # FAIL
```

Anyone who knows the scheme can also **forge** a different IMEI by running
`embed_imei.py` on top, because the new bits simply replace the old ones.
Treat an extracted IMEI as a **provenance hint, never as proof**.

---

## 8. Results summary

These results were measured with this project's scripts on an 800×600 test
image (IMEI `356938035643809`, 8×8 tile, 7,500 copies).

| Attack | Result | Confidence |
|---|---|---|
| Re-save PNG | ✅ PASS | 100 % |
| Lossless WebP | ✅ PASS | 100 % |
| Crop 97×61 (off-grid) | ✅ PASS | 100 % |
| Crop 8×8 (one tile) | ✅ PASS | 100 % |
| Crop 7×7 | ❌ FAIL | too small |
| 30 % random LSB flips | ✅ PASS | 40 % |
| Brightness +10 | ✅ PASS | 90 % |
| Brightness +1 | ❌ FAIL | 97 % (inverted) |
| Horizontal flip | ❌ FAIL | 100 % (reordered) |
| Rotate 90° / 180° | ❌ FAIL | 100 % (reordered) |
| Rotate 2° | ❌ FAIL | 3 % |
| Resize 50 % | ❌ FAIL | 3 % |
| Resize 99 % | ❌ FAIL | 2 % |
| Upscale 2× nearest | ❌ FAIL | 53 % |
| JPEG quality 100 / 95 / 75 | ❌ FAIL | 2 % |
| Gaussian blur 3×3 | ❌ FAIL | 2 % |
| Gaussian noise σ = 2 | ❌ FAIL | 2 % |
| Grayscale conversion | ❌ FAIL | 1 % |

**Conclusion:** the watermark handles **cropping and scattered bit damage**
very well. It fails against **any operation that recomputes pixel values**
(compression, resizing, filtering, rotation) or changes pixel order. That
covers most real-world sharing. For a watermark that survives these, look
at transform-domain methods (DCT/DWT, spread spectrum) or sensor-noise
fingerprinting (PRNU), which identifies the camera from its natural noise
pattern without embedding anything.
