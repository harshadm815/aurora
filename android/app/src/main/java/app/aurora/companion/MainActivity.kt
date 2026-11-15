package app.aurora.companion

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.InputFilter
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/**
 * AURORA on the phone: pairing over USB, then Home · Chat · Call · Memory · Control.
 * Chat is typed conversation; Call (its own screen, CallActivity) is spoken conversation.
 */
class MainActivity : Activity(), Live.Listener {
    companion object {
        private const val PICK_FILE = 7
        private const val MIC_FOR_CALL = 4
        private const val QUEUED = "⏳ Kept on this phone. AURORA gets it as soon as your PC is back, and the reply comes as a notification."
    }

    private lateinit var aurora: Aurora
    private lateinit var kit: Kit
    private val p get() = kit.p
    private val work = Executors.newSingleThreadExecutor()

    private lateinit var pageTitle: TextView
    private lateinit var routePill: TextView
    private lateinit var content: FrameLayout
    private lateinit var nav: LinearLayout
    private var tab = "home"

    // Chat
    private var chatList: LinearLayout? = null
    private var chatScroll: ScrollView? = null
    private var chatLoaded = false
    private var chatInput: EditText? = null

    // Memory
    private var memories: JSONArray? = null
    private var memoriesAt = ""
    private var memoryQuery = ""
    private var memoryCategory = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        aurora = Aurora.get(this)
        LinkService.channels(this)
        Calls.register(this)
        tab = savedInstanceState?.getString("tab") ?: "home"
        setUp()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("tab", tab)
    }

    /** Builds the frame (top bar, page, bottom bar) in the current theme. */
    private fun setUp() {
        kit = Kit(this)
        window.decorView.setBackgroundColor(p.background)
        window.statusBarColor = p.background
        window.navigationBarColor = p.surface
        if (Build.VERSION.SDK_INT >= 26) {
            window.decorView.systemUiVisibility = if (p.dark) 0
                else View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR or View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        }
        pageTitle = kit.text("", Ui.TITLE, p.text, Ui.medium)
        routePill = kit.pill("…", p.muted)
        val top = kit.row(kit.weight(pageTitle), routePill).apply { setPadding(kit.px(20), kit.px(14), kit.px(16), kit.px(6)) }
        content = FrameLayout(this)
        nav = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(p.surface)
            setPadding(kit.px(6), kit.px(6), kit.px(6), kit.px(6))
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(top)
            addView(content, LinearLayout.LayoutParams(-1, 0, 1f))
            addView(View(this@MainActivity).apply { setBackgroundColor(p.line) }, LinearLayout.LayoutParams(-1, kit.px(1)))
            addView(nav, LinearLayout.LayoutParams(-1, -2))
        })
        chatList = null
        chatLoaded = false
        show()
    }

    override fun onResume() {
        super.onResume()
        Live.visible = true
        Live.add(this)
        if (aurora.paired) {
            refreshRoute()
            LinkService.start(this)
            kotlin.concurrent.thread(name = "aurora-sync") {     // its own thread: sending can take a while
                val sent = Sync.run(this)
                val problem = Sync.problem
                Sync.problem = null
                ui {
                    if (problem != null) AlertDialog.Builder(this).setTitle("Something done offline wasn't accepted")
                        .setMessage(problem).setPositiveButton("OK", null).show()
                    else if (sent > 0) Toast.makeText(this, "Sent $sent thing(s) you did offline.", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }

    override fun onPause() {
        Live.visible = false
        Live.remove(this)
        super.onPause()
    }

    override fun onDestroy() {
        work.shutdownNow()
        super.onDestroy()
    }

    override fun onEvent(event: JSONObject) {
        ui {
            when {
                event.optString("kind") == "message" && event.optString("source") == "pc" && tab == "chat" && chatLoaded -> {
                    bubble("user", "🖥 " + event.optString("text"))
                    bubble("assistant", event.optString("reply"))
                }
                event.optString("kind") == "queued_reply" && tab == "chat" && chatLoaded -> {
                    bubble("user", event.optString("text"))
                    bubble("assistant", event.optString("reply"))
                }
                tab == "home" && event.optString("kind") != "message" -> show()
            }
        }
    }

    override fun onRoute(base: String?) = ui { showRoute(base) }

    private fun showRoute(base: String?) {
        if (!aurora.paired) { routePill.text = "●  Not paired"; tint(routePill, p.muted); return }
        routePill.text = "●  " + Protocol.routeName(base)
        tint(routePill, if (base == null) p.warn else p.ok)
    }

    private fun tint(pill: TextView, color: Int) {
        pill.setTextColor(color)
        pill.background = kit.shape(Ui.withAlpha(color, 0.12f), 14f, Ui.withAlpha(color, 0.35f))
    }

    private fun background(task: () -> Unit) = work.execute {
        try { task() } catch (error: Exception) { /* each task reports its own problems */ }
    }

    private fun ui(task: () -> Unit) = runOnUiThread { if (!isFinishing) task() }

    private fun refreshRoute() = background {
        val route = try { aurora.get("/api/status"); aurora.route } catch (error: IOException) { null }
        ui { showRoute(route); if (!aurora.paired) show() }
    }

    /** Fetches [path] in the background; [render] gets the JSON, [failed] a message. */
    private fun load(path: String, timeoutMs: Int = 30_000, failed: (String) -> Unit, render: (JSONObject) -> Unit) =
        background {
            val result = try { aurora.get(path, timeoutMs = timeoutMs) } catch (error: IOException) { null }
            ui {
                showRoute(aurora.route)
                when {
                    !aurora.paired -> show()
                    result == null -> failed("Can't reach your PC. Plug in the cable, be on your home Wi-Fi, or switch " +
                        "on Tailscale, with AURORA open on the PC.")
                    !result.ok -> failed(result.error)
                    else -> render(result.json)
                }
            }
        }

    // ------------------------------------------------------------------ frame

    private fun show() {
        content.removeAllViews()
        nav.removeAllViews()
        if (!aurora.paired) {
            pageTitle.text = "AURORA"
            showRoute(null)
            nav.visibility = View.GONE
            content.addView(pairScreen())
            return
        }
        nav.visibility = View.VISIBLE
        for ((key, symbol, label) in listOf(Triple("home", "⌂", "Home"), Triple("chat", "\u2709\uFE0E", "Chat"),
            Triple("call", "✆", "Call"), Triple("memory", "◈", "Memory"), Triple("control", "⚙", "Control"))) {
            nav.addView(navItem(key, symbol, label), LinearLayout.LayoutParams(0, -2, 1f))
        }
        pageTitle.text = mapOf("home" to "AURORA", "chat" to "Chat", "memory" to "Memory", "control" to "Control")[tab]
        content.addView(when (tab) {
            "chat" -> chatScreen()
            "memory" -> memoryScreen()
            "control" -> controlScreen()
            else -> homeScreen()
        })
    }

    private fun navItem(key: String, symbol: String, label: String): View {
        val selected = key == tab
        if (key == "call") {
            // The call button stands out: a filled circle in the middle.
            return LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER
                addView(kit.round(symbol, "Call AURORA", 52, p.accent, p.onAccent) { callAurora() })
                addView(kit.text(label, Ui.CAPTION, p.accent, Ui.medium).apply { gravity = Gravity.CENTER })
            }
        }
        val color = if (selected) p.accent else p.muted
        val item = kit.column(
            kit.text(symbol, 20f, color).apply { gravity = Gravity.CENTER },
            kit.text(label, Ui.CAPTION, color, if (selected) Ui.medium else Ui.regular).apply { gravity = Gravity.CENTER },
            gap = 0,
        ).apply {
            setPadding(0, kit.px(6), 0, kit.px(6))
            contentDescription = label
        }
        kit.tappable(item, 14f) { if (tab != key) { tab = key; show() } }
        return item
    }

    private fun callAurora() {
        if (!aurora.paired) return
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), MIC_FOR_CALL)
            return
        }
        // Is AURORA open on the PC? If it's closed, the phone helper can start it first.
        startAurora(then = {
            Calls.dial(this)?.let { problem -> AlertDialog.Builder(this).setMessage(problem).setPositiveButton("OK", null).show() }
        })
    }

    @Volatile private var starting = false

    /**
     * Makes sure AURORA is running on the PC, starting it through the phone helper if it's closed (the
     * PC's Phone page: "Let my phone start AURORA"), then runs [then] on the screen's thread.
     */
    private fun startAurora(then: () -> Unit) {
        if (starting) return
        starting = true
        val status = kit.text("Checking your PC…", Ui.BODY, p.text)
        val dialog = AlertDialog.Builder(this)
            .setView(FrameLayout(this).apply { setPadding(kit.px(24), kit.px(20), kit.px(24), kit.px(8)); addView(status) })
            .setNegativeButton("Cancel") { _, _ -> starting = false }
            .setCancelable(false)
            .create()
        val shown = Runnable { if (starting) dialog.show() }
        content.postDelayed(shown, 600)                  // only if it takes a moment
        fun finish(problem: String?) = ui {
            content.removeCallbacks(shown)
            if (dialog.isShowing) dialog.dismiss()
            val wanted = starting
            starting = false
            if (problem != null) AlertDialog.Builder(this).setMessage(problem).setPositiveButton("OK", null).show()
            else if (wanted) then()
        }
        background {
            try {
                var state = aurora.get("/api/status").json.optString("state")
                if (state == "running") { finish(null); return@background }
                if (state == "closed" || state == "paused") {
                    ui { status.text = if (state == "paused") "AURORA is paused. Starting it…"
                                       else "AURORA is closed on your PC. Starting it…" }
                    val reply = aurora.post("/api/control", JSONObject().put("action", "start"))
                    if (!reply.ok) { finish(reply.error); return@background }
                } else {
                    ui { status.text = "AURORA is starting on your PC…" }
                }
                val deadline = System.currentTimeMillis() + 120_000
                while (starting && System.currentTimeMillis() < deadline) {
                    Thread.sleep(2500)
                    state = try { aurora.get("/api/status", timeoutMs = 5000).json.optString("state") } catch (e: IOException) { "" }
                    if (state == "running") { finish(null); return@background }
                    ui { status.text = "Starting AURORA on your PC… (about half a minute)" }
                }
                finish(if (starting) "AURORA didn't start in time. Look at the PC, or try again." else null)
            } catch (error: IOException) {
                finish("Can't reach your PC. If AURORA is closed there, switch on \"Let my phone start AURORA\" on " +
                    "the PC's Phone page (the PC must be on, not asleep).")
            }
        }
    }

    override fun onRequestPermissionsResult(code: Int, permissions: Array<out String>, results: IntArray) {
        if (code != MIC_FOR_CALL) return
        if (results.firstOrNull() == PackageManager.PERMISSION_GRANTED) callAurora()
        else AlertDialog.Builder(this).setMessage("AURORA needs the microphone for calls. The microphone is on only " +
            "during a call, and nothing is recorded.").setPositiveButton("OK", null).show()
    }

    // ------------------------------------------------------------------ pairing

    private fun pairScreen(): View {
        val status = kit.note("")
        val code = kit.input("Code from the PC").apply {
            textSize = 24f
            letterSpacing = 0.2f
            gravity = Gravity.CENTER
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            filters = arrayOf(InputFilter.LengthFilter(9))
        }
        val name = kit.input("Name of this phone").apply {
            setText(Build.MODEL ?: "Phone")
            filters = arrayOf(InputFilter.LengthFilter(40))
        }
        lateinit var pair: TextView
        pair = kit.primary("Pair with my PC") {
            pair.isEnabled = false
            status.setTextColor(p.muted)
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
                        tab = "home"; show(); refreshRoute()
                        if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf("android.permission.POST_NOTIFICATIONS"), 1)
                        LinkService.start(this)
                    } else {
                        status.setTextColor(p.warn)
                        status.text = if (problem.startsWith("Can't reach"))
                            "Can't reach AURORA over the cable. Plug the phone in, keep USB debugging on, and switch " +
                                "it on on the PC's Phone page." else problem
                    }
                }
            }
        }
        val core = CoreView(this, p).apply { state = CoreState.CONNECTING }
        return kit.page(
            FrameLayout(this).apply { addView(core, FrameLayout.LayoutParams(kit.px(150), kit.px(150), Gravity.CENTER)) },
            kit.text("Pair this phone with your PC", Ui.TITLE, p.text, Ui.medium),
            kit.text("1. Plug the phone into the PC with the USB cable.\n" +
                "2. On the PC: AURORA → 📱 Phone → Pair my phone…\n" +
                "3. Type the code it shows here.", Ui.BODY, p.muted),
            code, name, pair, status,
            kit.note("Pairing works only over the cable, and only one phone can be paired. This phone makes a key in " +
                "its secure hardware that never leaves it; AURORA accepts only requests signed with it."),
        )
    }

    // ------------------------------------------------------------------ Home

    private fun homeScreen(): View {
        val hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
        val core = CoreView(this, p).apply { state = if (aurora.route == null) CoreState.CONNECTING else CoreState.IDLE }
        val model = kit.note("Checking your PC…")
        val coming = kit.column(kit.note("Loading…"), gap = 6)
        val recent = kit.column(gap = 6)
        val events = Live.recent().filter { it.optString("kind") != "message" || it.optString("source") == "pc" }.take(4)
        if (events.isEmpty()) recent.addView(kit.note("Nothing new since you opened the app."))
        for (event in events) recent.addView(kit.text("·  " + Format.event(event.optString("kind"), event.optString("text"),
            event.optString("reply"), event.optString("source"), event.optString("name")), Ui.SMALL, p.text).apply { maxLines = 2 })

        val startRow = kit.column(gap = 0)
        load("/api/status", failed = { message ->
            core.state = CoreState.DISCONNECTED; model.setTextColor(p.warn); model.text = message
        }) { data ->
            if (data.optString("state") == "closed") {
                core.state = CoreState.DISCONNECTED
                model.text = "AURORA is closed on your PC."
                startRow.addView(kit.secondary("▷  Start AURORA on the PC", p.accent) { startAurora { show() } })
            } else {
                core.state = CoreState.IDLE
                model.text = "AURORA is ${data.optString("ai", "ready")} · model ${data.optString("model")}"
            }
        }
        fun showReminders(reminders: JSONArray, offline: Boolean) {
            coming.removeAllViews()
            val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
            val upcoming = (0 until reminders.length()).map { reminders.getJSONObject(it) }
                .filter { (Alarms.dueAt(it.optString("due")) ?: 0L) + Alarms.GRACE_MS > System.currentTimeMillis() }
            if (offline) coming.addView(kit.note("From this phone's copy: they ring here even while your PC is off."))
            if (upcoming.isEmpty()) coming.addView(kit.note("No reminders waiting."))
            for (r in upcoming.take(4)) {
                coming.addView(kit.row(
                    kit.text(Format.due(r.optString("due"), today), Ui.SMALL, p.accent, Ui.medium).apply { minWidth = kit.px(52) },
                    kit.weight(kit.text(r.optString("text"), Ui.BODY)),
                    gap = 10,
                ))
            }
        }
        load("/api/today", failed = { showReminders(Offline.reminders(this), offline = true) }) { data ->
            val reminders = data.optJSONArray("reminders") ?: JSONArray()
            background { Sync.remember(this, reminders) }
            showReminders(reminders, offline = false)
        }

        val quick = kit.row(
            kit.weight(quickTile("\u2709\uFE0E", "Message") { tab = "chat"; show() }),
            kit.weight(quickTile("⇪", "Send file") { pickFile() }),
            kit.weight(quickTile("◈", "Memory") { tab = "memory"; show() }),
            gap = 10,
        )
        return kit.page(
            FrameLayout(this).apply { addView(core, FrameLayout.LayoutParams(kit.px(170), kit.px(170), Gravity.CENTER)) },
            kit.column(
                kit.text(Format.greeting(hour), Ui.DISPLAY, p.text, Ui.light).apply { gravity = Gravity.CENTER },
                model.apply { gravity = Gravity.CENTER },
                gap = 2,
            ),
            startRow,
            kit.primary("✆   Call AURORA") { callAurora() },
            kit.card(kit.overline("Coming up"), coming),
            quick,
            kit.card(kit.overline("Recent"), recent),
        )
    }

    private fun quickTile(symbol: String, label: String, onClick: () -> Unit): View {
        val tile = kit.column(
            kit.text(symbol, 22f, p.accent).apply { gravity = Gravity.CENTER },
            kit.text(label, Ui.SMALL, p.text, Ui.medium).apply { gravity = Gravity.CENTER },
            gap = 4,
        ).apply {
            background = kit.shape(p.surface, 16f, p.line)
            setPadding(0, kit.px(14), 0, kit.px(14))
            contentDescription = label
        }
        return kit.tappable(tile, 16f, onClick)
    }

    // ------------------------------------------------------------------ Chat

    private fun chatScreen(): View {
        val list = chatList ?: kit.column(gap = 0).apply { setPadding(kit.px(12), kit.px(4), kit.px(12), kit.px(12)) }
            .also { chatList = it }
        (list.parent as? android.view.ViewGroup)?.removeView(list)
        val scroll = ScrollView(this).apply { addView(list); isFillViewport = true; isVerticalScrollBarEnabled = false }
        chatScroll = scroll
        val input = kit.input("Message AURORA").apply {
            maxLines = 5
            imeOptions = EditorInfo.IME_ACTION_SEND
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES or InputType.TYPE_TEXT_FLAG_MULTI_LINE
        }
        chatInput = input
        lateinit var send: TextView
        val sendNow = {
            val message = input.text.toString().trim()
            if (message.isNotEmpty() && send.isEnabled) { input.setText(""); sendChat(message, send) }
        }
        send = kit.round("➤", "Send", 48, p.accent, p.onAccent) { sendNow() }
        input.setOnEditorActionListener { _, action, _ -> if (action == EditorInfo.IME_ACTION_SEND) { sendNow(); true } else false }
        val attach = kit.round("＋", "Attach a file (it's saved on the PC)", 48) { pickFile() }
        val bar = kit.row(attach, kit.weight(input), send, gap = 8).apply {
            setPadding(kit.px(10), kit.px(8), kit.px(10), kit.px(10))
        }
        val tools = kit.row(
            kit.weight(kit.note("Long-press a message to copy it.")),
            kit.secondary("Clear", p.muted) { confirmClear() },
        ).apply { setPadding(kit.px(14), 0, kit.px(14), 0) }
        if (!chatLoaded) loadHistory()
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(tools)
            addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
            addView(bar)
        }
    }

    private fun bubble(role: String, message: String): LinearLayout {
        val mine = role == "user"
        val note = role == "note"
        val holder = kit.column(gap = 6).apply {
            setPadding(kit.px(14), kit.px(10), kit.px(14), kit.px(10))
            background = when {
                mine -> kit.shape(Ui.withAlpha(p.accent, 0.18f), 18f, Ui.withAlpha(p.accent, 0.35f))
                note -> kit.shape(android.graphics.Color.TRANSPARENT, 18f)
                else -> kit.shape(p.surface, 18f, p.line)
            }
        }
        fill(holder, message, if (note) p.muted else p.text)
        holder.setOnLongClickListener {
            getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("AURORA", holder.tag as? String ?: message))
            Toast.makeText(this, "Copied", Toast.LENGTH_SHORT).show()
            true
        }
        holder.tag = message
        chatList?.addView(holder, LinearLayout.LayoutParams(-2, -2).apply {
            gravity = if (mine) Gravity.END else Gravity.START
            topMargin = kit.px(8)
            if (mine) leftMargin = kit.px(48) else rightMargin = kit.px(32)
        })
        chatScroll?.post { chatScroll?.fullScroll(View.FOCUS_DOWN) }
        return holder
    }

    /** Puts [message] into a bubble: text, with ``` code blocks in a monospaced box. */
    private fun fill(holder: LinearLayout, message: String, color: Int) {
        holder.removeAllViews()
        holder.tag = message
        for (part in Format.parts(message).ifEmpty { listOf(Format.Part(false, message)) }) {
            if (part.code) {
                holder.addView(HorizontalScrollView(this).apply {
                    addView(kit.text(part.text, Ui.SMALL, p.text, Ui.mono).apply {
                        setPadding(kit.px(10), kit.px(8), kit.px(10), kit.px(8))
                    })
                    background = kit.shape(p.background, 10f, p.line)
                })
            } else {
                holder.addView(kit.text(part.text, Ui.BODY, color))
            }
        }
    }

    private fun loadHistory() {
        chatLoaded = true
        load("/api/history", failed = { message ->
            chatList?.removeAllViews()
            bubble("note", "$message\n\nYou can still write: messages wait on this phone and go to AURORA when your PC is back.")
            for (waiting in Offline.queuedMessages(this)) bubble("user", "⏳ $waiting")
            chatLoaded = false
        }) { data ->
            chatList?.removeAllViews()
            val messages = data.optJSONArray("messages")
            if (messages == null || messages.length() == 0) bubble("note", "Say hello to AURORA.")
            for (i in 0 until (messages?.length() ?: 0)) {
                val m = messages!!.getJSONObject(i)
                bubble(m.optString("role"), m.optString("text"))
            }
        }
    }

    private fun sendChat(message: String, send: TextView) {
        bubble("user", message)
        val answer = bubble("assistant", "AURORA is thinking…")
        send.isEnabled = false
        send.alpha = 0.5f
        val op = Offline.newId()
        background {
            val (reply, problem) = try {
                val r = aurora.post("/api/chat", JSONObject().put("text", message).put("op", op), timeoutMs = 300_000)
                when {
                    r.ok -> r.json to null
                    r.status == 503 -> {                          // AURORA is closed on the PC: keep it for later
                        Offline.enqueue(this, "chat", JSONObject().put("text", message), op)
                        null to QUEUED
                    }
                    else -> null to r.error
                }
            } catch (error: IOException) {
                Offline.enqueue(this, "chat", JSONObject().put("text", message), op)
                null to QUEUED
            }
            ui {
                send.isEnabled = true
                send.alpha = 1f
                showRoute(aurora.route)
                if (reply != null) {
                    val sources = reply.optString("sources", "")
                    fill(answer, reply.optString("reply") + if (sources.isNotBlank()) "\n\n$sources" else "", p.text)
                    chatScroll?.post { chatScroll?.fullScroll(View.FOCUS_DOWN) }
                } else {
                    fill(answer, problem ?: "Something went wrong.", if (problem == QUEUED) p.muted else p.warn)
                    if (!aurora.paired) show()
                }
            }
        }
    }

    private fun confirmClear() {
        AlertDialog.Builder(this)
            .setTitle("Clear the conversation?")
            .setMessage("AURORA forgets this conversation on the PC and the phone (saved memories stay).")
            .setPositiveButton("Clear") { _, _ ->
                background {
                    val problem = try {
                        val r = aurora.post("/api/clear", JSONObject())
                        if (r.ok) null else r.error
                    } catch (error: IOException) { error.message }
                    ui {
                        chatList?.removeAllViews()
                        bubble("note", problem ?: "Conversation cleared.")
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ------------------------------------------------------------------ files (Chat's 📎, Home's Send file)

    private fun pickFile() {
        startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*"), PICK_FILE)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val uri = data?.data
        if (requestCode != PICK_FILE || resultCode != RESULT_OK || uri == null || uri.scheme != "content") return
        val name = Files.describe(this, uri).first
        if (tab != "chat") { tab = "chat"; show() }
        val note = bubble("note", "📎 Sending $name to your PC…")
        background {
            val open = { contentResolver.openInputStream(uri) ?: throw IOException("The phone couldn't read that file.") }
            val message = try {
                val (sha, size) = Files.checksum(open)
                if (size == 0L) throw IOException("$name is empty.")
                val reply = aurora.upload(name, size, sha, open)
                if (reply.ok) "📎 ${reply.json.optString("saved", name)} is on your PC in Documents › From phone."
                else "📎 $name wasn't sent: ${reply.error}"
            } catch (error: IOException) {
                "📎 $name wasn't sent: ${error.message}"
            } catch (error: SecurityException) {
                "📎 The phone didn't let AURORA read $name."
            }
            ui { fill(note, message, p.muted) }
        }
    }

    // ------------------------------------------------------------------ Memory

    private fun memoryScreen(): View {
        val list = kit.column(gap = 10)
        val chips = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val sync = kit.note("")
        val search = kit.input("Search memories").apply {
            setText(memoryQuery)
            inputType = InputType.TYPE_CLASS_TEXT
        }
        fun render() {
            list.removeAllViews()
            chips.removeAllViews()
            val all = memories ?: return
            val categories = (0 until all.length()).map { all.getJSONObject(it).optString("category") }
                .filter { it.isNotBlank() }.distinct().sorted()
            for (category in listOf("") + categories) {
                val chosen = category == memoryCategory
                chips.addView(kit.text(category.ifEmpty { "All" }, Ui.SMALL, if (chosen) p.onAccent else p.text, Ui.medium).apply {
                    background = kit.shape(if (chosen) p.accent else p.raised, 16f, if (chosen) null else p.line)
                    setPadding(kit.px(14), kit.px(8), kit.px(14), kit.px(8))
                    isClickable = true
                    setOnClickListener { memoryCategory = category; render() }
                }, LinearLayout.LayoutParams(-2, -2).apply { rightMargin = kit.px(8) })
            }
            var shown = 0
            for (i in 0 until all.length()) {
                val m = all.getJSONObject(i)
                if (!Format.matches(m.optString("value"), m.optString("category"), memoryQuery, memoryCategory)) continue
                shown++
                val card = kit.card(kit.overline(m.optString("category").ifBlank { "Memory" }),
                    kit.text(m.optString("value"), Ui.BODY), padding = 14, gap = 4)
                if (m.optBoolean("pending")) card.addView(kit.note("⏳ Goes to your PC when it's back."))
                else kit.tappable(card, 18f) { editMemory(m) }
                card.contentDescription = "Memory: ${m.optString("value")}. Tap to edit or delete."
                list.addView(card)
            }
            if (shown == 0) list.addView(kit.note(if (all.length() == 0) "Nothing remembered yet." else "No memories match."))
        }
        search.addTextChangedListener(object : android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: android.text.Editable?) { memoryQuery = s.toString(); render() }
        })
        fun reload() {
            sync.text = "Updating…"
            load("/api/memories", failed = { message ->
                val copy = Offline.memories(this)
                if (copy.length() > 0 && !message.contains("allowed")) {
                    memories = copy
                    memoriesAt = SimpleDateFormat("d MMM HH:mm", Locale.getDefault()).format(Date(Offline.memoriesTime(this)))
                    sync.text = "Offline copy from $memoriesAt"
                    render()
                } else {
                    sync.text = ""; list.removeAllViews(); list.addView(kit.note(message, p.warn))
                }
            }) { data ->
                memories = data.optJSONArray("memories") ?: JSONArray()
                Offline.saveMemories(this, memories!!)
                memoriesAt = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date())
                sync.text = "Synced $memoriesAt"
                render()
            }
        }
        if (memories == null) reload() else { sync.text = "Synced $memoriesAt"; render() }
        return kit.page(
            search,
            HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; addView(chips) },
            kit.row(kit.weight(sync), kit.secondary("＋ Add", p.accent) { addMemory { reload() } },
                kit.secondary("↻", p.muted) { reload() }),
            list,
        )
    }

    private fun editMemory(memory: JSONObject) {
        val field = kit.input("").apply { setText(memory.optString("value")) }
        AlertDialog.Builder(this)
            .setTitle(memory.optString("category").ifBlank { "Memory" })
            .setView(FrameLayout(this).apply { setPadding(kit.px(20), kit.px(8), kit.px(20), 0); addView(field) })
            .setPositiveButton("Save") { _, _ ->
                changeMemory("POST", "/api/memories/edit",
                    JSONObject().put("id", memory.optInt("id")).put("value", field.text.toString()))
            }
            .setNeutralButton("Delete") { _, _ ->
                AlertDialog.Builder(this)
                    .setMessage("Delete this memory? AURORA forgets it for good.")
                    .setPositiveButton("Delete") { _, _ ->
                        changeMemory("POST", "/api/memories/delete", JSONObject().put("id", memory.optInt("id")))
                    }
                    .setNegativeButton("Keep", null).show()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun addMemory(done: () -> Unit) {
        val field = kit.input("Something AURORA should remember")
        AlertDialog.Builder(this)
            .setTitle("Add a memory")
            .setView(FrameLayout(this).apply { setPadding(kit.px(20), kit.px(8), kit.px(20), 0); addView(field) })
            .setPositiveButton("Save") { _, _ ->
                changeMemory("POST", "/api/memories", JSONObject().put("value", field.text.toString()))
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun changeMemory(method: String, path: String, body: JSONObject) = background {
        val kind = mapOf("/api/memories" to "memory_add", "/api/memories/edit" to "memory_edit",
            "/api/memories/delete" to "memory_delete")[path] ?: return@background
        var offline = false
        val problem = try {
            val r = aurora.call(method, path, body.toString().toByteArray())
            when {
                r.ok -> null
                r.status == 503 -> { offline = true; null }
                else -> r.error
            }
        } catch (error: IOException) { offline = true; null }
        if (offline) {                                  // kept on the phone; the PC gets it when it's back
            Offline.enqueue(this, kind, body)
            Offline.changeMemory(this, kind, body.optInt("id"), body.optString("value"))
        }
        ui {
            if (problem != null) AlertDialog.Builder(this).setMessage(problem).setPositiveButton("OK", null).show()
            if (offline) Toast.makeText(this, "Saved on this phone. It goes to your PC when it's back.", Toast.LENGTH_LONG).show()
            memories = if (offline) Offline.memories(this) else null
            if (tab == "memory") show()
        }
    }

    // ------------------------------------------------------------------ Control

    private fun controlScreen(): View {
        val desktop = kit.column(kit.note("Loading…"), gap = 6)
        val services = kit.column(gap = 4)
        val commands = kit.column(gap = 8)
        val tasks = kit.column(kit.note("Loading…"), gap = 4)
        val phone = kit.column(gap = 4)

        load("/api/status", failed = { message ->
            desktop.removeAllViews(); desktop.addView(kit.note(message, p.warn))
        }) { data ->
            desktop.removeAllViews()
            desktop.addView(kit.row(kit.weight(kit.text("AURORA ${data.optString("version")}", Ui.HEADING, p.text, Ui.medium)),
                kit.pill(data.optString("ai", "ready"), if (data.optString("state") == "running") p.ok else p.warn)))
            desktop.addView(kit.text("Model ${data.optString("model")} · connected by ${Protocol.routeName(aurora.route)}",
                Ui.SMALL, p.muted))
            desktop.addView(meter("CPU", data.optDouble("cpu", Double.NaN)))
            desktop.addView(meter("RAM", data.optDouble("ram", Double.NaN)))
            services.removeAllViews()
            val list = data.optJSONArray("services")
            if (list == null || list.length() == 0) services.addView(kit.note("No service details from this PC."))
            for (i in 0 until (list?.length() ?: 0)) {
                val s = list!!.getJSONObject(i)
                val on = s.optBoolean("running", true)
                services.addView(kit.row(kit.weight(kit.text(s.optString("name"), Ui.BODY)),
                    kit.text(if (on) "● on" else "○ off", Ui.SMALL, if (on) p.ok else p.muted)))
            }
            commands.removeAllViews()
            if (data.optString("state") == "closed") {
                commands.addView(kit.secondary("▷  Start AURORA") { startAurora { show() } })
                commands.addView(kit.note("AURORA is closed on your PC; its phone helper can start it."))
                return@load
            }
            val running = data.optString("state") == "running"
            commands.addView(kit.row(
                kit.weight(kit.secondary(if (running) "‖  Pause AURORA" else "▷  Start AURORA") {
                    command(if (running) "pause" else "start")
                }),
                kit.weight(kit.secondary("◼  Stop speaking") { command("stop_speaking") }),
            ))
            commands.addView(kit.note("Only these. Anything AURORA would do on the PC still asks in Chat first."))
            phone.removeAllViews()
            phone.addView(kit.text(aurora.deviceName, Ui.HEADING, p.text, Ui.medium))
            phone.addView(kit.text("Key kept in " + (if (PhoneKey.inHardware()) "the phone's secure hardware" else "Android's Keystore"),
                Ui.SMALL, p.muted))
            data.optJSONObject("permissions")?.let { perm ->
                phone.addView(kit.text("Memories: " + (if (perm.optBoolean("memories")) "allowed" else "not allowed") +
                    " · Approve actions: " + (if (perm.optBoolean("actions")) "allowed" else "not allowed"), Ui.SMALL, p.muted))
                phone.addView(kit.note("Change these on the PC's Phone page."))
            }
        }
        load("/api/today", failed = { tasks.removeAllViews(); tasks.addView(kit.note(it, p.warn)) }) { data ->
            tasks.removeAllViews()
            val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
            val reminders = data.optJSONArray("reminders") ?: JSONArray()
            if (reminders.length() == 0) tasks.addView(kit.note("No reminders or tasks waiting."))
            for (i in 0 until reminders.length()) {
                val r = reminders.getJSONObject(i)
                tasks.addView(kit.text(Format.due(r.optString("due"), today) + "   " + r.optString("text"), Ui.BODY))
            }
        }

        val notices = kit.column(gap = 4)
        val events = Live.recent().take(8)
        if (events.isEmpty()) notices.addView(kit.note("Nothing since you opened the app."))
        for (event in events) notices.addView(kit.text(event.optString("time").substringAfter("T").take(5) + "   " +
            Format.event(event.optString("kind"), event.optString("text"), event.optString("reply"),
                event.optString("source"), event.optString("name")), Ui.SMALL, p.text).apply { maxLines = 2 })

        return kit.page(
            kit.card(kit.overline("Desktop"), desktop),
            kit.card(kit.overline("Commands"), commands),
            kit.card(kit.overline("Services"), services),
            kit.card(kit.overline("Pending"), tasks),
            kit.card(kit.overline("Notifications"), notices),
            kit.card(kit.overline("This phone"), phone, kit.secondary("Sign out of AURORA", p.danger) { confirmSignOut() }),
            settingsCard(),
        )
    }

    private fun meter(name: String, value: Double): View {
        val bar = FrameLayout(this).apply { background = kit.shape(p.raised, 6f) }
        val fillView = View(this).apply { background = kit.shape(if (value > 85) p.warn else p.accent, 6f) }
        val fraction = if (value.isNaN()) 0f else (value / 100.0).toFloat().coerceIn(0f, 1f)
        bar.addView(fillView, FrameLayout.LayoutParams(0, -1))
        bar.post { fillView.layoutParams = FrameLayout.LayoutParams((bar.width * fraction).toInt(), -1) }
        return kit.row(
            kit.text(name, Ui.SMALL, p.muted).apply { minWidth = kit.px(40) },
            bar.apply { layoutParams = LinearLayout.LayoutParams(0, kit.px(8), 1f) },
            kit.text(if (value.isNaN()) "–" else "${value.toInt()}%", Ui.SMALL, p.text).apply { minWidth = kit.px(40); gravity = Gravity.END },
            gap = 10,
        )
    }

    private fun command(action: String) = background {
        val message = try {
            val r = aurora.post("/api/control", JSONObject().put("action", action))
            if (r.ok) r.json.optString("message", "Done.") else r.error
        } catch (error: IOException) { error.message ?: "Couldn't reach your PC." }
        ui {
            Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
            if (tab == "control") show()
        }
    }

    private fun settingsCard(): View {
        fun toggle(label: String, on: Boolean, change: (Boolean) -> Unit) = Switch(this).apply {
            text = label
            textSize = Ui.BODY
            setTextColor(p.text)
            isChecked = on
            setPadding(0, kit.px(8), 0, kit.px(8))
            setOnCheckedChangeListener { _, value -> change(value) }
        }
        val themes = listOf("dark" to "Dark", "light" to "Light", "system" to "Like the phone")
        val themeRow = kit.row(*themes.map { (key, label) ->
            val chosen = Ui.theme(this) == key
            kit.weight(kit.text(label, Ui.SMALL, if (chosen) p.onAccent else p.text, Ui.medium).apply {
                gravity = Gravity.CENTER
                background = kit.shape(if (chosen) p.accent else p.raised, 16f, if (chosen) null else p.line)
                setPadding(0, kit.px(10), 0, kit.px(10))
                isClickable = true
                setOnClickListener { Ui.setTheme(this@MainActivity, key); setUp() }
            })
        }.toTypedArray())
        val battery = if (getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName))
            kit.text("✓ Allowed to run in the background.", Ui.SMALL, p.ok)
        else kit.secondary("Allow AURORA to run in the background") { askBatteryFree() }
        return kit.card(
            kit.overline("Phone settings"),
            toggle("Stay connected in the background", LinkService.enabled(this)) { LinkService.setEnabled(this, it) },
            toggle("Show reminders full screen (also when locked)", LinkService.option(this, "reminder_screen")) {
                LinkService.setOption(this, "reminder_screen", it) },
            toggle("Reminders ring as a call", LinkService.option(this, "reminder_call", false)) {
                LinkService.setOption(this, "reminder_call", it) },
            toggle("AURORA says reminders aloud", LinkService.option(this, "speak_reminders")) {
                LinkService.setOption(this, "speak_reminders", it) },
            kit.overline("Theme"),
            themeRow,
            battery,
            kit.note("On OPPO (ColorOS) also: Settings → Battery → App battery management → AURORA → allow background " +
                "activity and auto launch."),
        )
    }

    @android.annotation.SuppressLint("BatteryLife")
    private fun askBatteryFree() {
        try {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        } catch (error: Exception) {
            startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
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
                    ui { chatList = null; chatLoaded = false; memories = null; tab = "home"; show() }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
}
