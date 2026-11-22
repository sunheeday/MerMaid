package com.mermaid.passenger

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import com.mermaid.passenger.data.Prefs
import com.mermaid.passenger.data.SymStore

class MerMaidApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Prefs.init(this)
        SymStore.init(this)
        // 상주 알림 채널: 중요도 낮음 = 헤드업·소리·진동 없음 (APP-F-24)
        val ch = NotificationChannel(CHANNEL_LINK, getString(R.string.channel_link), NotificationManager.IMPORTANCE_LOW).apply {
            description = getString(R.string.channel_link_desc)
            setShowBadge(false)
        }
        // 멀미 점수 묻기 채널: 주행 중 2분마다 눈에 띄어야 해서 헤드업. 영상 보는 데 방해되지 않게 소리는 없고 진동만
        val sym = NotificationChannel(CHANNEL_SYM, getString(R.string.channel_sym), NotificationManager.IMPORTANCE_HIGH).apply {
            description = getString(R.string.channel_sym_desc)
            setSound(null, null)
            enableVibration(true)
            setShowBadge(false)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannels(listOf(ch, sym))
    }

    companion object {
        const val CHANNEL_LINK = "link"
        const val CHANNEL_SYM = "sym"
    }
}
