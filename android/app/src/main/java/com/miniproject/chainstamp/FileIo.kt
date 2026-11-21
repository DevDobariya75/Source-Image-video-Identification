package com.miniproject.chainstamp

import android.content.ClipData
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.graphics.ColorSpace
import android.graphics.ImageDecoder
import android.net.Uri
import android.provider.MediaStore
import android.provider.OpenableColumns
import androidx.core.content.FileProvider
import com.miniproject.chain.MediaType
import com.miniproject.chain.Png
import com.miniproject.chain.RgbImage
import java.io.File

/** Copying files in and out of the app: content URIs, gallery, sharing. */
object FileIo {

    fun displayName(context: Context, uri: Uri): String {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0)?.let { return sanitize(it) }
        }
        return sanitize(uri.lastPathSegment ?: "file")
    }

    private fun sanitize(name: String) = name.substringAfterLast('/').replace(Regex("[\\\\:*?\"<>|]"), "_")

    /** A fresh, empty folder inside the app cache. */
    fun freshDir(context: Context, name: String): File =
        File(context.cacheDir, name).apply { deleteRecursively(); mkdirs() }

    /** Copy a content URI into [dir] byte for byte (no re-encoding). Used for videos. */
    fun copyIn(context: Context, uri: Uri, dir: File, name: String): File {
        val out = File(dir, name)
        val input = context.contentResolver.openInputStream(uri) ?: throw IllegalStateException("Can't open the file.")
        input.use { i -> out.outputStream().use { o -> i.copyTo(o, 1 shl 20) } }
        return out
    }

    /** The first bytes of the file behind [uri], to tell PNG / MP4 / other apart. */
    fun readHead(context: Context, uri: Uri, n: Int = 12): ByteArray {
        val head = ByteArray(n)
        context.contentResolver.openInputStream(uri)?.use { s ->
            var got = 0
            while (got < n) {
                val k = s.read(head, got, n - got)
                if (k < 0) break
                got += k
            }
        } ?: throw IllegalStateException("Can't open the file.")
        return head
    }

    /** Read a PNG straight from the content URI with the chain library's exact decoder. */
    fun readPng(context: Context, uri: Uri): RgbImage =
        (context.contentResolver.openInputStream(uri) ?: throw IllegalStateException("Can't open the file."))
            .buffered(1 shl 16).use { Png.read(it) }

    /**
     * Decode any photo (JPEG, HEIC, WebP, a 16-bit PNG, ...) straight from the
     * content URI into 8-bit RGB pixels, so the chain can be hidden in them; it
     * is saved as a lossless PNG when it is stamped. ImageDecoder applies the
     * EXIF rotation, so the photo stays the right way up. Alpha is dropped.
     * Pixels are copied out in blocks of rows to keep memory use low.
     */
    fun decodeToRgb(context: Context, uri: Uri): RgbImage {
        val source = ImageDecoder.createSource(context.contentResolver, uri)
        val bitmap = ImageDecoder.decodeBitmap(source) { decoder, _, _ ->
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            decoder.isUnpremultipliedRequired = true
            decoder.setTargetColorSpace(ColorSpace.get(ColorSpace.Named.SRGB))
        }
        try {
            val w = bitmap.width
            val h = bitmap.height
            val img = RgbImage(w, h)
            val block = 64
            val px = IntArray(w * block)
            val rgb = img.rgb
            var y = 0
            while (y < h) {
                val rows = minOf(block, h - y)
                bitmap.getPixels(px, 0, w, 0, y, w, rows)
                var o = y * w * 3
                for (i in 0 until w * rows) {
                    val c = px[i]
                    rgb[o] = (c shr 16).toByte()
                    rgb[o + 1] = (c shr 8).toByte()
                    rgb[o + 2] = c.toByte()
                    o += 3
                }
                y += rows
            }
            return img
        } finally {
            bitmap.recycle()
        }
    }

    fun mimeType(file: File, media: MediaType): String = when {
        media == MediaType.IMAGE -> "image/png"
        file.extension.equals("mov", true) -> "video/quicktime"
        file.extension.equals("3gp", true) -> "video/3gpp"
        else -> "video/mp4"
    }

    /** Intent that opens the share sheet for [file] (the user picks Bluetooth). */
    fun shareIntent(context: Context, file: File, mime: String): Intent {
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)
        val send = Intent(Intent.ACTION_SEND).apply {
            type = mime
            putExtra(Intent.EXTRA_STREAM, uri)
            clipData = ClipData.newRawUri(file.name, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        return Intent.createChooser(send, "Send with Bluetooth")
    }

    /** Save [file] unchanged into Pictures/ChainStamp or Movies/ChainStamp. Returns the folder. */
    fun saveToGallery(context: Context, file: File, media: MediaType): String {
        val video = media == MediaType.VIDEO
        val folder = if (video) "Movies/ChainStamp" else "Pictures/ChainStamp"
        val collection = if (video) {
            MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        } else {
            MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        }
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, file.name)
            put(MediaStore.MediaColumns.MIME_TYPE, mimeType(file, media))
            put(MediaStore.MediaColumns.RELATIVE_PATH, folder)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val resolver = context.contentResolver
        val uri = resolver.insert(collection, values) ?: throw IllegalStateException("Can't create the file in the gallery.")
        try {
            resolver.openOutputStream(uri)?.use { o -> file.inputStream().use { it.copyTo(o, 1 shl 16) } }
                ?: throw IllegalStateException("Can't write to the gallery.")
            values.clear()
            values.put(MediaStore.MediaColumns.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
        } catch (e: Exception) {
            resolver.delete(uri, null, null)
            throw e
        }
        return folder
    }
}
