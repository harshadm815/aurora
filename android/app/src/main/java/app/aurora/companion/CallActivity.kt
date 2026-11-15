package app.aurora.companion

import android.app.Activity
import android.app.KeyguardManager
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.telecom.CallAudioState
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.CheckBox
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread

/**
 * AURORA Call: ringing (Answer / Decline), the call itself (the core showing what AURORA is doing,
 * duration, waveform, live transcript, Mute, audio output, End) and the after-call screen (summary,
 * points, tasks, transcript, save to memory). Works over the lock screen; the after-call screen shows
 * nothing from the call until the phone is unlocked.
 */
class CallActivity : Activity(), Calls.Listener {
    companion object {
        const val INCOMING = "incoming"
    }

    private lateinit var kit: Kit
    private val p get() = kit.p
    private val ticker = Handler(Looper.getMainLooper())

    private lateinit var root: FrameLayout
    private lateinit var core: CoreView
    private lateinit var wave: WaveView
    private lateinit var stateLabel: TextView
    private lateinit var timeLabel: TextView
    private lateinit var linkLabel: TextView
    private lateinit var lastLine: TextView
    private lateinit var transcriptBox: LinearLayout
    private lateinit var transcriptScroll: ScrollView
    private lateinit var ringingRow: LinearLayout
    private lateinit var callRow: LinearLayout
    private lateinit var muteButton: TextView
    private lateinit var routeButton: TextView
    private lateinit var transcriptButton: TextView
    private lateinit var muteCaption: TextView
    private lateinit var routeCaption: TextView
    private var showTranscript = false
    private var shownTurns = -1
    private var summaryShown = false

