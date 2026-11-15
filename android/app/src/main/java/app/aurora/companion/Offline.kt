package app.aurora.companion

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.SecureRandom

/**
 * What the phone keeps so AURORA is useful while the PC is away (in the app's private storage; backups
 * are off): a copy of the memories, the upcoming reminders, and the queue of things done offline that
 * go to the PC, in order, when it's back (Sync.kt). Each queued change has a random id, so the PC
 * applies it once even if it's sent twice.
 */
object Offline {
    private const val MAX_QUEUE = 200
    private val random = SecureRandom()
    private val lock = Any()

    private fun file(context: Context, name: String) = File(context.filesDir, "offline-$name.json")

    private fun read(context: Context, name: String): JSONObject = synchronized(lock) {
        val f = file(context, name)
        if (!f.exists()) JSONObject() else try { JSONObject(f.readText()) } catch (e: Exception) { JSONObject() }
    }

    private fun write(context: Context, name: String, value: JSONObject) = synchronized(lock) {
        val f = file(context, name)
        val temp = File(f.path + ".tmp")
        temp.writeText(value.toString())
        temp.renameTo(f)
    }

    fun newId(): String = "op-" + ByteArray(9).also { random.nextBytes(it) }.joinToString("") { "%02x".format(it) }

    // ------------------------------------------------------------------ the queue

    /** Things done while the PC was away, oldest first. */
    fun queue(context: Context): List<JSONObject> {
        val items = read(context, "queue").optJSONArray("items") ?: JSONArray()
        return (0 until items.length()).map { items.getJSONObject(it) }
    }

    /** Adds a change: [kind] is "chat", "memory_add", "memory_edit" or "memory_delete"; [body] is what the PC gets. */
    fun enqueue(context: Context, kind: String, body: JSONObject, op: String = newId()): JSONObject = synchronized(lock) {
        val item = JSONObject().put("op", op).put("kind", kind).put("time", System.currentTimeMillis() / 1000.0)
            .put("body", body)
        val items = queue(context).toMutableList()
        items += item
        while (items.size > MAX_QUEUE) items.removeAt(0)
        write(context, "queue", JSONObject().put("items", JSONArray(items)))
        item
    }

    fun remove(context: Context, op: String) = synchronized(lock) {
        write(context, "queue", JSONObject().put("items", JSONArray(queue(context).filter { it.optString("op") != op })))
    }

    fun queuedMessages(context: Context): List<String> =
        queue(context).filter { it.optString("kind") == "chat" }.map { it.optJSONObject("body")?.optString("text") ?: "" }

    // ------------------------------------------------------------------ memories

    fun memories(context: Context): JSONArray = read(context, "memories").optJSONArray("items") ?: JSONArray()
    fun memoriesTime(context: Context): Long = read(context, "memories").optLong("at", 0)

    fun saveMemories(context: Context, items: JSONArray) =
        write(context, "memories", JSONObject().put("items", items).put("at", System.currentTimeMillis()))

    /** Applies an offline change to the copy at once (the PC gets it later). */
    fun changeMemory(context: Context, kind: String, id: Int, value: String) = synchronized(lock) {
        val items = memories(context)
        val result = JSONArray()
        if (kind == "memory_add") result.put(JSONObject().put("id", 0).put("value", value).put("category", "Note")
            .put("pending", true))
        for (i in 0 until items.length()) {
            val m = items.getJSONObject(i)
            if (m.optInt("id") == id && id != 0) {
                if (kind == "memory_delete") continue
                if (kind == "memory_edit") m.put("value", value).put("pending", true)
            }
            result.put(m)
        }
        write(context, "memories", read(context, "memories").put("items", result))
    }

    // ------------------------------------------------------------------ reminders

    fun reminders(context: Context): JSONArray = read(context, "reminders").optJSONArray("items") ?: JSONArray()

    fun saveReminders(context: Context, items: JSONArray) =
        write(context, "reminders", read(context, "reminders").put("items", items))

    /** Reminders already shown (by the PC's event or the phone's own alarm), so none rings twice. */
    fun markShown(context: Context, key: String): Boolean = synchronized(lock) {
        val data = read(context, "reminders")
        val shown = data.optJSONArray("shown") ?: JSONArray()
        for (i in 0 until shown.length()) if (shown.optString(i) == key) return false
        val list = (0 until shown.length()).map { shown.optString(it) }.takeLast(100) + key
        write(context, "reminders", data.put("shown", JSONArray(list)))
        true
    }
}
