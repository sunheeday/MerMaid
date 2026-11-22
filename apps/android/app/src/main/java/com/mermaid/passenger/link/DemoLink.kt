package com.mermaid.passenger.link

import android.os.Handler
import android.os.SystemClock
import com.mermaid.passenger.protocol.Proto
import org.json.JSONObject
import org.json.JSONArray
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.sin

/**
 * 개발 빌드 전용 가짜 컨트롤러 (APP-F-44). 브리지 없이 오버레이를 확인한다.
 * 웹 데모(cabin3d/ui/js/demo.js)와 같은 90초 반복 시나리오, P5 규칙대로 Tier 3이 켜진 동안만 MOT 10 Hz.
 * M10 RISK도 10초마다 흉내 낸다 (흔들림이 큰 구간에서 위험 확률이 오름) → 기기 없이 대시보드 확인.
 */
class DemoLink(private val h: Handler, private val cb: Transport.Callback) : Transport {
    override val name = "데모"
    private val t0 = SystemClock.elapsedRealtime()
    private var dev: String? = null
    private val st = mutableMapOf(Proto.DEV_AIRFLOW to false, Proto.DEV_DISPLAY to false, "phone" to false)
    private var cooldownUntil = mutableMapOf<String, Double>()
    private var lastS = -1.0
    private var seq = 0L
    private var silentUntil = 0L
    private var running = false
    private var lastRiskAt = -1.0

    private val plan = listOf(
        14.0 to (Proto.DEV_AIRFLOW to true), 24.0 to (Proto.DEV_DISPLAY to true), 34.0 to ("phone" to true),
        70.0 to ("phone" to false), 76.0 to (Proto.DEV_DISPLAY to false), 82.0 to (Proto.DEV_AIRFLOW to false),
    )

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            step(); h.postDelayed(this, 100)
        }
    }
    private val hb = object : Runnable {
        override fun run() {
            if (!running) return
            out(JSONObject().put("t", "HB").put("seq", ++seq)); h.postDelayed(this, 1000)
        }
    }

    private val now get() = (SystemClock.elapsedRealtime() - t0) / 1000.0

    override fun open() {
        running = true
        h.postDelayed({ cb.onReady(Proto.MTU) }, 300)
        h.post(tick); h.post(hb)
    }

    /** 연결 끊김 재현: s초 동안 아무것도 보내지 않음 (앱의 5초 감시 → 안전 OFF) */
    fun drop(seconds: Int = 7) { silentUntil = SystemClock.elapsedRealtime() + seconds * 1000L }

    override fun send(line: String) {
        val m = try { JSONObject(line) } catch (e: Exception) { return }
        when (m.optString("t")) {
            "HELLO" -> { dev = m.optString("dev"); sendState() }
            "OVR" -> {
                val target = m.optString("dev"); val on = m.optBoolean("on")
                val key = when (target) { Proto.DEV_AIRFLOW, Proto.DEV_DISPLAY -> target; dev -> "phone"; else -> return }
                if (!on) cooldownUntil[key] = now + 30
                set(key, on)
            }
            "SYM", "GPS" -> Unit // M08 멀미 점수·M09 GPS: 진짜 컨트롤러도 기록만 하고 회신하지 않음
            "PROF" -> h.postDelayed({ out(JSONObject().put("t", "PROF").put("mssq", m.optDouble("mssq")).put("sens", m.optDouble("sens"))) }, 200)
        }
    }

    private fun out(o: JSONObject) { if (SystemClock.elapsedRealtime() >= silentUntil) h.post { if (running) cb.onLine(o.toString()) } }

    private fun tierOf(key: String) = when (key) { Proto.DEV_AIRFLOW -> 1; Proto.DEV_DISPLAY -> 2; else -> 3 }

    private fun set(key: String, on: Boolean, auto: Boolean = false) {
        if (auto && on && (cooldownUntil[key] ?: 0.0) > now) return
        st[key] = on
        out(JSONObject().put("t", "INT").put("tier", tierOf(key)).put("on", on))
    }

    private fun sendState() { for (k in st.keys) out(JSONObject().put("t", "INT").put("tier", tierOf(k)).put("on", st[k] == true)) }

    private fun motion(t: Double): Pair<Double, Double> {
        val s = ((t % 90) + 90) % 90
        val w = when { s < 10 -> 0.0; s < 16 -> (s - 10) / 6; s < 66 -> 1.0; s < 72 -> 1 - (s - 66) / 6; else -> 0.0 }
        val ay = w * (2.1 * sin(s * 0.42) + 0.6 * sin(s * 1.3))
        val ax = 0.35 * sin(s * 0.21) + (if (s < 6) 1.2 else 0.0) - (if (s > 84) 0.9 else 0.0)
        return ax to ay
    }

    /**
     * 흔들림 세기 w(0~1)로 예상 MISC 수준을 만들고, P(MISC ≥ k)를 로지스틱으로 (k가 클수록 작게, 늘 단조 감소).
     * 진짜 모델 값이 아니라 그래프 모양을 보기 위한 흉내 값
     */
    private fun risk(t: Double): List<Double> {
        val (ax, ay) = motion(t)
        val level = 0.8 + 1.6 * (abs(ay) + abs(ax)) + 0.4 * sin(t / 13)
        return (2..6).map { k -> Math.round(1000.0 / (1 + exp(-(level - k) * 1.8))) / 1000.0 }
    }

    private fun step() {
        if (dev != null && (lastRiskAt < 0 || now - lastRiskAt >= 10)) {
            lastRiskAt = now
            out(JSONObject().put("t", "RISK").put("p", JSONArray(risk(now))))
        }
        val s = now % 90
        if (s < lastS) cooldownUntil.clear()
        for ((at, act) in plan) if (lastS < at && s >= at) set(act.first, act.second, auto = true)
        lastS = s
        if (st["phone"] == true && dev != null) {
            val (ax, ay) = motion(now); val (pax, pay) = motion(now + 2)
            fun r(v: Double) = Math.round(v * 1000) / 1000.0
            out(JSONObject().put("t", "MOT").put("ax", r(ax)).put("ay", r(ay)).put("pax", r(pax)).put("pay", r(pay)))
        }
    }

    override fun close() {
        running = false
        h.removeCallbacks(tick); h.removeCallbacks(hb)
    }
}
