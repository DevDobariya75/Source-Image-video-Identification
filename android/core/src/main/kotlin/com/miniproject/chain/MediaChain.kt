package com.miniproject.chain

import java.io.File

enum class MediaType { IMAGE, VIDEO }

/** One entry point for photos (PNG) and videos (MP4): read the chain, or append a hop. */
object MediaChain {

    class ReadResult(
        val chain: Chain,
        val media: MediaType,
        val confidence: Double = 1.0,
        /** 3 or 6 for photos, null for videos. */
        val bitsPerPixel: Int? = null,
    )

    class AppendResult(
        val chain: Chain,
        val media: MediaType,
        /** False if this device was already the last hop (nothing added). */
        val added: Boolean,
        /** True if the file had no chain and this device is the origin. */
        val newChain: Boolean,
        /** Mode the photo was written in; null for videos. */
        val bitsPerPixel: Int? = null,
    )

    /**
     * Detect the type from the file's first bytes: IMAGE for a PNG, VIDEO for
     * an MP4 / MOV / 3GP, null for anything else (JPEG, HEIC, ...: the app
     * converts those to PNG once before stamping).
     */
    fun mediaType(file: File): MediaType? {
        val head = ByteArray(12)
        val n = file.inputStream().use { it.read(head) }
        if (n < 8) return null
        return when {
            Png.isPng(head) -> MediaType.IMAGE
            Mp4Chain.isMp4(head) -> MediaType.VIDEO
            else -> null
        }
    }

    /**
     * The chain hidden in [file], or null if it has none.
     * Throws [UnsupportedImageException] for a PNG this library can't read.
     */
    fun read(file: File): ReadResult? = when (mediaType(file)) {
        MediaType.IMAGE -> PixelChain.decode(Png.read(file))
            ?.let { ReadResult(it.chain, MediaType.IMAGE, it.confidence, it.bitsPerPixel) }
        MediaType.VIDEO -> Mp4Chain.read(file)?.let { ReadResult(it, MediaType.VIDEO) }
        null -> null
    }

    /**
     * Append this device's hop to the chain in [input] and save to [output]
     * ([output] may equal [input]).
     *
     * [bitsPerPixel] (photos only): 3 or 6. Null keeps the photo's current
     * mode, or uses 3 for a new chain. The whole chain is re-written in that mode.
     *
     * Throws [UnsupportedImageException] for a PNG this library can't read, and
     * [ChainException] for other files; the caller converts them to PNG first.
     */
    fun appendHop(
        input: File,
        output: File,
        deviceId: ByteArray,
        imei: String?,
        role: Role = Role.SEND,
        timestamp: Long = System.currentTimeMillis() / 1000,
        allowRepeat: Boolean = false,
        bitsPerPixel: Int? = null,
    ): AppendResult {
        when (mediaType(input)) {
            MediaType.VIDEO -> {
                val existing = Mp4Chain.read(input)
                val (chain, added) = nextChain(existing, { Mp4Chain.contentId(input) },
                    deviceId, imei, role, timestamp, allowRepeat)
                if (input.canonicalPath != output.canonicalPath) {
                    java.nio.file.Files.copy(input.toPath(), output.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)
                }
                Mp4Chain.write(output, chain)
                return AppendResult(chain, MediaType.VIDEO, added, existing == null)
            }
            MediaType.IMAGE -> {
                val img = Png.read(input)
                val existing = PixelChain.decode(img)
                return stampImage(img, existing?.chain, existing?.bitsPerPixel, output, deviceId, imei, role,
                    timestamp, allowRepeat, bitsPerPixel)
            }
            null -> throw ChainException("Unsupported file. Use a PNG photo or an MP4 / MOV / 3GP video.")
        }
    }

    /**
     * Stamp a photo that is already decoded in memory (the app keeps it after
     * opening, so nothing is read from disk again). [img] is modified in place
     * and then holds the stamped pixels; the PNG is written to [output].
     * [existing] / [existingBpp] are the chain and mode found when the photo was opened.
     */
    fun stampImage(
        img: RgbImage,
        existing: Chain?,
        existingBpp: Int?,
        output: File,
        deviceId: ByteArray,
        imei: String?,
        role: Role = Role.SEND,
        timestamp: Long = System.currentTimeMillis() / 1000,
        allowRepeat: Boolean = false,
        bitsPerPixel: Int? = null,
    ): AppendResult {
        if (bitsPerPixel != null && bitsPerPixel !in PixelChain.MODES) {
            throw ChainException("Bits per pixel must be 3 or 6.")
        }
        val (chain, added) = nextChain(existing, { PixelChain.contentId(img) },
            deviceId, imei, role, timestamp, allowRepeat)
        val bpp = bitsPerPixel ?: existingBpp ?: PixelChain.DEFAULT_BPP
        PixelChain.write(img, chain, bpp)
        Png.write(img, output)
        return AppendResult(chain, MediaType.IMAGE, added, existing == null, bpp)
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
