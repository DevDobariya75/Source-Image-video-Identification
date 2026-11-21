package com.miniproject.chain

import java.io.BufferedOutputStream
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.EOFException
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.util.zip.CRC32
import java.util.zip.Deflater
import java.util.zip.Inflater
import java.util.zip.InflaterInputStream

/** An 8-bit RGB image: [rgb] holds R, G, B for every pixel, row by row. */
class RgbImage(val width: Int, val height: Int, val rgb: ByteArray = ByteArray(width * height * 3)) {
    init {
        require(width > 0 && height > 0) { "Empty image." }
        require(rgb.size == width * height * 3) { "Pixel buffer has the wrong size." }
    }
}

/** A PNG this library doesn't read (e.g. 16-bit or interlaced). The app converts it first. */
class UnsupportedImageException(message: String) : Exception(message)

/**
 * Minimal lossless PNG reader / writer, so the chain code controls every
 * pixel value exactly (no colour management, no premultiplied alpha).
 *
 * Reads: 8-bit, non-interlaced PNGs of every colour type (grey, RGB, palette,
 * grey + alpha, RGBA). Alpha is dropped, like OpenCV's IMREAD_COLOR.
 * Anything else throws [UnsupportedImageException].
 * Writes: 8-bit RGB, adaptive row filters, zlib level 6.
 */
object Png {
    private val SIGNATURE = byteArrayOf(0x89.toByte(), 'P'.code.toByte(), 'N'.code.toByte(), 'G'.code.toByte(), 0x0D, 0x0A, 0x1A, 0x0A)

    fun isPng(head: ByteArray): Boolean = head.size >= 8 && (0 until 8).all { head[it] == SIGNATURE[it] }

    fun read(file: File): RgbImage = file.inputStream().buffered(1 shl 16).use { read(it) }

    fun read(input: InputStream): RgbImage {
        val data = DataInputStream(input)
        val sig = ByteArray(8)
        try { data.readFully(sig) } catch (e: EOFException) { throw ChainException("Not a PNG file.") }
        if (!isPng(sig)) throw ChainException("Not a PNG file.")

        var width = 0
        var height = 0
        var colorType = -1
        var palette: ByteArray? = null
        val idat = ByteArrayOutputStream()
        try {
            while (true) {
                val length = data.readInt()
                if (length < 0) throw ChainException("Damaged PNG.")
                val type = ByteArray(4).also { data.readFully(it) }.toString(Charsets.US_ASCII)
                val body = ByteArray(length).also { data.readFully(it) }
                data.readInt()                                          // CRC (zlib checks the data)
                when (type) {
                    "IHDR" -> {
                        val h = DataInputStream(ByteArrayInputStream(body))
                        width = h.readInt()
                        height = h.readInt()
                        val bitDepth = h.readUnsignedByte()
                        colorType = h.readUnsignedByte()
                        h.readUnsignedByte()                            // compression (always 0)
                        h.readUnsignedByte()                            // filter method (always 0)
                        val interlace = h.readUnsignedByte()
                        if (width <= 0 || height <= 0) throw ChainException("Damaged PNG.")
                        if (bitDepth != 8) throw UnsupportedImageException("$bitDepth-bit PNG")
                        if (interlace != 0) throw UnsupportedImageException("interlaced PNG")
                        if (colorType !in intArrayOf(0, 2, 3, 4, 6)) throw ChainException("Damaged PNG.")
                    }
                    "PLTE" -> palette = body
                    "IDAT" -> idat.write(body)
                    "IEND" -> break
                }
            }
        } catch (e: EOFException) {
            throw ChainException("Truncated PNG.")
        }
        if (colorType < 0) throw ChainException("Damaged PNG: no header.")
        if (colorType == 3 && palette == null) throw ChainException("Damaged PNG: no palette.")

        val channels = when (colorType) { 0 -> 1; 2 -> 3; 3 -> 1; 4 -> 2; else -> 4 }
        val stride = width.toLong() * channels
        if (stride > Int.MAX_VALUE / 2 || width.toLong() * height * 3 > Int.MAX_VALUE) {
            throw UnsupportedImageException("image too large")
        }
        val img = RgbImage(width, height)
        val out = img.rgb
        var prev = ByteArray(stride.toInt())
        var cur = ByteArray(stride.toInt())
        val z = DataInputStream(InflaterInputStream(ByteArrayInputStream(idat.toByteArray()), Inflater(), 1 shl 16))
        try {
            for (y in 0 until height) {
                val filter = z.readUnsignedByte()
                z.readFully(cur)
                unfilter(filter, cur, prev, channels)
                var o = y * width * 3
                when (colorType) {
                    2 -> { System.arraycopy(cur, 0, out, o, cur.size) }
                    6 -> for (x in 0 until width) {
                        out[o++] = cur[4 * x]; out[o++] = cur[4 * x + 1]; out[o++] = cur[4 * x + 2]
                    }
                    0, 4 -> for (x in 0 until width) {
                        val g = cur[x * channels]
                        out[o++] = g; out[o++] = g; out[o++] = g
                    }
                    3 -> {
                        val pal = palette!!
                        for (x in 0 until width) {
                            val i = 3 * (cur[x].toInt() and 0xFF)
                            if (i + 2 >= pal.size) throw ChainException("Damaged PNG: bad palette index.")
                            out[o++] = pal[i]; out[o++] = pal[i + 1]; out[o++] = pal[i + 2]
                        }
                    }
                }
                val t = prev; prev = cur; cur = t
            }
        } catch (e: EOFException) {
            throw ChainException("Truncated PNG data.")
        } catch (e: java.util.zip.ZipException) {
            throw ChainException("Damaged PNG data.")
        }
        return img
    }

