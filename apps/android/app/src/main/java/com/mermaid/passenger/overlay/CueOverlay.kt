package com.mermaid.passenger.overlay

import android.content.Context
import android.graphics.PixelFormat
import android.hardware.input.InputManager
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.WindowManager
import com.mermaid.passenger.data.Repo
import com.mermaid.passenger.data.Settings as AppSettings

/**
 * 타 앱 위 전체 화면 투명 창 (APP-F-10~15).
 * - 입력을 받지 않음: FLAG_NOT_TOUCHABLE | NOT_FOCUSABLE → 터치는 아래 앱(YouTube 등)으로
 * - Android 12+ '신뢰할 수 없는 터치' 차단을 피하려고 창 alpha ≤ InputManager 최대값(0.8)
 * - 펀치홀·시스템 바 영역까지 덮고(cutout ALWAYS, fitInsetsTypes 0), 점 배치에서 inset을 피함
 * 모든 메서드는 메인 스레드에서, feed()만 아무 스레드에서.
 */
class CueOverlay(private val ctx: Context) {
    private val wm = ctx.getSystemService(WindowManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var view: MotionDotsView? = null
    private val removeCheck = Runnable { view?.let { if (it.faded) detach() } }

    val visible get() = view != null

    fun show(s: AppSettings) {
        if (!Settings.canDrawOverlays(ctx)) return
        val v = view ?: MotionDotsView(ctx).also { nv ->
            try {
                wm.addView(nv, params(s))
                view = nv
            } catch (e: Exception) {
                Repo.log('!', "오버레이 표시 실패: ${e.message}")
                return
            }
        }
        v.gainDp = s.cueGainDp.toFloat(); v.amber = s.cueAmber; v.showPill = s.cuePill
        val lp = v.layoutParams as? WindowManager.LayoutParams
        val alpha = maxAlpha(s)
        if (lp != null && lp.alpha != alpha) { lp.alpha = alpha; wm.updateViewLayout(v, lp) }
        main.removeCallbacks(removeCheck)
        v.setOn(true)
    }

    /** 일반 OFF: 0.3초 페이드 후 창 제거 */
    fun hide() {
        val v = view ?: return
        v.setOn(false)
        main.removeCallbacks(removeCheck)
        main.postDelayed(removeCheck, 450)
    }

    /** 끊김·권한 해제: 즉시 제거 (NFR-01 ≤ 100 ms) */
    fun hideNow() {
        view?.hideNow()
        detach()
    }

    fun feed(ax: Float, ay: Float, pax: Float?, pay: Float?) { view?.feed(ax, ay, pax, pay) }

    private fun detach() {
        val v = view ?: return
        view = null
        try { wm.removeViewImmediate(v) } catch (_: Exception) {}
    }

    private fun maxAlpha(s: AppSettings): Float {
        val limit = ctx.getSystemService(InputManager::class.java)?.maximumObscuringOpacityForTouch ?: 0.8f
        return s.cueAlpha.coerceIn(0.3f, limit)
    }

    private fun params(s: AppSettings) = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
        PixelFormat.TRANSLUCENT,
    ).apply {
        layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
        fitInsetsTypes = 0
        alpha = maxAlpha(s)
        title = "MerMaidMotionCue"
    }
}
