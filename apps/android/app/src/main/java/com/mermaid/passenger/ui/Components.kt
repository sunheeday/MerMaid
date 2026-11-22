package com.mermaid.passenger.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mermaid.passenger.data.LinkState
import kotlin.math.sin

@Composable
fun MMCard(modifier: Modifier = Modifier, color: Color = MaterialTheme.colorScheme.surface, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(20.dp))
            .background(color)
            .border(1.dp, MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(20.dp))
            .padding(16.dp),
        content = content,
    )
}

@Composable
fun PrimaryButton(text: String, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    Button(
        onClick = onClick, enabled = enabled, shape = RoundedCornerShape(16.dp),
        modifier = modifier.fillMaxWidth().height(52.dp),
    ) { Text(text, style = MaterialTheme.typography.labelLarge) }
}

@Composable
fun GhostButton(text: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, shape = RoundedCornerShape(16.dp), modifier = modifier.fillMaxWidth().height(52.dp)) {
        Text(text, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
fun SmallButton(text: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, shape = RoundedCornerShape(12.dp), modifier = modifier.height(40.dp)) {
        Text(text, fontSize = 14.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
fun Muted(text: String, modifier: Modifier = Modifier) =
    Text(text, modifier = modifier, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)

@Composable
fun SectionLabel(text: String) =
    Text(text, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(top = 8.dp, bottom = 2.dp))

/** 연결 칩 (화면정의서 2장): 연결됨 / 차 찾는 중 / 신호 없음 / 미등록 */
@Composable
fun LinkChip(link: LinkState, registered: Boolean, onClick: () -> Unit = {}) {
    val (label, color) = when {
        !registered -> "미등록" to Color(0xFFBBBBBB)
        link == LinkState.CONNECTED -> "연결됨" to MM.Ok
        link == LinkState.LOST -> "신호 없음" to MM.Bad
        link == LinkState.CONNECTING -> "연결 중" to MM.Wait
        else -> "차 찾는 중" to MM.Wait
    }
    Row(
        Modifier
            .clip(RoundedCornerShape(15.dp))
            .background(if (link == LinkState.LOST) MM.Bad.copy(alpha = 0.10f) else MaterialTheme.colorScheme.surface)
            .border(1.dp, if (link == LinkState.LOST) MM.Bad.copy(alpha = 0.35f) else MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(15.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(6.dp))
        Text(label, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = if (link == LinkState.LOST) MM.Bad else MaterialTheme.colorScheme.onSurface)
    }
}

@Composable
fun Logo(size: Int = 24) {
    val ink = MaterialTheme.colorScheme.onBackground
    Canvas(Modifier.size(size.dp)) {
        val s = this.size.width / 32f
        val p = androidx.compose.ui.graphics.Path().apply {
            moveTo(4 * s, 22 * s)
            cubicTo(8 * s, 14 * s, 12 * s, 14 * s, 16 * s, 22 * s)
            cubicTo(20 * s, 30 * s, 24 * s, 30 * s, 28 * s, 22 * s)
        }
        drawPath(p, MM.Brand, style = Stroke(width = 3.4f * s, cap = androidx.compose.ui.graphics.StrokeCap.Round))
        drawCircle(ink, radius = 3.4f * s, center = Offset(16 * s, 9 * s))
    }
}

@Composable
fun TierBadge(text: String, color: Color) {
    Box(
        Modifier.size(36.dp).clip(RoundedCornerShape(11.dp)).background(color.copy(alpha = 0.14f)),
        contentAlignment = Alignment.Center,
    ) { Text(text, color = color, fontWeight = FontWeight.ExtraBold, fontSize = 12.sp) }
}

@Composable
fun Row2(modifier: Modifier = Modifier, content: @Composable RowScope.() -> Unit) =
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween, content = content)

/** 큐 미리보기 (APP-02, APP-06): 영상 카드 가장자리에서 점이 좌우로 흔들림 */
@Composable
fun CuePreview(modifier: Modifier = Modifier, gainDp: Int = 24, amber: Boolean = false, alpha: Float = 0.8f) {
    val t = rememberInfiniteTransition(label = "cue")
    val phase by t.animateFloat(0f, (2 * Math.PI).toFloat(), infiniteRepeatable(tween(4000, easing = LinearEasing), RepeatMode.Restart), label = "p")
    Box(modifier.fillMaxWidth().height(110.dp).clip(RoundedCornerShape(16.dp)).background(Color(0xFF1A1418))) {
        Box(
            Modifier.fillMaxSize().padding(horizontal = 34.dp, vertical = 12.dp).clip(RoundedCornerShape(12.dp))
                .background(Brush.linearGradient(listOf(Color(0xFF3D2B4F), Color(0xFF8A3E5C), Color(0xFFE8956A))))
        )
        Canvas(Modifier.fillMaxSize()) {
            val dpx = density
            val off = sin(phase) * gainDp * 0.45f * dpx
            val r = 3.5f * dpx
            val dot = if (amber) Color(0xFFFFB547) else Color.White
            for (col in 0..1) for (i in 0..3) {
                val y = size.height * (0.15f + 0.7f * (i + if (col == 1) 0.5f else 0f) / 3.5f)
                if (y > size.height * 0.88f) continue
                val xl = 10 * dpx + col * 10 * dpx + off
                val xr = size.width - 10 * dpx - col * 10 * dpx + off
                drawCircle(dot.copy(alpha = alpha), r, Offset(xl, y))
                drawCircle(dot.copy(alpha = alpha), r, Offset(xr, y))
            }
        }
    }
}
