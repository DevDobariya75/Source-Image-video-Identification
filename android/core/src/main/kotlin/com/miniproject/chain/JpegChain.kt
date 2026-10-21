package com.miniproject.chain

import java.security.MessageDigest

/**
 * Chain storage for photos: the frame's bits go into the LSBs of the JPEG's
 * luminance DCT coefficients, tiled over the photo and read back by majority
 * vote. Mirror of chain_imei.py + jpeg_dct.py (tile of 16 x 16 blocks,
 * 16 low frequencies, only coefficients with |c| >= 2).
 */
object JpegChain {
    const val TILE = 16
    const val MIN_MAGNITUDE = 2

    /** Zig-zag positions 1..16 as (row, col) - same order as FREQ_POSITIONS in jpeg_dct.py. */
    private val FREQ_POSITIONS = arrayOf(
        0 to 1, 1 to 0, 2 to 0, 1 to 1, 0 to 2, 0 to 3, 1 to 2, 2 to 1,
        3 to 0, 4 to 0, 3 to 1, 2 to 2, 1 to 3, 0 to 4, 0 to 5, 1 to 4,
    )
    val K = FREQ_POSITIONS.size
    val FREQ_NATURAL = IntArray(K) { FREQ_POSITIONS[it].first * 8 + FREQ_POSITIONS[it].second }
    internal val SLOT_OF_NATURAL = IntArray(64) { -1 }.also { a -> FREQ_NATURAL.forEachIndexed { j, n -> a[n] = j } }

    /** slot (r, c, k) of a tile -> frame bit. Slot number s = k*T*T + r*T + c, bit = s mod n. */
    private fun slotBit(n: Int) = IntArray(TILE * TILE * K) { i ->
        val r = i / (TILE * K)
        val c = (i / K) % TILE
        val k = i % K
        (k * TILE * TILE + r * TILE + c) % n
    }

    class ReadResult(val chain: Chain, val dy: Int, val dx: Int, val confidence: Double, val votesMin: Int)

    /** Majority-vote the chain frame for every grid offset; return the best valid one. */
    fun decode(jpeg: JpegCoefficients): ReadResult? {
        val y = jpeg.luma
        val n = ChainPayload.FRAME_BITS
        val cells = TILE * TILE * K
        val ones = IntArray(cells)
        val total = IntArray(cells)
        for (row in 0 until y.blocksHigh) for (col in 0 until y.blocksWide) {
            val cellBase = ((row % TILE) * TILE + (col % TILE)) * K
            val base = (row * y.blocksWide + col) * 64
            for (j in 0 until K) {
                val m = kotlin.math.abs(y.coef[base + FREQ_NATURAL[j]].toInt())
                if (m >= MIN_MAGNITUDE) {
                    total[cellBase + j]++
                    ones[cellBase + j] += m and 1
                }
            }
        }

        val slotBit = slotBit(n)
        var best: ReadResult? = null
        val b1 = LongArray(n)
        val bn = LongArray(n)
        for (dy in 0 until TILE) for (dx in 0 until TILE) {
            java.util.Arrays.fill(b1, 0)
            java.util.Arrays.fill(bn, 0)
            // np.roll(x, (-dy, -dx)): result[r][c] = x[(r + dy) % T][(c + dx) % T]
            for (r in 0 until TILE) for (c in 0 until TILE) {
                val src = (((r + dy) % TILE) * TILE + (c + dx) % TILE) * K
                val dst = (r * TILE + c) * K
                for (k in 0 until K) {
                    val bit = slotBit[dst + k]
                    b1[bit] += ones[src + k].toLong()
                    bn[bit] += total[src + k].toLong()
                }
            }
            if (bn.any { it == 0L }) continue
            val frame = ByteArray(ChainPayload.FRAME_BYTES)
            var margin = 0.0
            for (i in 0 until n) {
                if (2 * b1[i] > bn[i]) frame[i ushr 3] = (frame[i ushr 3].toInt() or (0x80 ushr (i and 7))).toByte()
                margin += kotlin.math.abs(2.0 * b1[i] / bn[i] - 1.0)
            }
            val chain = try { ChainPayload.unpack(frame) } catch (e: ChainException) { continue }
            val confidence = margin / n
            if (best == null || confidence > best.confidence) {
                best = ReadResult(chain, dy, dx, confidence, bn.min().toInt())
            }
        }
        return best
    }

    /**
     * Image ID of a new chain: SHA-256 over data the chain never changes -
     * the DC coefficient of every luminance block, then all coefficients of
     * the two chroma components (int16 little-endian), first 8 bytes.
     * Same bytes as content_id() in chain_imei.py.
     */
    fun contentId(jpeg: JpegCoefficients): ByteArray {
        val md = MessageDigest.getInstance("SHA-256")
        val y = jpeg.luma
        val dc = ByteArray(y.blocksHigh * y.blocksWide * 2)
        for (b in 0 until y.blocksHigh * y.blocksWide) {
            val v = y.coef[b * 64].toInt()
            dc[2 * b] = v.toByte()
            dc[2 * b + 1] = (v ushr 8).toByte()
        }
        md.update(dc)
        for (comp in jpeg.components.drop(1).take(2)) {
            val arr = ByteArray(comp.coef.size * 2)
            for (i in comp.coef.indices) {
                val v = comp.coef[i].toInt()
                arr[2 * i] = v.toByte()
                arr[2 * i + 1] = (v ushr 8).toByte()
            }
            md.update(arr)
        }
        return md.digest().copyOf(ChainPayload.IMAGE_ID_BYTES)
    }

    /** Write the chain frame into the coefficients (in memory; call toByteArray() after). */
    fun write(jpeg: JpegCoefficients, chain: Chain) {
        val frame = ChainPayload.pack(chain)
        val n = ChainPayload.FRAME_BITS
        val bits = IntArray(n) { (frame[it ushr 3].toInt() ushr (7 - (it and 7))) and 1 }
        val slotBit = slotBit(n)
        val y = jpeg.luma

        // Every bit needs at least one usable coefficient, or it can't be read back.
        val votes = IntArray(n)
        for (row in 0 until y.blocksHigh) for (col in 0 until y.blocksWide) {
            val cellBase = ((row % TILE) * TILE + (col % TILE)) * K
            val base = (row * y.blocksWide + col) * 64
            for (j in 0 until K) {
                if (kotlin.math.abs(y.coef[base + FREQ_NATURAL[j]].toInt()) >= MIN_MAGNITUDE) votes[slotBit[cellBase + j]]++
            }
        }
        if (votes.any { it == 0 }) {
            throw ChainException("Photo has too little detail to hold the chain. Use a larger or more textured photo.")
        }

        for (row in 0 until y.blocksHigh) for (col in 0 until y.blocksWide) {
            val block = row * y.blocksWide + col
            val cellBase = ((row % TILE) * TILE + (col % TILE)) * K
            for (j in 0 until K) {
                val natural = FREQ_NATURAL[j]
                val m = kotlin.math.abs(y.coef[block * 64 + natural].toInt())
                if (m >= MIN_MAGNITUDE && (m and 1) != bits[slotBit[cellBase + j]]) {
                    jpeg.flipLumaLsb(block, j, natural)
                }
            }
        }
    }
}
