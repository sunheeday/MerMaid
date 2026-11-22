package com.mermaid.passenger.protocol

import org.json.JSONException
import org.json.JSONObject
import java.util.UUID
import kotlin.math.exp
import kotlin.math.roundToLong

/**
 * MerMaid 프로토콜 v1 — GitHub sunheeday/MerMaid `protocol/` (uuids.json, constants.json, messages.schema.json,
 * PROTOCOL.md, docs/controller/RF_MESSAGES.md) 값을 그대로 옮김. 바뀌면 protocol/을 먼저 고치고 이 파일을 맞춘다.
 */
object Proto {
    const val NAME_PREFIX = "MSAgent-"
    val SERVICE: UUID = UUID.fromString("cd820001-2f55-4bae-8e0f-8474006c441e")
    val RX: UUID = UUID.fromString("cd820002-2f55-4bae-8e0f-8474006c441e") // 앱 → 컨트롤러, Write Without Response
    val TX: UUID = UUID.fromString("cd820003-2f55-4bae-8e0f-8474006c441e") // 컨트롤러 → 앱, Notify
    val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    const val DEV_AIRFLOW = "airflow-01"
    const val DEV_DISPLAY = "display-01"
    const val DEV_HUD = "hud-01"
    val RESERVED_IDS = setOf(DEV_AIRFLOW, DEV_DISPLAY, DEV_HUD)

    const val VERSION = 1
    const val MTU = 247
    const val MAX_MSG = 512
    const val HB_MS = 1000L
    const val LINK_TIMEOUT_MS = 5000L   // 5초 무수신 = 끊김 (NFR-01)
    const val MOT_STALE_MS = 500L       // MOT가 0.5초 안 오면 점을 가운데로
    const val OVR_TIMEOUT_MS = 3000L    // 수동 조작 회신 대기
    const val SYM_MIN = 0               // 멀미 점수 MISC 0~10 (M08)
    const val SYM_MAX = 10
    const val SYM_PROMPT_MS = 2 * 60_000L // 주행 중 멀미 점수를 물어보는 알림 간격
    const val GPS_MIN_INTERVAL_MS = 100L  // M09 GPS는 최대 10 Hz
    const val RISK_LEN = 5                // M10 RISK p = P(MISC ≥ 2, 3, 4, 5, 6)
    const val RISK_MIN_K = 2              // p[0]이 MISC ≥ 2
}

/** 컨트롤러 → 앱 메시지 (모르는 t는 null: 명세상 무시) */
sealed interface Incoming {
    /** M01 INT */
    data class Intervention(val tier: Int, val on: Boolean) : Incoming
    data class Mot(val ax: Float, val ay: Float, val pax: Float?, val pay: Float?) : Incoming
    data class Prof(val mssq: Double, val sens: Double) : Incoming
    data class Hb(val seq: Long) : Incoming
    /** M10 RISK: 이 폰의 P(MISC ≥ 2,3,4,5,6), 약 10초마다. 앱은 표시만 한다 */
    data class Risk(val p: List<Double>) : Incoming {
        /** P(MISC ≥ k), k = 2..6 */
        fun atLeast(k: Int): Double = p[k - Proto.RISK_MIN_K]
    }
}

object Msg {
    fun hello(dev: String): String =
        JSONObject().put("t", "HELLO").put("role", "app").put("dev", dev).put("v", Proto.VERSION).toString()

    /** 화면이 꺼져 있으면 att는 항상 false */
    fun scr(on: Boolean, att: Boolean): String =
        JSONObject().put("t", "SCR").put("on", on).put("att", on && att).toString()

    fun ovr(dev: String, on: Boolean): String =
        JSONObject().put("t", "OVR").put("dev", dev).put("on", on).toString()

    /** mssq 소수 2자리, sens 3자리 (RF_MESSAGES.md §3) */
    fun prof(mssq: Double, sens: Double): String =
        JSONObject().put("t", "PROF")
            .put("mssq", (mssq * 100).roundToLong() / 100.0)
            .put("sens", (sens * 1000).roundToLong() / 1000.0).toString()

    /** M08 SYM: 승객이 직접 고른 멀미 점수(MISC 0~10 정수). 기록 전용이라 회신이 없다 */
    fun sym(misc: Int): String =
        JSONObject().put("t", "SYM").put("misc", misc.coerceIn(Proto.SYM_MIN, Proto.SYM_MAX)).toString()

