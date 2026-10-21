package com.miniproject.chain

/**
 * Reads the quantized DCT coefficients of a Huffman-coded JPEG (baseline,
 * extended sequential or progressive) and can flip the LSB of chosen
 * luminance coefficients WITHOUT re-encoding the photo.
 *
 * Why flipping is safe
 * --------------------
 * The bit that holds LSB(|c|) is always a single "raw" bit in the
 * entropy-coded data, never part of a Huffman code:
 *
 * * Sequential JPEG, or a progressive first scan with point transform 0:
 *   c is stored as a Huffman code for its bit length ("size") plus `size`
 *   raw bits. For |c| >= 2 flipping the LSB of |c| keeps the size, so only
 *   the LAST raw bit changes (for negative values the raw bits are
 *   inverted, so it is still exactly that bit).
 * * Progressive refinement scan with point transform 0: for a coefficient
 *   that was already non-zero, bit 0 of |c| is sent as one raw
 *   "correction" bit.
 *
 * We record where that bit is while decoding, flip it, and re-insert the
 * 0x00 "stuffing" bytes. Every other byte of the file (EXIF, tables, ...)
 * is kept as-is.
 *
 * Coefficient layout matches Python's jpeglib: per component, blocks in
 * row-major order (row, col), 64 coefficients per block in natural
 * (row-major) order, block counts ceil(dim * samp / (max_samp * 8)).
 */
