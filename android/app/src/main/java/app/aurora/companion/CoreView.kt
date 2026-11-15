package app.aurora.companion

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.os.SystemClock
import android.view.View
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/** What the core shows; each has its own movement (see [CoreView]). */
enum class CoreState { IDLE, CONNECTING, LISTENING, THINKING, SPEAKING, MUTED, RECONNECTING, DISCONNECTED }

/** Maps the PC's call states (aurora/mobile/call.py) to what the core shows. */
object CoreStates {
    fun of(callState: String, muted: Boolean, connected: Boolean): CoreState = when {
        !connected && callState == "reconnecting" -> CoreState.RECONNECTING
        callState == "ended" || callState == "disconnected" -> CoreState.DISCONNECTED
        callState in setOf("ringing", "calling", "connecting", "") -> CoreState.CONNECTING
        muted -> CoreState.MUTED
        callState == "listening" -> CoreState.LISTENING
        callState == "thinking" -> CoreState.THINKING
        callState == "speaking" -> CoreState.SPEAKING
        else -> CoreState.IDLE
    }

    /** The words under the core. */
    fun label(state: CoreState): String = when (state) {
        CoreState.IDLE -> "Connected"
        CoreState.CONNECTING -> "Connecting…"
        CoreState.LISTENING -> "Listening"
        CoreState.THINKING -> "Thinking…"
        CoreState.SPEAKING -> "Speaking"
        CoreState.MUTED -> "Muted: AURORA can't hear you"
        CoreState.RECONNECTING -> "Reconnecting…"
        CoreState.DISCONNECTED -> "Disconnected"
    }
}

/**
 * AURORA's core: a glowing orb with rings. Animates only while visible (and at most ~30 frames a
 * second), so it costs nothing in the background. [level] (0-1) is the sound right now: your voice
 * while listening, AURORA's while speaking.
 */
class CoreView(context: Context, private val p: Ui.Palette) : View(context) {
    var state: CoreState = CoreState.IDLE
        set(value) { if (field != value) { field = value; changedAt = SystemClock.uptimeMillis(); invalidate() } }
    var level: () -> Float = { 0f }

    private var shown = 0f                         // smoothed level
    private var changedAt = SystemClock.uptimeMillis()
    private var lastFrame = 0L
    private var visibleNow = false
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val glow = Paint(Paint.ANTI_ALIAS_FLAG)
    private val arc = RectF()
    private var glowSize = 0f
    private var glowColor = 0

    init {
        contentDescription = "AURORA"
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
    }

    override fun onVisibilityAggregated(isVisible: Boolean) {
        super.onVisibilityAggregated(isVisible)
        visibleNow = isVisible
        if (isVisible) invalidate()
    }

    private fun colour(): Int = when (state) {
        CoreState.DISCONNECTED -> p.danger
        CoreState.RECONNECTING, CoreState.MUTED -> p.warn
        CoreState.THINKING -> p.accent2
        else -> p.accent
    }

