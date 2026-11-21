package com.miniproject.chain

import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.file.Files
import javax.imageio.ImageIO
import kotlin.random.Random
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ChainPayloadTest {
    private val devA = ChainPayload.parseDeviceId("phone-A")

    @Test
    fun packUnpackRoundTrip() {
        val chain = Chain(ByteArray(8) { it.toByte() }, listOf(
            Hop(devA, "356938035643809", 1792500000L, Role.SEND),
            Hop(ChainPayload.parseDeviceId("9774d56d682e549c"), null, 1792500100L, Role.CLAIM),
        ))
        val frame = ChainPayload.pack(chain)
        assertEquals(2016, frame.size)
        assertEquals(chain, ChainPayload.unpack(frame))
    }

    @Test
    fun hundredHopsFit() {
        val hops = (0 until 100).map { Hop(ChainPayload.parseDeviceId("dev$it"), null, 1000L + it) }
        val chain = Chain(ByteArray(8), hops)
        assertEquals(chain, ChainPayload.unpack(ChainPayload.pack(chain)))
        assertFailsWith<ChainException> { ChainPayload.pack(Chain(ByteArray(8), hops + hops.first())) }
    }

    @Test
    fun crcRejectsDamage() {
        val frame = ChainPayload.pack(Chain(ByteArray(8), listOf(Hop(devA, null, 1L))))
        frame[20] = (frame[20].toInt() xor 1).toByte()
        assertFailsWith<ChainException> { ChainPayload.unpack(frame) }
    }

    @Test
    fun deviceIdParsing() {
        assertEquals("9774d56d682e549c", ChainPayload.parseDeviceId("9774D56D682E549C").toHex())
        // Same as Python: sha256("phone-a")[:8]
        assertEquals("c71b552c6f362f9d", devA.toHex())
    }

    @Test
    fun luhn() {
        assertTrue(ChainPayload.luhnOk("356938035643809"))
        assertFalse(ChainPayload.luhnOk("356938035643808"))
        assertFailsWith<ChainException> { ChainPayload.validateImei("12345") }
    }
}

/** Test images that look a bit like photos: gradients + noise. */
private fun photo(w: Int, h: Int, seed: Int = 1): RgbImage {
    val rnd = Random(seed)
    val img = RgbImage(w, h)
    for (y in 0 until h) for (x in 0 until w) {
        val o = (y * w + x) * 3
        img.rgb[o] = (x * 255 / w + rnd.nextInt(-20, 20)).coerceIn(0, 255).toByte()
        img.rgb[o + 1] = (y * 255 / h + rnd.nextInt(-20, 20)).coerceIn(0, 255).toByte()
        img.rgb[o + 2] = ((x + y) * 255 / (w + h) + rnd.nextInt(-20, 20)).coerceIn(0, 255).toByte()
    }
    return img
}

private fun RgbImage.copy() = RgbImage(width, height, rgb.copyOf())

private fun RgbImage.crop(x0: Int, y0: Int, w: Int, h: Int): RgbImage {
    val out = RgbImage(w, h)
    for (y in 0 until h) System.arraycopy(rgb, ((y0 + y) * width + x0) * 3, out.rgb, y * w * 3, w * 3)
    return out
}

class PngTest {
    @Test
    fun rgbRoundTrip() {
        val img = photo(301, 187)
        val bytes = ByteArrayOutputStream().also { Png.write(img, it) }.toByteArray()
        assertContentEquals(img.rgb, Png.read(bytes.inputStream()).rgb)
        // Java's own decoder agrees with our encoder.
        val ref = ImageIO.read(bytes.inputStream())
        assertEquals(img.width, ref.width)
        assertEquals(ref.getRGB(5, 7) and 0xFFFFFF, rgbAt(img, 5, 7))
    }

    @Test
    fun parallelWriterEdgeCases() {
        // Tiny images, more threads than rows, every compression level: always a valid PNG.
        for ((w, h) in listOf(1 to 1, 17 to 5, 3 to 40, 301 to 187)) for (threads in listOf(1, 3, 16)) {
            for (level in listOf(1, 6, 9)) {
                val img = photo(w, h, w + h)
                val bytes = ByteArrayOutputStream().also { Png.write(img, it, level, threads) }.toByteArray()
                assertContentEquals(img.rgb, Png.read(bytes.inputStream()).rgb, "${w}x$h t=$threads l=$level")
                val ref = assertNotNull(ImageIO.read(bytes.inputStream()), "Java decoder ${w}x$h t=$threads")
                assertEquals(rgbAt(img, w - 1, h - 1), ref.getRGB(w - 1, h - 1) and 0xFFFFFF)
            }
        }
    }

