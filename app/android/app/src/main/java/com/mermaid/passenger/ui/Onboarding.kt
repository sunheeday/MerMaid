package com.mermaid.passenger.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mermaid.passenger.data.Prefs

// ───────── APP-01 시작
@Composable
fun WelcomeScreen(onStart: () -> Unit) {
    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp, vertical = 16.dp)) {
        Spacer(Modifier.height(48.dp))
        Logo(72)
        Spacer(Modifier.height(18.dp))
        Text("멀미가 오기 전에,\n먼저 움직입니다", style = MaterialTheme.typography.headlineMedium.copy(fontSize = 28.sp, lineHeight = 36.sp))
        Spacer(Modifier.height(10.dp))
        Text("MerMaid는 차와 연결되어 흔들림을 미리 알고 화면에 작은 점으로 알려 드려요.",
            style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(20.dp))
        MMCard {
            Feature("1", MM.Brand, "차에 타면 자동 연결", "앱을 열지 않아도 돼요")
            Spacer(Modifier.height(14.dp))
            Feature("2", MM.T3, "쓰던 앱 그대로", "YouTube 위에도 점이 나타나요")
            Spacer(Modifier.height(14.dp))
            Feature("3", MM.T1, "나에게 맞춰 학습", "끄고 켜는 것을 기억해요")
        }
        Spacer(Modifier.weight(1f))
        PrimaryButton("시작하기", onClick = onStart)
        Text("설정은 2분이면 끝나요", Modifier.fillMaxWidth().padding(top = 10.dp), textAlign = TextAlign.Center,
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun Feature(n: String, c: Color, title: String, sub: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        TierBadge(n, c)
        Spacer(Modifier.width(12.dp))
        Column { Text(title, style = MaterialTheme.typography.titleMedium); Muted(sub) }
    }
}

// ───────── APP-02 권한 안내 (블루투스 → 알림 → 다른 앱 위에 표시 → 배터리)
private data class PermStep(val icon: String, val title: String, val sub: String, val why: String, val button: String)

private val STEPS = listOf(
    PermStep("ᛒ", "근처 기기 (블루투스)", "차와 연결", "차 안의 MerMaid 장치(MSAgent)와 연결하려면 블루투스 권한이 필요해요. 위치는 쓰지 않아요.", "허용하기"),
    PermStep("🔔", "알림", "연결 상태 · 빠른 조작", "연결 상태를 알림창에 조용히 보여 드리고, 거기서 바로 모션 큐를 켜고 끌 수 있어요.", "허용하기"),
    PermStep("▣", "다른 앱 위에 표시", "모션 큐", "YouTube·SNS를 볼 때 화면 가장자리에만 점이 나타나요. 화면을 누르는 데는 영향이 없어요.", "설정에서 허용하기"),
    PermStep("⚡", "배터리 제한 없음", "주행 중 연결 유지", "절전 기능이 앱을 멈추면 주행 중 연결이 끊길 수 있어요. '제한 없음'으로 두면 안정적이에요.", "허용하기"),
)

@Composable
fun PermissionsScreen(act: MainActivity, tick: Int, onDone: () -> Unit) {
    val ctx = LocalContext.current
    var skipped by remember { mutableStateOf(setOf<Int>()) }
    var localTick by remember { mutableStateOf(0) }
    val granted = remember(tick, localTick) { listOf(Perms.bt(ctx), Perms.notif(ctx), Perms.overlay(ctx), Perms.battery(ctx)) }
    val current = (0..3).firstOrNull { !granted[it] && it !in skipped }

    val btLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { localTick++ }
    val notifLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { localTick++ }

    if (current == null) { LaunchedEffect(Unit) { onDone() }; return }
    val step = STEPS[current]

    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp, vertical = 12.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            for (i in 0..3) Box(Modifier.weight(1f).height(4.dp).clip(RoundedCornerShape(2.dp))
                .background(if (i <= current) MM.Brand else MaterialTheme.colorScheme.surfaceVariant))
        }
        Muted("${current + 1} / 4 단계")
        Text(when (current) {
            0 -> "차와 연결할 수 있게\n해 주세요"
            1 -> "알림창에서 바로\n조절할 수 있게 해 주세요"
            2 -> "다른 앱 위에\n점을 그릴 수 있게 해 주세요"
            else -> "주행 중에 연결이\n끊기지 않게 해 주세요"
        }, style = MaterialTheme.typography.headlineMedium)
        if (current == 2) CuePreview()
        Muted(step.why)
        STEPS.forEachIndexed { i, s ->
            val state = when { granted[i] -> "허용됨"; i == current -> "지금"; i in skipped -> "건너뜀"; else -> "다음" }
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(MaterialTheme.colorScheme.surface)
                    .border(if (i == current) 2.dp else 1.dp, if (i == current) MM.Brand else MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(16.dp))
                    .padding(horizontal = 14.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(Modifier.size(34.dp).clip(RoundedCornerShape(10.dp)).background(MaterialTheme.colorScheme.background), contentAlignment = Alignment.Center) {
                    Text(s.icon, fontSize = 17.sp)
                }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) { Text(s.title, style = MaterialTheme.typography.titleMedium); Muted(s.sub) }
                Text(state, fontSize = 13.sp, fontWeight = FontWeight.Bold,
                    color = when (state) { "허용됨" -> MM.Ok; "지금" -> MM.Brand; else -> MaterialTheme.colorScheme.onSurfaceVariant })
            }
        }
        Spacer(Modifier.height(4.dp))
        PrimaryButton(step.button) {
            when (current) {
                0 -> btLauncher.launch(Perms.BT)
                1 -> if (Build.VERSION.SDK_INT >= 33) notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS) else localTick++
                2 -> act.openOverlaySettings()
                else -> act.openBatterySettings()
            }
        }
        Text("나중에 할게요", Modifier.fillMaxWidth().clickable { skipped = skipped + current }.padding(10.dp),
            textAlign = TextAlign.Center, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

// ───────── APP-03 차량 등록
@Composable
fun RegisterScreen(act: MainActivity, onboarding: Boolean, onDone: () -> Unit) {
    val settings by Prefs.settings.collectAsState()
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var done by remember { mutableStateOf(false) }

    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp, vertical = 16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        if (!done) {
            Spacer(Modifier.height(24.dp))
            Text("차에 연결하기", style = MaterialTheme.typography.headlineMedium)
            Muted("차 안의 MerMaid 장치(MSAgent)를 찾아요. 차에 시동이 걸려 있고 폰의 블루투스가 켜져 있어야 해요.")
            MMCard {
                Text("처음 한 번만 등록하면", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(4.dp))
                Muted("다음부터는 차에 타면 Android가 앱을 깨워 자동으로 연결해요. 기기 선택 창은 Android가 띄우는 화면이에요.")
            }
            error?.let { Text(it, color = MM.Bad, style = MaterialTheme.typography.bodyMedium) }
            Spacer(Modifier.weight(1f))
            PrimaryButton(if (busy) "찾는 중…" else "차 찾기", enabled = !busy) {
                busy = true; error = null
                act.registerBridge { addr, err ->
                    busy = false
                    if (addr != null) done = true else error = err ?: "등록을 취소했어요"
                }
            }
            Text(if (onboarding) "나중에 등록할게요" else "취소", Modifier.fillMaxWidth().clickable(onClick = onDone).padding(10.dp),
                textAlign = TextAlign.Center, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            Spacer(Modifier.height(48.dp))
            Box(Modifier.size(110.dp).clip(RoundedCornerShape(34.dp)).background(MM.Ok.copy(alpha = 0.12f)), contentAlignment = Alignment.Center) {
                Text("✓", fontSize = 54.sp, color = MM.Ok)
            }
            Text("${settings.bridgeName ?: "차량"}\n등록했어요", style = MaterialTheme.typography.headlineMedium)
            Text("다음부터는 차에 타면 자동으로 연결돼요. 앱을 열 필요가 없어요.", style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            MMCard {
                Row2 { Muted("자동 연결"); Switch(checked = settings.autoConnect, onCheckedChange = { v -> Prefs.edit { putBoolean(Prefs.K_AUTO, v) } }) }
                Row2 { Muted("내 기기 ID"); Text(settings.devId.take(8) + "…", fontWeight = FontWeight.SemiBold, fontSize = 13.sp) }
            }
            Spacer(Modifier.weight(1f))
            PrimaryButton(if (onboarding) "다음: 멀미 프로필" else "완료", onClick = onDone)
        }
    }
}