    private fun unfilter(filter: Int, cur: ByteArray, prev: ByteArray, bpp: Int) {
        val n = cur.size
        when (filter) {
            0 -> {}
            1 -> for (i in bpp until n) cur[i] = (cur[i] + cur[i - bpp]).toByte()
            2 -> for (i in 0 until n) cur[i] = (cur[i] + prev[i]).toByte()
            3 -> for (i in 0 until n) {
                val a = if (i >= bpp) cur[i - bpp].toInt() and 0xFF else 0
                cur[i] = (cur[i] + ((a + (prev[i].toInt() and 0xFF)) ushr 1)).toByte()
            }
            4 -> for (i in 0 until n) {
                val a = if (i >= bpp) cur[i - bpp].toInt() and 0xFF else 0
                val c = if (i >= bpp) prev[i - bpp].toInt() and 0xFF else 0
                cur[i] = (cur[i] + paeth(a, prev[i].toInt() and 0xFF, c)).toByte()
            }
            else -> throw ChainException("Damaged PNG: unknown row filter $filter.")
        }
    }

    private fun paeth(a: Int, b: Int, c: Int): Int {
        val p = a + b - c
        val pa = kotlin.math.abs(p - a)
        val pb = kotlin.math.abs(p - b)
        val pc = kotlin.math.abs(p - c)
        return if (pa <= pb && pa <= pc) a else if (pb <= pc) b else c
    }

    /**
     * zlib level for PNG output. Level 6 gives the same file size as a classic
     * single-threaded encoder; with the parallel strips it is still fast, and a
     * smaller file is what makes the Bluetooth transfer quicker.
     */
    const val DEFAULT_LEVEL = 6

    fun write(img: RgbImage, file: File, level: Int = DEFAULT_LEVEL) =
        file.outputStream().use { write(img, it, level) }

