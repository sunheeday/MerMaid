package com.mermaid.passenger.overlay

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.view.Choreographer
import android.view.View
import android.view.WindowInsets
import com.mermaid.passenger.protocol.Proto
import kotlin.math.exp
import kotlin.math.max

/**
 * 시각 모션 큐 (화면정의서 2장 OVL 규격).
 * 점 지름 10 dp, 흰색 + 1 dp 어두운 테두리. 세로: 좌우 각 2열 × 8행 + 상하 각 1행 × 5개 / 가로: 2열 × 5행 + 7개.
 * 가장자리 18 dp, 열 간격 16 dp. 상태 표시줄·펀치홀·제스처 바는 피함(현재 보이는 inset 기준 → 전체 화면 동영상이면 끝까지).
 * 이동 = 관성 방향: ay+(왼쪽 가속) → 오른쪽, ax+(가속) → 아래. pax·pay가 있으면 30% 섞음. ±3 m/s²에서 최대 gain dp.
 * 프레임마다 보간(Choreographer) → 120 Hz 화면에서도 부드럽게. 꺼지면 0.3초 페이드, 다 사라지면 그리기 중지.
 */
@SuppressLint("ViewConstructor")
class MotionDotsView(ctx: Context) : View(ctx), Choreographer.FrameCallback {
    private val dp = resources.displayMetrics.density

    // RF 스레드가 쓰고 UI 스레드가 읽음
    @Volatile private var targetX = 0f
    @Volatile private var targetY = 0f
    @Volatile private var lastFeed = 0L

    private var curX = 0f
    private var curY = 0f
    private var alphaNow = 0f
    private var on = false
    private var running = false
    private var lastFrameNs = 0L
    private var pillUntil = 0L

    var gainDp = 24f
    var amber = false
    var showPill = true

    private var insetL = 0; private var insetT = 0; private var insetR = 0; private var insetB = 0

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 1f * dp }
    private val pillBg = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(210, 30, 27, 29) }
    private val pillText = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE; textSize = 13f * dp; isFakeBoldText = true }
    private val pillDot = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(255, 181, 71) }
    private val rect = RectF()

    init { setLayerType(LAYER_TYPE_HARDWARE, null) }

    fun feed(ax: Float, ay: Float, pax: Float?, pay: Float?) {
        targetX = if (pay != null) 0.7f * ay + 0.3f * pay else ay
        targetY = if (pax != null) 0.7f * ax + 0.3f * pax else ax
        lastFeed = SystemClock.uptimeMillis()
    }

    /** UI 스레드에서 */
    fun setOn(value: Boolean) {
        if (value && !on && showPill) pillUntil = SystemClock.uptimeMillis() + 1500
        on = value
        if (!running) { running = true; lastFrameNs = 0L; Choreographer.getInstance().postFrameCallback(this) }
    }

    /** 끊김 등: 페이드 없이 바로 지움 */
    fun hideNow() { on = false; alphaNow = 0f; invalidate() }

    val faded get() = !on && alphaNow < 0.01f

    override fun onApplyWindowInsets(insets: WindowInsets): WindowInsets {
        val i = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
        insetL = i.left; insetT = i.top; insetR = i.right; insetB = i.bottom
        invalidate()
        return insets
    }

    override fun doFrame(frameTimeNanos: Long) {
        val dt = if (lastFrameNs == 0L) 0.016f else ((frameTimeNanos - lastFrameNs) / 1e9f).coerceIn(0f, 0.1f)
        lastFrameNs = frameTimeNanos
        if (SystemClock.uptimeMillis() - lastFeed > Proto.MOT_STALE_MS) { targetX = 0f; targetY = 0f } // MOT 끊김 → 가운데로
        val k = 1f - exp(-dt * 6f)
        curX += (targetX - curX) * k
        curY += (targetY - curY) * k
        val fade = 1f - exp(-dt / 0.3f * 3f) // 약 0.3초
        alphaNow += ((if (on) 1f else 0f) - alphaNow) * fade
        invalidate()
        if (on || alphaNow > 0.01f) Choreographer.getInstance().postFrameCallback(this)
        else { running = false; alphaNow = 0f; invalidate() }
    }

    override fun onDetachedFromWindow() {
        Choreographer.getInstance().removeFrameCallback(this); running = false
        super.onDetachedFromWindow()
    }

    override fun onDraw(c: Canvas) {
        if (alphaNow < 0.01f) return
        val w = width.toFloat(); val h = height.toFloat()
        val landscape = w > h
        val r = 5f * dp
        val edge = 18f * dp
        val gap = 16f * dp
        val maxOff = gainDp * dp
        val dx = (curX.coerceIn(-3f, 3f) / 3f) * maxOff
        val dy = (curY.coerceIn(-3f, 3f) / 3f) * maxOff
        val a = (alphaNow * 255).toInt()
        fill.color = if (amber) Color.argb(a, 255, 181, 71) else Color.argb(a, 255, 255, 255)
        ring.color = Color.argb((alphaNow * 140).toInt(), 0, 0, 0)

        val left = insetL + edge; val right = w - insetR - edge
        val top = insetT + edge; val bottom = h - insetB - edge
        val rows = if (landscape) 5 else 8
        val across = if (landscape) 7 else 5

        fun dot(x: Float, y: Float) {
            val px = (x + dx).coerceIn(r + 2 * dp, w - r - 2 * dp)
            val py = (y + dy).coerceIn(r + 2 * dp, h - r - 2 * dp)
            c.drawCircle(px, py, r, fill); c.drawCircle(px, py, r, ring)
        }

        // 좌우 2열 (두 번째 열은 반 칸 어긋나게)
        val y0 = top + 16 * dp; val y1 = bottom - 16 * dp
        for (col in 0..1) {
            for (i in 0 until rows) {
                val t = if (col == 0) i / (rows - 1f) else (i + 0.5f) / rows
                val y = y0 + (y1 - y0) * t
                if (y > y1 + 1) continue
                dot(left + col * gap, y); dot(right - col * gap, y)
            }
        }
        // 상하 1행
        for (i in 1..across) {
            val x = left + (right - left) * i / (across + 1f)
            dot(x, top); dot(x, bottom)
        }

        // 시작 안내 필 (APP-F-17)
        val now = SystemClock.uptimeMillis()
        if (now < pillUntil) {
            val pa = ((pillUntil - now) / 300f).coerceAtMost(1f) * alphaNow
            val text = "모션 큐 시작"
            val tw = pillText.measureText(text)
            val ph = 32 * dp; val pw = tw + 40 * dp
            val cx = w / 2; val cy = insetT + 12 * dp + ph / 2 + max(0f, 4 * dp)
            rect.set(cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2)
            pillBg.alpha = (210 * pa).toInt(); pillText.alpha = (255 * pa).toInt(); pillDot.alpha = (255 * pa).toInt()
            c.drawRoundRect(rect, ph / 2, ph / 2, pillBg)
            c.drawCircle(rect.left + 16 * dp, cy, 4 * dp, pillDot)
            c.drawText(text, rect.left + 28 * dp, cy + pillText.textSize * 0.35f, pillText)
        }
    }
}