    override fun onDraw(canvas: Canvas) {
        val now = SystemClock.uptimeMillis()
        val t = now / 1000f
        val cx = width / 2f
        val cy = height / 2f
        val r = min(width, height) / 2f * 0.92f
        val c = colour()
        val target = when (state) { CoreState.LISTENING, CoreState.SPEAKING -> level().coerceIn(0f, 1f); else -> 0f }
        shown += (target - shown) * 0.35f

        // Glow behind the orb (the gradient is made again only when size or colour change).
        if (glowSize != r || glowColor != c) {
            glow.shader = RadialGradient(cx, cy, r, intArrayOf(Ui.withAlpha(c, 0.35f), Ui.withAlpha(c, 0.08f), 0),
                floatArrayOf(0f, 0.55f, 1f), Shader.TileMode.CLAMP)
            glowSize = r; glowColor = c
        }
        canvas.drawCircle(cx, cy, r, glow)

        val breathe = when (state) {
            CoreState.IDLE, CoreState.MUTED -> 0.03f * sin(t * 1.6f)
            CoreState.THINKING -> 0.06f * sin(t * 5f)
            CoreState.LISTENING, CoreState.SPEAKING -> 0.18f * shown
            else -> 0f
        }
        val orb = r * (0.36f + breathe)
        fill.shader = RadialGradient(cx - orb * 0.3f, cy - orb * 0.3f, orb * 1.4f,
            intArrayOf(Ui.withAlpha(0xFFFFFFFF.toInt(), 0.95f), c, Ui.withAlpha(c, 0.55f)),
            floatArrayOf(0f, 0.45f, 1f), Shader.TileMode.CLAMP)
        fill.alpha = if (state == CoreState.MUTED || state == CoreState.DISCONNECTED) 140 else 255
        canvas.drawCircle(cx, cy, orb, fill)

        ring.color = c
        when (state) {
            CoreState.CONNECTING, CoreState.RECONNECTING -> {          // slowly turning rings
                val speed = if (state == CoreState.RECONNECTING) 160f else 60f
                for (i in 0 until 3) {
                    val rr = r * (0.55f + i * 0.13f)
                    arc.set(cx - rr, cy - rr, cx + rr, cy + rr)
                    ring.strokeWidth = r * 0.018f
                    ring.alpha = 200 - i * 50
                    canvas.drawArc(arc, (t * speed * (if (i % 2 == 0) 1 else -1)) + i * 40f, 110f, false, ring)
                    canvas.drawArc(arc, (t * speed * (if (i % 2 == 0) 1 else -1)) + i * 40f + 180f, 60f, false, ring)
                }
            }
            CoreState.LISTENING -> {                                     // waves that follow your voice
                for (i in 0 until 3) {
                    val phase = ((t * 0.9f + i / 3f) % 1f)
                    val rr = orb + (r - orb) * phase * (0.55f + shown)
                    ring.strokeWidth = r * 0.012f
                    ring.alpha = ((1f - phase) * (90 + 165 * shown)).toInt().coerceIn(0, 255)
                    canvas.drawCircle(cx, cy, rr, ring)
                }
            }
            CoreState.THINKING -> {                                      // a pulsing net of points
                val count = 10
                ring.strokeWidth = r * 0.006f
                for (i in 0 until count) {
                    val a = (i * 2 * PI / count + t * 0.4).toFloat()
                    val pulse = 0.5f + 0.5f * sin(t * 3f + i)
                    val rr = r * (0.62f + 0.06f * pulse)
                    val x = cx + rr * cos(a); val y = cy + rr * sin(a)
                    val b = (a + (2 * PI / count) * 3).toFloat()
                    ring.alpha = (60 + 120 * pulse).toInt()
                    canvas.drawLine(x, y, cx + rr * cos(b), cy + rr * sin(b), ring)
                    fill.shader = null; fill.color = c; fill.alpha = (120 + 135 * pulse).toInt()
                    canvas.drawCircle(x, y, r * 0.025f, fill)
                }
            }
            CoreState.SPEAKING -> {                                      // a waveform around the orb
                val bars = 48
                ring.strokeWidth = r * 0.02f
                ring.alpha = 230
                for (i in 0 until bars) {
                    val a = (i * 2 * PI / bars).toFloat()
                    val wobble = 0.5f + 0.5f * sin(t * 9f + i * 0.7f) * sin(t * 4f + i * 0.31f)
                    val len = r * (0.06f + 0.30f * shown * wobble + 0.03f * wobble)
                    val r0 = orb + r * 0.08f
                    canvas.drawLine(cx + r0 * cos(a), cy + r0 * sin(a), cx + (r0 + len) * cos(a), cy + (r0 + len) * sin(a), ring)
                }
            }
            CoreState.IDLE, CoreState.MUTED -> {                         // calm: one slow ring
                ring.strokeWidth = r * 0.01f
                ring.alpha = 110
                canvas.drawCircle(cx, cy, r * (0.6f + 0.02f * sin(t)), ring)
            }
            CoreState.DISCONNECTED -> {
                ring.strokeWidth = r * 0.014f
                ring.alpha = 200
                arc.set(cx - r * 0.6f, cy - r * 0.6f, cx + r * 0.6f, cy + r * 0.6f)
                for (k in 0 until 8) canvas.drawArc(arc, k * 45f, 25f, false, ring)
            }
        }

        // Next frame only while visible and while something moves.
        if (visibleNow && isAttachedToWindow && state != CoreState.DISCONNECTED) {
            val wait = max(0L, 33L - (now - lastFrame))
            lastFrame = now
            postInvalidateDelayed(wait)
        }
    }
}

/** A bar waveform of the last few seconds of sound levels ([push] 0-1, about ten a second). */
class WaveView(context: Context, private val p: Ui.Palette) : View(context) {
    private val levels = FloatArray(40)
    private var at = 0
    private val bar = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeCap = Paint.Cap.ROUND }
    var color: Int = p.accent

    init { importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO }

    fun push(level: Float) {
        levels[at] = level.coerceIn(0f, 1f)
        at = (at + 1) % levels.size
        invalidate()
    }

    fun clear() { levels.fill(0f); invalidate() }

    override fun onDraw(canvas: Canvas) {
        val n = levels.size
        val step = width / n.toFloat()
        bar.strokeWidth = step * 0.55f
        val mid = height / 2f
        for (i in 0 until n) {
            val v = levels[(at + i) % n]
            val h = max(height * 0.04f, v * height * 0.9f) / 2f
            bar.color = color
            bar.alpha = (90 + 165 * (i / n.toFloat())).toInt()
            val x = step * i + step / 2
            canvas.drawLine(x, mid - h, x, mid + h, bar)
        }
    }
}

/** Loudness 0-1 of 16-bit PCM (rough, for the animations only). */
object Levels {
    fun of(pcm: ByteArray, length: Int = pcm.size): Float {
        if (length < 2) return 0f
        var sum = 0.0
        var count = 0
        var i = 0
        while (i + 1 < length) {
            val sample = (pcm[i].toInt() and 0xff) or (pcm[i + 1].toInt() shl 8)
            sum += sample.toDouble() * sample
            count++
            i += 2
        }
        val rms = kotlin.math.sqrt(sum / count) / 32768.0
        return (rms * 6).coerceIn(0.0, 1.0).toFloat()           // speech is quiet in absolute terms
    }
}
