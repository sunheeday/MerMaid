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
import android.widget.RemoteViews
import android.widget.Toast
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
import com.mermaid.passenger.protocol.Proto
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
        const val ACTION_SYM = "com.mermaid.passenger.SYM"   // 알림에서 고른 멀미 점수 (EXTRA_MISC)
        const val EXTRA_MISC = "misc"
        private const val NOTI_ID = 1
        private const val NOTI_SYM = 2
        private val SYM_CELLS = intArrayOf(
            R.id.sym_0, R.id.sym_1, R.id.sym_2, R.id.sym_3, R.id.sym_4, R.id.sym_5,
            R.id.sym_6, R.id.sym_7, R.id.sym_8, R.id.sym_9, R.id.sym_10,
        )

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
                    is Cmd.Symptom -> session.sendSymptom(cmd.misc)
                }
            }
        }
        // 상태가 바뀌면 상주 알림·오버레이 갱신
        lifecycleScope.launch {
            combine(Repo.live, Prefs.settings) { l, s -> l to s }.distinctUntilChanged().collect { (l, s) ->
                updateOverlay(l, s)
                postNotification(l)
                updateSymPrompt(l, s)
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
            ACTION_SYM -> {
                NotificationManagerCompat.from(this).cancel(NOTI_SYM)
                val misc = intent?.getIntExtra(EXTRA_MISC, -1) ?: -1
                val ok = Repo.live.value.link == LinkState.CONNECTED && Prefs.settings.value.logConsent
                when {
                    Repo.live.value.link == LinkState.IDLE -> stopEverything() // 연결이 없는데 남은 알림을 눌러 깨어난 경우
                    misc !in Proto.SYM_MIN..Proto.SYM_MAX -> Unit
                    ok -> { session.sendSymptom(misc); Toast.makeText(this, "멀미 $misc 기록됨", Toast.LENGTH_SHORT).show() }
                    else -> Toast.makeText(this, "차와 연결되지 않아 기록하지 못했어요", Toast.LENGTH_SHORT).show()
                }
            }
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
        stopSymPrompt()
        session.stop()
        overlay.hideNow()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        Repo.serviceRunning = false
        try { unregisterReceiver(screenReceiver) } catch (_: Exception) {}
        main.removeCallbacksAndMessages(null)
        NotificationManagerCompat.from(this).cancel(NOTI_SYM)
        session.stop()
        overlay.hideNow()
        rfThread.quitSafely()
        super.onDestroy()
    }

    private fun hasBtPermission() = checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED

    // ───────── 화면 상태 (APP-F-15, 20)
    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) {
            readScreen(userPresent = i.action == Intent.ACTION_USER_PRESENT)
            // 잠금 해제 직후에는 isKeyguardLocked가 아직 true일 수 있다. 한 번만 읽으면 att=false로 굳어
            // 컨트롤러의 Tier 3 게이트가 닫힌 채로 남는다 (실기에서 확인) → 조금 뒤 두 번 더 읽는다
            if (i.action != Intent.ACTION_SCREEN_OFF) {
                main.postDelayed({ readScreen() }, 500)
                main.postDelayed({ readScreen() }, 2000)
            }
        }
    }

    private fun readScreen(userPresent: Boolean = false) {
        screenOn = getSystemService(PowerManager::class.java).isInteractive
        unlocked = userPresent || !getSystemService(KeyguardManager::class.java).isKeyguardLocked
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

    // ───────── 멀미 점수 묻기 (M08 SYM): 주행 중 ∧ 동의 켜짐일 때 SYM_PROMPT_MS마다 알림
    private var symPromptOn = false
    private var symSeenAt: Long? = null

    private val symPrompt = object : Runnable {
        override fun run() {
            postSymPrompt()
            main.postDelayed(this, Proto.SYM_PROMPT_MS)
        }
    }

    /**
     * 조건: 연결됨(HELLO 뒤, 끊김·안전 OFF 아님) ∧ 이번 연결에서 INT를 받음 ∧ 주행 기록 수집 동의.
     * 조건이 깨지면 알림을 지우고 멈춘다. 점수를 보내면 그때부터 다시 잰다.
     * 응답이 없으면 아무것도 보내지 않는다 (이전 값 다시 보내기 없음).
     */
    private fun updateSymPrompt(l: Live, s: com.mermaid.passenger.data.Settings) {
        val want = l.link == LinkState.CONNECTED && l.intSeen && s.logConsent
        val answered = l.symAt != symSeenAt
        symSeenAt = l.symAt
        if (want == symPromptOn && !answered) return
        stopSymPrompt()
        symPromptOn = want
        if (want) main.postDelayed(symPrompt, Proto.SYM_PROMPT_MS)
    }

    private fun stopSymPrompt() {
        symPromptOn = false
        main.removeCallbacks(symPrompt)
        NotificationManagerCompat.from(this).cancel(NOTI_SYM)
    }

    /** 누르면 홈의 "지금 멀미 정도" 카드가 열리고, 펼치면 알림에서 바로 0~10을 고를 수 있다 */
    private fun postSymPrompt() {
        val nm = NotificationManagerCompat.from(this)
        if (!nm.areNotificationsEnabled()) return
        val flags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val open = PendingIntent.getActivity(
            this, 3, Intent(this, MainActivity::class.java).putExtra(MainActivity.EXTRA_SYM, true), flags,
        )
        val title = "지금 멀미 정도를 알려 주세요"
        val big = RemoteViews(packageName, R.layout.notif_sym).apply {
            setTextViewText(R.id.sym_title, title)
            setTextViewText(R.id.sym_guide, "0 없음 · 1 약간 불편 · 2~5 어지러움·더움·두통 · 6~9 메스꺼움 · 10 구토")
            SYM_CELLS.forEachIndexed { n, id ->
                setTextViewText(id, n.toString())
                setOnClickPendingIntent(id, PendingIntent.getService(
                    this@LinkService, 100 + n,
                    Intent(this@LinkService, LinkService::class.java).setAction(ACTION_SYM).putExtra(EXTRA_MISC, n), flags,
                ))
            }
        }
        val n = NotificationCompat.Builder(this, MerMaidApp.CHANNEL_SYM)
            .setSmallIcon(R.drawable.ic_cue)
            .setColor(0xFFA50034.toInt())
            .setContentTitle(title)
            .setContentText("눌러서 0(없음)~10(구토) 중에 골라 주세요")
            .setContentIntent(open)
            .setStyle(NotificationCompat.DecoratedCustomViewStyle())
            .setCustomBigContentView(big)
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setTimeoutAfter(Proto.SYM_PROMPT_MS)
            .build()
        try { nm.notify(NOTI_SYM, n) } catch (_: SecurityException) {}
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
