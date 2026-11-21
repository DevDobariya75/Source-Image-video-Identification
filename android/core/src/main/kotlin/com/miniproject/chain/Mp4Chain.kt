package com.miniproject.chain

import java.io.File
import java.io.RandomAccessFile
import java.security.MessageDigest

/**
 * Chain storage for videos (MP4 / MOV / 3GP). Mirror of LSB/mp4_chain.py:
 * one top-level box `uuid` + CHAIN_UUID + the complete chain (scrambled
 * 2016-byte frame, 100 hop slots), appended at the end of the file and
 * overwritten in place by later hops. The video is never re-encoded. A box
 * in the old 8-hop format is renamed to "free" and a new one is appended.
 */
object Mp4Chain {
    private val CHAIN_UUID = hex("6b1f8e3a52c94d07a3e1d9c46f20b8a5")
    private const val BOX_HEADER = 8 + 16
    const val BOX_SIZE = BOX_HEADER + ChainPayload.FRAME_BYTES      // 2040

    private val KEY: ByteArray = run {
        val out = java.io.ByteArrayOutputStream()
        var i = 0
        while (out.size() < ChainPayload.FRAME_BYTES) {
            val prefix = "imei-chain-mp4/".toByteArray(Charsets.US_ASCII)
            val counter = byteArrayOf((i ushr 24).toByte(), (i ushr 16).toByte(), (i ushr 8).toByte(), i.toByte())
            out.write(sha256(prefix + counter))
            i++
        }
        out.toByteArray().copyOf(ChainPayload.FRAME_BYTES)
    }

    private fun scramble(frame: ByteArray) = ByteArray(frame.size) { (frame[it].toInt() xor KEY[it].toInt()).toByte() }

    fun isMp4(head: ByteArray): Boolean =
        head.size >= 8 && head[4] == 'f'.code.toByte() && head[5] == 't'.code.toByte() &&
            head[6] == 'y'.code.toByte() && head[7] == 'p'.code.toByte()

    private class Box(val offset: Long, val size: Long, val type: String, val openEnded: Boolean, val uuid: ByteArray?)

    private fun topLevelBoxes(f: RandomAccessFile): List<Box> {
        val end = f.length()
        val boxes = mutableListOf<Box>()
        var pos = 0L
        while (pos < end) {
            if (end - pos < 8) throw ChainException("Truncated MP4 box header.")
            f.seek(pos)
            var size = f.readInt().toLong() and 0xFFFFFFFFL
            val typeBytes = ByteArray(4).also { f.readFully(it) }
            val type = String(typeBytes, Charsets.ISO_8859_1)
            var header = 8
            var openEnded = false
            when (size) {
                1L -> { size = f.readLong(); header = 16 }
                0L -> { size = end - pos; openEnded = true }
            }
            if (size < header || pos + size > end) throw ChainException("Invalid MP4 box '$type' at $pos.")
            val uuid = if (type == "uuid" && size >= header + 16) ByteArray(16).also { f.readFully(it) } else null
            boxes += Box(pos, size, type, openEnded, uuid)
            pos += size
        }
        return boxes
    }

    private fun List<Box>.ourBoxes() = filter { it.type == "uuid" && it.uuid.contentEquals(CHAIN_UUID) }

    /** Our box in the current format (old 200-byte boxes are ignored). */
    private fun List<Box>.chainBox() = ourBoxes().firstOrNull { it.size == BOX_SIZE.toLong() }

    /** Image ID: SHA-256 of the moov box (the video's index, never modified), first 8 bytes. */
    fun contentId(file: File): ByteArray = RandomAccessFile(file, "r").use { f ->
        val moov = topLevelBoxes(f).firstOrNull { it.type == "moov" }
            ?: throw ChainException("Not a playable video: no 'moov' box.")
        val md = MessageDigest.getInstance("SHA-256")
        val buf = ByteArray(1 shl 16)
        f.seek(moov.offset)
        var left = moov.size
        while (left > 0) {
            val n = f.read(buf, 0, minOf(left, buf.size.toLong()).toInt())
            if (n < 0) throw ChainException("Unexpected end of file.")
            md.update(buf, 0, n)
            left -= n
        }
        md.digest().copyOf(ChainPayload.IMAGE_ID_BYTES)
    }

    fun read(file: File): Chain? = RandomAccessFile(file, "r").use { f ->
        val box = topLevelBoxes(f).chainBox() ?: return null
        f.seek(box.offset + BOX_HEADER)
        val frame = ByteArray(ChainPayload.FRAME_BYTES).also { f.readFully(it) }
        try {
            ChainPayload.unpack(scramble(frame))
        } catch (e: ChainException) {
            null
        }
    }

    /** Store the chain in [file], modifying it in place (copy it first if needed). */
    fun write(file: File, chain: Chain) {
        val payload = java.nio.ByteBuffer.allocate(BOX_SIZE)
            .putInt(BOX_SIZE).put("uuid".toByteArray(Charsets.US_ASCII)).put(CHAIN_UUID)
            .put(scramble(ChainPayload.pack(chain))).array()
        RandomAccessFile(file, "rw").use { f ->
            val boxes = topLevelBoxes(f)
            val existing = boxes.chainBox()
            if (existing != null) {
                f.seek(existing.offset)
                f.write(payload)
                return
            }
            for (old in boxes.ourBoxes()) {          // old-format box: hide it
                f.seek(old.offset + 4)
                f.write("free".toByteArray(Charsets.US_ASCII))
            }
            val last = boxes.last()
            if (last.openEnded) {
                // Size 0 = "runs to end of file": make it explicit before appending.
                if (last.size > 0xFFFFFFFFL) throw ChainException("Unsupported MP4 layout (open-ended box > 4 GB).")
                f.seek(last.offset)
                f.writeInt(last.size.toInt())
            }
            f.seek(f.length())
            f.write(payload)
        }
    }

    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(2 * it, 2 * it + 2).toInt(16).toByte() }
}
