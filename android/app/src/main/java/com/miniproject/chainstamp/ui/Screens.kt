package com.miniproject.chainstamp.ui

import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Done
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.miniproject.chain.Hop
import com.miniproject.chain.MediaType
import com.miniproject.chain.PixelChain
import com.miniproject.chain.Role
import com.miniproject.chainstamp.ChainViewModel
import com.miniproject.chainstamp.LoadedFile
import com.miniproject.chainstamp.UiState
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

private val timeFormat = DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
    .withZone(ZoneId.systemDefault())

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChainStampApp(vm: ChainViewModel) {
    val state by vm.state.collectAsStateWithLifecycle()
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let(vm::open)
    }
    val pick = { picker.launch(arrayOf("image/*", "video/*")) }

    BackHandler(enabled = state.file != null) { vm.closeFile() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(if (state.file == null) "ChainStamp" else "Forwarding chain") },
                navigationIcon = {
                    if (state.file != null) {
                        IconButton(onClick = vm::closeFile) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                        }
                    }
                },
            )
        },
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            state.busy?.let {
                LinearProgressIndicator(Modifier.fillMaxWidth())
                Text(it, style = MaterialTheme.typography.bodyMedium)
            }
            state.error?.let { StatusCard(it, isError = true, onDismiss = vm::dismissMessages) }
            state.message?.let { StatusCard(it, isError = false, onDismiss = vm::dismissMessages) }

            val file = state.file
            if (file == null) {
                HomeScreen(state, onPick = pick, onEditImei = vm::editImei)
            } else {
                FileScreen(
                    file = file,
                    myDeviceId = state.deviceIdHex,
                    busy = state.busy != null,
                    bitsPerPixel = state.bitsPerPixel,
                    onBitsPerPixel = vm::setBitsPerPixel,
                    onSend = vm::stampAndSend,
                    onSave = vm::saveAsReceived,
                )
            }
        }
    }

    if (state.showImeiDialog) {
        ImeiDialog(initial = state.imei.orEmpty(), onSave = vm::saveImei, onSkip = vm::dismissImeiDialog)
    }
}

// ---------------------------------------------------------------------------
// Home
// ---------------------------------------------------------------------------