    private val tick = object : Runnable {
        override fun run() {
            val listening = Calls.state == "listening" && !Calls.muted
            wave.color = if (Calls.state == "speaking") p.accent2 else p.accent
            wave.push(if (listening) Calls.micLevel else if (Calls.state == "speaking") Calls.voiceLevel else 0f)
            if (Calls.connectedAt > 0 && Calls.connection != null)
                timeLabel.text = CallSummary.duration(SystemClock.elapsedRealtime() - Calls.connectedAt)
            ticker.postDelayed(this, 100)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) { setShowWhenLocked(true); setTurnScreenOn(true) }
        else @Suppress("DEPRECATION") window.addFlags(
            WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        kit = Kit(this)
        window.decorView.setBackgroundColor(p.background)
        window.statusBarColor = p.background
        window.navigationBarColor = p.background
        root = FrameLayout(this)
        setContentView(root)
        build()
    }

    // ------------------------------------------------------------------ the call

    private fun build() {
        root.removeAllViews()
        val landscape = resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
        core = CoreView(this, p).apply { level = { if (Calls.state == "speaking") Calls.voiceLevel else Calls.micLevel } }
        wave = WaveView(this, p)
        stateLabel = kit.text("", Ui.HEADING, p.accent, Ui.medium).apply {
            gravity = Gravity.CENTER
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        timeLabel = kit.text("", Ui.SMALL, p.muted).apply { gravity = Gravity.CENTER }
        linkLabel = kit.text("", Ui.SMALL, p.muted).apply { gravity = Gravity.CENTER }
        lastLine = kit.text("", Ui.BODY, p.text).apply { gravity = Gravity.CENTER; maxLines = 3 }
        transcriptBox = kit.column(gap = 6)
        transcriptScroll = ScrollView(this).apply { addView(transcriptBox); visibility = View.GONE }

        ringingRow = kit.row(
            kit.round("✕", "Decline", 68, p.danger, 0xFFFFFFFF.toInt()) { Calls.connection?.onReject(); finish() },
            View(this).apply { layoutParams = LinearLayout.LayoutParams(kit.px(72), 1) },
            kit.round("✆", "Answer", 68, p.ok, 0xFFFFFFFF.toInt()) { answer() },
        ).apply { gravity = Gravity.CENTER }
        muteButton = kit.round("◉", "Mute", 60) { Calls.mute(!Calls.muted) }
        routeButton = kit.round("◖", "Audio output", 60) { chooseRoute() }
        transcriptButton = kit.round("☰", "Show transcript", 60) {
            showTranscript = !showTranscript
            shownTurns = -1
            onCallChanged()
        }
        val end = kit.round("✆", "End call", 68, p.danger, 0xFFFFFFFF.toInt()) { Calls.hangUp() }.apply { rotation = 135f }
        muteCaption = caption("Mute")
        routeCaption = caption("Phone")
        callRow = kit.row(
            labelled(muteButton, muteCaption), labelled(routeButton, routeCaption),
            labelled(transcriptButton, caption("Transcript")), labelled(end, caption("End")),
            gap = 14,
        ).apply { gravity = Gravity.CENTER }

        val header = kit.column(
            kit.text("AURORA", Ui.TITLE, p.text, Ui.medium).apply { gravity = Gravity.CENTER; letterSpacing = 0.3f },
            kit.row(timeLabel, linkLabel, gap = 12).apply { gravity = Gravity.CENTER },
            gap = 2,
        )
        val coreSize = if (landscape) kit.px(200) else (resources.displayMetrics.widthPixels * 0.72f).toInt()
        val centre = kit.column(
            FrameLayout(this).apply { addView(core, FrameLayout.LayoutParams(coreSize, coreSize, Gravity.CENTER)) },
            stateLabel,
            wave.apply { layoutParams = LinearLayout.LayoutParams(-1, kit.px(40)) },
            gap = 10,
        )
        val lower = kit.column(lastLine, transcriptScroll.apply {
            layoutParams = LinearLayout.LayoutParams(-1, kit.px(if (landscape) 120 else 180))
        }, gap = 8)
        val controls = kit.column(ringingRow, callRow, gap = 0)

        if (landscape) {
            root.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(kit.px(24), kit.px(16), kit.px(24), kit.px(16))
                addView(centre, LinearLayout.LayoutParams(0, -1, 1f).apply { gravity = Gravity.CENTER_VERTICAL })
                addView(kit.column(header, lower, controls, gap = 16), LinearLayout.LayoutParams(0, -2, 1.2f).apply {
                    gravity = Gravity.CENTER_VERTICAL; leftMargin = kit.px(24)
                })
            })
        } else {
            root.addView(LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(kit.px(24), kit.px(28), kit.px(24), kit.px(28))
                addView(header)
                addView(centre, LinearLayout.LayoutParams(-1, 0, 1f).apply { topMargin = kit.px(12) })
                addView(lower, LinearLayout.LayoutParams(-1, -2))
                addView(controls, LinearLayout.LayoutParams(-1, -2).apply { topMargin = kit.px(20) })
            })
        }
    }

    private fun caption(value: String) = kit.text(value, Ui.CAPTION, p.muted).apply { gravity = Gravity.CENTER }

    /** A round call button with its name underneath. */
    private fun labelled(button: View, caption: TextView): View = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER_HORIZONTAL
        addView(button)
        addView(caption, LinearLayout.LayoutParams(kit.px(76), -2).apply { topMargin = kit.px(6) })
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        if (!summaryShown) { build(); onCallChanged() }
    }

    override fun onResume() {
        super.onResume()
        Calls.listener = this
        onCallChanged()
        ticker.post(tick)
    }

    override fun onPause() {
        ticker.removeCallbacks(tick)
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
        else if (code == 3) stateLabel.text = "AURORA needs the microphone to talk with you."
    }

    private fun routeName(route: Int): String = when (route) {
        CallAudioState.ROUTE_SPEAKER -> "Speaker"
        CallAudioState.ROUTE_BLUETOOTH -> "Bluetooth"
        CallAudioState.ROUTE_WIRED_HEADSET -> "Headset"
        else -> "Phone"
    }

    private fun routeSymbol(route: Int): String = when (route) {
        CallAudioState.ROUTE_SPEAKER -> "◗"
        CallAudioState.ROUTE_BLUETOOTH -> "ᛒ"
        CallAudioState.ROUTE_WIRED_HEADSET -> "Ω"
        else -> "◖"
    }

    /** Speaker on/off, or a choice when a Bluetooth or wired headset is there too. */
    private fun chooseRoute() {
        val choices = listOf(CallAudioState.ROUTE_EARPIECE, CallAudioState.ROUTE_SPEAKER,
            CallAudioState.ROUTE_BLUETOOTH, CallAudioState.ROUTE_WIRED_HEADSET).filter { Calls.routes and it != 0 }
        if (choices.size <= 2) {
            Calls.useRoute(if (Calls.route == CallAudioState.ROUTE_SPEAKER) choices.first { it != CallAudioState.ROUTE_SPEAKER }
                           else CallAudioState.ROUTE_SPEAKER)
            return
        }
        android.app.AlertDialog.Builder(this)
            .setTitle("Audio output")
            .setItems(choices.map { (if (it == Calls.route) "✓ " else "   ") + routeName(it) }.toTypedArray()) { _, which ->
                Calls.useRoute(choices[which])
            }
            .show()
    }

    override fun onCallChanged() {
        if (summaryShown) return
        val call = Calls.connection
        val ringingNow = call != null && call.incoming && Calls.state == "ringing"
        ringingRow.visibility = if (ringingNow) View.VISIBLE else View.GONE
        callRow.visibility = if (call != null && !ringingNow) View.VISIBLE else View.GONE
        val coreState = if (call == null) CoreState.DISCONNECTED
                        else CoreStates.of(Calls.state, Calls.muted, Calls.linked)
        core.state = coreState
        stateLabel.text = when {
            call == null -> Calls.ended.ifBlank { "Call ended" }
            ringingNow -> "is calling you…"
            else -> CoreStates.label(coreState)
        }
        stateLabel.setTextColor(when (coreState) {
            CoreState.DISCONNECTED -> p.danger
            CoreState.MUTED, CoreState.RECONNECTING -> p.warn
            else -> p.accent
        })
        linkLabel.text = if (call != null && Calls.linked) "· " + Protocol.routeName(Aurora.get(this).route) else ""
        if (Calls.connectedAt == 0L) timeLabel.text = if (ringingNow) "Incoming call" else ""
        muteButton.text = if (Calls.muted) "⦸" else "◉"
        muteCaption.text = if (Calls.muted) "Unmute" else "Mute"
        routeCaption.text = routeName(Calls.route)
        muteButton.contentDescription = if (Calls.muted) "Unmute" else "Mute"
        muteButton.background = kit.shape(if (Calls.muted) Ui.withAlpha(p.warn, 0.3f) else p.raised, 30f)
        routeButton.text = routeSymbol(Calls.route)
        routeButton.contentDescription = "Audio output: ${routeName(Calls.route)}"
        transcriptButton.background = kit.shape(if (showTranscript) Ui.withAlpha(p.accent, 0.25f) else p.raised, 30f)

        val turns = Calls.transcript.turns()
        transcriptScroll.visibility = if (showTranscript) View.VISIBLE else View.GONE
        lastLine.visibility = if (showTranscript) View.GONE else View.VISIBLE
        if (showTranscript && turns.size != shownTurns) {
            shownTurns = turns.size
            transcriptBox.removeAllViews()
            if (turns.isEmpty()) transcriptBox.addView(kit.note("What you and AURORA say appears here."))
            for (turn in turns) transcriptBox.addView(transcriptLine(turn))
            transcriptScroll.post { transcriptScroll.fullScroll(View.FOCUS_DOWN) }
        }
        lastLine.text = when {
            Calls.state == "listening" && Calls.heard.endsWith("…") -> Calls.heard
            Calls.reply.isNotBlank() -> Calls.reply
            else -> ""
        }
        if (call == null && !isFinishing) ticker.postDelayed({ if (Calls.connection == null) afterCall() }, 900)
    }

    private fun transcriptLine(turn: Turn): View {
        val mine = turn.speaker == "you"
        return kit.text((if (mine) "You  " else "AURORA  ") + turn.text, Ui.SMALL, if (mine) p.muted else p.text).apply {
            setPadding(kit.px(12), kit.px(8), kit.px(12), kit.px(8))
            background = kit.shape(if (mine) p.raised else Ui.withAlpha(p.accent, 0.10f), 12f)
        }
    }

    // ------------------------------------------------------------------ after the call

    private fun afterCall() {
        if (summaryShown || isFinishing) return
        summaryShown = true
        ticker.removeCallbacks(tick)
        val duration = if (Calls.connectedAt > 0) SystemClock.elapsedRealtime() - Calls.connectedAt else 0L
        val summary = CallSummary(Calls.transcript.turns(), duration)
        Calls.connectedAt = 0L
        if (summary.turns.isEmpty()) {
            // Nothing was said (declined, missed, or cut off): just say how it ended.
            root.removeAllViews()
            root.addView(kit.column(
                kit.text("Call ended", Ui.TITLE, p.text, Ui.medium).apply { gravity = Gravity.CENTER },
                kit.text(Calls.ended.ifBlank { "" }, Ui.BODY, p.muted).apply { gravity = Gravity.CENTER },
                gap = 8,
            ), FrameLayout.LayoutParams(-1, -2, Gravity.CENTER))
            ticker.postDelayed({ finish() }, 2500)
            return
        }
        val keyguard = getSystemService(KeyguardManager::class.java)
        if (keyguard.isKeyguardLocked) {
            root.removeAllViews()
            root.addView(kit.column(
                kit.text("Call ended · ${CallSummary.spokenDuration(duration)}", Ui.TITLE, p.text, Ui.medium)
                    .apply { gravity = Gravity.CENTER },
                kit.text("Unlock the phone to see the summary.", Ui.BODY, p.muted).apply { gravity = Gravity.CENTER },
                kit.primary("Unlock") {
                    if (Build.VERSION.SDK_INT >= 26) keyguard.requestDismissKeyguard(this,
                        object : KeyguardManager.KeyguardDismissCallback() {
                            override fun onDismissSucceeded() { showSummary(summary) }
                        })
                },
                kit.secondary("Close") { finish() },
                gap = 14,
            ).apply { setPadding(kit.px(32), 0, kit.px(32), 0) }, FrameLayout.LayoutParams(-1, -2, Gravity.CENTER))
            return
        }
        showSummary(summary)
    }

    private fun showSummary(summary: CallSummary) {
        root.removeAllViews()
        val status = kit.note("")
        val picks = mutableListOf<Pair<CheckBox, String>>()
        fun list(title: String, items: List<String>, empty: String) = kit.card(
            kit.overline(title),
            *(if (items.isEmpty()) arrayOf<View>(kit.note(empty))
              else items.map { kit.text("•  $it", Ui.BODY) }.toTypedArray()),
        )
        val saveCard = kit.card(kit.overline("Save to AURORA's memory"),
            kit.note("Nothing from a call is remembered unless you pick it here."))
        for (candidate in (summary.memoryCandidates + summary.points).distinct().take(8)) {
            val box = CheckBox(this).apply {
                text = candidate
                textSize = Ui.BODY
                setTextColor(p.text)
                buttonTintList = android.content.res.ColorStateList.valueOf(p.accent)
            }
            picks += box to candidate
            saveCard.addView(box)
        }
        if (picks.isEmpty()) saveCard.addView(kit.note("Nothing that looks worth remembering."))
        else saveCard.addView(kit.secondary("Save selected", p.accent) { saveMemories(picks, status) })

        val transcriptCard = kit.card(kit.overline("Transcript"),
            kit.note("Kept on this phone only until you close this screen."))
        val transcriptLines = kit.column(*summary.turns.map { transcriptLine(it) }.toTypedArray(), gap = 6).apply {
            visibility = View.GONE
        }
        transcriptCard.addView(kit.secondary("Show transcript (${summary.turns.size} lines)") {
            transcriptLines.visibility = if (transcriptLines.visibility == View.GONE) View.VISIBLE else View.GONE
        })
        transcriptCard.addView(transcriptLines)
        transcriptCard.addView(kit.secondary("Save transcript to the PC") { saveTranscript(summary, status) })

        root.addView(kit.page(
            kit.column(
                kit.text("Call ended", Ui.DISPLAY, p.text, Ui.light),
                kit.text("${CallSummary.spokenDuration(summary.durationMs)} · ${summary.exchanges} " +
                    (if (summary.exchanges == 1) "exchange" else "exchanges") +
                    (if (Calls.ended.isNotBlank() && Calls.ended != "Call ended") " · ${Calls.ended}" else ""),
                    Ui.SMALL, p.muted),
                gap = 2,
            ),
            list("What AURORA said", summary.points, "Nothing to note."),
            list("Tasks", summary.tasks, "No tasks or reminders asked for."),
            list("Follow up", summary.followUps, "Nothing left open."),
            saveCard,
            transcriptCard,
            status,
            kit.primary("Done") { Calls.transcript.clear(); finish() },
        ))
    }

    private fun saveMemories(picks: List<Pair<CheckBox, String>>, status: TextView) {
        val chosen = picks.filter { it.first.isChecked }.map { it.second }
        if (chosen.isEmpty()) { status.text = "Tick what to save first."; return }
        status.text = "Saving…"
        thread(name = "aurora-save-memory") {
            var saved = 0
            var problem: String? = null
            for (value in chosen) {
                try {
                    val reply = Aurora.get(this).post("/api/memories", JSONObject().put("value", value))
                    if (reply.ok) saved++ else { problem = reply.error; break }
                } catch (error: IOException) { problem = error.message; break }
            }
            runOnUiThread {
                status.setTextColor(if (problem == null) p.ok else p.warn)
                status.text = problem ?: "Saved $saved to AURORA's memory."
                if (problem == null) picks.forEach { if (it.first.isChecked) it.first.isEnabled = false }
            }
        }
    }

    private fun saveTranscript(summary: CallSummary, status: TextView) {
        val stamp = SimpleDateFormat("yyyy-MM-dd HHmm", Locale.getDefault()).format(Date())
        val bytes = summary.text("AURORA call, $stamp").toByteArray(Charsets.UTF_8)
        status.text = "Saving…"
        thread(name = "aurora-save-transcript") {
            val message = try {
                val open = { ByteArrayInputStream(bytes) }
                val (sha, size) = Files.checksum(open)
                val reply = Aurora.get(this).upload("AURORA call $stamp.txt", size, sha, open)
                if (reply.ok) "Saved on the PC in Documents › From phone as ${reply.json.optString("saved")}." else reply.error
            } catch (error: IOException) { error.message ?: "Couldn't reach your PC." }
            runOnUiThread { status.setTextColor(p.muted); status.text = message }
        }
    }

    override fun onDestroy() {
        ticker.removeCallbacksAndMessages(null)
        super.onDestroy()
    }
}
