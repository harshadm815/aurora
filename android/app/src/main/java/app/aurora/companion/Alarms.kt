package app.aurora.companion

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import org.json.JSONArray
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

/**
 * Reminders that ring from the phone itself, so they come even when the PC is off. The phone keeps the
 * upcoming reminders (Sync.kt) and sets an Android alarm for each, [GRACE_MS] after it's due. When the
 * PC announces the reminder first (the usual case), the alarm stays quiet: whichever comes first marks
 * it shown (Offline.markShown).
 */
object Alarms {
    const val GRACE_MS = 30_000L
    private const val ACTION = "app.aurora.companion.REMINDER"
    private val format = SimpleDateFormat("yyyy-MM-dd'T'HH:mm", Locale.US)
    private val nextNotification = AtomicInteger(5000)

    fun key(id: Int) = "r$id"

    /** When a reminder ("2026-11-15T18:00", local time) should ring, in ms, or null. */
    fun dueAt(iso: String): Long? = try { synchronized(format) { format.parse(iso.take(16))?.time } } catch (e: Exception) { null }

    private fun intent(context: Context, id: Int, text: String) = PendingIntent.getBroadcast(
        context, id, Intent(context, AlarmReceiver::class.java).setAction(ACTION).putExtra("id", id).putExtra("text", text),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    /** Sets alarms for [reminders] (id, text, due) and cancels ones no longer there. */
    fun schedule(context: Context, reminders: JSONArray) {
        val manager = context.getSystemService(AlarmManager::class.java) ?: return
        val prefs = Aurora.get(context).prefs
        val before = prefs.getString("alarm_ids", "")!!.split(",").mapNotNull { it.toIntOrNull() }.toSet()
        val now = System.currentTimeMillis()
        val set = mutableSetOf<Int>()
        for (i in 0 until reminders.length()) {
            val r = reminders.getJSONObject(i)
            val id = r.optInt("id")
            val due = dueAt(r.optString("due")) ?: continue
            if (id <= 0 || due + GRACE_MS < now) continue
            val at = due + GRACE_MS
            val pending = intent(context, id, r.optString("text"))
            if (Build.VERSION.SDK_INT < 31 || manager.canScheduleExactAlarms())
                manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending)
            else manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending)
            set += id
        }
        for (old in before - set) manager.cancel(intent(context, old, ""))
        prefs.edit().putString("alarm_ids", set.joinToString(",")).apply()
    }

    /** After a restart Android forgets alarms: set them again from the phone's copy. */
    fun reschedule(context: Context) = schedule(context, Offline.reminders(context))

    /** Shows a reminder: sound, the reminder screen (also over the lock screen), then AURORA says it. */
    fun remind(context: Context, text: String) {
        val id = nextNotification.incrementAndGet()
        val screen = PendingIntent.getActivity(
            context, id, Intent(context, ReminderActivity::class.java)
                .putExtra(ReminderActivity.TEXT, text)
                .putExtra(ReminderActivity.NOTIFICATION, id)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_USER_ACTION),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        LinkService.channels(context)
        val builder = Notification.Builder(context, LinkService.CHANNEL_REMINDERS)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Reminder")
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setCategory(Notification.CATEGORY_ALARM)
            .setContentIntent(screen)
            .setAutoCancel(true)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setPublicVersion(Notification.Builder(context, LinkService.CHANNEL_REMINDERS)
                .setSmallIcon(R.drawable.ic_notify).setContentTitle("AURORA: reminder").build())
        if (LinkService.option(context, "reminder_screen")) builder.setFullScreenIntent(screen, true)
        context.getSystemService(NotificationManager::class.java).notify(id, builder.build())
        if (LinkService.option(context, "speak_reminders")) {
            thread(name = "aurora-say-reminder") {
                Thread.sleep(1500)                              // after the notification sound
                Speech.say(context, "Reminder: $text", alarm = true)
            }
        }
    }
}

/** The phone's own reminder alarm went off: ring unless the PC already announced it. */
class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getIntExtra("id", 0)
        val text = intent.getStringExtra("text") ?: return
        // The PC is probably off (it would have announced it), so no "ring as a call": just the reminder.
        if (id > 0 && Offline.markShown(context, Alarms.key(id))) Alarms.remind(context, text)
    }
}
