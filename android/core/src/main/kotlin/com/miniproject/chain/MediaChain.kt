package com.miniproject.chain

import java.io.File

enum class MediaType { IMAGE, VIDEO }

/** One entry point for photos and videos: read the chain, or append a hop. */
object MediaChain {

    class ReadResult(val chain: Chain, val media: MediaType, val confidence: Double = 1.0)

    class AppendResult(
        val chain: Chain,
        val media: MediaType,
        /** False if this device was already the last hop (nothing added). */
        val added: Boolean,
        /** True if the file had no chain and this device is the origin. */
        val newChain: Boolean,
    )

    /** Detect the type from the file's first bytes, or null if unsupported. */
    fun mediaType(file: File): MediaType? {
        val head = ByteArray(12)
        val n = file.inputStream().use { it.read(head) }
        if (n < 8) return null
        return when {
            JpegCoefficients.isJpeg(head) -> MediaType.IMAGE
            Mp4Chain.isMp4(head) -> MediaType.VIDEO
            else -> null
        }
    }

    /** The chain hidden in [file], or null if it has none. */
    fun read(file: File): ReadResult? = when (mediaType(file)) {
        MediaType.IMAGE -> JpegChain.decode(JpegCoefficients.read(file.readBytes()))
            ?.let { ReadResult(it.chain, MediaType.IMAGE, it.confidence) }
        MediaType.VIDEO -> Mp4Chain.read(file)?.let { ReadResult(it, MediaType.VIDEO) }
        null -> null
    }

    /**
     * Append this device's hop to the chain in [input] and save to [output]
     * ([output] may equal [input] for videos and photos alike).
     *
     * Throws [UnsupportedJpegException] for a progressive / arithmetic JPEG;
     * the caller can re-save it once as a baseline JPEG and try again.
     */
    fun appendHop(
        input: File,
        output: File,
        deviceId: ByteArray,
        imei: String?,
        role: Role = Role.SEND,
        timestamp: Long = System.currentTimeMillis() / 1000,
        allowRepeat: Boolean = false,
    ): AppendResult {
        when (mediaType(input)) {
            MediaType.VIDEO -> {
                val existing = Mp4Chain.read(input)
                val (chain, added) = nextChain(existing, { Mp4Chain.contentId(input) },
                    deviceId, imei, role, timestamp, allowRepeat)
                if (input.canonicalPath != output.canonicalPath) input.copyTo(output, overwrite = true)
                Mp4Chain.write(output, chain)
                return AppendResult(chain, MediaType.VIDEO, added, existing == null)
            }
            MediaType.IMAGE -> {
                val jpeg = JpegCoefficients.read(input.readBytes())
                val existing = JpegChain.decode(jpeg)?.chain
                val (chain, added) = nextChain(existing, { JpegChain.contentId(jpeg) },
                    deviceId, imei, role, timestamp, allowRepeat)
                JpegChain.write(jpeg, chain)
                output.writeBytes(jpeg.toByteArray())
                return AppendResult(chain, MediaType.IMAGE, added, existing == null)
            }
            null -> throw ChainException("Unsupported file. Use a JPEG photo or an MP4 / MOV / 3GP video.")
        }
    }

    private fun nextChain(
        existing: Chain?, newId: () -> ByteArray, deviceId: ByteArray, imei: String?,
        role: Role, timestamp: Long, allowRepeat: Boolean,
    ): Pair<Chain, Boolean> {
        val chain = existing ?: Chain(newId())
        val last = chain.hops.lastOrNull()
        if (last != null && last.deviceId.contentEquals(deviceId) && !allowRepeat) return chain to false
        if (chain.hops.size >= ChainPayload.MAX_HOPS) {
            throw ChainException("Chain is full (${ChainPayload.MAX_HOPS} hops). No more devices can be added.")
        }
        imei?.let { ChainPayload.validateImei(it) }
        return Chain(chain.imageId, chain.hops + Hop(deviceId, imei, timestamp, role)) to true
    }
}
