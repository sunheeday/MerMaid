package com.mermaid.passenger.service

import android.Manifest
import android.app.KeyguardManager
import android.app.Notification
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.mermaid.passenger.MerMaidApp
import com.mermaid.passenger.R
import com.mermaid.passenger.data.Cmd
import com.mermaid.passenger.data.LinkState
import com.mermaid.passenger.data.Live
import com.mermaid.passenger.data.Prefs
import com.mermaid.passenger.data.Repo
import com.mermaid.passenger.link.RfSession
import com.mermaid.passenger.link.Target
import com.mermaid.passenger.overlay.CueOverlay
import com.mermaid.passenger.ui.MainActivity
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch

/**
 * 포그라운드 서비스 (connectedDevice) — 앱 화면을 닫아도 차와의 연결·모션 큐를 유지한다 (APP-F-04).
 * 시작: 앱 실행(APP-F-03) · CDM 기기 감지(APP-F-02) · 재부팅 후 START_STICKY
 * 종료: 브리지가 범위를 벗어난 뒤 30초 · 10분 재연결 실패 · 사용자가 등록 해제 (APP-F-08)
 */
class LinkService : LifecycleService() {

    companion object {
        const val ACTION_START = "com.mermaid.passenger.START"
        const val ACTION_DEMO = "com.mermaid.passenger.DEMO"
        const val ACTION_TOGGLE_CUE = "com.mermaid.passenger.TOGGLE_CUE"
        const val ACTION_DEVICE_GONE = "com.mermaid.passenger.DEVICE_GONE"
        const val ACTION_DEVICE_BACK = "com.mermaid.passenger.DEVICE_BACK"
        const val ACTION_STOP = "com.mermaid.passenger.STOP"
        private const val NOTI_ID = 1

        /** 연결 시작 (포그라운드 서비스로) */
        fun start(ctx: Context, action: String = ACTION_START) {
            ctx.startForegroundService(Intent(ctx, LinkService::class.java).setAction(action))
        }

        /** 이미 돌고 있을 때만 전달 (사라짐 알림 등) */
        fun sendIfRunning(ctx: Context, action: String) {
            if (Repo.serviceRunning) ctx.startService(Intent(ctx, LinkService::class.java).setAction(action))
        }
    }

    private lateinit var rfThread: HandlerThread
    private lateinit var session: RfSession
    private lateinit var overlay: CueOverlay
    private val main = Handler(Looper.getMainLooper())
    private var screenOn = true
    private var unlocked = true

