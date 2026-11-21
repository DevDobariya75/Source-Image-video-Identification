package com.miniproject.chain

import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.random.Random
import kotlin.test.Test

/** Timing of each stamping step on a 12 MP photo. Run with -Pbench=1 (skipped otherwise). */
class BenchTest {
    @Test
    fun bench() {
        if (System.getProperty("bench").isNullOrEmpty()) return
        val w = 4080
        val h = 3060
        val img = RgbImage(w, h)
        val rnd = Random(1)
        // Smooth photo-like content with mild noise (compresses like a real photo).
        for (y in 0 until h) for (x in 0 until w) {
            val o = (y * w + x) * 3
            val n = rnd.nextInt(-6, 7)
            img.rgb[o] = ((x * 255 / w + n).coerceIn(0, 255)).toByte()
            img.rgb[o + 1] = ((y * 255 / h + n).coerceIn(0, 255)).toByte()
            img.rgb[o + 2] = (((x + y) * 255 / (w + h) + n).coerceIn(0, 255)).toByte()
        }
        val chain = Chain(ByteArray(8), listOf(Hop(ChainPayload.parseDeviceId("a"), "356938035643809", 1L)))
        fun t(name: String, block: () -> Unit) {
            val t0 = System.nanoTime(); block(); println("BENCH $name: ${(System.nanoTime() - t0) / 1_000_000} ms")
        }
        repeat(2) { round ->
            println("BENCH ---- round $round")
            val work = RgbImage(w, h, img.rgb.copyOf())
            t("contentId") { PixelChain.contentId(work) }
            t("write 3bpp") { PixelChain.write(work, chain, 3) }
            var bytes = ByteArray(0)
            t("Png.write") { bytes = ByteArrayOutputStream().also { Png.write(work, it) }.toByteArray() }
            println("BENCH png size: ${bytes.size / 1024} KB")
            var back: RgbImage? = null
            t("Png.read") { back = Png.read(bytes.inputStream()) }
            t("decode (both modes)") { PixelChain.decode(back!!) }
            t("decode unstamped") { PixelChain.decode(img) }
            val f = File.createTempFile("bench", ".png"); f.writeBytes(bytes)
            t("MediaChain.read file") { MediaChain.read(f) }
            t("MediaChain.appendHop file") { MediaChain.appendHop(f, f, ChainPayload.parseDeviceId("b"), null) }
            f.delete()
        }
    }
}
