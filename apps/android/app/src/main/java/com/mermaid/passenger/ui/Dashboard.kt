package com.mermaid.passenger.ui

import android.os.SystemClock
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mermaid.passenger.data.By
import com.mermaid.passenger.data.LinkState
import com.mermaid.passenger.data.Prefs
import com.mermaid.passenger.data.Repo
import com.mermaid.passenger.data.SymDrive
import com.mermaid.passenger.data.SymStore
import com.mermaid.passenger.data.TierEvent
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.ceil

/**
 * 대시보드: 지금 차 안 · 멀미 위험 확률(M10) · 내 멀미 점수 · 연결 · 속도(GPS) · 최근 로그.
 * 값은 모두 이 폰 메모리(Repo)와 기기 안 파일(SymStore)에서만 읽는다. 보내는 것 없음
 */
@Composable
fun DashboardScreen(onBack: () -> Unit, go: (Screen) -> Unit) {
    val ctx = LocalContext.current
    val live by Repo.live.collectAsState()
    val st by Repo.stats.collectAsState()
    val drive by Repo.drive.collectAsState()
    val risk by Repo.risk.collectAsState()
    val speed by Repo.speed.collectAsState()
    val gps by Repo.gps.collectAsState()
    val log by Repo.log.collectAsState()
    val syms by SymStore.drives.collectAsState()
    val s by Prefs.settings.collectAsState()

    var now by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    var wall by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { now = SystemClock.elapsedRealtime(); wall = System.currentTimeMillis(); delay(1000) } }
    val connected = live.link == LinkState.CONNECTED

    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row2(Modifier.height(56.dp)) {
            Row { Text("←", fontSize = 22.sp, modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp)); Text("대시보드", style = MaterialTheme.typography.titleLarge) }
            LinkChip(live.link, s.registered) { go(Screen.DIAG) }
        }

        // ① 지금 차 안 + 이번 주행 이력
        SectionLabel("지금 차 안")
        MMCard {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TierNow(Modifier.weight(1f), "T1", MM.T1, "송풍", live.t1, connected)
                TierNow(Modifier.weight(1f), "T2", MM.T2, "앰비언트", live.t2, connected)
                TierNow(Modifier.weight(1f), "T3", MM.T3, "모션 큐", live.t3, connected)
            }
            Spacer(Modifier.height(12.dp))
            TierHistory(drive.events)
        }

        // ② 멀미 위험 확률 (M10 RISK)
        SectionLabel("멀미 위험 (컨트롤러 예측)")
        MMCard { RiskPanel(risk, wall) }

        // ③ 내 멀미 점수
        SectionLabel("내 멀미 점수")
        MMCard { SymPanel(syms, s.logConsent, drive.startedAt, wall) }

        // ④ 연결
        SectionLabel("연결")
        MMCard {
            Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
                Kv("상태", when (live.link) { LinkState.CONNECTED -> "연결됨"; LinkState.LOST -> "신호 없음"; LinkState.CONNECTING -> "연결 중"; else -> "대기" } +
                    if (live.kind.isNotEmpty() && live.link != LinkState.IDLE) " · ${live.kind}" else "",
                    when (live.link) { LinkState.CONNECTED -> MM.Ok; LinkState.LOST -> MM.Bad; else -> null })
                Kv("이번 연결", drive.connectedSince?.let { dur(now - it) } ?: "—")
                Kv("이번 주행 시작", if (drive.startedAt > 0) hms(drive.startedAt) else "—")
                Kv("끊김", "${drive.drops}회", if (drive.drops > 0) MM.Wait else null)
                Kv("신호 세기", st.rssi?.let { "$it dBm · ${rssiWord(it)}" } ?: "—")
                val active = live.link == LinkState.CONNECTED || live.link == LinkState.LOST
                Kv("마지막 수신", if (st.lastRxAt > 0 && active) String.format(Locale.US, "%.1f초 전", (now - st.lastRxAt) / 1000f) else "—")
            }
        }

        // ⑤ 속도 (GPS)
        SectionLabel("속도 (GPS)")
        MMCard { SpeedPanel(speed, gps.spd, gps.running, now - gps.lastAt, gps.state, wall) }
        GpsCard(gps, now)

        // ⑥ 최근 로그
        SectionLabel("최근 로그")
        MMCard {
            if (log.isEmpty()) Muted("아직 로그가 없어요")
            log.takeLast(6).forEach { l ->
                Text("${l.time} ${l.dir} ${l.text}", fontFamily = FontFamily.Monospace, fontSize = 11.sp, lineHeight = 16.sp, maxLines = 2,
                    color = when (l.dir) { '→' -> Color(0xFF1660B0); '←' -> Color(0xFF1F7A52); '!' -> MM.Bad; else -> MaterialTheme.colorScheme.onSurfaceVariant })
            }
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SmallButton("전체 보기", Modifier.weight(1f)) { go(Screen.DIAG) }
                SmallButton("로그 복사", Modifier.weight(1f)) { copyLog(ctx) }
            }
        }
        Muted("대시보드 값은 이 폰에만 있어요. 위험 확률·속도는 최근 30분, 멀미 점수는 최근 10주행을 보여 줘요.")
        Spacer(Modifier.height(16.dp))
    }
}

