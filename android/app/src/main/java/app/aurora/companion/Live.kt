package app.aurora.companion

import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

/** News passed inside the app: events from the PC and connection changes (any thread). */
object Live {
    interface Listener {
        fun onEvent(event: JSONObject) {}
        fun onRoute(base: String?) {}
    }

    private val listeners = CopyOnWriteArrayList<Listener>()

    /** True while one of the app's screens is on show (then replies aren't notified). */
    @Volatile var visible = false

    fun add(listener: Listener) = listeners.add(listener)
    fun remove(listener: Listener) = listeners.remove(listener)

    /** The last few events this session (Home's "Recent" and Control's notifications); memory only. */
    private val recent = java.util.ArrayDeque<JSONObject>()

    fun recent(): List<JSONObject> = synchronized(recent) { recent.toList().reversed() }

    fun event(event: JSONObject) {
        synchronized(recent) {
            recent.addLast(event)
            while (recent.size > 30) recent.removeFirst()
        }
        listeners.forEach { it.onEvent(event) }
    }
    fun routeChanged(base: String?) = listeners.forEach { it.onRoute(base) }
}
