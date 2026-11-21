package com.miniproject.chainstamp

import android.app.Application
import android.content.Intent
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.miniproject.chain.Chain
import com.miniproject.chain.ChainException
import com.miniproject.chain.ChainPayload
import com.miniproject.chain.MediaChain
import com.miniproject.chain.MediaType
import com.miniproject.chain.Mp4Chain
import com.miniproject.chain.PixelChain
import com.miniproject.chain.Png
import com.miniproject.chain.RgbImage
import com.miniproject.chain.Role
import com.miniproject.chain.UnsupportedImageException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * A photo or video opened in the app. Photos are decoded once and kept in
 * memory ([image]), so stamping never reads or converts the file again.
 * Videos are kept as a private copy in the cache ([file]).
 */
class LoadedFile(
    val name: String,
    val media: MediaType,
    val chain: Chain?,
    /** Shown under the file name, e.g. "Will be saved as PNG". */
    val note: String? = null,
    /** Photo mode the chain is currently stored in (3 or 6), null if none / video. */
    val bitsPerPixel: Int? = null,
    /** Photo pixels (photos only). */
    val image: RgbImage? = null,
    /** Private copy of the video (videos only). */
    val file: File? = null,
) {
    fun stamped(chain: Chain, bitsPerPixel: Int?, file: File?) =
        LoadedFile(name, media, chain, note, bitsPerPixel, image, file ?: this.file)
}

data class UiState(
    val deviceIdHex: String,
    val imei: String?,
    val showImeiDialog: Boolean,
    /** Photo mode used when stamping: 3 or 6 bits per pixel. */
    val bitsPerPixel: Int = PixelChain.DEFAULT_BPP,
    val file: LoadedFile? = null,
    /** Non-null while working, e.g. "Reading…". */
    val busy: String? = null,
    val message: String? = null,
    val error: String? = null,
)

class ChainViewModel(app: Application) : AndroidViewModel(app) {
    private val identity = Identity(app)

    private val _state = MutableStateFlow(
        UiState(identity.deviceIdHex, identity.imei, showImeiDialog = !identity.setupDone,
            bitsPerPixel = identity.bitsPerPixel),
    )
    val state: StateFlow<UiState> = _state

    /** Intents the Activity must start (the share sheet). */
    private val _launch = Channel<Intent>(Channel.BUFFERED)
    val launch = _launch.receiveAsFlow()

    private val context get() = getApplication<Application>()

    // ------------------------------------------------------------------
    // Identity
    // ------------------------------------------------------------------

    fun editImei() = _state.update { it.copy(showImeiDialog = true) }

    fun saveImei(imei: String?) {
        identity.imei = imei
        identity.setupDone = true
        _state.update { it.copy(imei = imei, showImeiDialog = false) }
    }

    fun dismissImeiDialog() {
        identity.setupDone = true
        _state.update { it.copy(showImeiDialog = false) }
    }

    /** Choose how photos are stamped: 3 or 6 bits per pixel. Remembered. */
    fun setBitsPerPixel(bpp: Int) {
        if (bpp !in PixelChain.MODES) return
        identity.bitsPerPixel = bpp
        _state.update { it.copy(bitsPerPixel = bpp) }
    }

    // ------------------------------------------------------------------
    // Files
    // ------------------------------------------------------------------

    fun dismissMessages() = _state.update { it.copy(message = null, error = null) }

    fun closeFile() = _state.update { it.copy(file = null, message = null, error = null) }

    /**
     * Open a photo/video picked in the app or shared to it.
     *
     * Photos are decoded straight from the content URI, once: a PNG with our
     * exact decoder (then its chain is read), any other format with Android's
     * ImageDecoder (it can't hold a chain yet). Nothing is written to disk until
     * the photo is stamped. Videos are copied into the cache, because the MP4
     * box is written into the file.
     */
    fun open(uri: Uri) = work("Opening…") {
        val name = FileIo.displayName(context, uri)
        val base = name.substringBeforeLast('.').ifEmpty { "photo" }
        val head = FileIo.readHead(context, uri)
        val unsupported = "Unsupported file. Use a photo (JPEG, PNG, HEIC…) or an MP4 / MOV / 3GP video."

        val loaded = when {
            Mp4Chain.isMp4(head) -> {
                step("Copying the video…")
                val copy = FileIo.copyIn(context, uri, FileIo.freshDir(context, "in"), name)
                step("Reading the chain…")
                LoadedFile(name, MediaType.VIDEO, Mp4Chain.read(copy), file = copy)
            }
            Png.isPng(head) -> {
                step("Reading the photo…")
                try {
                    val img = FileIo.readPng(context, uri)
                    step("Looking for a chain…")
                    val res = PixelChain.decode(img)
                    LoadedFile("$base.png", MediaType.IMAGE, res?.chain, null, res?.bitsPerPixel, img)
                } catch (e: UnsupportedImageException) {
                    val img = FileIo.decodeToRgb(context, uri)
                    LoadedFile("$base.png", MediaType.IMAGE, null,
                        "Will be saved as a standard 8-bit PNG (${e.message})", image = img)
                }
            }
            else -> {
                step("Reading the photo…")
                val img = try {
                    FileIo.decodeToRgb(context, uri)
                } catch (e: Exception) {
                    throw ChainException(unsupported)
                }
                LoadedFile("$base.png", MediaType.IMAGE, null,
                    "Will be saved as PNG (lossless), so the chain can be hidden in its pixels", image = img)
            }
        }
        _state.update { it.copy(file = loaded) }
    }

