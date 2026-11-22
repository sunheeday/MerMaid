package com.mermaid.passenger.link

import android.content.Context
import android.os.Handler
import android.os.SystemClock
import com.mermaid.passenger.data.LinkState
import com.mermaid.passenger.data.Prefs
import com.mermaid.passenger.data.Repo
import com.mermaid.passenger.protocol.Incoming
import com.mermaid.passenger.protocol.Msg
import com.mermaid.passenger.protocol.Proto
import kotlinx.coroutines.flow.update

sealed interface Target {
    data class Ble(val address: String, val name: String) : Target
    data object Demo : Target
}

/**
 * 프로토콜 계층 (모든 메서드는 RF Handler 스레드에서).
 * - 연결 직후 5초 안에 HELLO → PROF → SCR (APP-F-05)
 * - HB 1초, 5초 무수신 = LOST → 안전 OFF (APP-F-06, NFR-01)
 * - 끊기면 1·2·4…최대 30초 간격 재연결, 10분 동안 실패하면 포기 (APP-F-07, 08)
 */
class RfSession(private val ctx: Context, private val h: Handler, private val listener: Listener) {
    interface Listener {
        fun onMotion(ax: Float, ay: Float, pax: Float?, pay: Float?)
        fun onSafeOff()
        fun onGiveUp()
    }

    private var target: Target? = null
    private var transport: Transport? = null
    private var hbSeq = 0L
    private var lastRx = 0L
    private var backoff = 1000L
    private var downSince: Long? = null
    private var scrOn = true
    private var scrAtt = true
    private val ovrTimers = HashMap<String, Runnable>()

    private val me get() = Prefs.settings.value.devId
    private fun now() = SystemClock.elapsedRealtime()
    private fun setLink(s: LinkState, reason: String = "", retryAt: Long? = null) =
        Repo.live.update { it.copy(link = s, reason = reason, retryAt = retryAt) }

    fun connect(t: Target) = h.post {
        if (t == target && transport != null && Repo.live.value.link != LinkState.IDLE) return@post // 이미 연결 중/연결됨
        target = t
        backoff = 1000L; downSince = null
        openNow()
    }

    fun stop() = h.post {
        target = null
        h.removeCallbacksAndMessages(null)
        transport?.close(); transport = null
        safeOff()
        setLink(LinkState.IDLE)
    }

    fun reconnect() = h.post { if (target != null) { backoff = 1000L; openNow() } }

    private fun openNow() {
        val t = target ?: return
        h.removeCallbacks(retry)
        stopTimers()
        transport?.close()
        setLink(LinkState.CONNECTING)
        val tr = when (t) {
            is Target.Ble -> BleLink(ctx, t.address, t.name, h, cb)
            Target.Demo -> DemoLink(h, cb)
        }
        transport = tr
        Repo.live.update { it.copy(kind = if (t is Target.Demo) "데모" else "BLE") }
        Repo.stats.update { it.copy(name = tr.name) }
        Repo.log('·', "${tr.name} 연결 시도")
        tr.open()
    }

    private val retry = Runnable { openNow() }

    private val cb = object : Transport.Callback {
        override fun onReady(mtu: Int) {
            backoff = 1000L; downSince = null
            lastRx = now()
            Repo.stats.update { it.copy(mtu = mtu, lastRxAt = lastRx) }
            if (mtu < Proto.MTU) Repo.log('!', "MTU $mtu (247 요청) — 긴 메시지가 잘릴 수 있음")
            send(Msg.hello(me))
            val s = Prefs.settings.value
            if (s.hasProfile) send(Msg.prof(s.mssq, s.sens))
            sendScr(force = true)
            setLink(LinkState.CONNECTED)
            startTimers()
        }

        override fun onLine(line: String) = handleLine(line)

        override fun onRssi(rssi: Int) { Repo.stats.update { it.copy(rssi = rssi) } }

        override fun onClosed(reason: String) {
            stopTimers()
            safeOff()
            transport?.close(); transport = null
            Repo.log('!', reason)
            if (target == null) { setLink(LinkState.IDLE); return }
            val since = downSince ?: now().also { downSince = it }
            if (now() - since > 10 * 60_000L) { setLink(LinkState.IDLE, "10분 동안 차를 찾지 못함"); listener.onGiveUp(); return }
            val at = now() + backoff
            setLink(LinkState.CONNECTING, reason, at)
            h.postDelayed(retry, backoff)
            backoff = (backoff * 2).coerceAtMost(30_000L)
        }
    }