    /**
     * M09 GPS: 폰 GPS의 속도·정확도(필수)와 진행 방향·고도(있을 때만). 위도·경도는 보내지 않는다.
     * spd m/s 소수 2자리, brg 도 0~360 소수 1자리, acc·alt m 소수 1자리
     */
    fun gps(spd: Double, acc: Double, brg: Double? = null, alt: Double? = null): String {
        val o = JSONObject().put("t", "GPS").put("spd", round(spd.coerceAtLeast(0.0), 100))
        if (brg != null && !brg.isNaN()) o.put("brg", round(((brg % 360) + 360) % 360, 10).let { if (it >= 360.0) 0.0 else it })
        o.put("acc", round(acc.coerceAtLeast(0.0), 10))
        if (alt != null && !alt.isNaN()) o.put("alt", round(alt, 10))
        return o.toString()
    }

    private fun round(v: Double, scale: Int): Double = (v * scale).roundToLong().toDouble() / scale

    fun hb(seq: Long): String = JSONObject().put("t", "HB").put("seq", seq).toString()

    fun parse(line: String): Incoming? = try {
        val o = JSONObject(line)
        when (o.optString("t")) {
            "INT" -> {
                val tier = o.optInt("tier", -1)
                val on = o.opt("on") as? Boolean
                if (tier in 1..3 && on != null) Incoming.Intervention(tier, on) else null
            }
            "MOT" -> {
                val ax = o.optDouble("ax"); val ay = o.optDouble("ay")
                if (ax.isNaN() || ay.isNaN()) null else {
                    val pax = o.optDouble("pax"); val pay = o.optDouble("pay")
                    val both = !pax.isNaN() && !pay.isNaN()
                    Incoming.Mot(ax.toFloat(), ay.toFloat(), if (both) pax.toFloat() else null, if (both) pay.toFloat() else null)
                }
            }
            "PROF" -> {
                val m = o.optDouble("mssq"); val s = o.optDouble("sens")
                if (m.isNaN() || s.isNaN()) null else Incoming.Prof(m, s)
            }
            "HB" -> if (o.has("seq")) Incoming.Hb(o.optLong("seq")) else null
            "RISK" -> {
                val a = o.optJSONArray("p")
                if (a == null || a.length() != Proto.RISK_LEN) null else {
                    val p = (0 until a.length()).map { a.optDouble(it) }
                    if (p.all { !it.isNaN() && it in 0.0..1.0 }) Incoming.Risk(p) else null
                }
            }
            else -> null
        }
    } catch (e: JSONException) {
        null
    }
}

/** MSSQ-Short (Golding 2006) 채점과 민감도 (constants.json mssq) */
object Mssq {
    const val NORM_MEAN = 12.90
    const val NORM_SD = 9.90
    const val COEF = 0.3
    const val DEFAULT_MSSQ = 12.90
    const val DEFAULT_SENS = 1.0

    val ITEMS = listOf("자동차", "버스", "기차", "비행기", "작은 배", "큰 배", "그네", "회전기구", "롤러코스터")
    const val NA = -1      // 타 본 적 없음
    const val EMPTY = -2   // 아직 응답 안 함

    /** 구간 점수 = 합 × 9 ÷ (9 − 미경험 수) */
    fun section(answers: List<Int>): Double {
        val na = answers.count { it == NA }
        if (na >= 9) return 0.0
        val sum = answers.filter { it >= 0 }.sum()
        return sum * 9.0 / (9 - na)
    }

    fun raw(a: List<Int>, b: List<Int>): Double = (section(a) + section(b)).coerceAtMost(54.0)

    fun sens(mssq: Double): Double = exp(COEF * (mssq - NORM_MEAN) / NORM_SD).coerceIn(0.5, 2.0)

    fun label(sens: Double): String = when {
        sens > 1.5 -> "민감"
        sens > 1.15 -> "평균보다 조금 민감"
        sens >= 0.85 -> "보통"
        else -> "둔감한 편"
    }

    /** 저장 형식: 18자리 문자열, 0~3 숫자 · n(없음) · .(미응답) */
    fun encode(a: List<Int>, b: List<Int>): String =
        (a + b).joinToString("") { when (it) { NA -> "n"; EMPTY -> "."; else -> it.toString() } }

    fun decode(s: String): Pair<List<Int>, List<Int>> {
        val all = (0 until 18).map { i ->
            val c = s.getOrNull(i) ?: '.'
            when (c) { 'n' -> NA; '0', '1', '2', '3' -> c - '0'; else -> EMPTY }
        }
        return all.take(9) to all.drop(9)
    }
}
