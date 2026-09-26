#!/usr/bin/env bash
# End-to-end test for embed_imei.py + extract_imei.py:
#   1. generate a dummy cover image
#   2. embed a dummy IMEI
#   3. severely crop the watermarked image at an odd (grid-misaligned) offset
#   4. flip a large fraction of the remaining LSBs at random (simulated damage)
#   5. extract and check that majority voting still recovers the IMEI
#
# Usage:  bash run_test.sh            (uses `python` on PATH)
#         PYTHON=python3 bash run_test.sh
set -euo pipefail

PY="${PYTHON:-python}"
IMEI="356938035643809"
cd "$(dirname "$0")"
WORK="test_out"
mkdir -p "$WORK"

echo "=== 1. Generating dummy 800x600 cover image ==="
"$PY" - <<'EOF'
import cv2, numpy as np
h, w = 600, 800
y, x = np.mgrid[0:h, 0:w]
img = np.dstack([x * 255 // w, y * 255 // h, (x + y) * 255 // (w + h)]).astype(np.uint8)
img = cv2.add(img, np.random.default_rng(1).integers(0, 40, img.shape, dtype=np.uint8))
cv2.imwrite("test_out/input.png", img)
EOF

echo; echo "=== 2. Embedding IMEI $IMEI ==="
"$PY" embed_imei.py --image "$WORK/input.png" --payload "$IMEI" --output "$WORK/watermarked.png"

echo; echo "=== 3. Cropping to 97x61 px at offset (+333,+217) - keeps ~1.2% of the image ==="
if command -v magick >/dev/null 2>&1; then
    # ImageMagick 7. (`convert` is avoided: on Windows it is a disk utility.)
    magick "$WORK/watermarked.png" -crop 97x61+333+217 +repage "$WORK/cropped.png"
    echo "(cropped with ImageMagick)"
else
    "$PY" -c "import cv2; i=cv2.imread('$WORK/watermarked.png'); cv2.imwrite('$WORK/cropped.png', i[217:217+61, 333:333+97])"
    echo "(ImageMagick not found - cropped with Python)"
fi

echo; echo "=== 4. Corrupting: flipping 30% of all remaining LSBs at random ==="
"$PY" - <<'EOF'
import cv2, numpy as np
img = cv2.imread("test_out/cropped.png")
flip = (np.random.default_rng(7).random(img.shape) < 0.30).astype(np.uint8)
cv2.imwrite("test_out/cropped_noisy.png", img ^ flip)   # XOR with 1 flips the LSB
EOF

run_extract () {  # $1 = label, $2 = image, $3 = "pass" | "fail" (expected)
    echo; echo "=== $1 ==="
    set +e
    out=$("$PY" extract_imei.py --image "$2" --payload-length 15)
    set -e
    echo "$out"
    if echo "$out" | grep -q "Extracted payload : $IMEI"; then got=pass; else got=fail; fi
    if [ "$got" = "$3" ]; then echo ">> OK (expected $3)"; else echo ">> TEST FAILED (expected $3, got $got)"; FAILED=1; fi
}

FAILED=0
run_extract "5a. Extract from full watermarked image"               "$WORK/watermarked.png"   pass
run_extract "5b. Extract from 97x61 crop"                            "$WORK/cropped.png"       pass
run_extract "5c. Extract from 97x61 crop + 30% LSB noise (voting)"   "$WORK/cropped_noisy.png" pass

# Control: a single 8x8 tile of the noisy crop has only ONE copy, so there is
# nothing to vote with - 30% bit errors should make it fail. This shows that
# it is the majority vote that rescues case 5c.
"$PY" -c "import cv2; i=cv2.imread('$WORK/cropped_noisy.png'); cv2.imwrite('$WORK/single_tile_noisy.png', i[:8, :8])"
run_extract "5d. Control: single 8x8 copy + 30% noise (no redundancy)" "$WORK/single_tile_noisy.png" fail

echo
if [ "$FAILED" = 0 ]; then echo "ALL TESTS PASSED"; else echo "SOME TESTS FAILED"; exit 1; fi