// ───────── ① Tier

@Composable
private fun TierNow(modifier: Modifier, badge: String, color: Color, title: String, on: Boolean, known: Boolean) {
    Column(
        modifier.clip(RoundedCornerShape(14.dp)).background(if (on && known) color.copy(alpha = 0.10f) else MaterialTheme.colorScheme.background)
            .padding(vertical = 10.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        TierBadge(badge, color)
        Spacer(Modifier.height(6.dp))
        Text(title, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        Text(if (!known) "알 수 없음" else if (on) "켜짐" else "꺼짐", fontSize = 12.5.sp, fontWeight = FontWeight.Bold,
            color = if (on && known) color else MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun TierHistory(events: List<TierEvent>) {
    var all by remember { mutableStateOf(false) }
    Text("이번 주행 이력", style = MaterialTheme.typography.titleMedium)
    Spacer(Modifier.height(4.dp))
    if (events.isEmpty()) { Muted("아직 켜지거나 꺼진 적이 없어요"); return }
    val shown = events.asReversed().let { if (all) it else it.take(6) }
    shown.forEach { e ->
        val (name, color) = when (e.tier) { 1 -> "송풍" to MM.T1; 2 -> "앰비언트 빛" to MM.T2; else -> "내 모션 큐" to MM.T3 }
        Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(hms(e.at), fontSize = 12.5.sp, fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(10.dp))
            Box(Modifier.size(8.dp).clip(CircleShape).background(if (e.on) color else MaterialTheme.colorScheme.outline))
            Spacer(Modifier.width(6.dp))
            Text("$name ${if (e.on) "켜짐" else "꺼짐"}", Modifier.weight(1f), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            Muted(when (e.by) { By.CTRL -> "컨트롤러"; By.ME -> "내가 직접"; By.SAFE -> "연결 끊김" })
        }
    }
    if (events.size > 6) Text(if (all) "접기" else "모두 보기 (${events.size})", Modifier.clickable { all = !all }.padding(vertical = 6.dp),
        color = MM.Brand, fontWeight = FontWeight.Bold, fontSize = 13.sp)
}

// ───────── ② 위험 확률

private val RISK_LINES = listOf(Triple(3, MM.T1, "3 이상"), Triple(4, MM.Wait, "4 이상"), Triple(5, MM.Bad, "5 이상"))

@Composable
private fun RiskPanel(risk: List<com.mermaid.passenger.data.RiskPoint>, wall: Long) {
    val from = wall - Repo.CHART_WINDOW_MS
    val recent = risk.filter { it.at >= from }
    val last = risk.lastOrNull()
    if (last == null) {
        Muted("컨트롤러가 아직 위험 확률을 보내지 않았어요. 연결되면 약 10초마다 와요.")
        return
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        RISK_LINES.forEach { (k, c, label) ->
            Column(Modifier.weight(1f)) {
                Text(pct(last.p[k - 2]), fontSize = 22.sp, fontWeight = FontWeight.ExtraBold, color = c)
                Muted("멀미 $label")
            }
        }
    }
    Muted("${hms(last.at)} 기준 · 멀미 2 이상 ${pct(last.p[0])} · 6 이상 ${pct(last.p[4])}")
    Spacer(Modifier.height(10.dp))
    TimeChart(
        series = RISK_LINES.map { (k, c, _) -> c to recent.map { it.at to it.p[k - 2].toFloat() } },
        from = from, to = wall, yMax = 1f,
        yTicks = listOf(0f to "0", 0.5f to "50%", 1f to "100%"),
        xLabels = listOf("30분 전", "15분 전", "지금"),
        gapMs = 35_000L,
    )
    Legend(RISK_LINES.map { it.second to "멀미 ${it.third}" })
}

// ───────── ③ 내 멀미 점수

@Composable
private fun SymPanel(drives: List<SymDrive>, consent: Boolean, currentStart: Long, wall: Long) {
    var pick by remember { mutableStateOf<Long?>(null) }
    var confirm by remember { mutableStateOf(false) }
    val sel = drives.firstOrNull { it.start == pick } ?: drives.lastOrNull()
    if (!consent) { Muted("주행 기록 수집 동의가 꺼져 있어 새 점수는 저장하지 않아요."); Spacer(Modifier.height(6.dp)) }
    if (sel == null) {
        Muted("아직 기록이 없어요. 홈의 \"지금 멀미 정도\"에서 숫자를 누르면 여기에 쌓여요.")
        return
    }
    val isNow = sel.start == currentStart
    Row2 {
        Text((if (isNow) "이번 주행 · " else "") + dayHm(sel.start), style = MaterialTheme.typography.titleMedium)
        Muted("${sel.points.size}회 · 최고 ${sel.max ?: "-"}")
    }
    Spacer(Modifier.height(8.dp))
    val first = sel.points.first().at
    val end = maxOf(sel.points.last().at, if (isNow) wall else 0L, first + 10 * 60_000L)
    TimeChart(
        series = listOf(MM.Brand to sel.points.map { it.at to it.misc.toFloat() }),
        from = minOf(sel.start.takeIf { it > 0 } ?: first, first), to = end, yMax = 10f,
        yTicks = listOf(0f to "0", 5f to "5", 10f to "10"),
        xLabels = listOf(hm(minOf(sel.start.takeIf { it > 0 } ?: first, first)), "", hm(end)),
        gapMs = Long.MAX_VALUE, dots = true,
    )
    if (drives.size > 1) {
        Spacer(Modifier.height(10.dp))
        Text("최근 주행", style = MaterialTheme.typography.titleMedium)
        drives.asReversed().take(5).forEach { d ->
            val on = d.start == sel.start
            Row2(Modifier.clip(RoundedCornerShape(10.dp)).background(if (on) MM.Brand.copy(alpha = 0.08f) else Color.Transparent)
                .clickable { pick = d.start }.padding(horizontal = 8.dp, vertical = 6.dp)) {
                Text(dayHm(d.start), fontSize = 13.sp, fontWeight = if (on) FontWeight.Bold else FontWeight.Normal)
                Muted("${d.points.size}회 · 최고 ${d.max ?: "-"} · 평균 ${d.avg?.let { String.format(Locale.US, "%.1f", it) } ?: "-"}")
            }
        }
    }
    Text("기록 지우기", Modifier.clickable { confirm = true }.padding(vertical = 8.dp), color = MM.Brand, fontWeight = FontWeight.Bold, fontSize = 13.sp)

    if (confirm) AlertDialog(
        onDismissRequest = { confirm = false },
        title = { Text("멀미 점수 기록을 지울까요?") },
        text = { Text("이 폰에 저장된 최근 주행의 점수 기록을 지워요. 이미 차량 컨트롤러에 보낸 기록은 그대로예요.") },
        confirmButton = { TextButton({ SymStore.clear(); pick = null; confirm = false }) { Text("지우기", color = MM.Brand) } },
        dismissButton = { TextButton({ confirm = false }) { Text("취소") } },
    )
}

// ───────── ⑤ 속도

@Composable
private fun SpeedPanel(speed: List<com.mermaid.passenger.data.SpeedPoint>, spd: Double?, running: Boolean, ageMs: Long, state: String, wall: Long) {
    val from = wall - Repo.CHART_WINDOW_MS
    val recent = speed.filter { it.at >= from }
    val fresh = running && spd != null && ageMs < 3000
    Row(verticalAlignment = Alignment.Bottom) {
        Text(if (fresh) String.format(Locale.US, "%.0f", spd!! * 3.6) else "—", fontSize = 30.sp, fontWeight = FontWeight.ExtraBold)
        Spacer(Modifier.width(6.dp))
        Muted("km/h", Modifier.padding(bottom = 5.dp))
    }
    if (!fresh) Muted(if (running) "위치를 기다리는 중이에요" else state)
    if (recent.isNotEmpty()) {
        Spacer(Modifier.height(8.dp))
        val top = recent.maxOf { it.mps * 3.6 }.toFloat()
        val yMax = maxOf(60f, ceil(top / 20f) * 20f)
        TimeChart(
            series = listOf(MaterialTheme.colorScheme.onSurface.copy(alpha = 0.75f) to recent.map { it.at to (it.mps * 3.6).toFloat() }),
            from = from, to = wall, yMax = yMax,
            yTicks = listOf(0f to "0", yMax / 2 to String.format(Locale.US, "%.0f", yMax / 2), yMax to String.format(Locale.US, "%.0f", yMax)),
            xLabels = listOf("30분 전", "15분 전", "지금"),
            gapMs = 5_000L, height = 110.dp,
        )
    }
}

// ───────── 그래프 (Compose Canvas, 라이브러리 없음)

/**
 * 시간축 꺾은선. series = (색, (시각 ms, 값) 목록). gapMs보다 벌어진 두 점은 잇지 않는다 (끊김 구간).
 * 색·글자는 테마 것만 쓴다
 */
@Composable
fun TimeChart(
    series: List<Pair<Color, List<Pair<Long, Float>>>>,
    from: Long, to: Long, yMax: Float,
    yTicks: List<Pair<Float, String>>,
    xLabels: List<String>,
    gapMs: Long,
    dots: Boolean = false,
    height: Dp = 140.dp,
) {
    val measurer = rememberTextMeasurer()
    val grid = MaterialTheme.colorScheme.surfaceVariant
    val labelStyle = TextStyle(fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Canvas(Modifier.fillMaxWidth().height(height)) {
        val left = 34.dp.toPx(); val bottom = 16.dp.toPx(); val top = 6.dp.toPx()
        val w = size.width - left; val hgt = size.height - bottom - top
        val span = (to - from).coerceAtLeast(1L).toFloat()
        fun x(t: Long) = left + w * ((t - from) / span).coerceIn(0f, 1f)
        fun y(v: Float) = top + hgt * (1f - (v / yMax).coerceIn(0f, 1f))

        yTicks.forEach { (v, label) ->
            drawLine(grid, Offset(left, y(v)), Offset(size.width, y(v)), strokeWidth = 1.dp.toPx())
            val m = measurer.measure(label, labelStyle)
            drawText(m, topLeft = Offset(left - m.size.width - 6.dp.toPx(), y(v) - m.size.height / 2f))
        }
        xLabels.forEachIndexed { i, label ->
            if (label.isEmpty()) return@forEachIndexed
            val m = measurer.measure(label, labelStyle)
            val cx = left + w * i / (xLabels.size - 1).coerceAtLeast(1)
            val tx = (cx - m.size.width / 2f).coerceIn(left, size.width - m.size.width)
            drawText(m, topLeft = Offset(tx, size.height - m.size.height))
        }
        series.forEach { (color, pts) ->
            if (pts.isEmpty()) return@forEach
            val path = Path()
            var prev: Long? = null
            pts.forEach { (t, v) ->
                if (prev == null || t - prev!! > gapMs) path.moveTo(x(t), y(v)) else path.lineTo(x(t), y(v))
                prev = t
            }
            drawPath(path, color, style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
            if (dots || pts.size == 1) pts.forEach { (t, v) -> drawCircle(color, 3.dp.toPx(), Offset(x(t), y(v))) }
        }
    }
}

@Composable
private fun Legend(items: List<Pair<Color, String>>) {
    Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.CenterVertically) {
        items.forEach { (c, t) ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(width = 14.dp, height = 3.dp).clip(RoundedCornerShape(2.dp)).background(c))
                Spacer(Modifier.width(5.dp))
                Text(t, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

// ───────── 글자

private fun pct(p: Double) = "${Math.round(p * 100)}%"
private fun hms(at: Long) = SimpleDateFormat("HH:mm:ss", Locale.KOREA).format(Date(at))
private fun hm(at: Long) = SimpleDateFormat("HH:mm", Locale.KOREA).format(Date(at))
private fun dayHm(at: Long) = SimpleDateFormat("M/d HH:mm", Locale.KOREA).format(Date(at))
private fun rssiWord(r: Int) = when { r >= -60 -> "좋음"; r >= -75 -> "보통"; else -> "약함" }
private fun dur(ms: Long): String {
    val sec = (ms / 1000).coerceAtLeast(0)
    val h = sec / 3600; val m = (sec % 3600) / 60; val s = sec % 60
    return if (h > 0) String.format(Locale.US, "%d:%02d:%02d", h, m, s) else String.format(Locale.US, "%d분 %02d초", m, s)
}
