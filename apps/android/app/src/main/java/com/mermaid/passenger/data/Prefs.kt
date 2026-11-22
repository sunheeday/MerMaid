package com.mermaid.passenger.data

import android.content.Context
import android.content.SharedPreferences
import com.mermaid.passenger.protocol.Mssq
import com.mermaid.passenger.protocol.Proto
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.util.UUID

/** 기기에 저장하는 값 전부. 밖으로 보내는 것은 devId·mssq·sens, 그리고 동의했을 때만 멀미 점수 (NFR-08) */
data class Settings(
    val devId: String = "",
    val onboarded: Boolean = false,
    val bridgeAddress: String? = null,
    val bridgeName: String? = null,
    val autoConnect: Boolean = true,
    val hasProfile: Boolean = false,
    val mssq: Double = Mssq.DEFAULT_MSSQ,
    val sens: Double = Mssq.DEFAULT_SENS,
    val answers: String = "",
    val cueGainDp: Int = 24,        // 움직임 크기 약/중/강 = 16/24/32 dp
    val cueAlpha: Float = 0.8f,     // 0.3~0.8 (0.8 초과 시 터치 통과 불가)
    val cueAmber: Boolean = false,
    val cuePill: Boolean = true,
    val logConsent: Boolean = false,    // 주행 기록 수집 동의. 꺼져 있으면 멀미 점수(SYM)를 보내지 않고 알림도 없음
    val consentAsked: Boolean = false,  // 동의 안내를 한 번 보여 줬음
) {
    val registered get() = bridgeAddress != null
}

object Prefs {
    private lateinit var sp: SharedPreferences
    private val state = MutableStateFlow(Settings())
    val settings: StateFlow<Settings> get() = state

    fun init(ctx: Context) {
        sp = ctx.getSharedPreferences("mermaid", Context.MODE_PRIVATE)
        // 기기 ID: 처음 한 번 만든 UUID. 63자 이하, 고정 기기 ID와 겹치면 안 됨 (RF_MESSAGES.md §2-1)
        val id = sp.getString(K_DEV, null)
        if (id == null || id.length > 63 || id in Proto.RESERVED_IDS) {
            sp.edit().putString(K_DEV, UUID.randomUUID().toString()).apply()
        }
        state.value = read()
    }

    fun edit(block: SharedPreferences.Editor.() -> Unit) {
        sp.edit().apply(block).apply()
        state.value = read()
    }

    fun setBridge(address: String?, name: String?) = edit {
        putString(K_ADDR, address); putString(K_NAME, name)
    }

    fun setProfile(mssq: Double, sens: Double, answers: String) = edit {
        putBoolean(K_HAS_PROF, true); putFloat(K_MSSQ, mssq.toFloat()); putFloat(K_SENS, sens.toFloat()); putString(K_ANS, answers)
    }

    /** 주행 기록 수집 동의를 켜거나 끔. 한 번이라도 답했으면 안내를 다시 띄우지 않음 */
    fun setLogConsent(on: Boolean) = edit { putBoolean(K_CONSENT, on); putBoolean(K_CONSENT_ASKED, true) }

    private fun read() = Settings(
        devId = sp.getString(K_DEV, "")!!,
        onboarded = sp.getBoolean(K_ONB, false),
        bridgeAddress = sp.getString(K_ADDR, null),
        bridgeName = sp.getString(K_NAME, null),
        autoConnect = sp.getBoolean(K_AUTO, true),
        hasProfile = sp.getBoolean(K_HAS_PROF, false),
        mssq = sp.getFloat(K_MSSQ, Mssq.DEFAULT_MSSQ.toFloat()).toDouble(),
        sens = sp.getFloat(K_SENS, Mssq.DEFAULT_SENS.toFloat()).toDouble(),
        answers = sp.getString(K_ANS, "")!!,
        cueGainDp = sp.getInt(K_GAIN, 24),
        cueAlpha = sp.getFloat(K_ALPHA, 0.8f),
        cueAmber = sp.getBoolean(K_AMBER, false),
        cuePill = sp.getBoolean(K_PILL, true),
        logConsent = sp.getBoolean(K_CONSENT, false),
        consentAsked = sp.getBoolean(K_CONSENT_ASKED, false),
    )

    const val K_DEV = "dev_id"
    const val K_ONB = "onboarded"
    const val K_ADDR = "bridge_addr"
    const val K_NAME = "bridge_name"
    const val K_AUTO = "auto_connect"
    const val K_HAS_PROF = "has_profile"
    const val K_MSSQ = "mssq"
    const val K_SENS = "sens"
    const val K_ANS = "answers"
    const val K_GAIN = "cue_gain"
    const val K_ALPHA = "cue_alpha"
    const val K_AMBER = "cue_amber"
    const val K_PILL = "cue_pill"
    const val K_CONSENT = "log_consent"
    const val K_CONSENT_ASKED = "log_consent_asked"
}
