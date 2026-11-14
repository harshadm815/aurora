package app.aurora.companion

import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/** The call screen: ringing (Answer / Decline) or in a call (what was said, Mute, Speaker, Hang up). */
class CallActivity : Activity(), Calls.Listener {
    companion object {
        const val INCOMING = "incoming"
    }

    private lateinit var status: TextView
    private lateinit var heard: TextView
    private lateinit var reply: TextView
    private lateinit var ringing: LinearLayout
    private lateinit var talking: LinearLayout
    private var muted = false
    private var speaker = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) { setShowWhenLocked(true); setTurnScreenOn(true) }
        else @Suppress("DEPRECATION") window.addFlags(
            WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val dp = resources.displayMetrics.density
        fun px(v: Int) = (v * dp).toInt()
        fun line(size: Float, color: Int, bold: Boolean = false) = TextView(this).apply {
            textSize = size; setTextColor(getColor(color)); gravity = Gravity.CENTER
            if (bold) typeface = Typeface.DEFAULT_BOLD
        }
        fun button(label: String, onClick: (Button) -> Unit) = Button(this).apply {
            text = label; isAllCaps = false; textSize = 17f; setOnClickListener { onClick(this) }
        }
        status = line(16f, R.color.accent)
        heard = line(16f, R.color.muted).apply { setPadding(0, px(24), 0, px(8)) }
        reply = line(19f, R.color.text).apply { setPadding(0, px(8), 0, px(24)) }
        ringing = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(button("Decline") { Calls.connection?.onReject(); finish() }, LinearLayout.LayoutParams(0, -2, 1f))
            addView(button("Answer") { answer() }, LinearLayout.LayoutParams(0, -2, 1f).apply { leftMargin = px(16) })
        }
        talking = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(button("Mute") { muted = !muted; Calls.setMuted(muted); it.text = if (muted) "Unmute" else "Mute" },
                LinearLayout.LayoutParams(0, -2, 1f))
            addView(button("Speaker") { speaker = !speaker; Calls.setSpeaker(speaker); it.text = if (speaker) "Earpiece" else "Speaker" },
                LinearLayout.LayoutParams(0, -2, 1f).apply { leftMargin = px(8) })
            addView(button("Hang up") { Calls.hangUp() }, LinearLayout.LayoutParams(0, -2, 1f).apply { leftMargin = px(8) })
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(px(24), px(24), px(24), px(24))
            addView(line(56f, R.color.accent).apply { text = "◉" })
            addView(line(30f, R.color.text, bold = true).apply { text = "AURORA"; letterSpacing = 0.2f })
            addView(status)
            addView(heard)
            addView(reply)
            addView(ringing, LinearLayout.LayoutParams(-1, -2))
            addView(talking, LinearLayout.LayoutParams(-1, -2))
        })
    }

    override fun onResume() {
        super.onResume()
        Calls.listener = this
        onCallChanged()
    }

    override fun onPause() {
        if (Calls.listener === this) Calls.listener = null
        super.onPause()
    }

    private fun answer() {
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 3)
            return
        }
        Calls.answer(this)
    }

    override fun onRequestPermissionsResult(code: Int, permissions: Array<out String>, results: IntArray) {
        if (code == 3 && results.firstOrNull() == PackageManager.PERMISSION_GRANTED) Calls.answer(this)
        else if (code == 3) { status.text = "AURORA needs the microphone to talk with you."; }
    }

    override fun onCallChanged() {
        val call = Calls.connection
        val ringingNow = call != null && call.incoming && Calls.state == "ringing"
        ringing.visibility = if (ringingNow) View.VISIBLE else View.GONE
        talking.visibility = if (call != null && !ringingNow) View.VISIBLE else View.GONE
        status.text = when {
            call == null -> Calls.ended.ifBlank { "Call ended" }
            ringingNow -> "is calling you…"
            else -> when (Calls.state) {
                "calling", "connecting" -> "Connecting…"
                "listening" -> "Listening"
                "thinking" -> "Thinking…"
                "speaking" -> "Speaking"
                else -> Calls.state
            }
        }
        heard.text = if (Calls.heard.isNotBlank()) "You: ${Calls.heard}" else ""
        reply.text = Calls.reply
        if (call == null) status.postDelayed({ if (Calls.connection == null) finish() }, 1500)
    }
}
