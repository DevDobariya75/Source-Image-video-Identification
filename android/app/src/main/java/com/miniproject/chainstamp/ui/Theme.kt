package com.miniproject.chainstamp.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

// Role colours, the same as the dashboard's tree.
val OriginColor = Color(0xFF7C3AED)
val SendColor = Color(0xFF2563EB)
val ClaimColor = Color(0xFF059669)

@Composable
fun ChainStampTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val colors = when {
        Build.VERSION.SDK_INT >= 31 -> if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> darkColorScheme(primary = Color(0xFF6B9CFF))
        else -> lightColorScheme(primary = SendColor)
    }
    MaterialTheme(colorScheme = colors, content = content)
}
