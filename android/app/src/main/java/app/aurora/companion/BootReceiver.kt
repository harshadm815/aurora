package app.aurora.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** After the phone restarts, reconnects to AURORA if "Stay connected" is on. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) LinkService.start(context)
    }
}
