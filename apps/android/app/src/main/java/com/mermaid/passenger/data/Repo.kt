package com.mermaid.passenger.data

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 연결 상태 (요구사항정의서 2.3) */
enum class LinkState { IDLE, CONNECTING, CONNECTED, LOST }

/** 화면이 보는 현재 상태. 서비스(RF 스레드)가 쓰고 화면·알림·타일이 읽는다 */
data class Live(
    val link: LinkState = LinkState.IDLE,
    val kind: String = "",                 // "BLE" | "데모"
    val t1: Boolean = false,               // 송풍 (공용)
    val t2: Boolean = false,               // 앰비언트 (공용)
    val t3: Boolean = false,               // 내 모션 큐
    val pending: Map<String, Boolean> = emptyMap(), // 수동 조작 회신 대기 (기기 ID → 요청 값)
    val ctrlMssq: Double? = null,          // 컨트롤러가 보낸 PROF
    val ctrlSens: Double? = null,
    val retryAt: Long? = null,             // 다음 재연결 시각 (elapsedRealtime)
    val reason: String = "",
    val intSeen: Boolean = false,          // 이번 연결에서 INT를 받은 적 있음 (= 주행 중으로 봄, 멀미 점수 알림 조건)
    val symLast: Int? = null,              // 마지막으로 보낸 멀미 점수 (M08)
    val symAt: Long? = null,               // 보낸 시각 (System.currentTimeMillis)
)

data class Stats(
    val name: String = "",
    val tx: Long = 0, val rx: Long = 0,
    val hbTx: Long = 0, val hbRx: Long? = null,
    val lastRxAt: Long = 0,                // elapsedRealtime
    val mtu: Int = 0, val rssi: Int? = null,
    val scrOn: Boolean = false, val scrAtt: Boolean = false,
    val riskRx: Long = 0,                  // 받은 M10 RISK 수
)

data class LogLine(val time: String, val dir: Char, val text: String)

/** GPS 상태 (RF 진단·대시보드). 시각은 elapsedRealtime */
data class GpsStats(
    val perm: String = "확인 전",          // "정확한 위치 허용" | "대략적 위치만" | "없음"
    val state: String = "꺼짐",             // 한 줄 설명 (수신 중 / 꺼짐 · 이유)
    val running: Boolean = false,          // 위치 요청 중
    val demo: Boolean = false,             // 데모 모드의 가짜 GPS
    val startedAt: Long = 0,
    val fixes: Long = 0,                   // 받은 위치 수
    val sent: Long = 0,                    // 보낸 M09 수
    val noSpeed: Long = 0,                 // 속도·정확도가 없어 보내지 않은 수
    val hz: Double? = null,                // 최근 10초 실제 수신 주기
    val lastAt: Long = 0,
    val spd: Double? = null,               // m/s
    val acc: Double? = null,               // m
    val brg: Double? = null,
    val alt: Double? = null,
) {
    /** 시작 뒤 평균 수신 주기 */
    fun avgHz(now: Long): Double? =
        if (!running || fixes < 2 || now - startedAt < 1000) null else fixes * 1000.0 / (now - startedAt)
}

/** 누가 Tier를 바꿨는지 */
enum class By { CTRL, ME, SAFE }

/** Tier 켜짐/꺼짐 이력 한 건 (at = System.currentTimeMillis) */
data class TierEvent(val at: Long, val tier: Int, val on: Boolean, val by: By)

/** M10 RISK 한 번 (at = System.currentTimeMillis), p = P(MISC ≥ 2..6) */
data class RiskPoint(val at: Long, val p: List<Double>)

/** GPS 속도 기록 (at = System.currentTimeMillis, m/s) */
data class SpeedPoint(val at: Long, val mps: Double)

/**
 * 이번 주행: 앱이 차와 연결을 시작한 때(RfSession.connect)부터 끝낼 때(stop)까지.
 * 중간에 끊겼다 다시 붙어도 같은 주행. elapsed 값은 elapsedRealtime
 */
data class Drive(
    val startedAt: Long = 0,              // System.currentTimeMillis, 0 = 주행 없음
    val connectedSince: Long? = null,     // elapsed, 지금 연결이 붙은 때 (끊기면 null)
    val firstConnectedAt: Long? = null,   // elapsed
    val drops: Int = 0,                   // 연결됨 → 끊김/신호 없음 횟수
    val events: List<TierEvent> = emptyList(),
)

/** 화면·타일 → 서비스 명령 */
sealed interface Cmd {
    data class Override(val dev: String, val on: Boolean) : Cmd
    data object ToggleCue : Cmd
    data object SendProfile : Cmd
    data object RefreshScreen : Cmd
    data object Reconnect : Cmd
    data object DemoDrop : Cmd
    data class Symptom(val misc: Int) : Cmd
}

object Repo {
    val live = MutableStateFlow(Live())
    val stats = MutableStateFlow(Stats())
    val log = MutableStateFlow<List<LogLine>>(emptyList())
    val commands = MutableSharedFlow<Cmd>(extraBufferCapacity = 32)

    val gps = MutableStateFlow(GpsStats())
    val drive = MutableStateFlow(Drive())
    val risk = MutableStateFlow<List<RiskPoint>>(emptyList())
    val speed = MutableStateFlow<List<SpeedPoint>>(emptyList())

    @Volatile var serviceRunning = false

    const val RISK_CAP = 360         // 10초마다면 1시간 (그래프는 최근 30분)
    const val SPEED_CAP = 1800       // 1초에 하나만 남기므로 30분
    const val EVENT_CAP = 200
    const val CHART_WINDOW_MS = 30 * 60_000L

    private val riskRing = Ring<RiskPoint>(RISK_CAP)
    private val speedRing = Ring<SpeedPoint>(SPEED_CAP)

    fun addRisk(p: RiskPoint) = synchronized(riskRing) { riskRing.add(p); risk.value = riskRing.toList() }

    /** 1초에 하나만 남긴다 (GPS가 10 Hz여도 그래프·메모리는 같게) */
    fun addSpeed(p: SpeedPoint) = synchronized(speedRing) {
        val last = speedRing.last()
        if (last != null && p.at - last.at < 1000) return
        speedRing.add(p); speed.value = speedRing.toList()
    }

    /** 새 주행 시작: Tier 이력·끊김 횟수를 비운다 (위험 확률·속도 그래프는 시각으로 30분만 보여 주므로 그대로) */
    fun newDrive() { drive.value = Drive(startedAt = System.currentTimeMillis()) }

    fun addEvent(e: TierEvent) = drive.update { it.copy(events = (it.events + e).takeLast(EVENT_CAP)) }

    private val fmt = SimpleDateFormat("HH:mm:ss", Locale.KOREA)

    /** dir: '→' 보냄, '←' 받음, '!' 오류/알림, '·' 정보. 최근 300줄, 기기 안에만 */
    fun log(dir: Char, text: String) {
        val line = LogLine(fmt.format(Date()), dir, text)
        log.update { (it + line).takeLast(300) }
    }

    fun send(cmd: Cmd) { commands.tryEmit(cmd) }
}
