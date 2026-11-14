package app.aurora.companion

import android.app.Activity
import android.app.NotificationManager
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread

/**
 * The reminder screen: opens over the lock screen and turns the screen on when a reminder is due
 * (a full-screen notification). Done, or "In 10 minutes" (asks AURORA to remind you again).
 * Unlocking isn't needed for either; nothing else of the app is shown while locked.
 */
class ReminderActivity : Activity() {
    companion object {
        const val TEXT = "text"
        const val NOTIFICATION = "notification"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        show(intent.getStringExtra(TEXT) ?: "")
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        show(intent.getStringExtra(TEXT) ?: "")
    }

    private fun show(reminder: String) {
        val dp = resources.displayMetrics.density
        fun px(v: Int) = (v * dp).toInt()
        val status = TextView(this).apply {
            textSize = 14f
            setTextColor(getColor(R.color.muted))
            gravity = Gravity.CENTER
        }
        fun button(label: String, onClick: (Button) -> Unit) = Button(this).apply {
            text = label
            isAllCaps = false
            textSize = 18f
            setOnClickListener { onClick(this) }
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(px(28), px(28), px(28), px(28))
            addView(TextView(context).apply {
                text = "⏰"
                textSize = 64f
                gravity = Gravity.CENTER
            })
            addView(TextView(context).apply {
                text = "Reminder · " + SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date())
                textSize = 16f
                letterSpacing = 0.1f
                setTextColor(getColor(R.color.accent))
                gravity = Gravity.CENTER
                setPadding(0, px(12), 0, px(8))
            })
            addView(TextView(context).apply {
                text = reminder
                textSize = 28f
                typeface = Typeface.DEFAULT_BOLD
                setTextColor(getColor(R.color.text))
                gravity = Gravity.CENTER
                setPadding(0, 0, 0, px(36))
            })
            addView(button("Done") { finishReminder() }, LinearLayout.LayoutParams(-1, -2))
            addView(button("Remind me in 10 minutes") { snooze(reminder, it, status) },
                LinearLayout.LayoutParams(-1, -2).apply { topMargin = px(12) })
            addView(status, LinearLayout.LayoutParams(-1, -2).apply { topMargin = px(16) })
        })
    }

    private fun snooze(reminder: String, button: Button, status: TextView) {
        button.isEnabled = false
        status.text = "Asking AURORA…"
        thread(name = "aurora-snooze") {
            val problem = try {
                val reply = Aurora.get(this).post("/api/chat", JSONObject().put("text", Protocol.snooze(reminder)),
                    timeoutMs = 120_000)
                if (reply.ok) null else reply.error
            } catch (error: IOException) {
                error.message
            }
            runOnUiThread {
                if (problem == null) finishReminder()
                else { status.text = problem; button.isEnabled = true }
            }
        }
    }

    private fun finishReminder() {
        val id = intent.getIntExtra(NOTIFICATION, 0)
        if (id != 0) getSystemService(NotificationManager::class.java).cancel(id)
        Speech.stopReminder()
        finish()
    }
}
