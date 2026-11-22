package com.mermaid.passenger.service

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Handler
import android.os.SystemClock
import com.mermaid.passenger.data.Fix
import com.mermaid.passenger.data.RateMeter
import com.mermaid.passenger.data.Repo
import com.mermaid.passenger.data.SpeedPoint
import kotlinx.coroutines.flow.update
import java.util.Locale
import kotlin.math.PI
import kotlin.math.max
import kotlin.math.sin

/**
 * 폰 GPS → M09 (#20). 위도·경도는 읽기만 하고 버린다: 속도·정확도·방향·고도만 남긴다.
 * - 켜는 조건(LinkService가 판단): 차와 연결됨 ∧ 주행 기록 수집 동의 (데모 모드는 가짜 GPS, 권한·동의 없이)
 * - 조건이 깨지면 위치 요청을 바로 끊는다 (배터리)
 * - LocationManager.GPS_PROVIDER에 minTime 0, minDistance 0 = 폰이 주는 만큼 자주. 보내기는 RfSession이 10 Hz로 제한
 * 모든 메서드는 생성 때 받은 Handler(RF 스레드)에서 부른다.
 */
class GpsFeed(private val ctx: Context, private val h: Handler, private val onFix: (Fix) -> Unit) {

    companion object {
        fun fine(c: Context) = c.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        fun coarse(c: Context) = c.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED
        fun any(c: Context) = fine(c) || coarse(c)
        fun permLabel(c: Context) = when { fine(c) -> "정확한 위치 허용"; coarse(c) -> "대략적 위치만"; else -> "없음" }

        private const val SUMMARY_MS = 60_000L
    }

    private enum class Mode { OFF, REAL, DEMO }

    private var mode = Mode.OFF
    private val meter = RateMeter()
    private var minuteFixes = 0L
    private var minuteSent = 0L
    private val lm get() = ctx.getSystemService(LocationManager::class.java)

    private val listener = LocationListener { loc -> h.post { onLocation(loc) } }

    /**
     * 켜거나 끈다. want=false면 why가 꺼진 이유.
     * 같은 상태로 다시 불러도 괜찮다 (권한이 새로 생겼으면 그때 켠다)
     */
    fun set(want: Boolean, demo: Boolean, why: String) {
        val perm = permLabel(ctx)
        val next = when { !want -> Mode.OFF; demo -> Mode.DEMO; else -> Mode.REAL }
        if (next == Mode.REAL && !fine(ctx)) {
            // GPS_PROVIDER는 정확한 위치 권한이 있어야 한다. 대략적 위치만이면 속도가 없다
            stop(if (coarse(ctx)) "꺼짐 · 정확한 위치 권한이 필요해요" else "꺼짐 · 위치 권한이 없어요", perm)
            return
        }
        if (next == mode) { Repo.gps.update { it.copy(perm = perm) }; return }
        stop(why, perm)
        when (next) {
            Mode.OFF -> Unit
            Mode.DEMO -> { mode = Mode.DEMO; started(perm, demo = true); h.post(fake) }
            Mode.REAL -> startReal(perm)
        }
    }

    @SuppressLint("MissingPermission") // 바로 위 set()에서 fine() 확인
    private fun startReal(perm: String) {
        val m = lm
        if (m == null || !m.allProviders.contains(LocationManager.GPS_PROVIDER)) {
            Repo.gps.update { it.copy(perm = perm, state = "꺼짐 · 이 폰에 GPS가 없어요", running = false) }
            return
        }
        if (!m.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
            // 위치가 꺼져 있어도 요청은 걸어 둔다: 사용자가 켜면 그때부터 들어온다
            Repo.log('!', "폰 위치(GPS)가 꺼져 있음 — 켜면 속도를 보내요")
        }
        try {
            m.requestLocationUpdates(LocationManager.GPS_PROVIDER, 0L, 0f, listener, h.looper)
        } catch (e: Exception) {
            Repo.gps.update { it.copy(perm = perm, state = "꺼짐 · 위치 요청 실패: ${e.message}", running = false) }
            Repo.log('!', "GPS 요청 실패: ${e.message}")
            return
        }
        mode = Mode.REAL
        started(perm, demo = false)
    }

