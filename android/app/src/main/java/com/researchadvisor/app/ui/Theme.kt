package com.researchadvisor.app.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val Accent = Color(0xFF6C8CFF)
val OpenRed = Color(0xFFFF6B6B)
val PartialAmber = Color(0xFFFFC65C)
val AddressedGreen = Color(0xFF4ADE80)
val MutedGrey = Color(0xFF8B93A7)

private val DarkColors = darkColorScheme(
    primary = Accent,
    onPrimary = Color.White,
    secondary = Color(0xFFA06CFF),
    background = Color(0xFF0B0D12),
    onBackground = Color(0xFFE8ECF4),
    surface = Color(0xFF161A24),
    onSurface = Color(0xFFE8ECF4),
    surfaceVariant = Color(0xFF1C2130),
    onSurfaceVariant = MutedGrey,
    outline = Color(0xFF262C3A),
    error = OpenRed,
    onError = Color.White,
)

private val LightColors = lightColorScheme(
    primary = Accent,
    onPrimary = Color.White,
    background = Color(0xFFF7F8FC),
    onBackground = Color(0xFF14171F),
    surface = Color.White,
    onSurface = Color(0xFF14171F),
    surfaceVariant = Color(0xFFEDF0F7),
    onSurfaceVariant = Color(0xFF5A6376),
    outline = Color(0xFFD8DEEA),
    error = Color(0xFFD64545),
)

/** Verdict colour used by gap cards. */
fun verdictColor(verdict: String?): Color = when (verdict) {
    "open" -> OpenRed
    "partial" -> PartialAmber
    "addressed" -> AddressedGreen
    else -> MutedGrey
}

fun verdictLabel(verdict: String?): String = when (verdict) {
    "open" -> "STILL OPEN"
    "partial" -> "PARTIALLY ADDRESSED"
    "addressed" -> "ADDRESSED"
    "unverified" -> "UNVERIFIED"
    else -> "NOT CHECKED"
}

@Composable
fun AppTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        content = content,
    )
}
