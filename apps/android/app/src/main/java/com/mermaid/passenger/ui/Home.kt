package com.mermaid.passenger.ui

import android.Manifest
import android.os.SystemClock
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInParent
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mermaid.passenger.data.Cmd
import com.mermaid.passenger.data.LinkState
import com.mermaid.passenger.data.Prefs
import com.mermaid.passenger.data.Repo
import com.mermaid.passenger.protocol.Proto
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** APP-05 홈: 연결 상태 · 개입 상태 · 수동 조작 */
@Composable
fun HomeScreen(act: MainActivity, tick: Int, symFocus: Int = 0, go: (Screen) -> Unit) {
    val ctx = LocalContext.current
    val live by Repo.live.collectAsState()
    val s by Prefs.settings.collectAsState()
    val connected = live.link == LinkState.CONNECTED
    val btLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { act.connectIfReady() }
    val notifLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    val perms = remember(tick) { listOf(Perms.bt(ctx), Perms.notif(ctx), Perms.overlay(ctx), Perms.battery(ctx)) }

    var now by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    LaunchedEffect(Unit) { while (true) { delay(500); now = SystemClock.elapsedRealtime() } }

    // 멀미 점수 알림을 누르고 들어오면 "지금 멀미 정도" 카드가 보이게 내려감
    val scroll = rememberScrollState()
    var symCardY by remember { mutableIntStateOf(0) }
    LaunchedEffect(symFocus) { if (symFocus > 0) { delay(150); scroll.animateScrollTo(symCardY) } }

    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp).verticalScroll(scroll),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row2(Modifier.height(56.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Logo(); Spacer(Modifier.width(8.dp)); Text("MerMaid", fontWeight = FontWeight.ExtraBold, fontSize = 19.sp)
            }
            LinkChip(live.link, s.registered) { go(Screen.DIAG) }
        }

        // 빠진 권한 (APP-F-40)
        if (!perms[0]) Banner("블루투스 권한이 필요해요", "허용") { btLauncher.launch(Perms.BT) }
        if (!perms[2]) Banner("“다른 앱 위에 표시”가 꺼져 있어 점을 그릴 수 없어요", "허용") { act.openOverlaySettings() }
        if (!perms[1]) Banner("알림이 꺼져 있어 알림창에서 조절할 수 없어요", "허용") {
            if (android.os.Build.VERSION.SDK_INT >= 33) notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS) else act.openAppSettings()
        }
        if (!perms[3]) Banner("절전 기능 때문에 연결이 끊길 수 있어요", "제한 없음") { act.openBatterySettings() }

        // 상태 카드
        when {
            !s.registered -> MMCard {
                Text("차를 등록해 주세요", style = MaterialTheme.typography.titleLarge)
                Spacer(Modifier.height(4.dp))
                Muted("한 번만 등록하면 다음부터 차에 타면 자동으로 연결돼요.")
                Spacer(Modifier.height(10.dp))
                PrimaryButton("차 등록하기") { go(Screen.REGISTER_AGAIN) }
            }
            connected -> MMCard(color = MM.Ink) {
                Text(s.bridgeName ?: "차량", color = Color(0xFFCBBFBA), fontSize = 12.5.sp)
                Spacer(Modifier.height(4.dp))
                Text("차와 연결됐어요.\n이제 다른 앱을 쓰셔도 돼요", color = Color.White, style = MaterialTheme.typography.titleLarge)
                Spacer(Modifier.height(6.dp))
                Text("필요할 때 화면 가장자리에 점이 나타나요. 앱을 닫아도 계속 동작해요.", color = Color(0xFFCBBFBA), fontSize = 13.sp)
                Spacer(Modifier.height(10.dp))
                Text("홈 화면으로", Modifier.clip(RoundedCornerShape(12.dp)).background(Color.White).clickable { act.moveTaskToBack(true) }
                    .padding(horizontal = 14.dp, vertical = 9.dp), color = MM.Ink, fontWeight = FontWeight.Bold, fontSize = 14.sp)
            }
            live.link == LinkState.LOST -> MMCard(color = MM.Bad.copy(alpha = 0.08f)) {
                Text("차와 연결이 끊겼어요", color = MM.Bad, fontWeight = FontWeight.Bold, fontSize = 16.sp)
                Spacer(Modifier.height(4.dp))
                Text("안전을 위해 화면의 점을 껐어요. 신호가 돌아오면 바로 이어서 챙겨 드려요.", color = Color(0xFF7A3B3B), fontSize = 13.sp)
            }
            else -> MMCard {
                val retry = live.retryAt?.let { ((it - now) / 1000).coerceAtLeast(0) }
                Text(if (live.link == LinkState.CONNECTING) "차와 연결하는 중" else "차를 찾는 중", style = MaterialTheme.typography.titleLarge)
                Spacer(Modifier.height(4.dp))
                Muted(when {
                    retry != null -> "${live.reason.ifEmpty { "연결 실패" }} · ${retry}초 뒤 다시 시도"
                    else -> "차에 타면 자동으로 연결돼요. 앱을 닫아도 괜찮아요."
                })
                Spacer(Modifier.height(10.dp))
                SmallButton("지금 연결") { if (Repo.serviceRunning) Repo.send(Cmd.Reconnect) else act.connectIfReady() }
            }
        }

        // 지금 차 안 (INT 상태 + 수동 조작 OVR)
        SectionLabel("지금 차 안")
        MMCard(Modifier.alpha(if (connected) 1f else 0.55f)) {
            TierRow("T1", MM.T1, "송풍", "차 안 공용", Proto.DEV_AIRFLOW, live.t1, live.pending, connected)
            TierRow("T2", MM.T2, "앰비언트 빛", "차 안 공용", Proto.DEV_DISPLAY, live.t2, live.pending, connected)
            TierRow("T3", MM.T3, "내 화면 모션 큐", "이 폰에만", s.devId, live.t3, live.pending, connected)
        }
        Muted("켜진 지 30초 안에 끄면 다음부터 덜 켜져요. 꺼진 큐를 직접 켜면 민감도를 올려요.")

        // 지금 멀미 정도 (M08 SYM, 기록 전용)
        Column(Modifier.onGloballyPositioned { symCardY = it.positionInParent().y.toInt() }, verticalArrangement = Arrangement.spacedBy(12.dp)) {
            SectionLabel("지금 멀미 정도")
            SymptomCard(connected, s.logConsent, s.consentAsked, live.symLast, live.symAt)
        }

        Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(MaterialTheme.colorScheme.surface)
            .border(1.dp, MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(20.dp)).clickable { go(Screen.PROFILE) }
            .padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("멀미 프로필", style = MaterialTheme.typography.titleMedium)
                Muted(if (s.hasProfile) "MSSQ ${fmt1(s.mssq)} · 민감도 ${fmt2(s.sens)}" else "아직 설문 전 (기본값 사용)")
            }
            Muted("›")
        }
        Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
            Text("⚙ 설정", Modifier.clickable { go(Screen.SETTINGS) }.padding(10.dp), fontSize = 14.sp)
            Text("⌁ RF 진단", Modifier.clickable { go(Screen.DIAG) }.padding(10.dp), fontSize = 14.sp)
        }
    }
}