class JpegCoefficients private constructor(
    private val bytes: ByteArray,
    val width: Int,
    val height: Int,
    val progressive: Boolean,
    val components: List<Component>,
    private val scans: List<Scan>,
    private val lsbPos: IntArray,
    private val lsbScan: ShortArray,
) {
    class Component(
        val id: Int,
        val h: Int,
        val v: Int,
        val blocksWide: Int,
        val blocksHigh: Int,
    ) {
        /** blocksHigh * blocksWide * 64 coefficients, natural order inside a block. */
        val coef = ShortArray(blocksHigh * blocksWide * 64)
        internal var seen = false

        fun get(row: Int, col: Int, k: Int): Int = coef[(row * blocksWide + col) * 64 + k].toInt()
    }

    /** Entropy-coded data of one scan, with stuffing removed. */
    internal class Scan(
        val dataStart: Int,                    // offset of the first entropy byte in the file
        val dataEnd: Int,                      // offset of the marker that ends the scan
        val data: ByteArray,                   // de-stuffed bytes of all segments, concatenated
        val segmentStarts: IntArray,           // offset of each restart segment in `data`
        val segmentEnds: IntArray,
        val markers: List<ByteArray>,          // RST marker bytes after segments 0..n-2
    ) {
        var modified = false
    }

    /** Luminance (first component). */
    val luma: Component get() = components[0]

    /**
     * Flip LSB(|c|) of luminance block [block], embedding frequency [slot]
     * (natural index [natural]), in the file data and in [luma].
     * Throws [UnsupportedJpegException] if that bit is not stored in the file
     * (a progressive JPEG whose refinement scans stop before bit 0).
     */
    internal fun flipLumaLsb(block: Int, slot: Int, natural: Int) {
        val idx = block * JpegChain.K + slot
        val pos = lsbPos[idx]
        if (pos < 0) throw UnsupportedJpegException("progressive JPEG without full-precision scans")
        val scan = scans[lsbScan[idx].toInt()]
        scan.data[pos ushr 3] = (scan.data[pos ushr 3].toInt() xor (0x80 ushr (pos and 7))).toByte()
        scan.modified = true
        val i = block * 64 + natural
        val c = luma.coef[i].toInt()
        val mag = kotlin.math.abs(c) xor 1
        luma.coef[i] = (if (c < 0) -mag else mag).toShort()
    }

    /** The JPEG file with all flips applied. */
    fun toByteArray(): ByteArray {
        val out = java.io.ByteArrayOutputStream(bytes.size + 4096)
        var pos = 0
        for (scan in scans.filter { it.modified }) {
            out.write(bytes, pos, scan.dataStart - pos)
            for (s in scan.segmentStarts.indices) {
                for (i in scan.segmentStarts[s] until scan.segmentEnds[s]) {
                    val b = scan.data[i].toInt() and 0xFF
                    out.write(b)
                    if (b == 0xFF) out.write(0)                // byte stuffing
                }
                if (s < scan.markers.size) out.write(scan.markers[s])
            }
            pos = scan.dataEnd
        }
        out.write(bytes, pos, bytes.size - pos)
        return out.toByteArray()
    }

    companion object {
        /** Natural-order index of zig-zag position k. */
        val NATURAL_ORDER = intArrayOf(
            0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
            12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
            35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
            58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
        )

        fun isJpeg(head: ByteArray): Boolean =
            head.size >= 3 && head[0] == 0xFF.toByte() && head[1] == 0xD8.toByte() && head[2] == 0xFF.toByte()

        /**
         * Parse and decode [bytes]. Throws [UnsupportedJpegException] for JPEGs this
         * reader can't edit in place (arithmetic-coded, lossless, hierarchical), and
         * [ChainException] for corrupt files.
         */
        fun read(bytes: ByteArray): JpegCoefficients = Parser(bytes).parse()
    }

    // -----------------------------------------------------------------------
    // Huffman decoding
    // -----------------------------------------------------------------------

    private class Huffman(bits: IntArray, private val values: IntArray) {
        // Canonical code tables (JPEG spec, Annex C / F.2.2.3).
        private val maxCode = IntArray(18) { -1 }
        private val valPtr = IntArray(17)
        private val minCode = IntArray(17)
        // 9-bit lookahead: (length << 8) | value, or 0 if the code is longer.
        private val lookup = IntArray(1 shl LOOKAHEAD)

        init {
            var code = 0
            var k = 0
            for (len in 1..16) {
                valPtr[len] = k
                minCode[len] = code
                code += bits[len - 1]
                k += bits[len - 1]
                maxCode[len] = if (bits[len - 1] > 0) code - 1 else -1
                code = code shl 1
            }
            maxCode[17] = Int.MAX_VALUE
            code = 0
            k = 0
            for (len in 1..16) {
                for (i in 0 until bits[len - 1]) {
                    if (len <= LOOKAHEAD) {
                        val shift = LOOKAHEAD - len
                        val base = code shl shift
                        for (j in 0 until (1 shl shift)) lookup[base + j] = (len shl 8) or values[k]
                    }
                    code++
                    k++
                }
                code = code shl 1
            }
        }

        fun decode(r: BitReader): Int {
            val look = lookup[r.peek(LOOKAHEAD)]
            if (look != 0) {
                r.skip(look ushr 8)
                return look and 0xFF
            }
            var code = r.bits(LOOKAHEAD)
            var len = LOOKAHEAD
            while (code > maxCode[len]) {
                code = (code shl 1) or r.bits(1)
                len++
                if (len > 16) throw ChainException("Corrupt JPEG (bad Huffman code).")
            }
            return values[valPtr[len] + code - minCode[len]]
        }

        companion object { const val LOOKAHEAD = 9 }
    }

    internal class BitReader(private val data: ByteArray, var pos: Int, var end: Int) {
        // Bits past `end` read as 0 (like libjpeg). Peeking ahead past the end is
        // normal near a segment boundary; only *consuming* past it means corruption.
        var overrun = false

        fun checkEnd() { if (pos > end) overrun = true }

        private fun bit(p: Int): Int =
            if (p >= end) 0 else (data[p ushr 3].toInt() ushr (7 - (p and 7))) and 1

        fun peek(n: Int): Int {
            var v = 0
            for (i in 0 until n) v = (v shl 1) or bit(pos + i)
            return v
        }

        fun skip(n: Int) { pos += n }

        fun bits(n: Int): Int {
            val v = peek(n)
            pos += n
            return v
        }
    }

    // -----------------------------------------------------------------------
    // Parsing
    // -----------------------------------------------------------------------

    private class ScanComp(val comp: Component, val dc: Huffman?, val ac: Huffman?)

    private class Parser(val b: ByteArray) {
        var width = 0
        var height = 0
        var progressive = false
        var comps = listOf<Component>()
        val dcTables = arrayOfNulls<Huffman>(4)
        val acTables = arrayOfNulls<Huffman>(4)
        var restartInterval = 0
        val scans = mutableListOf<Scan>()
        lateinit var lsbPos: IntArray
        lateinit var lsbScan: ShortArray
        var hMax = 1
        var vMax = 1

        fun u16(p: Int) = ((b[p].toInt() and 0xFF) shl 8) or (b[p + 1].toInt() and 0xFF)
        fun u8(p: Int) = b[p].toInt() and 0xFF

        fun parse(): JpegCoefficients {
            if (!isJpeg(b)) throw ChainException("Not a JPEG file.")
            var p = 2
            loop@ while (p < b.size) {
                if (u8(p) != 0xFF) throw ChainException("Corrupt JPEG (marker expected at $p).")
                var marker = u8(p + 1)
                while (marker == 0xFF) { p++; marker = u8(p + 1) }        // fill bytes
                p += 2
                when (marker) {
                    0xD9 -> break@loop                                      // EOI
                    in 0xD0..0xD7, 0x01 -> continue@loop                    // no length
                }
                if (p + 2 > b.size) throw ChainException("Truncated JPEG.")
                val len = u16(p)
                val seg = p + 2
                val segEnd = p + len
                if (segEnd > b.size || len < 2) throw ChainException("Truncated JPEG segment.")
                when (marker) {
                    0xC0, 0xC1 -> parseSof(seg, false)
                    0xC2 -> parseSof(seg, true)
                    0xC3, 0xC7, 0xCB, 0xCF -> throw UnsupportedJpegException("lossless JPEG")
                    0xC5, 0xC6, 0xC9, 0xCA, 0xCD, 0xCE -> throw UnsupportedJpegException("hierarchical / arithmetic JPEG")
                    0xCC -> throw UnsupportedJpegException("arithmetic-coded JPEG")
                    0xC4 -> parseDht(seg, segEnd)
                    0xDD -> restartInterval = u16(seg)
                    0xDA -> { p = parseScan(seg, segEnd); continue@loop }
                }
                p = segEnd
            }
            if (comps.isEmpty()) throw ChainException("JPEG has no frame header.")
            if (comps.any { !it.seen }) throw ChainException("JPEG is missing image data.")
            return JpegCoefficients(b, width, height, progressive, comps, scans, lsbPos, lsbScan)
        }

        fun parseSof(p: Int, isProgressive: Boolean) {
            if (comps.isNotEmpty()) throw UnsupportedJpegException("multi-frame JPEG")
            progressive = isProgressive
            height = u16(p + 1)
            width = u16(p + 3)
            val n = u8(p + 5)
            if (height == 0) throw UnsupportedJpegException("JPEG with DNL marker")
            val raw = (0 until n).map { i -> Triple(u8(p + 6 + 3 * i), u8(p + 7 + 3 * i) ushr 4, u8(p + 7 + 3 * i) and 15) }
            hMax = raw.maxOf { it.second }
            vMax = raw.maxOf { it.third }
            comps = raw.map { (id, h, v) ->
                Component(id, h, v, ceilDiv(width * h, hMax * 8), ceilDiv(height * v, vMax * 8))
            }
            val n0 = comps[0].blocksHigh * comps[0].blocksWide * JpegChain.K
            lsbPos = IntArray(n0) { -1 }
            lsbScan = ShortArray(n0)
        }

        fun parseDht(start: Int, end: Int) {
            var p = start
            while (p < end) {
                val tc = u8(p) ushr 4
                val th = u8(p) and 15
                val counts = IntArray(16) { u8(p + 1 + it) }
                val total = counts.sum()
                val values = IntArray(total) { u8(p + 17 + it) }
                val table = Huffman(counts, values)
                if (tc == 0) dcTables[th and 3] = table else acTables[th and 3] = table
                p += 17 + total
            }
        }

        /** Parse SOS + its entropy data; returns the offset of the next marker. */
        fun parseScan(seg: Int, segEnd: Int): Int {
            if (comps.isEmpty()) throw ChainException("Scan before frame header.")
            val n = u8(seg)
            val ss = u8(seg + 1 + 2 * n)
            val se = u8(seg + 2 + 2 * n)
            val ah = u8(seg + 3 + 2 * n) ushr 4
            val al = u8(seg + 3 + 2 * n) and 15
            val isDc = ss == 0
            val scanComps = (0 until n).map { i ->
                val id = u8(seg + 1 + 2 * i)
                val tables = u8(seg + 2 + 2 * i)
                val c = comps.firstOrNull { it.id == id } ?: throw ChainException("Unknown component $id.")
                c.seen = true
                // Only the tables this kind of scan actually uses must exist.
                val needDc = isDc && ah == 0
                val needAc = !progressive || !isDc
                ScanComp(
                    c,
                    if (needDc) dcTables[(tables ushr 4) and 3] ?: throw ChainException("Missing DC table.") else null,
                    if (needAc) acTables[tables and 3] ?: throw ChainException("Missing AC table.") else null,
                )
            }
            // Split the entropy data into restart segments and remove stuffing.
            val dataStart = segEnd
            val data = java.io.ByteArrayOutputStream()
            val starts = mutableListOf(0)
            val ends = mutableListOf<Int>()
            val markers = mutableListOf<ByteArray>()
            var p = dataStart
            while (true) {
                if (p >= b.size) throw ChainException("Truncated JPEG (no end of image).")
                val x = u8(p)
                if (x != 0xFF) { data.write(x); p++; continue }
                var q = p + 1
                while (q < b.size && u8(q) == 0xFF) q++                 // fill bytes
                if (q >= b.size) throw ChainException("Truncated JPEG.")
                val m = u8(q)
                when {
                    m == 0x00 -> { data.write(0xFF); p = q + 1 }
                    m in 0xD0..0xD7 -> {
                        ends += data.size()
                        markers += b.copyOfRange(p, q + 1)
                        starts += data.size()
                        p = q + 1
                    }
                    else -> { ends += data.size(); break }               // end of scan
                }
            }
            val scan = Scan(dataStart, p, data.toByteArray(), starts.toIntArray(), ends.toIntArray(), markers)
            scans += scan
            ScanDecoder(this, scan, scans.size - 1, scanComps, ss, se, ah, al).run()
            return p
        }

        fun ceilDiv(a: Int, b: Int) = (a + b - 1) / b
    }

    /** Decodes one scan into the components' coefficient arrays. */
    private class ScanDecoder(
        val p: Parser,
        val scan: Scan,
        val scanIndex: Int,
        val sc: List<ScanComp>,
        val ss: Int,
        val se: Int,
        val ah: Int,
        val al: Int,
    ) {
        val r = BitReader(scan.data, 0, scan.segmentEnds[0] * 8)        // positions are in bits
        val pred = IntArray(sc.size)
        var eobrun = 0
        val scratch = ShortArray(64)
        val luma = p.comps[0]

        fun extend(v: Int, s: Int): Int = if (v < (1 shl (s - 1))) v - (1 shl s) + 1 else v

        /** Remember where LSB(|c|) of a luminance embedding coefficient is stored. */
        fun record(comp: Component, block: Int, natural: Int, bitPos: Int) {
            if (comp !== luma || block < 0) return
            val slot = JpegChain.SLOT_OF_NATURAL[natural]
            if (slot < 0) return
            val i = block * JpegChain.K + slot
            p.lsbPos[i] = bitPos
            p.lsbScan[i] = scanIndex.toShort()
        }

        fun run() {
            val interleaved = sc.size > 1
            val mcusWide: Int
            val mcusHigh: Int
            if (interleaved) {
                mcusWide = p.ceilDiv(p.width, p.hMax * 8)
                mcusHigh = p.ceilDiv(p.height, p.vMax * 8)
            } else {
                mcusWide = sc[0].comp.blocksWide
                mcusHigh = sc[0].comp.blocksHigh
            }
            var segment = 0
            for (mcu in 0 until mcusWide * mcusHigh) {
                if (p.restartInterval > 0 && mcu > 0 && mcu % p.restartInterval == 0) {
                    r.checkEnd()
                    segment++
                    if (segment >= scan.segmentStarts.size) throw ChainException("Corrupt JPEG (missing restart marker).")
                    r.pos = scan.segmentStarts[segment] * 8
                    r.end = scan.segmentEnds[segment] * 8
                    java.util.Arrays.fill(pred, 0)
                    eobrun = 0
                }
                val my = mcu / mcusWide
                val mx = mcu % mcusWide
                for ((ci, s) in sc.withIndex()) {
                    if (interleaved) {
                        for (v in 0 until s.comp.v) for (h in 0 until s.comp.h) {
                            block(ci, s, my * s.comp.v + v, mx * s.comp.h + h)
                        }
                    } else {
                        block(ci, s, my, mx)
                    }
                }
            }
            r.checkEnd()
            if (r.overrun) throw ChainException("Corrupt or truncated JPEG data.")
        }

        fun block(ci: Int, s: ScanComp, row: Int, col: Int) {
            val comp = s.comp
            val keep = row < comp.blocksHigh && col < comp.blocksWide
            val arr: ShortArray
            val base: Int
            val blockIndex: Int
            if (keep) {
                arr = comp.coef
                blockIndex = row * comp.blocksWide + col
                base = blockIndex * 64
            } else {                                     // padding block: decode and discard
                java.util.Arrays.fill(scratch, 0)
                arr = scratch
                base = 0
                blockIndex = -1
            }
            when {
                !p.progressive -> sequential(ci, s, comp, arr, base, blockIndex)
                ss == 0 && ah == 0 -> dcFirst(ci, s, arr, base)
                ss == 0 -> if (r.bits(1) == 1) arr[base] = (arr[base].toInt() or (1 shl al)).toShort()
                ah == 0 -> acFirst(s, comp, arr, base, blockIndex)
                else -> acRefine(s, comp, arr, base, blockIndex)
            }
        }

        fun sequential(ci: Int, s: ScanComp, comp: Component, arr: ShortArray, base: Int, blockIndex: Int) {
            val t = s.dc!!.decode(r)
            if (t > 16) throw ChainException("Corrupt JPEG (bad DC size).")
            pred[ci] += if (t == 0) 0 else extend(r.bits(t), t)
            arr[base] = pred[ci].toShort()
            var k = 1
            while (k < 64) {
                val rs = s.ac!!.decode(r)
                val run = rs ushr 4
                val size = rs and 15
                if (size == 0) {
                    if (run == 15) { k += 16; continue }
                    break                                              // EOB
                }
                k += run
                if (k > 63) throw ChainException("Corrupt JPEG (coefficient index out of range).")
                val natural = NATURAL_ORDER[k]
                val bitStart = r.pos
                arr[base + natural] = extend(r.bits(size), size).toShort()
                record(comp, blockIndex, natural, bitStart + size - 1)
                k++
            }
        }

        fun dcFirst(ci: Int, s: ScanComp, arr: ShortArray, base: Int) {
            val t = s.dc!!.decode(r)
            if (t > 16) throw ChainException("Corrupt JPEG (bad DC size).")
            pred[ci] += if (t == 0) 0 else extend(r.bits(t), t)
            arr[base] = (pred[ci] shl al).toShort()
        }

        fun acFirst(s: ScanComp, comp: Component, arr: ShortArray, base: Int, blockIndex: Int) {
            if (eobrun > 0) { eobrun--; return }
            var k = ss
            while (k <= se) {
                val rs = s.ac!!.decode(r)
                val run = rs ushr 4
                val size = rs and 15
                if (size != 0) {
                    k += run
                    if (k > 63) throw ChainException("Corrupt JPEG (coefficient index out of range).")
                    val natural = NATURAL_ORDER[k]
                    val bitStart = r.pos
                    arr[base + natural] = (extend(r.bits(size), size) shl al).toShort()
                    if (al == 0) record(comp, blockIndex, natural, bitStart + size - 1)
                } else {
                    if (run < 15) {
                        eobrun = (1 shl run) - 1
                        if (run > 0) eobrun += r.bits(run)
                        break
                    }
                    k += 15                                            // ZRL (+1 below)
                }
                k++
            }
        }

        /** Successive-approximation refinement of AC coefficients (libjpeg decode_mcu_AC_refine). */
        fun acRefine(s: ScanComp, comp: Component, arr: ShortArray, base: Int, blockIndex: Int) {
            val p1 = 1 shl al
            val m1 = -1 shl al
            var k = ss

            fun correct(natural: Int) {
                val c = arr[base + natural].toInt()
                val bitPos = r.pos
                if (r.bits(1) == 1 && (c and p1) == 0) {
                    arr[base + natural] = (if (c >= 0) c + p1 else c + m1).toShort()
                }
                if (al == 0) record(comp, blockIndex, natural, bitPos)
            }

            if (eobrun == 0) {
                while (k <= se) {
                    val rs = s.ac!!.decode(r)
                    var run = rs ushr 4
                    val size = rs and 15
                    var newValue = 0
                    if (size != 0) {
                        newValue = if (r.bits(1) == 1) p1 else m1
                    } else if (run != 15) {
                        eobrun = 1 shl run
                        if (run > 0) eobrun += r.bits(run)
                        break
                    }
                    while (k <= se) {
                        val natural = NATURAL_ORDER[k]
                        if (arr[base + natural].toInt() != 0) {
                            correct(natural)
                        } else {
                            if (--run < 0) break
                        }
                        k++
                    }
                    if (newValue != 0 && k <= 63) arr[base + NATURAL_ORDER[k]] = newValue.toShort()
                    k++
                }
            }
            if (eobrun > 0) {
                while (k <= se) {
                    val natural = NATURAL_ORDER[k]
                    if (arr[base + natural].toInt() != 0) correct(natural)
                    k++
                }
                eobrun--
            }
        }
    }
}

/** A valid JPEG this reader can't edit in place; the app re-saves it once as baseline. */
class UnsupportedJpegException(kind: String) : Exception("Unsupported JPEG type: $kind.")
