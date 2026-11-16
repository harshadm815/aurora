package app.aurora.companion

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException

/**
 * Brings the phone and the PC back in step whenever the phone reaches AURORA:
 *  1. phone → PC: what was done offline, oldest first (messages, memories added, changed or deleted).
 *     Each carries its id, so the PC applies it once; a change the PC refuses for good (no permission)
 *     is dropped and reported; if the PC can't be reached or AURORA is closed, the rest waits.
 *  2. PC → phone: the memories (if allowed) and the upcoming reminders, which the phone then rings
 *     itself if the PC is off (Alarms.kt).
 */
object Sync {
    private const val PULL_EVERY_MS = 10 * 60 * 1000L
    private val PATHS = mapOf("chat" to "/api/chat", "memory_add" to "/api/memories",
        "memory_edit" to "/api/memories/edit", "memory_delete" to "/api/memories/delete",
        "history_import" to "/api/history/import")

    @Volatile private var running = false
    @Volatile private var lastPull = 0L

    /** Problems from the last run that the user should hear about (a refused change), shown once. */
    @Volatile var problem: String? = null

    fun needed(context: Context): Boolean =
        Offline.queue(context).isNotEmpty() || System.currentTimeMillis() - lastPull > PULL_EVERY_MS

    /** Runs a sync (blocking; call it off the screen's thread). Returns how many queued items were sent. */
    fun run(context: Context): Int {
        val aurora = Aurora.get(context)
        if (running || !aurora.paired) return 0
        running = true
        var sent = 0
        try {
            for (item in Offline.queue(context)) {
                val kind = item.optString("kind")
                val path = PATHS[kind]
                if (path == null) { Offline.remove(context, item.optString("op")); continue }
                val body = JSONObject(item.optJSONObject("body")?.toString() ?: "{}").put("op", item.optString("op"))
                if (kind == "memory_edit" || kind == "memory_delete") body.put("changed_at", item.optDouble("time"))
                val reply = try {
                    aurora.post(path, body, timeoutMs = if (kind == "chat") 300_000 else 20_000)
                } catch (error: IOException) {
                    return sent                                       // not reachable: everything else waits
                }
                when {
                    reply.ok -> {
                        Offline.remove(context, item.optString("op"))
                        sent++
                        if (kind == "chat" && !reply.json.optBoolean("duplicate")) {
                            Live.event(JSONObject().put("kind", "queued_reply")
                                .put("text", body.optString("text")).put("reply", reply.json.optString("reply")))
                        }
                    }
                    reply.status == 503 || reply.status == 401 -> return sent   // AURORA closed or not ready
                    // An older AURORA on the PC that doesn't know this kind of change yet: keep it for later.
                    reply.status == 404 && kind == "history_import" -> return sent
                    else -> {                                         // refused for good: don't retry forever
                        Offline.remove(context, item.optString("op"))
                        problem = reply.error
                    }
                }
            }
            pull(context)
            return sent
        } finally {
            running = false
        }
    }

    private fun pull(context: Context) {
        val aurora = Aurora.get(context)
        try {
            val today = aurora.get("/api/today", timeoutMs = 30_000)
            if (today.ok) remember(context, today.json.optJSONArray("reminders") ?: JSONArray())
            val memories = aurora.get("/api/memories", timeoutMs = 30_000)
            if (memories.ok) Offline.saveMemories(context, memories.json.optJSONArray("memories") ?: JSONArray())
            if (today.ok) lastPull = System.currentTimeMillis()
        } catch (error: IOException) {
            // try again next time
        }
    }

    /** Keeps the upcoming reminders and sets the phone's own alarms for them. */
    fun remember(context: Context, reminders: JSONArray) {
        Offline.saveReminders(context, reminders)
        Alarms.schedule(context, reminders)
    }
}