    override fun onCreate() {
        super.onCreate()
        Repo.serviceRunning = true
        goForeground()
        rfThread = HandlerThread("mermaid-rf").apply { start() }
        overlay = CueOverlay(this)
        session = RfSession(this, Handler(rfThread.looper), object : RfSession.Listener {
            override fun onMotion(ax: Float, ay: Float, pax: Float?, pay: Float?) = overlay.feed(ax, ay, pax, pay)
            override fun onSafeOff() { main.post { overlay.hideNow() } }
            override fun onGiveUp() { main.post { stopEverything() } }
        })

        readScreen()
        ContextCompat.registerReceiver(this, screenReceiver, IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_SCREEN_OFF); addAction(Intent.ACTION_USER_PRESENT)
        }, ContextCompat.RECEIVER_NOT_EXPORTED)

        lifecycleScope.launch {
            Repo.commands.collect { cmd ->
                when (cmd) {
                    is Cmd.Override -> session.requestOverride(cmd.dev, cmd.on)
                    Cmd.ToggleCue -> session.toggleCue()
                    Cmd.SendProfile -> session.sendProfile()
                    Cmd.RefreshScreen -> readScreen()
                    Cmd.Reconnect -> session.reconnect()
                    Cmd.DemoDrop -> session.demoDrop()
                }
            }
        }
        // 상태가 바뀌면 상주 알림·오버레이 갱신
        lifecycleScope.launch {
            combine(Repo.live, Prefs.settings) { l, s -> l to s }.distinctUntilChanged().collect { (l, s) ->
                updateOverlay(l, s)
                postNotification(l)
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        // startForegroundService 뒤에는 매번 5초 안에 startForeground가 필요
        if (!goForeground()) { stopSelf(); return START_NOT_STICKY }
        when (intent?.action ?: ACTION_START) {
            ACTION_START, ACTION_DEVICE_BACK -> {
                main.removeCallbacks(goneTimeout)
                val s = Prefs.settings.value
                val addr = s.bridgeAddress
                when {
                    addr == null -> { Repo.log('!', "등록된 차량 없음"); stopEverything() }
                    !hasBtPermission() -> { Repo.log('!', "블루투스 권한 없음"); stopEverything() }
                    else -> session.connect(Target.Ble(addr, s.bridgeName ?: addr))
                }
            }
            ACTION_DEMO -> session.connect(Target.Demo)
            ACTION_TOGGLE_CUE -> session.toggleCue()
            ACTION_DEVICE_GONE -> {
                Repo.log('·', "차량이 범위를 벗어남 — 30초 뒤 종료")
                main.removeCallbacks(goneTimeout); main.postDelayed(goneTimeout, 30_000)
            }
            ACTION_STOP -> stopEverything()
        }
        return START_STICKY
    }

    /** Android 14+: connectedDevice 유형은 블루투스 권한이나 CDM 등록이 있어야 함. 없으면 시작하지 않음 */
    private fun goForeground(): Boolean = try {
        startForeground(NOTI_ID, buildNotification(Repo.live.value), ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        true
    } catch (e: Exception) {
        Repo.log('!', "포그라운드 서비스 시작 실패: ${e.message}")
        false
    }

    private val goneTimeout = Runnable {
        if (Repo.live.value.link != LinkState.CONNECTED) stopEverything()
    }

    private fun stopEverything() {
        session.stop()
        overlay.hideNow()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        Repo.serviceRunning = false
        try { unregisterReceiver(screenReceiver) } catch (_: Exception) {}
        main.removeCallbacksAndMessages(null)
        session.stop()
        overlay.hideNow()
        rfThread.quitSafely()
        super.onDestroy()
    }

    private fun hasBtPermission() = checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED

    // ───────── 화면 상태 (APP-F-15, 20)
    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) = readScreen()
    }

    private fun readScreen() {
        screenOn = getSystemService(PowerManager::class.java).isInteractive
        unlocked = !getSystemService(KeyguardManager::class.java).isKeyguardLocked
        session.updateScreen(screenOn, unlocked && Settings.canDrawOverlays(this))
        main.post { updateOverlay(Repo.live.value, Prefs.settings.value) }
    }

    // ───────── 오버레이: Tier 3 ON ∧ 연결됨 ∧ 화면 켜짐 ∧ 잠금 해제 ∧ 권한
    private fun updateOverlay(l: Live, s: com.mermaid.passenger.data.Settings) {
        val want = l.t3 && l.link == LinkState.CONNECTED && screenOn && unlocked && Settings.canDrawOverlays(this)
        when {
            want -> overlay.show(s)
            l.link != LinkState.CONNECTED || !screenOn || !unlocked -> overlay.hideNow()
            else -> overlay.hide()
        }
    }

    // ───────── 상주 알림 (화면정의서 9쪽 상태별 문구, APP-F-21·24)
    private fun postNotification(l: Live) {
        if (NotificationManagerCompat.from(this).areNotificationsEnabled()) {
            try { NotificationManagerCompat.from(this).notify(NOTI_ID, buildNotification(l)) } catch (_: SecurityException) {}
        }
    }

    private fun buildNotification(l: Live): Notification {
        val s = Prefs.settings.value
        val car = s.bridgeName ?: "차량"
        val overlayOk = Settings.canDrawOverlays(this)
        val tiers = "송풍 ${if (l.t1) "켜짐" else "꺼짐"} · 앰비언트 ${if (l.t2) "켜짐" else "꺼짐"}"
        val (title, text) = when {
            l.link == LinkState.CONNECTED && !overlayOk -> "점을 표시할 수 없어요" to "\"다른 앱 위에 표시\"를 허용해 주세요"
            l.link == LinkState.CONNECTED && l.t3 -> "모션 큐가 켜져 있어요" to "$tiers · $car"
            l.link == LinkState.CONNECTED && (l.t1 || l.t2) -> "차량과 연결됨" to "$tiers · $car"
            l.link == LinkState.CONNECTED -> "차량과 연결됨" to "편안한 주행이에요 · $car"
            l.link == LinkState.LOST -> "연결이 끊겼어요" to "안전을 위해 점을 껐어요 · 다시 연결 중"
            l.link == LinkState.CONNECTING -> "차와 연결하는 중" to car
            else -> "MerMaid" to "대기 중"
        }
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val b = NotificationCompat.Builder(this, MerMaidApp.CHANNEL_LINK)
            .setSmallIcon(R.drawable.ic_cue)
            .setColor(0xFFA50034.toInt())
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setPublicVersion(
                NotificationCompat.Builder(this, MerMaidApp.CHANNEL_LINK).setSmallIcon(R.drawable.ic_cue)
                    .setContentTitle(if (l.link == LinkState.CONNECTED) "차량과 연결됨" else "MerMaid").build()
            )
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
        if (l.link == LinkState.CONNECTED && overlayOk) {
            val toggle = PendingIntent.getService(
                this, 1, Intent(this, LinkService::class.java).setAction(ACTION_TOGGLE_CUE), PendingIntent.FLAG_IMMUTABLE,
            )
            val cueOn = l.pending[s.devId] ?: l.t3
            b.addAction(0, if (cueOn) "모션 큐 끄기" else "모션 큐 켜기", toggle)
        }
        if (l.link == LinkState.CONNECTED && !overlayOk) {
            val grant = PendingIntent.getActivity(
                this, 2, Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, android.net.Uri.parse("package:$packageName"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE,
            )
            b.addAction(0, "허용하기", grant)
        }
        b.addAction(0, "앱 열기", open)
        return b.build()
    }
}
