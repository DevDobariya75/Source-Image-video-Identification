package com.miniproject.chain

import java.security.MessageDigest
import kotlin.math.ceil
import kotlin.math.sqrt

/**
 * Chain storage for photos: the 2016-byte frame goes into the RGB pixel LSBs
 * as a square tile repeated over the whole photo, read back by majority
 * vote. Mirror of LSB/pixel_chain.py (same layout, same Image ID).
 *
 *   3 bits/pixel: bit 0 of R, G, B            tile 74 x 74 px, values change by <= 1
 *   6 bits/pixel: bits 1 and 0 of R, G, B     tile 52 x 52 px, values change by <= 3
 *
 * Frame bit i -> tile pixel p = i / bpp (row p / side, column p % side),
 * slot j = i % bpp. Photo pixel (y, x) carries tile pixel (y % side, x % side).
 */
object PixelChain {
    /** Supported modes, in bits per pixel. */
    val MODES = intArrayOf(3, 6)
    const val DEFAULT_BPP = 3

    fun tileSide(bpp: Int): Int = ceil(sqrt(ceil(ChainPayload.FRAME_BITS / bpp.toDouble()))).toInt()

    class ReadResult(
        val chain: Chain,
        val bitsPerPixel: Int,
        val dy: Int,
        val dx: Int,
        val confidence: Double,
        val votesMin: Int,
    )

    /** Rows of tiles read in the fast first pass (8 x 74 px = 592 rows: hundreds of copies). */
    private const val BAND_TILES = 8

    /**
     * The chain in [img] (either mode, any crop offset), or null.
     *
     * Fast path: every tile holds the whole chain, so a band of [BAND_TILES]
     * tile rows already gives hundreds of votes per bit. The top band is read
     * first, then one from the middle of the photo; the whole photo is only
     * read if a band showed the chain's header but its CRC failed (heavy damage).
     */
    fun decode(img: RgbImage): ReadResult? =
        MODES.toList().mapNotNull { decodeFast(img, it) }.maxByOrNull { it.confidence }

    private fun decodeFast(img: RgbImage, bpp: Int): ReadResult? {
        val side = tileSide(bpp)
        val h = img.height
        val band = BAND_TILES * side
        if (h <= 2 * band) return decodeMode(img, bpp, 0, h).first
        val mid = (h / 2 / side) * side
        var sawHeader = false
        for (y0 in intArrayOf(0, mid)) {
            val (res, header) = decodeMode(img, bpp, y0, y0 + band)
            if (res != null) return res
            sawHeader = sawHeader || header
        }
        return if (sawHeader) decodeMode(img, bpp, 0, h).first else null
    }

    /** Number of rows y in [y0, y1) with y % side == r. */
    private fun rowsWithResidue(y0: Int, y1: Int, r: Int, side: Int): Int {
        fun below(n: Int) = if (r < n) (n - 1 - r) / side + 1 else 0
        return below(y1) - below(y0)
    }

