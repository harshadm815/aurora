package app.aurora.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import org.json.JSONObject
import java.io.IOException
import kotlin.concurrent.thread

/**
 * Keeps the phone connected to AURORA in the background ("Stay connected"): one request to
 * /api/events is always waiting, so reminders and replies arrive at once. Android requires a
 * notification while it runs ("AURORA · connected by …"); it's silent and low in the list.
 */
class LinkService : Service(), Live.Listener {
    companion object {
        private const val LINK_ID = 1
        private const val CHANNEL_LINK = "link"
        private const val CHANNEL_REMINDERS = "reminder_alerts"     // sound, vibration, full screen
        private const val CHANNEL_REPLIES = "replies"
        private const val WAIT_SECONDS = 25
        private val BACKOFF = longArrayOf(5_000, 10_000, 20_000, 30_000, 60_000)

        fun enabled(context: Context) = Aurora.get(context).prefs.getBoolean("stay_connected", true)

        /** Reminder options (PC tab): speak them in AURORA's voice; show the full-screen reminder. */
        fun option(context: Context, name: String, default: Boolean = true) =
            Aurora.get(context).prefs.getBoolean(name, default)
        fun setOption(context: Context, name: String, on: Boolean) =
            Aurora.get(context).prefs.edit().putBoolean(name, on).apply()

        fun setEnabled(context: Context, on: Boolean) {
            Aurora.get(context).prefs.edit().putBoolean("stay_connected", on).apply()
            if (on) start(context) else stop(context)
        }

        fun start(context: Context) {
            if (!Aurora.get(context).paired || !enabled(context)) return
            context.startForegroundService(Intent(context, LinkService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, LinkService::class.java))
        }

        fun channels(context: Context) {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(
                CHANNEL_LINK, "Connection to your PC", NotificationManager.IMPORTANCE_MIN
            ).apply { description = "Shown while AURORA stays connected in the background." })
            manager.deleteNotificationChannel("reminders")          // 0.3.0's quieter channel
            manager.createNotificationChannel(NotificationChannel(
                CHANNEL_REMINDERS, "Reminders", NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Reminders from AURORA when they're due: sound, vibration and the reminder screen."
                setSound(
                    android.media.RingtoneManager.getDefaultUri(android.media.RingtoneManager.TYPE_NOTIFICATION),
                    android.media.AudioAttributes.Builder()
                        .setUsage(android.media.AudioAttributes.USAGE_ALARM)
                        .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
                )
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 400, 200, 400)
                lockscreenVisibility = Notification.VISIBILITY_PRIVATE
            })
            manager.createNotificationChannel(NotificationChannel(
                CHANNEL_REPLIES, "Replies", NotificationManager.IMPORTANCE_DEFAULT
            ).apply { description = "AURORA's answers while the app is in the background." })
        }
    }

    @Volatile private var running = false
    private var worker: Thread? = null
    private var nextId = 100

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        channels(this)
        startForeground(LINK_ID, linkNotification(Aurora.get(this).route))
        Live.add(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!running) {
            running = true
            worker = thread(name = "aurora-link") { loop() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        worker?.interrupt()
        Live.remove(this)
        super.onDestroy()
    }

    override fun onRoute(base: String?) {
        getSystemService(NotificationManager::class.java).notify(LINK_ID, linkNotification(base))
    }

    private fun loop() {
        val aurora = Aurora.get(this)
        var failures = 0
        while (running) {
            if (!aurora.paired) break
            val session = aurora.prefs.getString("event_session", "") ?: ""
            val after = aurora.prefs.getInt("event_last", -1)
            try {
                val reply = aurora.get(
                    "/api/events?after=$after&wait=$WAIT_SECONDS&session=$session",
                    timeoutMs = (WAIT_SECONDS + 15) * 1000
                )
                if (!reply.ok) throw IOException(reply.error)
                failures = 0
                val data = reply.json
                aurora.prefs.edit()
                    .putString("event_session", data.optString("session"))
                    .putInt("event_last", data.optInt("last", after))
                    .apply()
                val events = data.optJSONArray("events")
                // The first answer only says where the feed is: nothing old is shown as new.
                if (after >= 0 || session.isNotEmpty()) {
                    for (i in 0 until (events?.length() ?: 0)) handle(events!!.getJSONObject(i))
                }
            } catch (error: IOException) {
                if (!running) break
                val pause = BACKOFF[minOf(failures, BACKOFF.size - 1)]
                failures++
                try { Thread.sleep(pause) } catch (stop: InterruptedException) { break }
            }
        }
        if (!aurora.paired) stopSelf()
    }

    private fun handle(event: JSONObject) {
        Live.event(event)
        when (event.optString("kind")) {
            "reminder" -> if (option(this, "reminder_call", false)) {
                notify(CHANNEL_REPLIES, "Reminder", event.optString("text"))      // kept if you miss the call
                Calls.ring(this, "reminder", event.optString("text"))
            } else remind(event.optString("text"))
            "call" -> Calls.ring(this, event.optString("reason", "pc"), "")
            "file" -> fetch(event.optString("file"), event.optString("name", "file"))
            "message" -> if (event.optString("source") == "phone" && !Live.visible)
                notify(CHANNEL_REPLIES, "AURORA replied", event.optString("reply"))
        }
    }

    /** A file you sent from the PC: saved in Downloads/AURORA; the notification opens it. */
    private fun fetch(id: String, name: String) {
        if (id.isBlank()) return
        thread(name = "aurora-file") {
            try {
                val uri = Aurora.get(this).download(id) { real, size, input -> Files.saveDownload(this, real, size, input) }
                val open = if (uri.scheme == "content") PendingIntent.getActivity(
                    this, nextId, Intent(Intent.ACTION_VIEW).setDataAndType(uri, Files.mimeOf(name))
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK),
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
                ) else openApp()
                notify(CHANNEL_REPLIES, "From your PC", "$name is in Downloads › ${Files.FOLDER}. Tap to open.", open)
            } catch (error: IOException) {
                notify(CHANNEL_REPLIES, "A file from your PC didn't arrive", error.message ?: "Send it again from the PC.")
            }
        }
    }

    private fun openApp(): PendingIntent = PendingIntent.getActivity(
        this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
    )

    /** A due reminder: sound and the reminder screen (over the lock screen), then AURORA says it. */
    private fun remind(text: String) {
        val id = nextId++
        val screen = PendingIntent.getActivity(
            this, id, Intent(this, ReminderActivity::class.java)
                .putExtra(ReminderActivity.TEXT, text)
                .putExtra(ReminderActivity.NOTIFICATION, id)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_USER_ACTION),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val builder = Notification.Builder(this, CHANNEL_REMINDERS)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Reminder")
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setCategory(Notification.CATEGORY_ALARM)
            .setContentIntent(screen)
            .setAutoCancel(true)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setPublicVersion(Notification.Builder(this, CHANNEL_REMINDERS)
                .setSmallIcon(R.drawable.ic_notify).setContentTitle("AURORA: reminder").build())
        if (option(this, "reminder_screen")) builder.setFullScreenIntent(screen, true)
        getSystemService(NotificationManager::class.java).notify(id, builder.build())
        if (option(this, "speak_reminders")) {
            thread(name = "aurora-say-reminder") {
                Thread.sleep(1500)                              // after the notification sound
                Speech.say(this, "Reminder: $text", alarm = true)
            }
        }
    }

    private fun notify(channel: String, title: String, text: String, tap: PendingIntent? = null) {
        val notification = Notification.Builder(this, channel)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setContentIntent(tap ?: openApp())
            .setAutoCancel(true)
            .setVisibility(Notification.VISIBILITY_PRIVATE)      // the lock screen shows no content
            .setPublicVersion(Notification.Builder(this, channel)
                .setSmallIcon(R.drawable.ic_notify).setContentTitle("AURORA").build())
            .build()
        getSystemService(NotificationManager::class.java).notify(nextId++, notification)
    }

    private fun linkNotification(base: String?): Notification =
        Notification.Builder(this, CHANNEL_LINK)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("AURORA")
            .setContentText(if (base == null) "Looking for your PC…" else "Connected by ${Protocol.routeName(base).lowercase()}")
            .setContentIntent(openApp())
            .setOngoing(true)
            .build()
}
