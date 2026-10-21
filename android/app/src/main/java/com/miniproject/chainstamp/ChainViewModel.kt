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
import com.miniproject.chain.Role
import com.miniproject.chain.UnsupportedJpegException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/** A photo or video opened in the app (a private copy in the cache). */
data class LoadedFile(
    val name: String,
    val media: MediaType,
    val file: File,
    val chain: Chain?,
    /** Shown under the file name, e.g. "Converted to JPEG". */
    val note: String? = null,
)

data class UiState(
    val deviceIdHex: String,
    val imei: String?,
    val showImeiDialog: Boolean,
    val file: LoadedFile? = null,
    /** Non-null while working, e.g. "Reading…". */
    val busy: String? = null,
    val message: String? = null,
    val error: String? = null,
)

class ChainViewModel(app: Application) : AndroidViewModel(app) {
    private val identity = Identity(app)

    private val _state = MutableStateFlow(
        UiState(identity.deviceIdHex, identity.imei, showImeiDialog = !identity.setupDone),
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

    // ------------------------------------------------------------------
    // Files
    // ------------------------------------------------------------------

    fun dismissMessages() = _state.update { it.copy(message = null, error = null) }

    fun closeFile() = _state.update { it.copy(file = null, message = null, error = null) }

    /** Open a photo/video picked in the app or shared to it. */
    fun open(uri: Uri) = work("Reading…") {
        val name = FileIo.displayName(context, uri)
        val dir = FileIo.freshDir(context, "in")
        val copy = FileIo.copyIn(context, uri, dir, name)
        var file = copy
        var note: String? = null

        val detected: MediaType? = MediaChain.mediaType(copy)
        val media: MediaType = detected ?: MediaType.IMAGE
        if (detected == null) {
            // Not a JPEG / MP4: try to convert a photo to JPEG once.
            file = File(dir, name.substringBeforeLast('.') + ".jpg")
            try {
                FileIo.convertToJpeg(copy, file)
            } catch (e: Exception) {
                throw ChainException("Unsupported file. Use a photo (JPEG, PNG, HEIC…) or an MP4 / MOV / 3GP video.")
            }
            note = "Converted to JPEG (the original was not a JPEG)"
        }

        val chain = try {
            MediaChain.read(file)?.chain
        } catch (e: UnsupportedJpegException) {
            val converted = File(dir, "converted_" + file.nameWithoutExtension + ".jpg")
            FileIo.convertToJpeg(file, converted)
            file = converted
            note = "Converted to a standard JPEG (unusual JPEG type)"
            null
        }
        _state.update { it.copy(file = LoadedFile(file.name.removePrefix("converted_"), media, file, chain, note)) }
    }

    /** Add this phone to the chain and open the share sheet (pick Bluetooth). */
    fun stampAndSend() = stamp(Role.SEND) { loaded, out, added ->
        _launch.send(FileIo.shareIntent(context, out, FileIo.mimeType(out, loaded.media)))
        if (added) "Added this phone to the chain. Choose Bluetooth to send it."
        else "This phone is already the last in the chain, so it was sent unchanged."
    }

    /** Add this phone as the final receiver and save the file to the gallery. */
    fun saveAsReceived() = stamp(Role.CLAIM) { loaded, out, added ->
        val folder = FileIo.saveToGallery(context, out, loaded.media)
        if (added) "Saved to $folder with this phone as the receiver."
        else "This phone is already in the chain. Saved to $folder unchanged."
    }

    private fun stamp(role: Role, after: suspend (LoadedFile, File, Boolean) -> String) = work("Stamping…") {
        val loaded = _state.value.file ?: return@work
        // A new folder per stamp: Bluetooth may still be reading an earlier one.
        val outRoot = File(context.cacheDir, "out")
        val hourAgo = System.currentTimeMillis() - 3_600_000
        outRoot.listFiles()?.filter { it.lastModified() < hourAgo }?.forEach { it.deleteRecursively() }
        val outDir = File(outRoot, System.currentTimeMillis().toString()).apply { mkdirs() }
        val out = File(outDir, loaded.name)
        val result = MediaChain.appendHop(
            input = loaded.file,
            output = out,
            deviceId = identity.deviceId,
            imei = identity.imei,
            role = role,
        )
        // Keep working on the stamped copy: sending it again won't add this phone twice.
        val keep = File(loaded.file.parentFile, "stamped_" + loaded.name)
        out.copyTo(keep, overwrite = true)
        _state.update { it.copy(file = loaded.copy(file = keep, chain = result.chain)) }
        val msg = after(loaded, out, result.added)
        _state.update { it.copy(message = msg) }
    }

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
