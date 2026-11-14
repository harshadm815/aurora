package app.aurora.companion

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import android.widget.Switch
import android.view.MotionEvent
import kotlin.concurrent.thread
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.text.InputFilter
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.Executors

/** AURORA on the phone: pairing over USB, then Chat, Today, PC and Memories. */
class MainActivity : Activity(), Live.Listener {
    private lateinit var aurora: Aurora
    private val work = Executors.newSingleThreadExecutor()
    private val dp by lazy { resources.displayMetrics.density }

    private lateinit var routeLabel: TextView
    private lateinit var content: FrameLayout
    private lateinit var tabs: LinearLayout
    private var tab = "Chat"

    // Chat
    private var chatList: LinearLayout? = null
    private var chatScroll: ScrollView? = null
    private var chatLoaded = false
    private val recorder = Recorder()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        aurora = Aurora.get(this)
        LinkService.channels(this)
        Calls.register(this)

        val title = text("AURORA", 22f, R.color.accent, bold = true).apply { letterSpacing = 0.25f }
        routeLabel = text("", 13f, R.color.muted).apply { gravity = Gravity.END }
        val callButton = text("📞", 22f, R.color.accent).apply {
            contentDescription = "Call AURORA"
            setPadding(px(16), 0, 0, 0)
            setOnClickListener { callAurora() }
        }
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(px(16), px(14), px(16), px(10))
            addView(title, LinearLayout.LayoutParams(0, -2, 1f))
            addView(routeLabel)
            addView(callButton)
        }
        content = FrameLayout(this)
        tabs = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(getColor(R.color.panel))
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(header)
            addView(content, LinearLayout.LayoutParams(-1, 0, 1f))
            addView(tabs, LinearLayout.LayoutParams(-1, -2))
        })
        show()
    }

    override fun onResume() {
        super.onResume()
        Live.visible = true
        Live.add(this)
        if (aurora.paired) {
            refreshRoute()
            LinkService.start(this)
        }
    }

    override fun onPause() {
        Live.visible = false
        Live.remove(this)
        super.onPause()
    }

    /** Something said on the PC appears here too (the phone's own messages are already shown). */
    override fun onEvent(event: org.json.JSONObject) {
        if (event.optString("kind") != "message" || event.optString("source") != "pc") return
        ui {
            if (chatList == null || !chatLoaded) return@ui
            bubble("user", "🖥 " + event.optString("text"))
            bubble("assistant", event.optString("reply"))
        }
    }

    override fun onRoute(base: String?) = ui { routeLabel.text = if (aurora.paired) Protocol.routeName(base) else "Not paired" }

    private fun callAurora() {
        if (!aurora.paired) return
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 4)
            return
        }
        Calls.dial(this)?.let { problem ->
            AlertDialog.Builder(this).setMessage(problem).setPositiveButton("OK", null).show()
        }
    }

    override fun onRequestPermissionsResult(code: Int, permissions: Array<out String>, results: IntArray) {
        if (code == 4 && results.firstOrNull() == PackageManager.PERMISSION_GRANTED) callAurora()
    }

    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED
        ) requestPermissions(arrayOf("android.permission.POST_NOTIFICATIONS"), 1)
    }

    private fun batteryFree(): Boolean =
        getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)

    @android.annotation.SuppressLint("BatteryLife")
    private fun askBatteryFree() {
        try {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        } catch (error: Exception) {
            startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
        }
    }

    override fun onDestroy() {
        work.shutdownNow()
        super.onDestroy()
    }

    // ------------------------------------------------------------------ layout helpers

    private fun px(value: Int) = (value * dp).toInt()

    private fun text(value: String, size: Float = 15f, color: Int = R.color.text, bold: Boolean = false) =
        TextView(this).apply {
            text = value
            textSize = size
            setTextColor(getColor(color))
            if (bold) typeface = Typeface.DEFAULT_BOLD
        }

    private fun card(background: Int = R.color.panel) = GradientDrawable().apply {
        cornerRadius = 14 * dp
        setColor(getColor(background))
    }

    private fun page(vararg views: View): ScrollView = ScrollView(this).apply {
        addView(LinearLayout(this@MainActivity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(16), px(8), px(16), px(16))
            views.forEach { addView(it, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = px(10) }) }
        })
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        isAllCaps = false
        setOnClickListener { onClick() }
    }

    private fun background(task: () -> Unit) = work.execute {
        try {
            task()
        } catch (error: Exception) {
            // Each task reports its own errors; this only keeps the worker alive.
        }
    }

    private fun ui(task: () -> Unit) = runOnUiThread { if (!isFinishing) task() }

    // ------------------------------------------------------------------ screens

    private fun show() {
        content.removeAllViews()
        tabs.removeAllViews()
        if (!aurora.paired) {
            routeLabel.text = "Not paired"
            content.addView(pairScreen())
            tabs.visibility = View.GONE
            return
        }
        tabs.visibility = View.VISIBLE
        for (name in listOf("Chat", "Today", "PC", "Memories")) {
            tabs.addView(text(name, 15f, if (name == tab) R.color.accent else R.color.muted, bold = name == tab).apply {
                gravity = Gravity.CENTER
                setPadding(0, px(14), 0, px(14))
                setOnClickListener { tab = name; show() }
            }, LinearLayout.LayoutParams(0, -2, 1f))
        }
        content.addView(when (tab) {
            "Today" -> todayScreen()
            "PC" -> pcScreen()
            "Memories" -> memoriesScreen()
            else -> chatScreen()
        })
    }

    private fun refreshRoute() = background {
        val state = try { aurora.get("/api/status"); aurora.route } catch (error: IOException) { null }
        ui {
            routeLabel.text = if (aurora.paired) Protocol.routeName(state) else "Not paired"
            if (!aurora.paired) show()
        }
    }

    private fun pairScreen(): View {
        val status = text("", 14f, R.color.muted)
        val code = EditText(this).apply {
            hint = "Code from the PC"
            textSize = 24f
            letterSpacing = 0.2f
            gravity = Gravity.CENTER
            setTextColor(getColor(R.color.text))
            setHintTextColor(getColor(R.color.muted))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS or
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            filters = arrayOf(InputFilter.LengthFilter(9))
        }
        val name = EditText(this).apply {
            setText(Build.MODEL ?: "Phone")
            setTextColor(getColor(R.color.text))
            filters = arrayOf(InputFilter.LengthFilter(40))
        }
        val pair = button("Pair with my PC") {}
        pair.setOnClickListener {
            pair.isEnabled = false
            status.setTextColor(getColor(R.color.muted))
            status.text = "Pairing…"
            val typed = code.text.toString()
            val label = name.text.toString().ifBlank { "Phone" }
            background {
                val problem = try { aurora.pair(typed, label); null } catch (error: IOException) {
                    error.message ?: "Couldn't pair."
                } catch (error: Exception) {
                    "Couldn't make the phone's key: ${error.message}"
                }
                ui {
                    pair.isEnabled = true
                    if (problem == null) {
                        tab = "Chat"; chatLoaded = false; show(); refreshRoute()
                        askForNotifications()
                        LinkService.start(this)
                    } else {
                        status.setTextColor(getColor(R.color.warn))
                        status.text = if (problem.startsWith("Can't reach"))
                            "Can't reach AURORA over the cable. Plug the phone in, keep USB debugging on, and " +
                                "switch on Settings → Phone on the PC." else problem
                    }
                }
            }
        }
        return page(
            text("Pair this phone with your PC", 20f, bold = true),
            text("1. Plug the phone into the PC with the USB cable.\n" +
                "2. On the PC: AURORA → Settings → Phone → Pair my phone…\n" +
                "3. Type the code it shows here.\n\n" +
                "Pairing works only over the cable, and only one phone can be paired. This phone makes a key " +
                "in its secure hardware that never leaves it; AURORA will accept only requests signed with it.",
                14f, R.color.muted),
            code, text("Name of this phone", 13f, R.color.muted), name, pair, status
        )
    }

    // Chat ---------------------------------------------------------------

    private fun chatScreen(): View {
        val list = chatList ?: LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(12), px(4), px(12), px(8))
        }.also { chatList = it }
        (list.parent as? android.view.ViewGroup)?.removeView(list)
        val scroll = ScrollView(this).apply { addView(list); isFillViewport = true }
        chatScroll = scroll
        val input = EditText(this).apply {
            hint = "Message AURORA"
            setTextColor(getColor(R.color.text))
            setHintTextColor(getColor(R.color.muted))
            maxLines = 4
            imeOptions = EditorInfo.IME_ACTION_SEND
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES or
                InputType.TYPE_TEXT_FLAG_MULTI_LINE
        }
        val send = button("Send") {}
        val talk = button("🎤") {}.apply {
            contentDescription = "Hold to talk"
            setOnTouchListener { view, motion ->
                when (motion.action) {
                    MotionEvent.ACTION_DOWN -> { startTalking(input); true }
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                        view.performClick(); stopTalking(input, send); true
                    }
                    else -> false
                }
            }
        }
        val sendNow = {
            val message = input.text.toString().trim()
            if (message.isNotEmpty()) {
                input.setText("")
                sendChat(message, send)
            }
        }
        send.setOnClickListener { sendNow() }
        input.setOnEditorActionListener { _, action, _ ->
            if (action == EditorInfo.IME_ACTION_SEND) { sendNow(); true } else false
        }
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(px(8), px(4), px(8), px(4))
            addView(talk)
            addView(input, LinearLayout.LayoutParams(0, -2, 1f))
            addView(send)
        }
        if (!chatLoaded) loadHistory()
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
            addView(bar)
        }
    }

    private fun bubble(role: String, message: String): TextView {
        val mine = role == "user"
        val view = text(message, 15f, if (role == "note") R.color.muted else R.color.text).apply {
            background = card(if (mine) R.color.mine else R.color.panel)
            setPadding(px(12), px(9), px(12), px(9))
            setTextIsSelectable(true)
        }
        chatList?.addView(view, LinearLayout.LayoutParams(-2, -2).apply {
            gravity = if (mine) Gravity.END else Gravity.START
            topMargin = px(6)
            if (mine) leftMargin = px(48) else rightMargin = px(48)
        })
        chatScroll?.post { chatScroll?.fullScroll(View.FOCUS_DOWN) }
        return view
    }

    private fun loadHistory() {
        chatLoaded = true
        background {
            val result = try { aurora.get("/api/history") } catch (error: IOException) { null }
            ui {
                chatList?.removeAllViews()
                if (result == null || !result.ok) {
                    bubble("note", result?.error ?: "Can't reach your PC right now. Messages will send when it's back.")
                    chatLoaded = false
                    return@ui
                }
                val messages = result.json.optJSONArray("messages")
                if (messages == null || messages.length() == 0) bubble("note", "Say hello to AURORA.")
                for (i in 0 until (messages?.length() ?: 0)) {
                    val m = messages!!.getJSONObject(i)
                    bubble(m.optString("role"), m.optString("text"))
                }
                routeLabel.text = Protocol.routeName(aurora.route)
            }
        }
    }

    private fun startTalking(input: EditText) {
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 2)
            return
        }
        try {
            recorder.start()
            input.hint = "Listening… let go to send"
            input.setHintTextColor(getColor(R.color.accent))
        } catch (error: Exception) {
            bubble("note", error.message ?: "The microphone isn't available.")
        }
    }

    private fun stopTalking(input: EditText, send: Button) {
        if (!recorder.active) return
        val wav = recorder.stop()
        input.hint = "Message AURORA"
        input.setHintTextColor(getColor(R.color.muted))
        if (wav.isEmpty()) {
            bubble("note", "Hold the 🎤 button while you speak.")
            return
        }
        val heard = bubble("user", "🎤 …")
        send.isEnabled = false
        background {
            val (text, problem) = try {
                val reply = aurora.hear(wav)
                if (reply.ok) reply.json.optString("heard") to null else null to reply.error
            } catch (error: IOException) {
                null to (error.message ?: "Couldn't reach your PC.")
            }
            ui {
                send.isEnabled = true
                when {
                    text == null -> { heard.text = "🎤 " + problem; heard.setTextColor(getColor(R.color.warn)) }
                    text.isBlank() -> heard.text = "🎤 (I didn't catch that. Hold the button and try again.)"
                    else -> {
                        (heard.parent as? android.view.ViewGroup)?.removeView(heard)
                        sendChat(text, send, spoken = true)
                    }
                }
            }
        }
    }

    private fun sendChat(message: String, send: Button, spoken: Boolean = false) {
        bubble("user", if (spoken) "🎤 $message" else message)
        val thinking = bubble("assistant", "…")
        send.isEnabled = false
        background {
            val (reply, problem) = try {
                val r = aurora.post("/api/chat", JSONObject().put("text", message), timeoutMs = 300_000)
                if (r.ok) r.json to null else null to r.error
            } catch (error: IOException) {
                null to (error.message ?: "Couldn't reach your PC.")
            }
            ui {
                send.isEnabled = true
                routeLabel.text = Protocol.routeName(aurora.route)
                if (reply != null) {
                    val sources = reply.optString("sources", "")
                    thinking.text = reply.optString("reply") + if (sources.isNotBlank()) "\n\n$sources" else ""
                    if (spoken) {                                // you spoke, so AURORA answers aloud
                        val answer = reply.optString("reply")
                        thread(name = "aurora-say") { Speech.say(this, answer) }
                    }
                } else {
                    thinking.setTextColor(getColor(R.color.warn))
                    thinking.text = problem
                    if (!aurora.paired) show()
                }
            }
        }
    }

    // Today, PC, Memories --------------------------------------------------

    private fun loading(title: String, load: (LinearLayout) -> Unit): View {
        val body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        body.addView(text("Loading…", 14f, R.color.muted))
        val refresh = button("Refresh") { body.removeAllViews(); body.addView(text("Loading…", 14f, R.color.muted)); load(body) }
        load(body)
        return page(text(title, 20f, bold = true), body, refresh)
    }

    private fun section(body: LinearLayout, heading: String, value: String) {
        body.addView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = card()
            setPadding(px(14), px(12), px(14), px(12))
            addView(text(heading, 13f, R.color.muted))
            addView(text(value, 15f).apply { setTextIsSelectable(true) })
        }, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = px(10) })
    }

    private fun fill(body: LinearLayout, path: String, render: (JSONObject) -> Unit) = background {
        val result = try { aurora.get(path, timeoutMs = 60_000) } catch (error: IOException) { null }
        ui {
            body.removeAllViews()
            routeLabel.text = Protocol.routeName(aurora.route)
            when {
                result == null -> body.addView(text("Can't reach your PC. Plug in the cable, or be on your home " +
                    "Wi-Fi with AURORA open.", 14f, R.color.warn))
                !result.ok -> body.addView(text(result.error, 14f, R.color.warn))
                else -> render(result.json)
            }
            if (!aurora.paired) show()
        }
    }

    private fun todayScreen() = loading("Today") { body ->
        fill(body, "/api/today") { data ->
            section(body, "Briefing", data.optString("briefing"))
            val reminders = data.optJSONArray("reminders")
            val lines = (0 until (reminders?.length() ?: 0)).joinToString("\n") {
                val r = reminders!!.getJSONObject(it)
                r.optString("due").replace("T", " ") + "  " + r.optString("text")
            }
            section(body, "Coming up", lines.ifBlank { "No reminders waiting." })
            val earlier = data.optJSONArray("earlier")
            if (earlier != null && earlier.length() > 0) {
                section(body, "Earlier today (already reminded)", (0 until earlier.length()).joinToString("\n") {
                    val r = earlier.getJSONObject(it)
                    r.optString("due").substringAfter("T") + "  " + r.optString("text")
                })
            }
        }
    }

    private fun pcScreen() = loading("Your PC") { body ->
        fill(body, "/api/status") { data ->
            section(body, "Connected by", Protocol.routeName(aurora.route))
            section(body, "AURORA", "${data.optString("ai")} · model ${data.optString("model")} · version ${data.optString("version")}")
            val cpu = data.optDouble("cpu", Double.NaN)
            val ram = data.optDouble("ram", Double.NaN)
            section(body, "CPU and memory", (if (cpu.isNaN()) "CPU –" else "CPU ${cpu.toInt()}%") +
                "  ·  " + (if (ram.isNaN()) "RAM –" else "RAM ${ram.toInt()}%"))
            section(body, "This phone", "${aurora.deviceName}\nKey kept in " +
                (if (PhoneKey.inHardware()) "the phone's secure hardware" else "Android's Keystore") + ".")
            body.addView(Switch(this).apply {
                text = "Stay connected in the background"
                textSize = 15f
                setTextColor(getColor(R.color.text))
                isChecked = LinkService.enabled(this@MainActivity)
                setPadding(0, px(6), 0, px(6))
                setOnCheckedChangeListener { _, on -> LinkService.setEnabled(this@MainActivity, on) }
            })
            body.addView(Switch(this).apply {
                text = "Show reminders full screen (also when locked)"
                textSize = 15f
                setTextColor(getColor(R.color.text))
                isChecked = LinkService.option(this@MainActivity, "reminder_screen")
                setPadding(0, px(6), 0, px(6))
                setOnCheckedChangeListener { _, on -> LinkService.setOption(this@MainActivity, "reminder_screen", on) }
            })
            body.addView(Switch(this).apply {
                text = "Reminders ring as a call (answer to talk)"
                textSize = 15f
                setTextColor(getColor(R.color.text))
                isChecked = LinkService.option(this@MainActivity, "reminder_call", false)
                setPadding(0, px(6), 0, px(6))
                setOnCheckedChangeListener { _, on -> LinkService.setOption(this@MainActivity, "reminder_call", on) }
            })
            body.addView(Switch(this).apply {
                text = "AURORA says reminders aloud"
                textSize = 15f
                setTextColor(getColor(R.color.text))
                isChecked = LinkService.option(this@MainActivity, "speak_reminders")
                setPadding(0, px(6), 0, px(6))
                setOnCheckedChangeListener { _, on -> LinkService.setOption(this@MainActivity, "speak_reminders", on) }
            })
            body.addView(text("Reminders and replies arrive as notifications even when the app is closed. Android " +
                "shows a quiet \"AURORA\" notification while it's connected.", 13f, R.color.muted))
            if (batteryFree()) {
                body.addView(text("✓ Allowed to run in the background.", 14f, R.color.ok).apply { setPadding(0, px(10), 0, 0) })
            } else {
                body.addView(button("Allow AURORA to run in the background") { askBatteryFree() })
            }
            body.addView(text("On OPPO (ColorOS) also: Settings → Battery → App battery management → AURORA → " +
                "allow background activity and auto launch. Otherwise the phone may close AURORA to save " +
                "battery and notifications stop until you open it.", 13f, R.color.muted).apply {
                setPadding(0, px(6), 0, px(14))
            })
            body.addView(button("Sign out of AURORA") { confirmSignOut() })
        }
    }

    private fun confirmSignOut() {
        AlertDialog.Builder(this)
            .setTitle("Sign out?")
            .setMessage("This phone's key is deleted and the PC forgets it. To use AURORA again, pair over the USB cable.")
            .setPositiveButton("Sign out") { _, _ ->
                LinkService.stop(this)
                background {
                    aurora.signOut()
                    ui { chatList = null; chatLoaded = false; show() }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun memoriesScreen() = loading("Memories") { body ->
        fill(body, "/api/memories") { data ->
            val memories = data.optJSONArray("memories")
            if (memories == null || memories.length() == 0) body.addView(text("Nothing remembered yet.", 14f, R.color.muted))
            for (i in 0 until (memories?.length() ?: 0)) {
                val m = memories!!.getJSONObject(i)
                section(body, m.optString("category"), m.optString("value"))
            }
        }
    }
}
