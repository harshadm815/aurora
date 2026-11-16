package app.aurora.companion

import android.content.Context
import android.os.Handler
import android.os.Looper
import java.io.File

/**
 * AURORA's mind on the phone (offline plan C): Qwen3 0.6B run by llama.cpp (libaurora_brain.so), used
 * only while the PC can't be reached. The model file is copied over the cable into the app's own
 * folder (MODEL_FILE). Loaded on first use, let go after [IDLE_MS] unused so the phone gets its memory
 * back. Everything stays on the phone.
 */
object LocalBrain {
    const val MODEL_FILE = "Qwen_Qwen3-0.6B-Q4_K_M.gguf"
    const val MODEL_BYTES = 484_220_320L
    private const val IDLE_MS = 5 * 60 * 1000L
    private const val THREADS = 4                         // the Snapdragon 665's four fast cores
    private const val CONTEXT = 2048

    /** What AURORA is told when it answers from the phone: what it is, and what it can't do here. */
    const val SYSTEM = "You are AURORA, the user's personal assistant. Right now you are running on their " +
        "phone because their PC can't be reached, so you are a smaller, offline version of yourself. Answer " +
        "briefly and plainly, in a few sentences at most. You can't use the PC, its files, the internet, " +
        "devices, reminders or memories right now; if asked, say so and that the request will go to the " +
        "PC when it's back. Never invent facts about the user."

    fun interface PieceListener { fun onPiece(bytes: ByteArray) }

    private val loaded = runCatching { System.loadLibrary("aurora_brain") }.isSuccess
    private var handle = 0L
    private val lock = Any()
    private val main = Handler(Looper.getMainLooper())

    /**
     * AURORA's recent answers exactly as the model wrote them (with Qwen3's empty "thinking" block), keyed by
     * the cleaned text the app shows. Putting the exact text back into the next prompt keeps it identical to
     * what the model already holds in memory, so only your new message has to be read.
     */
    private val written = object : LinkedHashMap<String, String>() {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, String>?) = size > 16
    }
    private val release = Runnable { synchronized(lock) { if (handle != 0L) { nativeFree(handle); handle = 0L } } }

    @JvmStatic private external fun nativeLoad(path: String, threads: Int, context: Int): Long
    @JvmStatic private external fun nativeFormat(handle: Long, roles: Array<String>, contents: Array<String>): ByteArray
    @JvmStatic private external fun nativeGenerate(handle: Long, prompt: String, maxTokens: Int, listener: PieceListener): ByteArray
    @JvmStatic private external fun nativeStop(handle: Long)
    @JvmStatic private external fun nativePrime(handle: Long, prompt: String): Boolean
    @JvmStatic private external fun nativeFree(handle: Long)

    fun modelFile(context: Context): File = File(context.getExternalFilesDir("models"), MODEL_FILE)

    /** True when the app has the model runner and the whole model file. */
    fun available(context: Context): Boolean = loaded && modelFile(context).length() == MODEL_BYTES

    /**
     * AURORA's reply to [history] (role, text pairs, oldest first, ending with the user's message).
     * [onText] gets the reply so far as it's written (on this thread). Blocking: call it off the screen's thread.
     */
    fun reply(context: Context, history: List<Pair<String, String>>, maxTokens: Int = 220,
              onText: (String) -> Unit = {}): String = synchronized(lock) {
        main.removeCallbacks(release)
        val started = System.currentTimeMillis()
        if (handle == 0L) {
            handle = nativeLoad(modelFile(context).path, THREADS, CONTEXT)
            android.util.Log.i("AuroraBrain", "load ms=${System.currentTimeMillis() - started}")
        }
        if (handle == 0L) throw IllegalStateException("The offline model couldn't be loaded.")
        try {
            val prompt = format(history.takeLast(8))
            val bytes = java.io.ByteArrayOutputStream()
            var pieces = 0
            var first = 0L
            val begun = System.currentTimeMillis()
            val raw = nativeGenerate(handle, prompt, maxTokens) { piece ->
                if (pieces++ == 0) first = System.currentTimeMillis() - begun
                bytes.write(piece)
                onText(Thinking.strip(bytes.toString(Charsets.UTF_8.name())))
            }
            val total = System.currentTimeMillis() - begun
            // Timing only (never what was said): how long the first word took, and pieces per second after it.
            android.util.Log.i("AuroraBrain", "reply first_ms=$first pieces=$pieces total_ms=$total " +
                "rate=${if (total > first && pieces > 1) "%.1f".format((pieces - 1) * 1000.0 / (total - first)) else "-"}/s")
            val exact = String(raw, Charsets.UTF_8)
            Thinking.strip(exact).also { shown -> if (shown.isNotEmpty()) written[shown] = exact }
        } finally {
            main.postDelayed(release, IDLE_MS)
        }
    }

    /**
     * The conversation in the model's own chat format. Qwen3: "/no_think" on every message of yours asks
     * for a direct answer (no long reasoning first), and keeping it on all of them keeps earlier parts of
     * the prompt identical from one message to the next, so the model reuses what it has already read.
     */
    private fun format(history: List<Pair<String, String>>): String {
        val turns = listOf("system" to SYSTEM) + history.map { (role, text) ->
            role to if (role == "user") "$text /no_think" else (written[text] ?: text)
        }
        return String(nativeFormat(handle, turns.map { it.first }.toTypedArray(),
            turns.map { it.second }.toTypedArray()), Charsets.UTF_8)
    }

    /**
     * Gets ready while you're still typing or AURORA is greeting you: loads the model and reads its
     * instructions, so the first answer starts in about a second. Blocking; call it off the screen's thread.
     */
    fun warm(context: Context) {
        if (!available(context)) return
        synchronized(lock) {
            main.removeCallbacks(release)
            val started = System.currentTimeMillis()
            if (handle == 0L) handle = nativeLoad(modelFile(context).path, THREADS, CONTEXT)
            if (handle != 0L) nativePrime(handle, format(emptyList()))
            android.util.Log.i("AuroraBrain", "warm ms=${System.currentTimeMillis() - started}")
            main.postDelayed(release, IDLE_MS)
        }
    }

    /** Stops a reply being written (you interrupted, or left the screen). */
    fun stop() { val h = handle; if (h != 0L) nativeStop(h) }
}

/** Qwen3 may still write an (empty) <think>…</think> block first: it's never shown. */
object Thinking {
    fun strip(text: String): String {
        var result = text.replace(Regex("(?s)<think>.*?</think>"), "")
        val open = result.indexOf("<think>")
        if (open >= 0) result = result.substring(0, open)      // still thinking: show nothing of it yet
        return result.trim()
    }
}
