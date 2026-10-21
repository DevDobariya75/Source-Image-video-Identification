package com.miniproject.chainstamp

import android.content.ClipData
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.net.Uri
import android.provider.MediaStore
import android.provider.OpenableColumns
import androidx.core.content.FileProvider
import com.miniproject.chain.MediaType
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

    /** Copy a content URI into [dir] byte for byte (no re-encoding). */
    fun copyIn(context: Context, uri: Uri, dir: File, name: String): File {
        val out = File(dir, name)
        val input = context.contentResolver.openInputStream(uri) ?: throw IllegalStateException("Can't open the file.")
        input.use { i -> out.outputStream().use { o -> i.copyTo(o, 1 shl 16) } }
        return out
    }

    /**
     * Convert a non-JPEG photo (PNG, HEIC, WebP, ...) or an exotic JPEG to a
     * normal JPEG once, so a chain can be stored in it. ImageDecoder applies
     * the EXIF rotation, so the photo stays the right way up.
     */
    fun convertToJpeg(src: File, dst: File) {
        val bitmap = ImageDecoder.decodeBitmap(ImageDecoder.createSource(src)) { decoder, _, _ ->
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
        }
        dst.outputStream().use { bitmap.compress(Bitmap.CompressFormat.JPEG, 95, it) }
        bitmap.recycle()
    }

    fun mimeType(file: File, media: MediaType): String = when {
        media == MediaType.IMAGE -> "image/jpeg"
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
