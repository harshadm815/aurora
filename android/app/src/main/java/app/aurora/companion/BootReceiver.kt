package app.aurora.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** After the phone restarts: sets the reminder alarms again, and reconnects if "Stay connected" is on. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        Alarms.reschedule(context)                     // Android forgets alarms when it restarts
        LinkService.start(context)
    }
}