    /**
     * Encode [img] as an 8-bit RGB PNG, using every CPU core.
     *
     * The rows are cut into horizontal strips. Each strip is filtered (adaptive
     * per row) and deflated on its own thread, ending with a sync flush; the
     * strips are then joined into ONE zlib stream (the pigz technique) with the
     * Adler-32 checksums combined. The result is a normal PNG that any viewer
     * reads, produced several times faster than a single-threaded encoder.
     */
    fun write(img: RgbImage, output: OutputStream, level: Int = DEFAULT_LEVEL,
              threads: Int = Runtime.getRuntime().availableProcessors()) {
        val h = img.height
        val strips = minOf(h, maxOf(1, threads * 3))
        val rowsPer = (h + strips - 1) / strips
        val ranges = (0 until h step rowsPer).map { it until minOf(h, it + rowsPer) }
        val pool = java.util.concurrent.Executors.newFixedThreadPool(maxOf(1, minOf(threads, ranges.size)))
        try {
            val jobs = ranges.mapIndexed { i, r ->
                pool.submit(java.util.concurrent.Callable { compressStrip(img, r.first, r.last + 1, level, i == ranges.lastIndex) })
            }
            val out = BufferedOutputStream(output, 1 shl 16)
            out.write(SIGNATURE)
            val ihdr = java.nio.ByteBuffer.allocate(13).putInt(img.width).putInt(h)
                .put(8).put(2).put(0).put(0).put(0).array()                   // 8-bit RGB, no interlace
            writeChunk(out, "IHDR", ihdr, ihdr.size)
            val idat = ChunkStream(out, "IDAT")
            idat.write(0x78); idat.write(0x9C)                               // zlib header
            var adler = 1L
            for (job in jobs) {
                val s = job.get()
                idat.write(s.data, 0, s.size)
                adler = adlerCombine(adler, s.adler, s.rawLength)
            }
            for (k in 3 downTo 0) idat.write((adler ushr (8 * k)).toInt() and 0xFF)
            idat.flushChunk()
            writeChunk(out, "IEND", ByteArray(0), 0)
            out.flush()
        } catch (e: java.util.concurrent.ExecutionException) {
            throw e.cause ?: e
        } finally {
            pool.shutdown()
        }
    }

    private class Strip(val data: ByteArray, val size: Int, val adler: Long, val rawLength: Long)

    private class Bytes(initial: Int) : ByteArrayOutputStream(initial) {
        val buffer: ByteArray get() = buf
    }

    /** Filter rows [y0, y1) and raw-deflate them; the last strip finishes the stream. */
    private fun compressStrip(img: RgbImage, y0: Int, y1: Int, level: Int, last: Boolean): Strip {
        val stride = img.width * 3
        val raw = ByteArray((y1 - y0) * (stride + 1))
        var o = 0
        for (y in y0 until y1) o = filterRow(img.rgb, y, stride, raw, o)
        val adler = java.util.zip.Adler32().apply { update(raw) }.value

        val d = Deflater(level, true)
        val out = Bytes(raw.size / 3 + 1024)
        val buf = ByteArray(1 shl 16)
        try {
            d.setInput(raw)
            if (last) {
                d.finish()
                while (!d.finished()) out.write(buf, 0, d.deflate(buf))
            } else {
                while (true) {
                    val n = d.deflate(buf, 0, buf.size, Deflater.SYNC_FLUSH)
                    out.write(buf, 0, n)
                    if (n < buf.size) break                  // flush complete (all input consumed)
                }
            }
        } finally {
            d.end()
        }
        return Strip(out.buffer, out.size(), adler, raw.size.toLong())
    }

