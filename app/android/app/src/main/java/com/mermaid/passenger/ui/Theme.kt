package com.mermaid.passenger.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/** 화면정의서 2장 색·글자 */
object MM {
    val Brand = Color(0xFFA50034)
    val BrandHi = Color(0xFFE3245F)
    val Ink = Color(0xFF1E1B1D)
    val Paper = Color(0xFFF6F3F0)
    val Muted = Color(0xFF7A7074)
    val Line = Color(0xFFECE6E1)
    val T1 = Color(0xFF1683D8)
    val T2 = Color(0xFFD61F69)
    val T3 = Color(0xFFC77700)
    val Ok = Color(0xFF1F9D68)
    val Bad = Color(0xFFC62828)
    val Wait = Color(0xFFC77700)
}

private val Light = lightColorScheme(
    primary = MM.Brand, onPrimary = Color.White,
    background = MM.Paper, onBackground = MM.Ink,
    surface = Color.White, onSurface = MM.Ink,
    surfaceVariant = Color(0xFFEBE5E0), onSurfaceVariant = MM.Muted,
    outline = Color(0xFFD8D0CA), error = MM.Bad,
)
private val Dark = darkColorScheme(
    primary = MM.BrandHi, onPrimary = Color.White,
    background = Color(0xFF121011), onBackground = Color(0xFFF6F3F0),
    surface = Color(0xFF1F1B1D), onSurface = Color(0xFFF6F3F0),
    surfaceVariant = Color(0xFF2C2729), onSurfaceVariant = Color(0xFFA79D99),
    outline = Color(0xFF4A4346), error = Color(0xFFFF6B6B),
)

private val Type = Typography(
    headlineMedium = TextStyle(fontSize = 24.sp, fontWeight = FontWeight.ExtraBold, lineHeight = 31.sp),
    titleLarge = TextStyle(fontSize = 18.sp, fontWeight = FontWeight.ExtraBold, lineHeight = 24.sp),
    titleMedium = TextStyle(fontSize = 15.5.sp, fontWeight = FontWeight.Bold, lineHeight = 21.sp),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontSize = 13.5.sp, lineHeight = 20.sp),
    labelLarge = TextStyle(fontSize = 16.sp, fontWeight = FontWeight.Bold),
    labelMedium = TextStyle(fontSize = 12.5.sp, fontWeight = FontWeight.Bold),
)

@Composable
fun MerMaidTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = if (isSystemInDarkTheme()) Dark else Light, typography = Type, content = content)
}