    private fun started(perm: String, demo: Boolean) {
        meter.reset(); minuteFixes = 0
        val now = SystemClock.elapsedRealtime()
        Repo.gps.update {
            it.copy(perm = perm, state = if (demo) "수신 중 · 데모 (가짜 GPS)" else "수신 중 · 첫 위치를 기다려요",
                running = true, demo = demo, startedAt = now, fixes = 0, sent = 0, noSpeed = 0, hz = null, lastAt = 0)
        }
        minuteSent = 0
        Repo.log('·', if (demo) "GPS 시작 (데모 가짜 값)" else "GPS 시작 (GPS_PROVIDER, 최소 간격 0)")
        h.removeCallbacks(summary); h.postDelayed(summary, SUMMARY_MS)
    }

    fun stop(why: String = "꺼짐", perm: String = permLabel(ctx)) {
        val was = mode
        if (was == Mode.REAL) try { lm?.removeUpdates(listener) } catch (_: Exception) {}
        h.removeCallbacks(fake); h.removeCallbacks(summary)
        mode = Mode.OFF
        val g = Repo.gps.value
        if (was != Mode.OFF) {
            val now = SystemClock.elapsedRealtime()
            Repo.log('·', "GPS 중지 ($why) · 받음 ${g.fixes} · 보냄 ${g.sent} · 평균 ${fmtHz(g.avgHz(now))}")
        }
        Repo.gps.update { it.copy(perm = perm, state = why, running = false, hz = null) }
    }

    /** 위도·경도는 여기서 버린다 */
    private fun onLocation(loc: Location) {
        if (mode != Mode.REAL) return
        val fix = Fix(
            spd = if (loc.hasSpeed()) loc.speed.toDouble() else null,
            acc = if (loc.hasAccuracy()) loc.accuracy.toDouble() else null,
            brg = if (loc.hasBearing()) loc.bearing.toDouble() else null,
            alt = if (loc.hasAltitude()) loc.altitude else null,
        )
        accept(fix)
    }

    private fun accept(fix: Fix) {
        val now = SystemClock.elapsedRealtime()
        meter.mark(now)
        minuteFixes++
        val hz = meter.hz(now)
        Repo.gps.update {
            it.copy(
                state = if (it.demo) "수신 중 · 데모 (가짜 GPS)" else "수신 중",
                fixes = it.fixes + 1, noSpeed = it.noSpeed + if (fix.sendable) 0 else 1, hz = hz, lastAt = now,
                spd = fix.spd, acc = fix.acc, brg = fix.brg, alt = fix.alt,
            )
        }
        fix.spd?.let { Repo.addSpeed(SpeedPoint(System.currentTimeMillis(), it)) }
        if (fix.sendable) onFix(fix) // hasSpeed()가 없으면 보내지 않음
    }

    /** 1분마다 로그 한 줄 (GPS 줄을 매번 남기지 않는 대신) */
    private val summary = object : Runnable {
        override fun run() {
            if (mode == Mode.OFF) return
            val g = Repo.gps.value
            val now = SystemClock.elapsedRealtime()
            Repo.log('·', "GPS 1분: 받음 $minuteFixes · 보냄 ${g.sent - minuteSent} · 최근 ${fmtHz(meter.hz(now))}" +
                (g.spd?.let { String.format(Locale.US, " · %.1f km/h", it * 3.6) } ?: "") +
                (g.acc?.let { String.format(Locale.US, " · ±%.0f m", it) } ?: ""))
            minuteFixes = 0; minuteSent = g.sent
            h.postDelayed(this, SUMMARY_MS)
        }
    }

    // ───────── 데모: 1 Hz, 0 → 60 km/h 가속 · 정속 · 감속이 90초마다 (DemoLink 시나리오와 같은 주기)
    private val t0 = SystemClock.elapsedRealtime()
    private val fake = object : Runnable {
        override fun run() {
            if (mode != Mode.DEMO) return
            val s = ((SystemClock.elapsedRealtime() - t0) / 1000.0) % 90
            val top = 60 / 3.6
            val v = when {
                s < 15 -> top * s / 15
                s < 70 -> top * (0.85 + 0.15 * sin(s * 2 * PI / 20))
                s < 85 -> top * 0.85 * (85 - s) / 15
                else -> 0.0
            }
            accept(Fix(spd = max(0.0, v), acc = 3.0 + 2 * sin(s / 7), brg = (s * 4) % 360, alt = 45.0))
            h.postDelayed(this, 1000)
        }
    }
}

fun fmtHz(hz: Double?): String = hz?.let { String.format(Locale.US, "%.1f Hz", it) } ?: "—"
