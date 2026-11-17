package com.mermaid.passenger

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import com.mermaid.passenger.data.Prefs

class MerMaidApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Prefs.init(this)
        // 상주 알림 채널: 중요도 낮음 = 헤드업·소리·진동 없음 (APP-F-24)
        val ch = NotificationChannel(CHANNEL_LINK, getString(R.string.channel_link), NotificationManager.IMPORTANCE_LOW).apply {
            description = getString(R.string.channel_link_desc)
            setShowBadge(false)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
    }

    companion object {
        const val CHANNEL_LINK = "link"
    }
}