    /** Majority-vote rows [y0, y1). Returns (best chain or null, whether any offset matched the header). */
    private fun decodeMode(img: RgbImage, bpp: Int, y0: Int, y1: Int): Pair<ReadResult?, Boolean> {
        val side = tileSide(bpp)
        val w = img.width
        val h = y1 - y0
        if (w < side || h < side) return null to false
        val two = bpp == 6
        val px = img.rgb

        // 1. Votes: number of 1s per (tile cell, slot).
        val ones = IntArray(side * side * bpp)
        for (y in y0 until y1) {
            val rowBase = (y % side) * side
            var p = y * w * 3
            var cx = 0
            for (x in 0 until w) {
                val base = (rowBase + cx) * bpp
                for (ch in 0 until 3) {
                    val v = px[p + ch].toInt()
                    if (two) {
                        ones[base + 2 * ch] += (v ushr 1) and 1
                        ones[base + 2 * ch + 1] += v and 1
                    } else {
                        ones[base + ch] += v and 1
                    }
                }
                p += 3
                if (++cx == side) cx = 0
            }
        }
        val rowCount = IntArray(side) { r -> rowsWithResidue(y0, y1, r, side) }
        val colCount = IntArray(side) { c -> if (c < w) (w - 1 - c) / side + 1 else 0 }

        val n = ChainPayload.FRAME_BITS
        val tr = IntArray(n) { (it / bpp) / side }
        val tc = IntArray(n) { (it / bpp) % side }
        val slot = IntArray(n) { it % bpp }
        val head = ChainPayload.MAGIC + byteArrayOf(ChainPayload.VERSION.toByte())
        val headBits = head.size * 8

        var best: ReadResult? = null
        var sawHeader = false
        for (dy in 0 until side) for (dx in 0 until side) {
            // 2. Quick test of magic + version at this offset.
            var ok = true
            for (i in 0 until headBits) {
                val r = Math.floorMod(tr[i] - dy, side)
                val c = Math.floorMod(tc[i] - dx, side)
                val bit = if (2 * ones[(r * side + c) * bpp + slot[i]] > rowCount[r] * colCount[c]) 1 else 0
                if (bit != (head[i ushr 3].toInt() ushr (7 - (i and 7))) and 1) { ok = false; break }
            }
            if (!ok) continue
            sawHeader = true

            // 3. Full majority vote, then the CRC decides.
            val frame = ByteArray(ChainPayload.FRAME_BYTES)
            var margin = 0.0
            var votesMin = Int.MAX_VALUE
            for (i in 0 until n) {
                val r = Math.floorMod(tr[i] - dy, side)
                val c = Math.floorMod(tc[i] - dx, side)
                val o = ones[(r * side + c) * bpp + slot[i]]
                val t = rowCount[r] * colCount[c]
                if (2 * o > t) frame[i ushr 3] = (frame[i ushr 3].toInt() or (0x80 ushr (i and 7))).toByte()
                margin += kotlin.math.abs(2.0 * o / t - 1.0)
                if (t < votesMin) votesMin = t
            }
            val chain = try { ChainPayload.unpack(frame) } catch (e: ChainException) { continue }
            val res = ReadResult(chain, bpp, dy, dx, margin / n, votesMin)
            if (best == null || res.confidence > best.confidence) best = res
        }
        return best to sawHeader
    }

    /** Write the chain into the pixels of [img] (in place), repeated over the whole photo. */
    fun write(img: RgbImage, chain: Chain, bpp: Int = DEFAULT_BPP) {
        if (bpp !in MODES) throw ChainException("Bits per pixel must be 3 or 6.")
        val side = tileSide(bpp)
        if (img.width < side || img.height < side) {
            throw ChainException("Photo is too small (${img.width}x${img.height}): it must be at least " +
                "${side}x$side pixels to hold the chain.")
        }
        val frame = ChainPayload.pack(chain)
        val n = ChainPayload.FRAME_BITS
        val slots = IntArray(side * side * bpp)
        for (i in 0 until n) slots[i] = (frame[i ushr 3].toInt() ushr (7 - (i and 7))) and 1
        // Low-bit value of every channel of every tile pixel.
        val vals = IntArray(side * side * 3) { k ->
            if (bpp == 3) slots[k] else {
                val cell = k / 3
                val ch = k % 3
                (slots[cell * 6 + 2 * ch] shl 1) or slots[cell * 6 + 2 * ch + 1]
            }
        }
        val keep = if (bpp == 3) 0xFE else 0xFC
        val px = img.rgb
        val w = img.width
        for (y in 0 until img.height) {
            val rowBase = (y % side) * side
            var p = y * w * 3
            var cx = 0
            for (x in 0 until w) {
                val v = (rowBase + cx) * 3
                px[p] = ((px[p].toInt() and keep) or vals[v]).toByte()
                px[p + 1] = ((px[p + 1].toInt() and keep) or vals[v + 1]).toByte()
                px[p + 2] = ((px[p + 2].toInt() and keep) or vals[v + 2]).toByte()
                p += 3
                if (++cx == side) cx = 0
            }
        }
    }

    /**
     * Image ID of a new chain: SHA-256 of width, height (big-endian) and every
     * R, G, B value with its two lowest bits cleared, first 8 bytes. Same as
     * content_id() in pixel_chain.py; stamping never changes it.
     */
    fun contentId(img: RgbImage): ByteArray {
        val md = MessageDigest.getInstance("SHA-256")
        md.update(java.nio.ByteBuffer.allocate(8).putInt(img.width).putInt(img.height).array())
        val buf = ByteArray(1 shl 16)
        var i = 0
        val px = img.rgb
        while (i < px.size) {
            val k = minOf(buf.size, px.size - i)
            for (j in 0 until k) buf[j] = (px[i + j].toInt() and 0xFC).toByte()
            md.update(buf, 0, k)
            i += k
        }
        return md.digest().copyOf(ChainPayload.IMAGE_ID_BYTES)
    }
}