    /** Add this phone to the chain and open the share sheet (pick Bluetooth). */
    fun stampAndSend() = stamp(Role.SEND) { loaded, out, added ->
        _launch.send(FileIo.shareIntent(context, out, FileIo.mimeType(out, loaded.media)))
        if (added) "Added this phone to the chain. Choose Bluetooth to send it."
        else "This phone is already the last in the chain, so no hop was added. Choose Bluetooth to send it."
    }

    /** Add this phone as the final receiver and save the file to the gallery. */
    fun saveAsReceived() = stamp(Role.CLAIM) { loaded, out, added ->
        val folder = FileIo.saveToGallery(context, out, loaded.media)
        if (added) "Saved to $folder with this phone as the receiver."
        else "This phone is already the last in the chain. Saved to $folder."
    }

    private fun stamp(role: Role, after: suspend (LoadedFile, File, Boolean) -> String) = work("Stamping…") {
        val loaded = _state.value.file ?: return@work
        // A new folder per stamp: Bluetooth may still be reading an earlier one.
        // Old folders are cleaned up, except the one holding the current video.
        val outRoot = File(context.cacheDir, "out")
        val hourAgo = System.currentTimeMillis() - 3_600_000
        val keepDir = loaded.file?.parentFile?.canonicalPath
        outRoot.listFiles()?.filter { it.lastModified() < hourAgo && it.canonicalPath != keepDir }
            ?.forEach { it.deleteRecursively() }
        val outDir = File(outRoot, System.currentTimeMillis().toString()).apply { mkdirs() }
        val out = File(outDir, loaded.name)

        val result = if (loaded.media == MediaType.IMAGE) {
            step("Hiding the chain in the pixels…")
            // The pixels are already in memory: stamp them and write the PNG once.
            MediaChain.stampImage(
                img = loaded.image ?: throw ChainException("The photo is no longer loaded. Open it again."),
                existing = loaded.chain,
                existingBpp = loaded.bitsPerPixel,
                output = out,
                deviceId = identity.deviceId,
                imei = identity.imei,
                role = role,
                bitsPerPixel = _state.value.bitsPerPixel,
            )
        } else {
            step("Adding the chain to the video…")
            MediaChain.appendHop(
                input = loaded.file ?: throw ChainException("The video is no longer loaded. Open it again."),
                output = out,
                deviceId = identity.deviceId,
                imei = identity.imei,
                role = role,
            )
        }
        // Keep working on the stamped version: sending it again won't add this phone twice.
        val now = loaded.stamped(result.chain, result.bitsPerPixel, if (loaded.media == MediaType.VIDEO) out else null)
        _state.update { it.copy(file = now) }
        step(if (role == Role.SEND) "Opening the share sheet…" else "Saving to the gallery…")
        val msg = after(now, out, result.added)
        _state.update { it.copy(message = msg) }
    }

    /** Show what is happening right now in the busy bar. */
    private fun step(label: String) = _state.update { it.copy(busy = label) }

    private fun work(label: String, block: suspend () -> Unit) {
        if (_state.value.busy != null) return
        _state.update { it.copy(busy = label, message = null, error = null) }
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { block() }
            } catch (e: ChainException) {
                _state.update { it.copy(error = e.message) }
            } catch (e: Exception) {
                _state.update { it.copy(error = "Something went wrong: ${e.message ?: e.javaClass.simpleName}") }
            } finally {
                _state.update { it.copy(busy = null) }
            }
        }
    }

    companion object {
        /** Live validation for the IMEI field: null if OK, else a hint. */
        fun imeiProblem(imei: String): String? = when {
            imei.isEmpty() -> null
            imei.length != 15 || !imei.all(Char::isDigit) -> "An IMEI has exactly 15 digits"
            !ChainPayload.luhnOk(imei) -> "Check digit doesn't match. Is there a typo?"
            else -> null
        }
    }
}
