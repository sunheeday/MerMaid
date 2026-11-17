package com.mermaid.passenger.ui

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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mermaid.passenger.data.Cmd
import com.mermaid.passenger.data.Prefs
import com.mermaid.passenger.data.Repo
import com.mermaid.passenger.protocol.Mssq
import java.util.Locale

/** APP-04 멀미 프로필: MSSQ-Short 설문(a) → 결과(b) → 저장 시 PROF 전송 (APP-F-30~33) */
@Composable
fun ProfileScreen(onboarding: Boolean, onBack: (() -> Unit)?, onDone: () -> Unit) {
    val settings by Prefs.settings.collectAsState()
    val live by Repo.live.collectAsState()
    val (a0, b0) = remember { Mssq.decode(settings.answers) }
    val a = remember { mutableStateListOf(*a0.toTypedArray()) }
    val b = remember { mutableStateListOf(*b0.toTypedArray()) }
    var section by remember { mutableStateOf(0) }
    var showResult by remember { mutableStateOf(false) }
    val score = Mssq.raw(a, b)
    val sens = Mssq.sens(score)
    val answered = (a + b).count { it != Mssq.EMPTY }

    fun save() {
        Prefs.setProfile(score, sens, Mssq.encode(a, b))
        Repo.send(Cmd.SendProfile)
        onDone()
    }

    fun skip() { // 기본 프로필 (APP-F-32)
        Prefs.setProfile(Mssq.DEFAULT_MSSQ, Mssq.DEFAULT_SENS, "")
        Repo.send(Cmd.SendProfile)
        onDone()
    }

    if (!showResult) {
        Column(Modifier.fillMaxSize().padding(horizontal = 20.dp, vertical = 12.dp)) {
            Row2 {
                Text(if (onBack != null) "←" else "", fontSize = 22.sp, modifier = Modifier.clickable { onBack?.invoke() }.padding(4.dp))
                if (onboarding) Muted("건너뛰기", Modifier.clickable { skip() }.padding(4.dp))
            }
            Text("탈것마다 얼마나 자주\n멀미를 느꼈나요?", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(vertical = 8.dp))
            Segmented(listOf("어릴 때 (12세 전)", "최근 10년"), section) { section = it }
            Spacer(Modifier.height(8.dp))
            Row(Modifier.fillMaxWidth().padding(start = 80.dp)) {
                listOf("없음", "전혀", "가끔", "때때로", "자주").forEach {
                    Text(it, Modifier.weight(1f), fontSize = 10.5.sp, textAlign = TextAlign.Center, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                val list = if (section == 0) a else b
                Mssq.ITEMS.forEachIndexed { i, name ->
                    Row(Modifier.fillMaxWidth().height(48.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(name, Modifier.width(80.dp), fontSize = 13.sp)
                        listOf(Mssq.NA, 0, 1, 2, 3).forEach { v ->
                            val sel = list[i] == v
                            Box(
                                Modifier.weight(1f).padding(horizontal = 2.dp).height(34.dp).clip(RoundedCornerShape(8.dp))
                                    .background(if (sel) (if (v == Mssq.NA) Color(0xFF4A4346) else MM.Brand) else MaterialTheme.colorScheme.surface)
                                    .border(1.dp, if (sel) Color.Transparent else MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp))
                                    .clickable { list[i] = v },
                                contentAlignment = Alignment.Center,
                            ) {
                                Text(if (v == Mssq.NA) "—" else v.toString(), fontSize = 12.sp,
                                    fontWeight = if (sel) FontWeight.Bold else FontWeight.Normal,
                                    color = if (sel) Color.White else MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
            }
            MMCard {
                Row2 {
                    Column {
                        Muted("응답 $answered / 18")
                        Text("지금 점수 ${fmt1(score)}", style = MaterialTheme.typography.titleMedium)
                    }
                    SmallButton(if (section == 0) "다음" else "결과 보기") { if (section == 0) section = 1 else showResult = true }
                }
            }
        }
    } else {
        Column(Modifier.fillMaxSize().padding(horizontal = 20.dp, vertical = 12.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text("내 멀미 프로필", style = MaterialTheme.typography.headlineMedium, modifier = Modifier.padding(top = 16.dp))
            MMCard {
                Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                    ScoreRing(score)
                    Spacer(Modifier.height(6.dp))
                    Text("민감도 ${fmt2(sens)}", style = MaterialTheme.typography.titleLarge)
                    Text(Mssq.label(sens), Modifier.padding(top = 4.dp).clip(RoundedCornerShape(8.dp)).background(MM.Brand.copy(alpha = 0.1f))
                        .padding(horizontal = 9.dp, vertical = 3.dp), color = MM.Brand, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                }
            }
            Muted("이 값은 차량 컨트롤러로 보내져 언제 개입할지 판단하는 데 쓰여요. 주행 중 끄고 켜는 기록에 따라 조금씩 바뀌어요.")
            MMCard {
                Row2 { Muted("어릴 때 (A)"); Text(fmt1(Mssq.section(a)), fontWeight = FontWeight.Bold) }
                Row2 { Muted("최근 10년 (B)"); Text(fmt1(Mssq.section(b)), fontWeight = FontWeight.Bold) }
                live.ctrlSens?.let { Row2 { Muted("컨트롤러 저장값"); Text("민감도 ${fmt2(it)}", fontWeight = FontWeight.Bold) } }
            }
            PrimaryButton(if (onboarding) "저장하고 시작하기" else "저장", onClick = ::save)
            GhostButton("다시 하기") { showResult = false; section = 0 }
        }
    }
}

@Composable
fun Segmented(options: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(MaterialTheme.colorScheme.surfaceVariant).padding(4.dp)) {
        options.forEachIndexed { i, label ->
            val sel = i == selected
            Text(label,
                Modifier.weight(1f).clip(RoundedCornerShape(9.dp))
                    .background(if (sel) MaterialTheme.colorScheme.surface else Color.Transparent)
                    .clickable { onSelect(i) }.padding(vertical = 8.dp),
                textAlign = TextAlign.Center, fontSize = 13.5.sp,
                fontWeight = if (sel) FontWeight.Bold else FontWeight.Normal,
                color = if (sel) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ScoreRing(score: Double) {
    val track = MaterialTheme.colorScheme.surfaceVariant
    Box(Modifier.size(150.dp), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(150.dp)) {
            val w = 14.dp.toPx()
            val tl = Offset(w / 2, w / 2)
            val sz = Size(size.width - w, size.height - w)
            drawArc(track, 0f, 360f, false, tl, sz, style = Stroke(w))
            drawArc(MM.Brand, -90f, (score / 54.0 * 360).toFloat(), false, tl, sz, style = Stroke(w, cap = StrokeCap.Round))
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(fmt1(score), fontSize = 34.sp, fontWeight = FontWeight.ExtraBold)
            Muted("/ 54")
        }
    }
}

fun fmt1(v: Double) = String.format(Locale.US, "%.1f", v)
fun fmt2(v: Double) = String.format(Locale.US, "%.2f", v)
