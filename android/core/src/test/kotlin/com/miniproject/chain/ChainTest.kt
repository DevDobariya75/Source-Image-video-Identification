package com.miniproject.chain

import java.io.File
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
        assertEquals(176, frame.size)
        assertEquals(chain, ChainPayload.unpack(frame))
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
        val contentId: String?, val unsupported: Boolean, val imei: String, val ts: Long,
    )

    private fun manifest(): List<Entry> {
        // Tiny line-based manifest (avoids a JSON dependency): fields separated by |
        return File(dir, "manifest.txt").readLines().filter { it.isNotBlank() }.map { line ->
            val f = line.split("|")
            Entry(f[0], f[1], f[2], f[3].ifEmpty { null }, f[4].ifEmpty { null }, f[5] == "1", f[6], f[7].toLong())
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
                assertFailsWith<UnsupportedJpegException>(e.name) { JpegCoefficients.read(plain.readBytes()) }
                println("${e.name}: unsupported as expected")
                continue
            }

            // 1. Same Image ID as Python for the unstamped file.
            val id = if (e.kind == "image") JpegChain.contentId(JpegCoefficients.read(plain.readBytes()))
                     else Mp4Chain.contentId(plain)
            assertEquals(e.contentId, id.toHex(), "${e.name}: content ID")

            // 2. Kotlin reads the chain Python wrote.
            val py = File(dir, e.pyStamped!!)
            val pyRead = assertNotNull(MediaChain.read(py), "${e.name}: read Python-stamped file")
            val expected = Chain(id, listOf(Hop(devA, e.imei, e.ts, Role.SEND)))
            assertEquals(expected, pyRead.chain, "${e.name}: chain from Python file")

            // 3. Kotlin stamps the plain file the same way (Python checks the result).
            val kt = File(dir, "kt_${e.file}")
            val t0 = System.nanoTime()
            val res = MediaChain.appendHop(plain, kt, devA, e.imei, Role.SEND, e.ts)
            val stampMs = (System.nanoTime() - t0) / 1_000_000
            assertTrue(res.added && res.newChain)
            assertEquals(expected, assertNotNull(MediaChain.read(kt)).chain, "${e.name}: Kotlin round trip")

            // 4. Kotlin appends a second hop onto the Python-stamped file.
            val kt2 = File(dir, "kt2_${e.file}")
            MediaChain.appendHop(py, kt2, devB, null, Role.CLAIM, e.ts + 60)
            val two = assertNotNull(MediaChain.read(kt2)).chain
            assertEquals(listOf(devA.toHex(), devB.toHex()), two.hops.map { it.deviceIdHex }, e.name)
            assertContentEquals(id, two.imageId)

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
