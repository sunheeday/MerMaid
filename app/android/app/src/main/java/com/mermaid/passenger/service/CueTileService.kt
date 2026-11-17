package com.mermaid.passenger.service

import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import com.mermaid.passenger.data.Cmd
import com.mermaid.passenger.data.LinkState
import com.mermaid.passenger.data.Prefs
import com.mermaid.passenger.data.Repo
import com.mermaid.passenger.ui.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/** 빠른 설정 타일 "모션 큐" — 켜짐 / 꺼짐 / 연결 안 됨. 탭 = OVR(내 기기 ID) (APP-F-22) */
class CueTileService : TileService() {
    private var job: Job? = null

    override fun onStartListening() {
        super.onStartListening()
        job = CoroutineScope(Dispatchers.Main).launch { Repo.live.collect { render() } }
    }

    override fun onStopListening() {
        job?.cancel(); job = null
        super.onStopListening()
    }

    private fun render() {
        val tile = qsTile ?: return
        val l = Repo.live.value
        val connected = Repo.serviceRunning && l.link == LinkState.CONNECTED
        val on = l.pending[Prefs.settings.value.devId] ?: l.t3
        tile.state = when { !connected -> Tile.STATE_UNAVAILABLE; on -> Tile.STATE_ACTIVE; else -> Tile.STATE_INACTIVE }
        tile.subtitle = when { !connected -> "연결 안 됨"; on -> "켜짐"; else -> "꺼짐" }
        tile.updateTile()
    }

    override fun onClick() {
        super.onClick()
        if (Repo.serviceRunning && Repo.live.value.link == LinkState.CONNECTED) {
            Repo.send(Cmd.ToggleCue)
        } else {
            val i = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (Build.VERSION.SDK_INT >= 34) {
                startActivityAndCollapse(PendingIntent.getActivity(this, 0, i, PendingIntent.FLAG_IMMUTABLE))
            } else {
                @Suppress("DEPRECATION", "StartActivityAndCollapseDeprecated")
                startActivityAndCollapse(i)
            }
        }
    }
}
