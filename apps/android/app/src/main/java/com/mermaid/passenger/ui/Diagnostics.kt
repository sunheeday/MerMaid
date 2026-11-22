package com.mermaid.passenger.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.location.LocationManager
import android.os.SystemClock
import android.widget.Toast
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
import com.mermaid.passenger.data.GpsStats
import com.mermaid.passenger.data.LinkState
import com.mermaid.passenger.data.Prefs
import com.mermaid.passenger.data.Repo
import com.mermaid.passenger.service.GpsFeed
import com.mermaid.passenger.service.LinkService
import com.mermaid.passenger.service.fmtHz
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
    val gps by Repo.gps.collectAsState()
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
                    Kv("RISK 받음", st.riskRx.toString())
                }
            }
        }
        GpsCard(gps, now)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SmallButton("다시 연결", Modifier.weight(1f), tight = true) { Repo.send(Cmd.Reconnect) }
            SmallButton("로그 복사", Modifier.weight(1f), tight = true) { copyLog(ctx) }
            SmallButton("로그 지우기", Modifier.weight(1f), tight = true) { Repo.log.value = emptyList() }
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
        Muted("로그는 이 폰에만 저장돼요 (복사는 직접 누를 때만). 최근 300줄 · MOT·HB·GPS는 개수만, RISK는 연결마다 첫 줄만.")
        Spacer(Modifier.height(4.dp))
    }
}

/**
 * GPS 상태 (#20): 권한 · 수신 여부 · 실제 수신 주기(이 폰이 GPS를 얼마나 자주 주는지) · 마지막 속도·정확도.
 * 대시보드에서도 쓴다
 */
@Composable
fun GpsCard(gps: GpsStats, now: Long) {
    val ctx = LocalContext.current
    val locOn = remember(now / 2000) { ctx.getSystemService(LocationManager::class.java)?.isLocationEnabled != false }
    val age = if (gps.lastAt > 0) (now - gps.lastAt) / 1000f else null
    val stale = gps.running && (age == null || age > 3f)
    MMCard {
        Row2 {
            Text("GPS", style = MaterialTheme.typography.titleMedium)
            Text(if (gps.running) (if (stale) "수신 없음" else "수신 중") else "꺼짐", fontSize = 13.sp, fontWeight = FontWeight.Bold,
                color = when { !gps.running -> MaterialTheme.colorScheme.onSurfaceVariant; stale -> MM.Wait; else -> MM.Ok })
        }
        Spacer(Modifier.height(6.dp))
        Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
            val perm = GpsFeed.permLabel(ctx)
            Kv("권한", perm + if (!locOn) " · 폰 위치 꺼짐" else "", if (perm != "정확한 위치 허용" || !locOn) MM.Wait else null)
            Kv("상태", gps.state)
            Kv("수신 주기", if (!gps.running) "—" else "${fmtHz(gps.hz)} (최근 10초) · 평균 ${fmtHz(gps.avgHz(now))}")
            Kv("마지막", if (age == null) "—" else listOfNotNull(
                gps.spd?.let { String.format(Locale.US, "%.1f km/h", it * 3.6) } ?: "속도 없음",
                gps.acc?.let { String.format(Locale.US, "±%.0f m", it) },
                String.format(Locale.US, "%.1fs 전", age),
            ).joinToString(" · "), if (stale) MM.Wait else null)
            Kv("받음 / 보냄", "${gps.fixes} / ${gps.sent}" + if (gps.noSpeed > 0) " (속도 없음 ${gps.noSpeed})" else "")
        }
    }
}

/** 로그 전체를 화면에 보이는 형식 그대로 클립보드에. 첫 줄은 기기 ID 앞 8자리 · 앱 버전 · 연결 상태 */
fun copyLog(ctx: Context) {
    val lines = Repo.log.value
    if (lines.isEmpty()) { Toast.makeText(ctx, "복사할 로그가 없어요", Toast.LENGTH_SHORT).show(); return }
    val live = Repo.live.value
    val state = when (live.link) { LinkState.CONNECTED -> "연결됨"; LinkState.LOST -> "신호 없음"; LinkState.CONNECTING -> "연결 중"; else -> "대기" }
    val via = listOf(live.kind, Repo.stats.value.name).filter { it.isNotEmpty() }.distinct().joinToString(" ")
    val header = "MerMaid RF 로그 · 기기 ${Prefs.settings.value.devId.take(8)} · 앱 ${BuildConfig.VERSION_NAME} · $state" +
        if (via.isEmpty()) "" else " ($via)"
    val body = lines.joinToString("\n") { "${it.time} ${it.dir} ${it.text}" }
    ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("MerMaid RF 로그", "$header\n$body\n"))
    Toast.makeText(ctx, "로그 ${lines.size}줄 복사됨", Toast.LENGTH_SHORT).show()
}

@Composable
fun Kv(k: String, v: String, color: Color? = null) {
    Row2 {
        Text(k, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(v, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = color ?: MaterialTheme.colorScheme.onSurface)
    }
}
