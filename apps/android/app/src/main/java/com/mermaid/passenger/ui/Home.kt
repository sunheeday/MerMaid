package com.mermaid.passenger.ui

import android.Manifest
import android.os.SystemClock
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
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
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
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

/** APP-05 홈: 연결 상태 · 개입 상태 · 수동 조작 */
@Composable
fun HomeScreen(act: MainActivity, tick: Int, go: (Screen) -> Unit) {
    val ctx = LocalContext.current
    val live by Repo.live.collectAsState()
    val s by Prefs.settings.collectAsState()
    val connected = live.link == LinkState.CONNECTED
    val btLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { act.connectIfReady() }
    val notifLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    val perms = remember(tick) { listOf(Perms.bt(ctx), Perms.notif(ctx), Perms.overlay(ctx), Perms.battery(ctx)) }

    var now by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    LaunchedEffect(Unit) { while (true) { delay(500); now = SystemClock.elapsedRealtime() } }

    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp).verticalScroll(rememberScrollState()),
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

@Composable
private fun Banner(text: String, action: String, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(MM.Wait.copy(alpha = 0.12f)).padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Text(text, Modifier.weight(1f), fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.width(8.dp))
        Text(action, Modifier.clickable(onClick = onClick).padding(6.dp), color = MM.Brand, fontWeight = FontWeight.Bold, fontSize = 13.sp)
    }
}
