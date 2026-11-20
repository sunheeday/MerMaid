package com.mermaid.passenger.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mermaid.passenger.BuildConfig
import com.mermaid.passenger.data.Prefs
import com.mermaid.passenger.service.Presence
import java.util.Locale

/** APP-06 설정: 큐 표시 · 연결 · 프로필 · 권한 (APP-F-41, 42) */
@Composable
fun SettingsScreen(act: MainActivity, tick: Int, onBack: () -> Unit, go: (Screen) -> Unit) {
    val ctx = LocalContext.current
    val s by Prefs.settings.collectAsState()
    var confirm by remember { mutableStateOf(false) }
    val missing = remember(tick) { Perms.missing(ctx) }

    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row2(Modifier.height(56.dp)) {
            Row { Text("←", fontSize = 22.sp, modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp)); Text("설정", style = MaterialTheme.typography.titleLarge) }
        }

        SectionLabel("모션 큐")
        MMCard {
            CuePreview(gainDp = s.cueGainDp, amber = s.cueAmber, alpha = s.cueAlpha)
            Spacer(Modifier.height(14.dp))
            Row2 { Text("움직임 크기", style = MaterialTheme.typography.titleMedium); Muted("최대 ${s.cueGainDp} dp") }
            Spacer(Modifier.height(6.dp))
            Segmented(listOf("약", "중", "강"), listOf(16, 24, 32).indexOf(s.cueGainDp).coerceAtLeast(0)) { i ->
                Prefs.edit { putInt(Prefs.K_GAIN, listOf(16, 24, 32)[i]) }
            }
            Spacer(Modifier.height(12.dp))
            Row2 { Text("진하기", style = MaterialTheme.typography.titleMedium); Muted(String.format(Locale.US, "%.2f", s.cueAlpha) + if (s.cueAlpha >= 0.79f) " (최대)" else "") }
            // 0.8 넘으면 Android가 아래 앱으로 가는 터치를 막으므로 상한
            Slider(value = s.cueAlpha, onValueChange = { v -> Prefs.edit { putFloat(Prefs.K_ALPHA, v) } }, valueRange = 0.3f..0.8f)
            Row2 {
                Text("점 색", style = MaterialTheme.typography.titleMedium)
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    ColorDot(Color.White, !s.cueAmber) { Prefs.edit { putBoolean(Prefs.K_AMBER, false) } }
                    ColorDot(Color(0xFFFFB547), s.cueAmber) { Prefs.edit { putBoolean(Prefs.K_AMBER, true) } }
                }
            }
            Spacer(Modifier.height(8.dp))
            Row2 { Text("시작 안내 표시", style = MaterialTheme.typography.titleMedium); Switch(s.cuePill, { v -> Prefs.edit { putBoolean(Prefs.K_PILL, v) } }) }
        }

        SectionLabel("연결")
        MMCard {
            Row2 {
                Column(Modifier.weight(1f)) { Text("차에 타면 자동 연결", style = MaterialTheme.typography.titleMedium); Muted("끄면 앱을 열 때만 연결해요") }
                Switch(s.autoConnect, { v ->
                    Prefs.edit { putBoolean(Prefs.K_AUTO, v) }
                    s.bridgeAddress?.let { if (v) Presence.observe(ctx, it) else Presence.stop(ctx, it) }
                })
            }
            Spacer(Modifier.height(10.dp))
            if (s.registered) {
                Row2 {
                    Column(Modifier.weight(1f)) { Text(s.bridgeName ?: "차량", style = MaterialTheme.typography.titleMedium); Muted("등록된 차량") }
                    Text("해제", Modifier.clip(androidx.compose.foundation.shape.RoundedCornerShape(8.dp)).background(MM.Brand.copy(alpha = 0.1f))
                        .clickable { confirm = true }.padding(horizontal = 10.dp, vertical = 4.dp), color = MM.Brand, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                }
            } else {
                SmallButton("차 등록하기") { go(Screen.REGISTER_AGAIN) }
            }
        }

        SectionLabel("주행 기록")
        MMCard {
            Row2 {
                Column(Modifier.weight(1f)) {
                    Text("주행 기록 수집 동의", style = MaterialTheme.typography.titleMedium)
                    Muted("멀미 점수를 차량 컨트롤러에 저장해요. 끄면 보내지 않고 알림도 없어요")
                }
                Switch(s.logConsent, { v -> Prefs.setLogConsent(v) })
            }
        }

        SectionLabel("기타")
        MMCard {
            Row2(Modifier.clickable { go(Screen.PROFILE) }.padding(vertical = 6.dp)) { Text("멀미 프로필 다시 하기"); Muted("›") }
            Row2(Modifier.clickable { act.openAppSettings() }.padding(vertical = 6.dp)) {
                Text("권한 상태")
                Text(if (missing == 0) "모두 허용" else "${missing}개 필요", color = if (missing == 0) MM.Ok else MM.Brand, fontWeight = FontWeight.Bold, fontSize = 13.sp)
            }
            Row2(Modifier.padding(vertical = 6.dp)) { Text("버전 · 기기 ID"); Muted("${BuildConfig.VERSION_NAME} · ${s.devId.take(4)}…") }
        }
        Spacer(Modifier.height(16.dp))
    }

    if (confirm) AlertDialog(
        onDismissRequest = { confirm = false },
        title = { Text("차량 등록을 해제할까요?") },
        text = { Text("${s.bridgeName ?: "차량"} 연결을 끊고 자동 연결을 멈춰요. 다시 쓰려면 다시 등록해야 해요.") },
        confirmButton = { TextButton({ act.unregisterBridge(); confirm = false }) { Text("해제", color = MM.Brand) } },
        dismissButton = { TextButton({ confirm = false }) { Text("취소") } },
    )
}

@Composable
private fun ColorDot(c: Color, selected: Boolean, onClick: () -> Unit) {
    Box(Modifier.size(28.dp).clip(CircleShape).background(c)
        .border(if (selected) 2.dp else 1.dp, if (selected) MM.Brand else MaterialTheme.colorScheme.outline, CircleShape)
        .clickable(onClick = onClick))
}
