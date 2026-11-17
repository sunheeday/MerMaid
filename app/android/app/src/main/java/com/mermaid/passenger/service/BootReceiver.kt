package com.mermaid.passenger.service

import android.companion.CompanionDeviceManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.mermaid.passenger.data.Prefs

/** 재부팅 후 등록 차량의 존재 감시를 다시 건다 (시스템이 유지하지만 안전하게 한 번 더) */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val s = Prefs.settings.value
        val addr = s.bridgeAddress ?: return
        if (!s.autoConnect) return
        Presence.observe(ctx, addr)
    }
}

object Presence {
    @Suppress("DEPRECATION") // API 31~ 주소 버전. 36+의 ObservingDevicePresenceRequest는 minSdk 때문에 쓰지 않음
    fun observe(ctx: Context, address: String) {
        try { ctx.getSystemService(CompanionDeviceManager::class.java)?.startObservingDevicePresence(address) } catch (_: Exception) {}
    }

    @Suppress("DEPRECATION")
    fun stop(ctx: Context, address: String) {
        try { ctx.getSystemService(CompanionDeviceManager::class.java)?.stopObservingDevicePresence(address) } catch (_: Exception) {}
    }
}