    @Test
    fun readsOtherColourTypes() {
        for (type in listOf(BufferedImage.TYPE_BYTE_GRAY, BufferedImage.TYPE_INT_ARGB, BufferedImage.TYPE_BYTE_INDEXED)) {
            val bi = BufferedImage(97, 61, type)
            val rnd = Random(type)
            for (y in 0 until 61) for (x in 0 until 97) bi.setRGB(x, y, (0xFF shl 24) or rnd.nextInt(0x1000000))
            val bytes = ByteArrayOutputStream().also { ImageIO.write(bi, "png", it) }.toByteArray()
            val img = Png.read(bytes.inputStream())
            val ref = ImageIO.read(bytes.inputStream())
            for (y in 0 until 61) for (x in 0 until 97) {
                // getRGB() gamma-converts grey images, so use the raw sample for those.
                val want = if (type == BufferedImage.TYPE_BYTE_GRAY) ref.raster.getSample(x, y, 0) * 0x010101
                           else ref.getRGB(x, y) and 0xFFFFFF
                assertEquals(want, rgbAt(img, x, y), "type $type at $x,$y")
            }
        }
    }

    @Test
    fun rejectsSixteenBit() {
        val bi = BufferedImage(20, 20, BufferedImage.TYPE_USHORT_GRAY)
        val bytes = ByteArrayOutputStream().also { ImageIO.write(bi, "png", it) }.toByteArray()
        assertFailsWith<UnsupportedImageException> { Png.read(bytes.inputStream()) }
    }

    private fun rgbAt(img: RgbImage, x: Int, y: Int): Int {
        val o = (y * img.width + x) * 3
        return ((img.rgb[o].toInt() and 0xFF) shl 16) or ((img.rgb[o + 1].toInt() and 0xFF) shl 8) or (img.rgb[o + 2].toInt() and 0xFF)
    }
}

class PixelChainTest {
    private val devA = ChainPayload.parseDeviceId("phone-A")
    private val chain = Chain(ByteArray(8) { 7 }, listOf(Hop(devA, "356938035643809", 1792500000L)))

    @Test
    fun tileSizes() {
        assertEquals(74, PixelChain.tileSide(3))
        assertEquals(52, PixelChain.tileSide(6))
    }

    @Test
    fun bothModesRoundTripAndChangeLittle() {
        for ((bpp, maxDelta) in listOf(3 to 1, 6 to 3)) {
            val orig = photo(400, 300)
            val img = orig.copy()
            PixelChain.write(img, chain, bpp)
            val res = assertNotNull(PixelChain.decode(img), "mode $bpp")
            assertEquals(chain, res.chain)
            assertEquals(bpp, res.bitsPerPixel)
            assertEquals(0, res.dy)
            assertEquals(0, res.dx)
            val delta = img.rgb.indices.maxOf { kotlin.math.abs((img.rgb[it].toInt() and 0xFF) - (orig.rgb[it].toInt() and 0xFF)) }
            assertTrue(delta <= maxDelta, "mode $bpp changed a value by $delta")
            assertContentEquals(PixelChain.contentId(orig), PixelChain.contentId(img), "ID unchanged by stamping")
        }
    }

    @Test
    fun survivesMisalignedCrop() {
        for (bpp in PixelChain.MODES) {
            val img = photo(500, 400)
            PixelChain.write(img, chain, bpp)
            val side = PixelChain.tileSide(bpp)
            val res = assertNotNull(PixelChain.decode(img.crop(91, 37, 2 * side, side + 5)), "mode $bpp")
            assertEquals(chain, res.chain)
            assertEquals(37 % side, res.dy)
            assertEquals(91 % side, res.dx)
        }
    }

    @Test
    fun hundredHopsInAPhotoAndModeSwitch() {
        val dir = Files.createTempDirectory("chain").toFile()
        val f = File(dir, "p.png")
        Png.write(photo(320, 240), f)
        for (i in 0 until 100) {
            MediaChain.appendHop(f, f, ChainPayload.parseDeviceId("dev$i"), null, timestamp = 1000L + i,
                bitsPerPixel = if (i % 2 == 0) 3 else 6)
        }
        val res = assertNotNull(MediaChain.read(f))
        assertEquals(100, res.chain.hops.size)
        assertEquals(6, res.bitsPerPixel)
        assertFailsWith<ChainException> { MediaChain.appendHop(f, f, ChainPayload.parseDeviceId("x"), null) }
        dir.deleteRecursively()
    }

    @Test
    fun fastReaderFallsBackWhenTopIsDamaged() {
        for (bpp in PixelChain.MODES) {
            val img = photo(300, 1500, bpp)
            PixelChain.write(img, chain, bpp)
            // Wipe the top 700 rows with noise: the fast top band can't see the chain.
            val rnd = Random(9)
            for (i in 0 until 700 * 300 * 3) img.rgb[i] = rnd.nextInt(256).toByte()
            assertEquals(chain, assertNotNull(PixelChain.decode(img), "mode $bpp").chain)
            // 25 % of the low bits flipped: 32 copies in the band are too few (CRC fails), all 80 copies are enough.
            val noisy = photo(300, 1500, bpp + 1)
            PixelChain.write(noisy, chain, bpp)
            for (i in noisy.rgb.indices) if (rnd.nextDouble() < 0.25) noisy.rgb[i] = (noisy.rgb[i].toInt() xor 1).toByte()
            assertEquals(chain, assertNotNull(PixelChain.decode(noisy), "noisy mode $bpp").chain)
        }
    }