@Composable
private fun TierRow(
    badge: String, color: Color, title: String, sub: String,
    dev: String, actual: Boolean, pending: Map<String, Boolean>, enabled: Boolean,
) {
    val want = pending[dev]
    val shown = want ?: actual
    val state = when { !enabled -> "알 수 없음"; want != null -> "요청 중"; shown -> "켜짐"; else -> "꺼짐" }
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        TierBadge(badge, color)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Muted("$sub · $state")
        }
        Switch(
            checked = shown && enabled,
            enabled = enabled,
            onCheckedChange = { Repo.send(Cmd.Override(dev, !shown)) },
            colors = SwitchDefaults.colors(checkedTrackColor = color),
            modifier = Modifier.alpha(if (want != null) 0.6f else 1f),
        )
    }
}

/**
 * 멀미 점수 0~10 입력. 누르면 바로 보내고 "N 기록됨 · HH:MM"을 보여 준다.
 * 연결이 없거나 주행 기록 수집 동의가 꺼져 있으면 누를 수 없고 이유를 한 줄로 알린다.
 * 동의 안내는 처음 누를 때 한 번만 저절로 뜨고, 그 뒤에는 "동의하기"나 설정에서 켠다.
 */
@Composable
private fun SymptomCard(connected: Boolean, consent: Boolean, consentAsked: Boolean, last: Int?, lastAt: Long?) {
    val enabled = connected && consent
    var ask by remember { mutableStateOf(false) }
    var wanted by remember { mutableStateOf<Int?>(null) }   // 동의 안내를 띄운 그 누름의 점수

    MMCard {
        BoxWithConstraints(Modifier.fillMaxWidth().alpha(if (enabled) 1f else 0.55f)) {
            // 넓으면 한 줄(11칸), 폰 세로 폭에서는 두 줄(0~5, 6~10 + 빈 칸)
            val rows = if (maxWidth >= 480.dp) listOf((0..10).toList()) else listOf((0..5).toList(), (6..10).toList() + listOf(-1))
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                rows.forEach { row ->
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        row.forEach { n ->
                            if (n < 0) Spacer(Modifier.weight(1f)) else {
                                val sel = enabled && n == last
                                Box(
                                    Modifier.weight(1f).height(40.dp).clip(RoundedCornerShape(12.dp))
                                        .background(if (sel) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surface)
                                        .border(1.dp, if (sel) Color.Transparent else MaterialTheme.colorScheme.outline, RoundedCornerShape(12.dp))
                                        .clickable(enabled = enabled || (!consent && !consentAsked)) {
                                            if (enabled) Repo.send(Cmd.Symptom(n)) else { wanted = n; ask = true }
                                        },
                                    contentAlignment = Alignment.Center,
                                ) {
                                    Text(n.toString(), fontSize = 15.sp, fontWeight = FontWeight.Bold,
                                        color = if (sel) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface)
                                }
                            }
                        }
                    }
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        Muted("0 증상 없음 · 1 약간 불편 · 2~5 어지러움·더움·두통(약~강) · 6~9 메스꺼움(약~강) · 10 구토")
        Spacer(Modifier.height(6.dp))
        when {
            !consent -> Row2 {
                Muted("주행 기록 수집 동의가 꺼져 있어요", Modifier.weight(1f))
                Text("동의하기", Modifier.clickable { wanted = null; ask = true }.padding(6.dp), color = MM.Brand, fontWeight = FontWeight.Bold, fontSize = 13.sp)
            }
            !connected -> Muted("차와 연결되면 기록할 수 있어요")
            last != null && lastAt != null -> Text("$last 기록됨 · ${hhmm(lastAt)}", fontSize = 13.5.sp, fontWeight = FontWeight.Bold, color = MM.Ok)
            else -> Muted("숫자를 누르면 바로 기록돼요. 주행 중에는 2분마다 알림으로 물어봐요.")
        }
    }

    if (ask) AlertDialog(
        onDismissRequest = { ask = false; Prefs.edit { putBoolean(Prefs.K_CONSENT_ASKED, true) } },
        title = { Text("주행 기록 수집에 동의할까요?") },
        text = {
            Text("고른 멀미 점수(0~10)를 차량 컨트롤러에 보내 주행 기록과 함께 저장해요. 멀미 예측을 더 잘하게 만드는 데만 쓰고, " +
                "지금 송풍·모션 큐가 켜지고 꺼지는 데에는 영향을 주지 않아요.\n\n동의하면 주행 중 2분마다 알림으로 멀미 정도를 물어봐요. 설정에서 언제든 끌 수 있어요.")
        },
        confirmButton = {
            TextButton({
                Prefs.setLogConsent(true)
                wanted?.let { if (connected) Repo.send(Cmd.Symptom(it)) }
                ask = false
            }) { Text("동의", color = MM.Brand) }
        },
        dismissButton = { TextButton({ ask = false; Prefs.edit { putBoolean(Prefs.K_CONSENT_ASKED, true) } }) { Text("나중에") } },
    )
}

private fun hhmm(at: Long): String = SimpleDateFormat("HH:mm", Locale.KOREA).format(Date(at))

@Composable
private fun Banner(text: String, action: String, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(MM.Wait.copy(alpha = 0.12f)).padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Text(text, Modifier.weight(1f), fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.width(8.dp))
        Text(action, Modifier.clickable(onClick = onClick).padding(6.dp), color = MM.Brand, fontWeight = FontWeight.Bold, fontSize = 13.sp)
    }
}
