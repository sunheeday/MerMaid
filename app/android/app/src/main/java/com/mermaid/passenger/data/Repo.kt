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
)

data class LogLine(val time: String, val dir: Char, val text: String)

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

    @Volatile var serviceRunning = false

    private val fmt = SimpleDateFormat("HH:mm:ss", Locale.KOREA)

    /** dir: '→' 보냄, '←' 받음, '!' 오류/알림, '·' 정보. 최근 300줄, 기기 안에만 */
    fun log(dir: Char, text: String) {
        val line = LogLine(fmt.format(Date()), dir, text)
        log.update { (it + line).takeLast(300) }
    }

    fun send(cmd: Cmd) { commands.tryEmit(cmd) }
}