    /**
     * Filter row [y] of [src] into [out] at [off]: filter-type byte + filtered
     * bytes. Picks the filter (None, Sub, Up, Average, Paeth) with the smallest
     * sum of |signed bytes|, the usual PNG heuristic, in two passes. Returns the
     * new offset.
     */
    private fun filterRow(src: ByteArray, y: Int, stride: Int, out: ByteArray, off: Int): Int {
        val cur = y * stride
        val prv = cur - stride
        val hasPrev = y > 0
        var s0 = 0L; var s1 = 0L; var s2 = 0L; var s3 = 0L; var s4 = 0L
        for (i in 0 until stride) {
            val x = src[cur + i].toInt() and 0xFF
            val a = if (i >= 3) src[cur + i - 3].toInt() and 0xFF else 0
            val b = if (hasPrev) src[prv + i].toInt() and 0xFF else 0
            val c = if (hasPrev && i >= 3) src[prv + i - 3].toInt() and 0xFF else 0
            s0 += kotlin.math.abs(x.toByte().toInt())
            s1 += kotlin.math.abs((x - a).toByte().toInt())
            s2 += kotlin.math.abs((x - b).toByte().toInt())
            s3 += kotlin.math.abs((x - ((a + b) ushr 1)).toByte().toInt())
            s4 += kotlin.math.abs((x - paeth(a, b, c)).toByte().toInt())
        }
        var type = 0
        var best = s0
        if (s1 < best) { best = s1; type = 1 }
        if (s2 < best) { best = s2; type = 2 }
        if (s3 < best) { best = s3; type = 3 }
        if (s4 < best) { type = 4 }
        out[off] = type.toByte()
        var o = off + 1
        for (i in 0 until stride) {
            val x = src[cur + i].toInt() and 0xFF
            val a = if (i >= 3) src[cur + i - 3].toInt() and 0xFF else 0
            val b = if (hasPrev) src[prv + i].toInt() and 0xFF else 0
            out[o++] = when (type) {
                0 -> x
                1 -> x - a
                2 -> x - b
                3 -> x - ((a + b) ushr 1)
                else -> x - paeth(a, b, if (hasPrev && i >= 3) src[prv + i - 3].toInt() and 0xFF else 0)
            }.toByte()
        }
        return o
    }

    /** zlib's adler32_combine: checksum of A followed by B, from those of A and B. */
    private fun adlerCombine(adler1: Long, adler2: Long, len2: Long): Long {
        val base = 65521L
        val rem = len2 % base
        var sum1 = adler1 and 0xFFFF
        var sum2 = (rem * sum1) % base
        sum1 += (adler2 and 0xFFFF) + base - 1
        sum2 += ((adler1 ushr 16) and 0xFFFF) + ((adler2 ushr 16) and 0xFFFF) + base - rem
        if (sum1 >= base) sum1 -= base
        if (sum1 >= base) sum1 -= base
        if (sum2 >= base shl 1) sum2 -= base shl 1
        if (sum2 >= base) sum2 -= base
        return sum1 or (sum2 shl 16)
    }

    private fun writeChunk(out: OutputStream, type: String, data: ByteArray, len: Int) {
        val t = type.toByteArray(Charsets.US_ASCII)
        out.write(byteArrayOf((len ushr 24).toByte(), (len ushr 16).toByte(), (len ushr 8).toByte(), len.toByte()))
        out.write(t)
        out.write(data, 0, len)
        val crc = CRC32().apply { update(t); update(data, 0, len) }.value.toInt()
        out.write(byteArrayOf((crc ushr 24).toByte(), (crc ushr 16).toByte(), (crc ushr 8).toByte(), crc.toByte()))
    }

    /** Collects compressed bytes and emits them as chunks of up to 256 KB. */
    private class ChunkStream(private val out: OutputStream, private val type: String) : OutputStream() {
        private val buf = ByteArray(1 shl 18)
        private var n = 0

        override fun write(b: Int) {
            if (n == buf.size) flushChunk()
            buf[n++] = b.toByte()
        }

        override fun write(b: ByteArray, off: Int, len: Int) {
            var o = off
            var left = len
            while (left > 0) {
                if (n == buf.size) flushChunk()
                val k = minOf(left, buf.size - n)
                System.arraycopy(b, o, buf, n, k)
                n += k; o += k; left -= k
            }
        }

        fun flushChunk() {
            if (n > 0) writeChunk(out, type, buf, n)
            n = 0
        }

        override fun close() {}                // the PNG stream stays open for IEND
    }
}