@Composable
private fun HomeScreen(state: UiState, onPick: () -> Unit, onEditImei: () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("This phone", style = MaterialTheme.typography.titleMedium)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("IMEI", style = MaterialTheme.typography.labelMedium)
                    Text(
                        state.imei ?: "Not set",
                        fontFamily = FontFamily.Monospace,
                        color = if (state.imei == null) MaterialTheme.colorScheme.error else Color.Unspecified,
                    )
                }
                IconButton(onClick = onEditImei) { Icon(Icons.Default.Edit, contentDescription = "Edit IMEI") }
            }
            Text("Device ID", style = MaterialTheme.typography.labelMedium)
            Text(state.deviceIdHex, fontFamily = FontFamily.Monospace)
        }
    }

    Button(onClick = onPick, modifier = Modifier.fillMaxWidth().height(56.dp)) {
        Icon(Icons.Default.Search, contentDescription = null)
        Spacer(Modifier.width(8.dp))
        Text("Choose a photo or video")
    }
    Text(
        "Or open a file received over Bluetooth and tap Share → ChainStamp.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )

    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("How it works", style = MaterialTheme.typography.titleSmall)
            Step(1, "Open a photo or video. You'll see every phone it has already passed through.")
            Step(2, "Stamp & send adds this phone to the hidden chain, then pick Bluetooth to send it.")
            Step(3, "The last phone taps Save as received to add itself and keep the file.")
            Step(4, "Upload any copy to the dashboard to see the whole forwarding tree.")
            Text(
                "Photos are saved as PNG (lossless) with the chain hidden in their pixels, " +
                    "repeated all over the photo. Always send the file itself (Bluetooth, USB). " +
                    "Re-compressing or resizing, like WhatsApp in normal mode, erases the chain.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun Step(n: Int, text: String) {
    Row(verticalAlignment = Alignment.Top) {
        Text("$n.", fontWeight = FontWeight.Bold, modifier = Modifier.width(20.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium)
    }
}

// ---------------------------------------------------------------------------
// File
// ---------------------------------------------------------------------------

@Composable
private fun FileScreen(
    file: LoadedFile,
    myDeviceId: String,
    busy: Boolean,
    bitsPerPixel: Int,
    onBitsPerPixel: (Int) -> Unit,
    onSend: () -> Unit,
    onSave: () -> Unit,
) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(file.name, style = MaterialTheme.typography.titleMedium)
            Text(
                if (file.media == MediaType.VIDEO) "Video" else "Photo",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            file.note?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.tertiary)
            }
            file.bitsPerPixel?.let {
                Text(
                    "Chain stored with $it bits per pixel",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }

    val chain = file.chain
    if (chain == null || chain.hops.isEmpty()) {
        Text(
            "No forwarding chain yet. When you send it, this phone becomes the origin.",
            style = MaterialTheme.typography.bodyMedium,
        )
    } else {
        Text("Passed through ${chain.hops.size} phone(s)", style = MaterialTheme.typography.titleSmall)
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(vertical = 8.dp)) {
                chain.hops.forEachIndexed { i, hop ->
                    if (i > 0) HorizontalDivider(Modifier.padding(start = 56.dp))
                    HopRow(i, hop, isMe = hop.deviceIdHex == myDeviceId)
                }
            }
        }
        Text(
            "${if (file.media == MediaType.VIDEO) "Video" else "Photo"} ID ${chain.imageIdHex}",
            style = MaterialTheme.typography.bodySmall,
            fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }

    if (file.media == MediaType.IMAGE) {
        ModeChooser(bitsPerPixel, enabled = !busy, onSelect = onBitsPerPixel)
    }

    Button(onClick = onSend, enabled = !busy, modifier = Modifier.fillMaxWidth().height(56.dp)) {
        Icon(Icons.AutoMirrored.Filled.Send, contentDescription = null)
        Spacer(Modifier.width(8.dp))
        Text("Stamp & send")
    }
    OutlinedButton(onClick = onSave, enabled = !busy, modifier = Modifier.fillMaxWidth().height(56.dp)) {
        Icon(Icons.Default.Done, contentDescription = null)
        Spacer(Modifier.width(8.dp))
        Text("Save as received")
    }
    Text(
        "Stamp & send: adds this phone and opens the share sheet. Choose Bluetooth.\n" +
            "Save as received: adds this phone as the final receiver and saves the file to your gallery.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** 3 or 6 bits per pixel: how many low bits of each R, G, B value carry the chain. */
@Composable
private fun ModeChooser(selected: Int, enabled: Boolean, onSelect: (Int) -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Hide the chain using", style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                for (bpp in PixelChain.MODES) {
                    FilterChip(
                        selected = selected == bpp,
                        onClick = { onSelect(bpp) },
                        enabled = enabled,
                        label = { Text("$bpp bits / pixel") },
                    )
                }
            }
            val side = PixelChain.tileSide(selected)
            Text(
                if (selected == 3) {
                    "1 bit in each R, G, B value (changes it by at most 1). Invisible. " +
                        "A full copy of the chain every $side × $side pixels."
                } else {
                    "2 bits in each R, G, B value (changes it by at most 3). Still invisible in " +
                        "normal photos, and twice as many copies: one every $side × $side pixels."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun HopRow(index: Int, hop: Hop, isMe: Boolean) {
    val (color, roleText) = when {
        index == 0 -> OriginColor to "origin"
        hop.role == Role.CLAIM -> ClaimColor to "received"
        else -> SendColor to "forwarded"
    }
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(28.dp).background(color, CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            Text("${index + 1}", color = Color.White, fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    hop.imei ?: "IMEI not set",
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.SemiBold,
                )
                if (isMe) {
                    Spacer(Modifier.width(6.dp))
                    Text("this phone", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                }
            }
            Text(
                "ID ${hop.deviceIdHex.take(4)}…${hop.deviceIdHex.takeLast(4)} · ${timeFormat.format(hop.time)}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Text(roleText, style = MaterialTheme.typography.labelMedium, color = color)
    }
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

@Composable
private fun StatusCard(text: String, isError: Boolean, onDismiss: () -> Unit) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (isError) MaterialTheme.colorScheme.errorContainer
            else MaterialTheme.colorScheme.secondaryContainer,
        ),
    ) {
        Row(Modifier.padding(start = 16.dp, end = 4.dp, top = 4.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(text, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
            TextButton(onClick = onDismiss) { Text("OK") }
        }
    }
}

@Composable
private fun ImeiDialog(initial: String, onSave: (String?) -> Unit, onSkip: () -> Unit) {
    var imei by remember { mutableStateOf(initial) }
    val problem = ChainViewModel.imeiProblem(imei)
    val wellFormed = imei.isEmpty() || (imei.length == 15 && imei.all(Char::isDigit))
    AlertDialog(
        onDismissRequest = onSkip,
        title = { Text("This phone's IMEI") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    "Android doesn't let apps read the IMEI, so type it once. " +
                        "Dial *#06# in the Phone app to see it. It is stored only on this phone " +
                        "and added to files you stamp.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                OutlinedTextField(
                    value = imei,
                    onValueChange = { v -> imei = v.filter(Char::isDigit).take(15) },
                    label = { Text("IMEI (15 digits)") },
                    singleLine = true,
                    isError = problem != null,
                    supportingText = { problem?.let { Text(it) } },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onSave(imei.ifEmpty { null }) }, enabled = wellFormed) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onSkip) { Text("Skip") } },
    )
}
