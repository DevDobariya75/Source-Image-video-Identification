package com.miniproject.chain

import java.nio.ByteBuffer
import java.security.MessageDigest
import java.time.Instant
import java.util.zip.CRC32

/**
 * Binary format of the forwarding chain. Byte-for-byte identical to
 * LSB/chain_payload.py, so a file stamped on a phone is read by the backend
 * and the other way round.
 *
 * Frame (2016 bytes, big-endian), the same for photos and videos:
 *   0    2     magic "IC"
 *   2    1     version (2)
 *   3    1     hop count N (0..100)
 *   4    8     image ID
 *   12   2000  100 hop slots x 20 bytes: device ID (8), IMEI as integer (7),
 *              Unix time (4), role (1). Slots >= N are zero.
 *   2012 4     CRC-32 of bytes 0..2011
 */
object ChainPayload {
    val MAGIC = byteArrayOf('I'.code.toByte(), 'C'.code.toByte())
    const val VERSION = 2
    const val MAX_HOPS = 100
    const val DEVICE_ID_BYTES = 8
    const val IMEI_BYTES = 7
    const val IMAGE_ID_BYTES = 8
    const val HOP_BYTES = 20
    const val HEADER_BYTES = 12
    const val CRC_BYTES = 4
    const val FRAME_BYTES = HEADER_BYTES + MAX_HOPS * HOP_BYTES + CRC_BYTES   // 2016
    const val FRAME_BITS = FRAME_BYTES * 8                                   // 16128

    fun pack(chain: Chain): ByteArray {
        if (chain.hops.size > MAX_HOPS) throw ChainException("Chain is full ($MAX_HOPS hops max).")
        require(chain.imageId.size == IMAGE_ID_BYTES) { "Image ID must be 8 bytes." }
        val buf = ByteBuffer.allocate(FRAME_BYTES)          // zero-filled
        buf.put(MAGIC).put(VERSION.toByte()).put(chain.hops.size.toByte()).put(chain.imageId)
        for (h in chain.hops) {
            require(h.deviceId.size == DEVICE_ID_BYTES) { "Device ID must be 8 bytes." }
            buf.put(h.deviceId)
            val imei = h.imei?.let { validateImei(it).toLong() } ?: 0L
            for (i in IMEI_BYTES - 1 downTo 0) buf.put((imei ushr (8 * i)).toByte())
            buf.putInt(h.timestamp.toInt())                 // low 32 bits, like Python's & 0xFFFFFFFF
            buf.put(h.role.code.toByte())
        }
        val crc = CRC32().apply { update(buf.array(), 0, FRAME_BYTES - CRC_BYTES) }.value
        buf.position(FRAME_BYTES - CRC_BYTES)
        buf.putInt(crc.toInt())
        return buf.array()
    }

    /** Parse a frame; throws [ChainException] if it is not a valid chain. */
    fun unpack(data: ByteArray): Chain {
        if (data.size != FRAME_BYTES) throw ChainException("Frame must be $FRAME_BYTES bytes.")
        val buf = ByteBuffer.wrap(data)
        if (data[0] != MAGIC[0] || data[1] != MAGIC[1]) throw ChainException("No chain marker found.")
        val crc = CRC32().apply { update(data, 0, FRAME_BYTES - CRC_BYTES) }.value
        if (buf.getInt(FRAME_BYTES - CRC_BYTES).toLong() and 0xFFFFFFFFL != crc) {
            throw ChainException("Chain checksum mismatch (data damaged).")
        }
        val version = data[2].toInt() and 0xFF
        if (version != VERSION) throw ChainException("Unsupported chain version $version.")
        val count = data[3].toInt() and 0xFF
        if (count > MAX_HOPS) throw ChainException("Invalid hop count.")
        val imageId = data.copyOfRange(4, 12)

        val hops = (0 until count).map { i ->
            val o = HEADER_BYTES + i * HOP_BYTES
            val device = data.copyOfRange(o, o + DEVICE_ID_BYTES)
            var imei = 0L
            for (k in 0 until IMEI_BYTES) imei = (imei shl 8) or (data[o + 8 + k].toLong() and 0xFF)
            val ts = buf.getInt(o + 15).toLong() and 0xFFFFFFFFL
            val role = Role.fromCode(data[o + 19].toInt() and 0xFF)
            Hop(device, if (imei == 0L) null else "%015d".format(imei), ts, role)
        }
        return Chain(imageId, hops)
    }

    /**
     * 16 hex characters (an Android ANDROID_ID) -> those 8 bytes. Any other text
     * is hashed (SHA-256, first 8 bytes), exactly like parse_device_id() in Python.
     */
    fun parseDeviceId(text: String): ByteArray {
        val t = text.trim().lowercase()
        if (t.length == DEVICE_ID_BYTES * 2 && t.all { it in "0123456789abcdef" }) {
            return ByteArray(DEVICE_ID_BYTES) { t.substring(2 * it, 2 * it + 2).toInt(16).toByte() }
        }
        if (t.isEmpty()) throw ChainException("Device ID must not be empty.")
        return sha256(t.toByteArray(Charsets.UTF_8)).copyOf(DEVICE_ID_BYTES)
    }

    fun validateImei(imei: String): String {
        if (imei.length != 15 || !imei.all { it in '0'..'9' }) {
            throw ChainException("IMEI must be exactly 15 digits.")
        }
        return imei
    }

    /** IMEIs end in a Luhn check digit; a failure usually means a typo. */
    fun luhnOk(digits: String): Boolean {
        var total = 0
        digits.reversed().forEachIndexed { i, ch ->
            var d = ch - '0'
            if (i % 2 == 1) {
                d *= 2
                if (d > 9) d -= 9
            }
            total += d
        }
        return total % 10 == 0
    }
}

class ChainException(message: String) : Exception(message)

enum class Role(val code: Int, val label: String) {
    SEND(1, "send"),
    CLAIM(2, "claim");

    companion object {
        fun fromCode(code: Int): Role =
            entries.firstOrNull { it.code == code } ?: throw ChainException("Unknown role $code.")
    }
}

class Hop(
    val deviceId: ByteArray,
    val imei: String?,
    /** Unix seconds. */
    val timestamp: Long,
    val role: Role = Role.SEND,
) {
    val deviceIdHex: String get() = deviceId.toHex()
    val time: Instant get() = Instant.ofEpochSecond(timestamp)

    override fun equals(other: Any?): Boolean =
        other is Hop && deviceId.contentEquals(other.deviceId) && imei == other.imei &&
            timestamp == other.timestamp && role == other.role

    override fun hashCode(): Int = deviceId.contentHashCode() * 31 + timestamp.hashCode()
    override fun toString(): String = "Hop(${deviceIdHex}, imei=$imei, t=$timestamp, $role)"
}

class Chain(val imageId: ByteArray, val hops: List<Hop> = emptyList()) {
    val imageIdHex: String get() = imageId.toHex()

    override fun equals(other: Any?): Boolean =
        other is Chain && imageId.contentEquals(other.imageId) && hops == other.hops

    override fun hashCode(): Int = imageId.contentHashCode() * 31 + hops.hashCode()
    override fun toString(): String = "Chain($imageIdHex, $hops)"
}

internal fun sha256(data: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(data)

fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it.toInt() and 0xFF) }
