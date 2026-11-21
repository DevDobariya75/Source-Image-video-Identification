# How the forwarding chain is stored

This document explains, down to the individual bit, how the forwarding chain is
stored inside a **photo** (in the RGB pixels of a PNG) and inside a **video**
(in a hidden box of the MP4 file). It also explains how the chain is read back.

The Python code (`LSB/`) and the Android code (`android/core/`) implement exactly
the same format, so a file stamped on a phone is read by the backend and the
other way round.

| Part | Python | Kotlin (Android) |
|---|---|---|
| The chain frame (bytes) | `chain_payload.py` | `ChainPayload.kt` |
| Photos (RGB pixel LSB) | `pixel_chain.py` | `PixelChain.kt`, `Png.kt` |
| Videos (MP4 box) | `mp4_chain.py` | `Mp4Chain.kt` |
| Append / read a hop | `chain_imei.py` | `MediaChain.kt` |

- [1. The big picture](#1-the-big-picture)
- [2. The chain frame (2016 bytes)](#2-the-chain-frame-2016-bytes)
- [3. Photos: storing the frame in the pixels](#3-photos-storing-the-frame-in-the-pixels)
- [4. Photos: reading the frame back](#4-photos-reading-the-frame-back)
- [5. Videos: storing the frame in an MP4 box](#5-videos-storing-the-frame-in-an-mp4-box)
- [6. Videos: reading the frame back](#6-videos-reading-the-frame-back)
- [7. The Image ID](#7-the-image-id)
- [8. What happens at each hop](#8-what-happens-at-each-hop)
- [9. What survives and what doesn't](#9-what-survives-and-what-doesnt)
- [10. Quick reference](#10-quick-reference)

---

## 1. The big picture

Every phone that forwards a file adds one **hop** (who it is and when) to a list
called the **chain**. The chain is packed into a fixed block of bytes called the
**frame**. Photos and videos carry exactly the same frame; only the place where
it is hidden differs:

```
                 ┌──────────────────────── 2016-byte frame ────────────────────────┐
                 │ magic · version · hop count · Image ID · 100 hop slots · CRC-32  │
                 └───────────────┬──────────────────────────────────┬──────────────┘
                                 │                                  │
                  PHOTO          ▼                   VIDEO          ▼
   16,128 bits written into the lowest bit(s)    the whole frame (scrambled) in one
   of the R, G, B values of the pixels, as a     2,040-byte "uuid" box appended at
   small tile repeated over the whole photo;     the END of the MP4 file; the video
   saved as a lossless PNG                       itself is never re-encoded
```

---

## 2. The chain frame (2016 bytes)

The frame always has the same size, so the reader knows exactly how many bits to
look for. All numbers are **big-endian** (most significant byte first).

| Offset (bytes) | Size | Field | Notes |
|---:|---:|---|---|
| 0 | 2 | Magic | ASCII `IC` = `0x49 0x43` ("IMEI Chain") |
| 2 | 1 | Version | `2` (version 1 was the old 8-hop format) |
| 3 | 1 | Hop count N | `0`–`100` |
| 4 | 8 | Image ID | Same for every copy of one original (section 7) |
| 12 | 2000 | 100 hop slots | 20 bytes each; slots ≥ N are all zero |
| 2012 | 4 | CRC-32 | Over bytes 0–2011 (standard zlib/PNG CRC-32) |

**Total: 2016 bytes = 16,128 bits.**

### One hop slot (20 bytes)

| Offset in slot | Size | Field | Encoding |
|---:|---:|---|---|
| 0 | 8 | Device ID | Android `ANDROID_ID` (64-bit, the 16 hex characters as 8 bytes). Any other text, e.g. `phone-A` on a PC, is hashed: first 8 bytes of SHA-256 of the lower-cased text. |
| 8 | 7 | IMEI | The 15-digit IMEI as one integer (fits in 50 bits). `0` means "not set". |
| 15 | 4 | Time | Unix time in seconds (unsigned 32-bit) |
| 19 | 1 | Role | `1` = send (forwarding), `2` = claim (final receiver) |

### Worked example

One hop: device `9774d56d682e549c`, IMEI `356938035643809`, time `1792500000`,
role send, Image ID `3f1a9c0d5e7b2468`.

```
bytes 0–11   49 43 02 01 3f 1a 9c 0d 5e 7b 24 68
             └─┬─┘ │  │  └──────── Image ID ───────┘
            "IC"  v2  1 hop

bytes 12–31  97 74 d5 6d 68 2e 54 9c | 01 44 a2 1c d2 45 a1 | 6a d7 61 20 | 01
             └────── device ID ────┘   └──── IMEI ─────────┘   └─ time ──┘   send
             (356938035643809 = 0x0144A21CD245A1, 1792500000 = 0x6AD76120)

bytes 32–2011  00 00 … 00        (99 empty hop slots)
bytes 2012–15  65 d4 4d 5e       (CRC-32 of bytes 0–2011)
```

### Why magic + CRC matter

Random pixel bits, or the chain read at a wrong alignment, would almost never
start with `IC 02` **and** have a matching CRC-32 over 2012 bytes. So noise is
never reported as a phone. The CRC also detects damaged data: a frame with even
one wrong bit is rejected.

---

## 3. Photos: storing the frame in the pixels

### 3.1 From bytes to bits

The 2016 bytes are turned into 16,128 bits, **most significant bit first**:

```
byte 0 = 0x49 = 0 1 0 0 1 0 0 1   → frame bits 0–7
byte 1 = 0x43 = 0 1 0 0 0 0 1 1   → frame bits 8–15
byte 2 = 0x02 = 0 0 0 0 0 0 1 0   → frame bits 16–23
...
```

### 3.2 Where a bit goes inside one pixel

A pixel has three 8-bit values: **R, G, B**. The sender chooses one of two modes.

**3 bits per pixel (default).** Bit 0 (the least significant bit) of each value
carries one frame bit:

```
            R            G            B
        ┌────────┐   ┌────────┐   ┌────────┐
        │xxxxxxx▣│   │xxxxxxx▣│   │xxxxxxx▣│     ▣ = hidden bit
        └────────┘   └────────┘   └────────┘
slot:       0            1            2
```

```
write:  v' = (v AND 1111 1110₂) OR b          change: at most ±1
read:   b  =  v' AND 1
```

**6 bits per pixel.** Bits 1 and 0 of each value carry two frame bits (the first
one in bit 1):

```
            R            G            B
        ┌────────┐   ┌────────┐   ┌────────┐
        │xxxxxx▣▣│   │xxxxxx▣▣│   │xxxxxx▣▣│
        └────────┘   └────────┘   └────────┘
slots:     0 1          2 3          4 5        (first slot of each pair = bit 1)
```

```
write:  v' = (v AND 1111 1100₂) OR (2·b₁ + b₀)   change: at most ±3
read:   b₁ = (v' >> 1) AND 1,   b₀ = v' AND 1
```

The upper bits (`xxxxxx`) never change, so the photo looks the same.

**Example.** A pixel with R, G, B = 181, 92, 47 and the first frame bits
`0 1 0 0 1 0`:

| Mode | R | G | B |
|---|---|---|---|
| Original | 181 = `1011 0101` | 92 = `0101 1100` | 47 = `0010 1111` |
| 3 bits/px (bits 0, 1, 0) | 180 = `1011 0100` | 93 = `0101 1101` | 46 = `0010 1110` |
| 6 bits/px (bits 01, 00, 10) | 181 = `1011 01`**`01`** | 92 = `0101 11`**`00`** | 46 = `0010 11`**`10`** |

### 3.3 The tile: one complete copy of the frame

The 16,128 bits are laid out in a small **square tile** of pixels. With `bpp`
hidden bits per pixel (3 or 6), the tile side is the smallest square that fits
the whole frame:

```
T = ⌈ √⌈16128 / bpp⌉ ⌉
```

| Mode | Pixels needed | Tile T × T | Bit slots | Unused slots (set to 0) |
|---|---:|---|---:|---:|
| 3 bits/px | 5,376 | **74 × 74** = 5,476 px | 16,428 | 300 |
| 6 bits/px | 2,688 | **52 × 52** = 2,704 px | 16,224 | 96 |

Inside the tile, frame bit `i` goes to:

```
tile pixel   p = ⌊ i / bpp ⌋          (pixels numbered row by row)
tile row     r = ⌊ p / T ⌋
tile column  c = p mod T
slot         j = i mod bpp            (which value / bit inside the pixel, section 3.2)
```

So, at 3 bits/px, pixel (0, 0) of the tile holds bits 0, 1, 2; pixel (0, 1)
holds bits 3, 4, 5; the first row of 74 pixels holds bits 0–221; and so on.

```
 tile (74 × 74 at 3 bits/px)
 ┌───────────────────────────────────────────┐
 │ px(0,0) px(0,1) px(0,2) …        px(0,73) │  ← bits 0–221  (magic, version, count, Image ID, hop 1 …)
 │ px(1,0) …                                 │  ← bits 222–443
 │   ⋮                                       │
 │ … px(72,47)   | last frame bit = 16127    │
 │ remaining 100 pixels: padding (bits = 0)  │
 └───────────────────────────────────────────┘
```

### 3.4 Repeating the tile over the whole photo

The tile is copied side by side over the entire photo. Pixel `(y, x)` of the
photo carries tile pixel:

```
(y mod T,  x mod T)
```

```
 photo
 ┌──────┬──────┬──────┬──────┬───
 │ tile │ tile │ tile │ tile │ …       every T × T window, wherever it starts,
 ├──────┼──────┼──────┼──────┼───      contains each tile pixel exactly once,
 │ tile │ tile │ tile │ tile │ …       i.e. one complete copy of the chain
 ├──────┼──────┼──────┼──────┼───
 │  …   │  …   │  …   │  …   │
```

Number of complete copies (`⌊H/T⌋ × ⌊W/T⌋`), plus the partial copies at the
edges, which also take part in voting:

| Photo | 3 bits/px (T = 74) | 6 bits/px (T = 52) |
|---|---:|---:|
| 1200 × 1600 | 336 | 690 |
| 1920 × 1080 | 350 | 720 |
| 4080 × 3060 (12 MP) | 2,255 | 4,524 |

The smallest photo that can be stamped is one tile: 74 × 74 px (or 52 × 52).

### 3.5 Saving: lossless PNG only

After the bits are written, the photo is saved as an **8-bit RGB PNG**. PNG is
lossless, so every bit is stored exactly. A camera JPEG (or HEIC, WebP…) is
decoded once at the first hop, with the EXIF rotation applied, and from then on
it stays a PNG. Saving as JPEG would recompute the pixels and destroy the hidden
bits.

The Android app writes the PNG with its own encoder (`Png.kt`): the rows are
split into strips that are filtered and compressed in parallel on all CPU cores,
then joined into one standard zlib stream. The file is an ordinary PNG that any
viewer opens.

---

## 4. Photos: reading the frame back

The reader does not need the original photo, the mode or the position of a crop.

**Step 1 – collect votes.** For every tile cell `(r, c)` and slot `j`, count how
many copies say `1` (N₁) out of all copies (N). Pixel `(y, x)` adds its hidden
bit(s) to cell `(y mod T, x mod T)`.

**Step 2 – find the alignment.** A cropped photo may start in the middle of a
tile, so the grid can be shifted by `(dy, dx)`, with `0 ≤ dy, dx < T`. For each of
the T × T possible shifts (5,476 at T = 74), the reader first checks only the 24
bits that should spell `IC` and version `2`. That is cheap and rejects almost
every wrong shift at once.

**Step 3 – majority vote.** At a shift that passes, each of the 16,128 bits is
decided by majority:

```
b = 1  if 2·N₁ > N,  else 0
```

**Step 4 – check the CRC.** The bits are packed back into 2016 bytes. If the
magic, version, hop count and CRC-32 are all valid, the chain is accepted. Its
**confidence** is reported as

```
C = mean over all bits of | 2·N₁ / N − 1 |     (100 % = every copy agrees)
```

**Step 5 – both modes.** Steps 1–4 run for T = 74 (3 bits/px) and T = 52
(6 bits/px). The valid result with the highest confidence wins, so the reader
needs no setting.

**Speed-up.** Because every tile holds the whole chain, the reader first uses
only a band of 8 tile rows at the top (hundreds of copies), then a band from the
middle of the photo. The whole photo is read only if a band showed the header
but its CRC failed (heavy damage).

**Why it survives crops and damage.**

- Any crop at least one tile wide and high still contains every tile cell, so the
  whole chain is still there (the shift is found in step 2).
- With hundreds of copies per bit, the majority vote corrects random damage: in
  tests the chain survived 35 % (3 bits/px) and 40 % (6 bits/px) of the hidden
  bits flipped at random.

---

## 5. Videos: storing the frame in an MP4 box

Phone videos (MP4, MOV, 3GP) are compressed with lossy H.264/H.265. Pixel bits
would only survive a lossless re-encode, which makes files 10–20× larger and
which phones can't do. So for videos the frame is stored in the **file
container**, not in the pixels.

### 5.1 MP4 boxes

An MP4 file is a list of **boxes**. Each box starts with its size and a
4-letter type:

```
┌──────────┬──────────┬───────────────────┐
│ size (4) │ type (4) │ data …            │     size includes the 8-byte header
└──────────┴──────────┴───────────────────┘
```

A normal phone video looks like this:

```
[ftyp] "this is an MP4"
[moov] the index: duration, tracks, where every frame is (absolute byte offsets)
[mdat] the compressed video and audio data (almost the whole file)
```

Players skip box types they don't recognise. The standard type `uuid` exists
exactly for private data: it is followed by a 16-byte ID chosen by whoever wrote
it.

### 5.2 Our chain box (2,040 bytes)

```
┌──────────────┬──────────┬──────────────────────────────┬───────────────────────────┐
│ size = 2040  │ "uuid"   │ 16-byte chain UUID           │ scrambled frame           │
│ 00 00 07 f8  │ 75756964 │ 6b1f8e3a52c94d07a3e1d9c46f20b8a5 │ 2016 bytes            │
└──────────────┴──────────┴──────────────────────────────┴───────────────────────────┘
   4 bytes        4 bytes        16 bytes                       2016 bytes
```

| Part | Bytes | Content |
|---|---:|---|
| Size | 4 | `0x000007F8` = 2040 |
| Type | 4 | ASCII `uuid` |
| Extended type | 16 | `6b1f8e3a52c94d07a3e1d9c46f20b8a5`, a fixed random ID that marks *our* box |
| Payload | 2016 | The chain frame of section 2, XOR-scrambled |

**Scrambling.** The frame is XOR-ed with a fixed keystream so it can't be read as
plain text in a hex viewer:

```
keystream = SHA-256("imei-chain-mp4/" + 00 00 00 00)
          ‖ SHA-256("imei-chain-mp4/" + 00 00 00 01)
          ‖ …                                       (cut to 2016 bytes)

stored byte k = frame byte k  XOR  keystream byte k
```

For the frame example of section 2, the first stored bytes are:

```
frame      49 43 02 01 3f 1a 9c 0d …
keystream  ba ce 9c 64 72 e6 5b 35 …
stored     f3 8d 9e 65 4d fc c7 38 …
```

XOR-ing again with the same keystream gives the frame back. This only hides the
data from casual viewing; it is **not encryption**, because the keystream is
public in the source code.

### 5.3 Where the box goes

The box is appended at the **very end** of the file:

```
[ftyp][moov][mdat] … [uuid: chain]          ← video bytes before it never move
```

- The `moov` index stores **absolute** byte offsets of the video frames. Adding
  the box at the end shifts nothing, so every offset stays valid and the video
  plays exactly as before. Nothing is decoded or re-encoded.
- **Later hops overwrite the same box in place.** It always has the same size, so
  the file grows by exactly 2,040 bytes once, no matter how many hops follow.
- If the last box has size `0` ("runs to the end of the file"), its real size is
  written first; otherwise it would swallow our box.
- A box in the old 8-hop format (200 bytes) is renamed to `free`, the standard
  "ignore me" type, and a new 2,040-byte box is appended.

---

## 6. Videos: reading the frame back

1. Walk the top-level boxes from the start of the file (size + type, using the
   64-bit size when the size field is `1`).
2. Find the box with type `uuid`, our 16-byte UUID and size 2040.
3. Read its 2016-byte payload and XOR it with the keystream.
4. Check magic, version and CRC-32 (section 2). If they are valid, that is the
   chain; otherwise the video has no chain.

There is one copy of the frame, so there is nothing to vote on. Because the box
sits in the file container, the video quality is untouched.

---

## 7. The Image ID

The 8-byte Image ID lets the dashboard put every copy of the same original into
one forwarding tree. It is computed once, when the first phone starts the chain,
from data that stamping never changes:

**Photo:**

```
Image ID = first 8 bytes of SHA-256(
               width (4 bytes, big-endian) ‖ height (4 bytes, big-endian) ‖
               every R, G, B value, row by row, AND 1111 1100₂ )
```

Clearing the two lowest bits removes everything either mode could have written,
so the ID is the same before and after stamping, in both modes.

**Video:**

```
Image ID = first 8 bytes of SHA-256( the whole moov box )
```

Our box never touches `moov`, so every copy of the video gives the same ID.

---

## 8. What happens at each hop

**Photo:**

1. Open: a PNG is decoded and its chain read (section 4). Any other format is
   decoded and has no chain yet.
2. If the last hop already belongs to this phone, nothing is added (a phone
   that sends the same photo to several people appears once, and the copies
   branch from it).
3. Otherwise add a hop (device ID, IMEI, time, role). A new chain also gets its
   Image ID (section 7).
4. Pack the frame, write it into **every** tile of the photo in the chosen mode
   (the whole frame is rewritten, so the mode may change between hops), and save
   the PNG.

**Video:**

1. Copy the video, find our box and read the chain (section 6).
2. Add the hop as for photos.
3. Overwrite the box with the new scrambled frame, or append it if this is the
   first hop.

At most 100 hops fit; the 101st is refused.

---

## 9. What survives and what doesn't

| Operation | Photo (pixels) | Video (box) | Why |
|---|:---:|:---:|---|
| Bluetooth, USB, e-mail attachment, cloud drive | ✅ | ✅ | File bytes copied exactly |
| Sending as a *document* in a chat app | ✅ | ✅ | File bytes copied exactly |
| Crop at least one tile, saved as PNG | ✅ | n/a | Every tile holds the whole chain |
| Crop smaller than one tile | ❌ | n/a | Not all bits are present |
| Up to 35–40 % random bit damage | ✅ | ❌ | Photo: majority vote; video: single copy, CRC rejects |
| Saving as JPEG (even quality 100) | ❌ | n/a | Pixels recomputed, low bits lost |
| Resize (even to 99 %), rotate, blur, filters | ❌ | n/a | Pixels recomputed |
| Screenshot, editing and saving | ❌ | ❌ | A new file is created |
| Chat app "photo/video" mode, social media | ❌ | ❌ | Re-compressed and resized |
| Re-encoding or trimming the video | n/a | ❌ | A new file is created |

Anyone who knows this format can read, remove or forge the chain: the CRC-32
detects accidents, not tampering. Treat the chain as a provenance hint, not
proof.

---

## 10. Quick reference

| Item | Value |
|---|---|
| Frame | 2016 bytes = 16,128 bits; magic `IC`, version 2, ≤ 100 hops, CRC-32 |
| Hop | 20 bytes: device ID 8, IMEI 7, time 4, role 1 |
| Bit order | Most significant bit of each byte first |
| Photo, 3 bits/px | Bit 0 of R, G, B; tile 74 × 74 px; change ≤ ±1; ≈ 51 dB PSNR |
| Photo, 6 bits/px | Bits 1, 0 of R, G, B; tile 52 × 52 px; change ≤ ±3; ≈ 43–44 dB PSNR |
| Bit position | pixel `⌊i/bpp⌋` of the tile, slot `i mod bpp`; photo pixel `(y, x)` → tile pixel `(y mod T, x mod T)` |
| Photo file | 8-bit RGB PNG (lossless) |
| Video box | `uuid` box, 2,040 bytes, UUID `6b1f8e3a52c94d07a3e1d9c46f20b8a5`, at the end of the file |
| Video scrambling | XOR with SHA-256("imei-chain-mp4/" + 4-byte counter) keystream |
| Image ID | Photo: SHA-256 of size + pixels with 2 low bits cleared; video: SHA-256 of `moov`; first 8 bytes |