    @Test
    fun tooSmallPhotoRefused() {
        assertFailsWith<ChainException> { PixelChain.write(photo(60, 300), chain, 3) }
        assertNull(PixelChain.decode(photo(60, 300)))
    }

    @Test
    fun unstampedPhotoHasNoChain() {
        assertNull(PixelChain.decode(photo(300, 300)))
    }
}

/**
 * Cross-checks against files made by the Python implementation
 * (LSB/test_kotlin_compat.py passes their folder as -PvectorsDir=...).
 * Skipped when run without vectors.
 */
class PythonCompatTest {
    private val dir = System.getProperty("vectors.dir").orEmpty().let { if (it.isEmpty()) null else File(it) }
    private val devA = ChainPayload.parseDeviceId("phone-A")
    private val devB = ChainPayload.parseDeviceId("phone-B")

    private data class Entry(
        val name: String, val kind: String, val file: String, val pyStamped: String?,
        val contentId: String?, val unsupported: Boolean, val imei: String, val ts: Long, val bpp: Int?,
    )

    private fun manifest(): List<Entry> {
        // Tiny line-based manifest (avoids a JSON dependency): fields separated by |
        return File(dir, "manifest.txt").readLines().filter { it.isNotBlank() }.map { line ->
            val f = line.split("|")
            Entry(f[0], f[1], f[2], f[3].ifEmpty { null }, f[4].ifEmpty { null }, f[5] == "1", f[6], f[7].toLong(),
                f[8].ifEmpty { null }?.toInt())
        }
    }

    @Test
    fun matchesPython() {
        if (dir == null) {
            println("vectors.dir not set: skipping Python compatibility checks")
            return
        }
        val entries = manifest()
        assertTrue(entries.isNotEmpty())
        for (e in entries) {
            val plain = File(dir, e.file)
            if (e.unsupported) {
                if (MediaChain.mediaType(plain) == null) {
                    assertFailsWith<ChainException>(e.name) { MediaChain.appendHop(plain, File(dir, "x"), devA, null) }
                } else {
                    assertFailsWith<UnsupportedImageException>(e.name) { Png.read(plain) }
                }
                println("${e.name}: unsupported as expected")
                continue
            }

            // 1. Same Image ID as Python for the unstamped file.
            val id = if (e.kind == "image") PixelChain.contentId(Png.read(plain)) else Mp4Chain.contentId(plain)
            assertEquals(e.contentId, id.toHex(), "${e.name}: content ID")

            // 2. Kotlin reads the chain Python wrote.
            val py = File(dir, e.pyStamped!!)
            val pyRead = assertNotNull(MediaChain.read(py), "${e.name}: read Python-stamped file")
            val expected = Chain(id, listOf(Hop(devA, e.imei, e.ts, Role.SEND)))
            assertEquals(expected, pyRead.chain, "${e.name}: chain from Python file")
            assertEquals(e.bpp, pyRead.bitsPerPixel, "${e.name}: mode")

            // 3. Kotlin stamps the plain file the same way (Python checks the result).
            val kt = File(dir, "kt_${e.file}")
            val t0 = System.nanoTime()
            val res = MediaChain.appendHop(plain, kt, devA, e.imei, Role.SEND, e.ts, bitsPerPixel = e.bpp)
            val stampMs = (System.nanoTime() - t0) / 1_000_000
            assertTrue(res.added && res.newChain)
            assertEquals(expected, assertNotNull(MediaChain.read(kt)).chain, "${e.name}: Kotlin round trip")

            // 4. Kotlin appends a second hop onto the Python-stamped file (keeping its mode).
            val kt2 = File(dir, "kt2_${e.file}")
            MediaChain.appendHop(py, kt2, devB, null, Role.CLAIM, e.ts + 60)
            val two = assertNotNull(MediaChain.read(kt2))
            assertEquals(listOf(devA.toHex(), devB.toHex()), two.chain.hops.map { it.deviceIdHex }, e.name)
            assertContentEquals(id, two.chain.imageId)
            assertEquals(e.bpp, two.bitsPerPixel)

            // 5. Same device again: nothing added.
            assertFalse(MediaChain.appendHop(kt, File(dir, "kt3_${e.file}"), devA, e.imei).added)
            println("${e.name}: OK (stamped in $stampMs ms)")
        }
    }

    @Test
    fun unstampedFilesHaveNoChain() {
        if (dir == null) return
        for (e in manifest().filter { !it.unsupported }) {
            assertNull(MediaChain.read(File(dir, e.file)), e.name)
        }
    }
}
