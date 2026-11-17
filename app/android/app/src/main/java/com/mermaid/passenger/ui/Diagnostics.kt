package com.mermaid.passenger.ui

import android.os.SystemClock
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mermaid.passenger.BuildConfig
import com.mermaid.passenger.data.Cmd
import com.mermaid.passenger.data.LinkState
import com.mermaid.passenger.data.Repo
import com.mermaid.passenger.service.LinkService
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.delay
import java.util.Locale

/** APP-07 RF 진단: 5초 링 · 지표 · 로그 (APP-F-43). 로그는 기기 안에만 */
@Composable
fun DiagnosticsScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    val live by Repo.live.collectAsState()
    val st by Repo.stats.collectAsState()
    val log by Repo.log.collectAsState()
    var now by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    LaunchedEffect(Unit) { while (true) { delay(200); now = SystemClock.elapsedRealtime() } }
    val listState = rememberLazyListState()
    LaunchedEffect(log.size) { if (log.isNotEmpty()) listState.scrollToItem(log.size - 1) }

    val active = live.link == LinkState.CONNECTED || live.link == LinkState.LOST
    val age = if (st.lastRxAt > 0 && active) (now - st.lastRxAt) / 1000f else null
    val frac = ((age ?: 5f) / 5f).coerceIn(0f, 1f)
    val ringColor = when { live.link != LinkState.CONNECTED -> MM.Bad; frac > 0.5f -> MM.Wait; else -> MM.Ok }

    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row2(Modifier.height(56.dp)) {
            Row { Text("←", fontSize = 22.sp, modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp)); Text("RF 진단", style = MaterialTheme.typography.titleLarge) }
        }
        MMCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                val track = MaterialTheme.colorScheme.surfaceVariant
                Box(Modifier.size(104.dp), contentAlignment = Alignment.Center) {
                    Canvas(Modifier.size(104.dp)) {
                        val w = 10.dp.toPx(); val tl = Offset(w / 2, w / 2); val sz = Size(size.width - w, size.height - w)
                        drawArc(track, 0f, 360f, false, tl, sz, style = Stroke(w))
                        drawArc(ringColor, -90f, 360f * (1 - frac), false, tl, sz, style = Stroke(w, cap = StrokeCap.Round))
                    }
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(age?.let { String.format(Locale.US, "%.1fs", it) } ?: "—", fontSize = 22.sp, fontWeight = FontWeight.ExtraBold)
                        Text("마지막 수신", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                Spacer(Modifier.width(16.dp))
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    Kv("상태", when (live.link) { LinkState.CONNECTED -> "연결됨"; LinkState.LOST -> "신호 없음"; LinkState.CONNECTING -> "연결 중"; else -> "대기" },
                        when (live.link) { LinkState.CONNECTED -> MM.Ok; LinkState.LOST -> MM.Bad; else -> null })
                    Kv("브리지", st.name.ifEmpty { "—" })
                    Kv("RSSI", st.rssi?.let { "$it dBm" } ?: "—")
                    Kv("MTU", if (st.mtu > 0) st.mtu.toString() else "—", if (st.mtu in 1..246) MM.Wait else null)
                    Kv("HB 보냄/받음", "${st.hbTx} / ${st.hbRx ?: "-"}")
                    Kv("TX / RX", "${st.tx} / ${st.rx}")
                    Kv("SCR", "on=${st.scrOn} · att=${st.scrAtt}")
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SmallButton("다시 연결", Modifier.weight(1f)) { Repo.send(Cmd.Reconnect) }
            SmallButton("로그 지우기", Modifier.weight(1f)) { Repo.log.value = emptyList() }
        }
        if (BuildConfig.DEBUG) Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SmallButton("데모 모드", Modifier.weight(1f)) { LinkService.start(ctx, LinkService.ACTION_DEMO) }
            SmallButton("끊김 재현", Modifier.weight(1f)) { Repo.send(Cmd.DemoDrop) }
        }
        LazyColumn(
            Modifier.fillMaxWidth().weight(1f).clip(RoundedCornerShape(14.dp)).background(MaterialTheme.colorScheme.surface)
                .border(1.dp, MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(14.dp)).padding(10.dp),
            state = listState,
        ) {
            items(log) { l ->
                Text("${l.time} ${l.dir} ${l.text}", fontFamily = FontFamily.Monospace, fontSize = 11.sp, lineHeight = 16.sp,
                    color = when (l.dir) { '→' -> Color(0xFF1660B0); '←' -> Color(0xFF1F7A52); '!' -> MM.Bad; else -> MaterialTheme.colorScheme.onSurfaceVariant })
            }
        }
        Muted("로그는 이 폰에만 저장되고 밖으로 보내지 않아요. 최근 300줄 · MOT·HB는 개수만.")
        Spacer(Modifier.height(4.dp))
    }
}

@Composable
private fun Kv(k: String, v: String, color: Color? = null) {
    Row2 {
        Text(k, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(v, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = color ?: MaterialTheme.colorScheme.onSurface)
    }
}
