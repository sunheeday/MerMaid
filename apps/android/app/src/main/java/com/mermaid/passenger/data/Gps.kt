package com.mermaid.passenger.data

/**
 * GPS 한 번의 위치 결과에서 앱이 쓰는 값만. 위도·경도는 담지 않는다 (밖으로 보내지도, 저장하지도 않음).
 * spd m/s, acc 수평 정확도 m, brg 진행 방향 도, alt 고도 m. 없는 값은 null
 */
data class Fix(val spd: Double?, val acc: Double?, val brg: Double? = null, val alt: Double? = null) {
    /** M09로 보낼 수 있는가: 속도와 정확도가 있어야 함 */
    val sendable get() = spd != null && acc != null && !spd.isNaN() && !acc.isNaN()
}

/**
 * 실제 수신 주기 측정: 최근 windowMs 안에 들어온 시각들로 Hz를 잰다.
 * 시각은 단조 증가하는 ms (elapsedRealtime). 2개 미만이면 null
 */
class RateMeter(private val windowMs: Long = 10_000L, maxSamples: Int = 256) {
    private val times = ArrayDeque<Long>()
    private val max = maxSamples

    fun mark(nowMs: Long) {
        times.addLast(nowMs)
        while (times.size > max) times.removeFirst()
        trim(nowMs)
    }

    /** 최근 창 안의 수신 주기 (Hz). 창 안에 2개 미만이면 null */
    fun hz(nowMs: Long): Double? {
        trim(nowMs)
        if (times.size < 2) return null
        val span = times.last() - times.first()
        return if (span <= 0) null else (times.size - 1) * 1000.0 / span
    }

    fun count(nowMs: Long): Int { trim(nowMs); return times.size }

    fun reset() = times.clear()

    private fun trim(nowMs: Long) {
        while (times.isNotEmpty() && nowMs - times.first() > windowMs) times.removeFirst()
    }
}

/** 최소 간격 제한 (M09 최대 10 Hz). 통과하면 그 시각을 기억 */
class Throttle(private val minIntervalMs: Long) {
    private var last: Long? = null

    fun pass(nowMs: Long): Boolean {
        val l = last
        if (l != null && nowMs - l < minIntervalMs) return false
        last = nowMs
        return true
    }

    fun reset() { last = null }
}
