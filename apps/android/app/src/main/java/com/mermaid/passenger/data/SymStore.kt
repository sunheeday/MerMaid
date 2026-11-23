package com.mermaid.passenger.data

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.File

/** 내가 누른 멀미 점수 한 번 (at = System.currentTimeMillis) */
data class SymPoint(val at: Long, val misc: Int)

/** 한 주행(앱이 차와 연결을 시작한 때부터 끝낼 때까지) 동안 누른 점수들 */
data class SymDrive(val start: Long, val points: List<SymPoint>) {
    val max get() = points.maxOfOrNull { it.misc }
    val avg get() = if (points.isEmpty()) null else points.sumOf { it.misc }.toDouble() / points.size
}

/** 저장 규칙 (순수 함수, JVM 시험 대상) */
object SymHistory {
    const val MAX_DRIVES = 10
    const val MAX_POINTS = 300   // 2분 알림이면 10시간 주행분

    /** start 주행에 점수를 더한다. 새 주행이면 끝에 붙이고, 오래된 주행·점수부터 버린다 */
    fun add(
        drives: List<SymDrive>, start: Long, p: SymPoint,
        maxDrives: Int = MAX_DRIVES, maxPoints: Int = MAX_POINTS,
    ): List<SymDrive> {
        val last = drives.lastOrNull()
        val next = if (last != null && last.start == start) {
            drives.dropLast(1) + last.copy(points = (last.points + p).takeLast(maxPoints))
        } else {
            drives + SymDrive(start, listOf(p))
        }
        return next.takeLast(maxDrives)
    }

    fun toJson(drives: List<SymDrive>): String = JSONArray().apply {
        drives.forEach { d ->
            put(JSONObject().put("start", d.start).put("pts", JSONArray().apply {
                d.points.forEach { put(JSONArray().put(it.at).put(it.misc)) }
            }))
        }
    }.toString()

    /** 깨진 파일이면 빈 목록 */
    fun fromJson(s: String): List<SymDrive> = try {
        val a = JSONArray(s)
        (0 until a.length()).mapNotNull { i ->
            val o = a.optJSONObject(i) ?: return@mapNotNull null
            val pts = o.optJSONArray("pts") ?: JSONArray()
            SymDrive(o.optLong("start"), (0 until pts.length()).mapNotNull { j ->
                val p = pts.optJSONArray(j) ?: return@mapNotNull null
                val misc = p.optInt(1, -1)
                if (misc in 0..10) SymPoint(p.optLong(0), misc) else null
            })
        }.takeLast(MAX_DRIVES)
    } catch (e: JSONException) {
        emptyList()
    }
}

/**
 * 멀미 점수 기록을 기기 안 파일(files/sym_history.json)에 둔다. 최근 10주행.
 * 주행 기록 수집 동의가 켜져 있고 컨트롤러에 실제로 보낸 점수만 저장한다 (RfSession.sendSymptom).
 */
object SymStore {
    private var file: File? = null
    private val state = MutableStateFlow<List<SymDrive>>(emptyList())
    val drives: StateFlow<List<SymDrive>> get() = state

    fun init(ctx: Context) {
        val f = File(ctx.filesDir, "sym_history.json")
        file = f
        state.value = try { if (f.exists()) SymHistory.fromJson(f.readText()) else emptyList() } catch (_: Exception) { emptyList() }
    }

    @Synchronized
    fun record(driveStart: Long, p: SymPoint) {
        state.value = SymHistory.add(state.value, driveStart, p)
        save()
    }

    @Synchronized
    fun clear() {
        state.value = emptyList()
        save()
    }

    private fun save() {
        val f = file ?: return
        try {
            val tmp = File(f.parentFile, f.name + ".tmp")
            tmp.writeText(SymHistory.toJson(state.value))
            if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
        } catch (_: Exception) {}
    }
}