    private fun handleLine(line: String) {
        lastRx = now()
        Repo.stats.update { it.copy(rx = it.rx + 1, lastRxAt = lastRx) }
        if (Repo.live.value.link == LinkState.LOST) { setLink(LinkState.CONNECTED); Repo.log('·', "신호 복구") }
        when (val m = Msg.parse(line)) {
            null -> Repo.log('!', "무시: ${line.take(80)}") // 모르는 t, 깨진 JSON
            is Incoming.Intervention -> {
                Repo.log('←', line)
                val dev = when (m.tier) { 1 -> Proto.DEV_AIRFLOW; 2 -> Proto.DEV_DISPLAY; else -> me }
                ovrTimers.remove(dev)?.let { h.removeCallbacks(it) }
                Repo.live.update {
                    when (m.tier) {
                        1 -> it.copy(t1 = m.on)
                        2 -> it.copy(t2 = m.on)
                        else -> it.copy(t3 = m.on)
                    }.copy(pending = it.pending - dev)
                }
            }
            is Incoming.Mot -> listener.onMotion(m.ax, m.ay, m.pax, m.pay)
            is Incoming.Prof -> { Repo.log('←', line); Repo.live.update { it.copy(ctrlMssq = m.mssq, ctrlSens = m.sens) } }
            is Incoming.Hb -> Repo.stats.update { it.copy(hbRx = m.seq) }
        }
    }

    // ───────── 보내기
    private fun send(line: String) {
        val tr = transport ?: return
        if (!line.startsWith("{\"t\":\"HB\"")) Repo.log('→', line)
        tr.send(line)
        Repo.stats.update { it.copy(tx = it.tx + 1) }
    }

    /** 수동 조작 (M05). 회신 INT가 오면 확정, 3초 안 오면 대기 해제 (APP-F-23) */
    fun requestOverride(dev: String, on: Boolean) = h.post {
        if (Repo.live.value.link != LinkState.CONNECTED) return@post
        Repo.live.update { it.copy(pending = it.pending + (dev to on)) }
        send(Msg.ovr(dev, on))
        ovrTimers.remove(dev)?.let { h.removeCallbacks(it) }
        val r = Runnable { ovrTimers.remove(dev); Repo.live.update { it.copy(pending = it.pending - dev) } }
        ovrTimers[dev] = r
        h.postDelayed(r, Proto.OVR_TIMEOUT_MS)
    }

    fun toggleCue() = h.post {
        val l = Repo.live.value
        requestOverride(me, !(l.pending[me] ?: l.t3))
    }

    fun sendProfile() = h.post {
        val s = Prefs.settings.value
        if (Repo.live.value.link == LinkState.CONNECTED) send(Msg.prof(s.mssq, s.sens))
    }

    /** 화면 상태 (M03). on = 화면 켜짐, att = 켜짐 ∧ 잠금 해제 ∧ 오버레이 권한 (APP-F-20) */
    fun updateScreen(on: Boolean, att: Boolean) = h.post {
        scrOn = on; scrAtt = on && att
        sendScr(force = false)
    }

    private var sentOn: Boolean? = null
    private var sentAtt: Boolean? = null
    private fun sendScr(force: Boolean) {
        if (Repo.live.value.link != LinkState.CONNECTED && !force) return
        if (!force && sentOn == scrOn && sentAtt == scrAtt) return
        sentOn = scrOn; sentAtt = scrAtt
        send(Msg.scr(scrOn, scrAtt))
        Repo.stats.update { it.copy(scrOn = scrOn, scrAtt = scrAtt) }
    }

    fun demoDrop() = h.post { (transport as? DemoLink)?.drop() }

    // ───────── 주기 작업
    private val hb = object : Runnable {
        override fun run() {
            send(Msg.hb(++hbSeq))
            Repo.stats.update { it.copy(hbTx = hbSeq) }
            h.postDelayed(this, Proto.HB_MS)
        }
    }
    private val watchdog = object : Runnable {
        override fun run() {
            if (Repo.live.value.link == LinkState.CONNECTED && now() - lastRx > Proto.LINK_TIMEOUT_MS) {
                setLink(LinkState.LOST, "5초 동안 신호 없음")
                Repo.log('!', "5초 무수신 → 안전 OFF")
                safeOff()
            }
            h.postDelayed(this, 200)
        }
    }
    private val rssi = object : Runnable {
        override fun run() { transport?.readRssi(); h.postDelayed(this, 5000) }
    }

    private fun startTimers() {
        stopTimers()
        h.postDelayed(hb, Proto.HB_MS); h.postDelayed(watchdog, 200); h.postDelayed(rssi, 1000)
    }

    private fun stopTimers() { h.removeCallbacks(hb); h.removeCallbacks(watchdog); h.removeCallbacks(rssi) }

    /** 통신이 불확실하면 개입 표시를 남기지 않는다. 공용 장치 상태는 '알 수 없음'으로 */
    private fun safeOff() {
        ovrTimers.values.forEach { h.removeCallbacks(it) }; ovrTimers.clear()
        Repo.live.update { it.copy(t1 = false, t2 = false, t3 = false, pending = emptyMap()) }
        sentOn = null; sentAtt = null
        listener.onSafeOff()
    }
}
