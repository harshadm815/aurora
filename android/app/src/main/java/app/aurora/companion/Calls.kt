package app.aurora.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.telecom.CallAudioState
import android.telecom.Connection
import android.telecom.ConnectionRequest
import android.telecom.ConnectionService
import android.telecom.DisconnectCause
import android.telecom.PhoneAccount
import android.telecom.PhoneAccountHandle
import android.telecom.TelecomManager

/**
 * Calls with AURORA through Android's own call system (a "self-managed" calling app): a real ringing
 * screen that works over the lock screen, the earpiece, speaker or a Bluetooth headset, and other
 * calls take priority. These calls never touch the phone network: they go to AURORA on your PC.
 */
object Calls {
    interface Listener {
        fun onCallChanged() {}
    }

    const val CHANNEL = "calls"
    const val NOTIFICATION_ID = 7
    private const val EXTRA_REASON = "aurora_reason"
    private const val EXTRA_TEXT = "aurora_text"
    private const val RING_SECONDS = 40L

    @Volatile var connection: AuroraConnection? = null
        private set
    @Volatile var listener: Listener? = null
    private val main = Handler(Looper.getMainLooper())

    // What the screen shows.
    @Volatile var state = ""
    @Volatile var heard = ""
    @Volatile var reply = ""
    @Volatile var ended = ""

    fun changed() = main.post { listener?.onCallChanged() }

    private fun handle(context: Context) =
        PhoneAccountHandle(ComponentName(context, CallService::class.java), "aurora")

    /** Registers AURORA as a calling app (once; Android keeps it). */
    fun register(context: Context) {
        val telecom = context.getSystemService(TelecomManager::class.java)
        telecom.registerPhoneAccount(
            PhoneAccount.builder(handle(context), "AURORA")
                .setCapabilities(PhoneAccount.CAPABILITY_SELF_MANAGED)
                .addSupportedUriScheme("aurora")
                .build()
        )
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Calls from AURORA",
            NotificationManager.IMPORTANCE_HIGH).apply {
            description = "AURORA ringing you (a reminder you asked to ring, or \"Call my phone\" on the PC)."
            setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE),
                AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 1000, 1000)
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        })
    }

    private fun extras(context: Context, reason: String, text: String) = Bundle().apply {
        putParcelable(TelecomManager.EXTRA_PHONE_ACCOUNT_HANDLE, handle(context))
        putBundle(TelecomManager.EXTRA_INCOMING_CALL_EXTRAS, Bundle().apply {
            putString(EXTRA_REASON, reason); putString(EXTRA_TEXT, text)
        })
        putBundle(TelecomManager.EXTRA_OUTGOING_CALL_EXTRAS, Bundle().apply {
            putString(EXTRA_REASON, reason); putString(EXTRA_TEXT, text)
        })
    }

    /** You call AURORA. Returns a problem to show, or null. */
    fun dial(context: Context): String? {
        if (connection != null) return "You're already on a call with AURORA."
        register(context)
        val telecom = context.getSystemService(TelecomManager::class.java)
        if (!telecom.isOutgoingCallPermitted(handle(context))) return "The phone is busy with another call."
        return try {
            telecom.placeCall(Uri.fromParts("aurora", "pc", null), extras(context, "", ""))
            null
        } catch (error: SecurityException) {
            "Android didn't allow the call: ${error.message}"
        }
    }

    /** AURORA calls you (from the PC's "Call my phone", or a reminder set to ring). */
    fun ring(context: Context, reason: String, text: String) {
        if (connection != null) return
        register(context)
        val telecom = context.getSystemService(TelecomManager::class.java)
        if (!telecom.isIncomingCallPermitted(handle(context))) return
        try {
            telecom.addNewIncomingCall(handle(context), extras(context, reason, text))
        } catch (error: SecurityException) {
            // Not allowed now (another call): the reminder still arrives as a notification.
        }
    }

    fun answer(context: Context) = connection?.onAnswer()
    fun hangUp() = connection?.onDisconnect()

    fun setSpeaker(on: Boolean) {
        connection?.setAudioRoute(if (on) CallAudioState.ROUTE_SPEAKER else CallAudioState.ROUTE_EARPIECE)
    }

    fun setMuted(on: Boolean) {
        connection?.audio?.muted = on
    }

    fun screen(context: Context, incoming: Boolean): Intent =
        Intent(context, CallActivity::class.java)
            .putExtra(CallActivity.INCOMING, incoming)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)

    // --------------------------------------------------------------------- the connection

    class AuroraConnection(private val context: Context, val incoming: Boolean,
                           private val reason: String, private val text: String) : Connection() {
        var audio: CallAudio? = null
        private val timeout = Runnable { if (state == STATE_RINGING) end(DisconnectCause(DisconnectCause.MISSED)) }

        init {
            connectionProperties = PROPERTY_SELF_MANAGED
            audioModeIsVoip = true
            setAddress(Uri.fromParts("aurora", "pc", null), TelecomManager.PRESENTATION_ALLOWED)
            setCallerDisplayName("AURORA", TelecomManager.PRESENTATION_ALLOWED)
            connectionCapabilities = CAPABILITY_MUTE
        }

        override fun onShowIncomingCallUi() {
            val screen = PendingIntent.getActivity(context, 1, screen(context, true),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val notification = Notification.Builder(context, CHANNEL)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle("AURORA is calling")
                .setContentText(if (reason == "reminder") "A reminder" else "Tap to answer")
                .setCategory(Notification.CATEGORY_CALL)
                .setOngoing(true)
                .setContentIntent(screen)
                .setFullScreenIntent(screen, true)
                .build()
            notification.flags = notification.flags or Notification.FLAG_INSISTENT     // ring until answered
            context.getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification)
            main.postDelayed(timeout, RING_SECONDS * 1000)
        }

        override fun onAnswer() {
            if (state != STATE_RINGING) return
            main.removeCallbacks(timeout)
            context.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
            setActive()
            startAudio()
            context.startActivity(screen(context, false))
        }

        override fun onReject() = end(DisconnectCause(DisconnectCause.REJECTED))
        override fun onDisconnect() = end(DisconnectCause(DisconnectCause.LOCAL))
        override fun onAbort() = end(DisconnectCause(DisconnectCause.CANCELED))

        fun startAudio() {
            if (audio != null) return
            state_("connecting")
            audio = CallAudio(context, reason, text, object : CallAudio.Listener {
                override fun onConnected() { if (!incoming) main.post { setActive() }; state_("listening") }
                override fun onState(state: String) = state_(state)
                override fun onPartial(text: String) { heard = "$text…"; changed() }
                override fun onHeard(text: String) { heard = text; changed() }
                override fun onReply(text: String) { reply = text; changed() }
                override fun onEnded(reason: String) {
                    Calls.ended = when (reason) {
                        "goodbye", "ended", "hangup" -> "Call ended"
                        "silence" -> "Call ended (nothing was said)"
                        else -> reason
                    }
                    main.post { end(DisconnectCause(DisconnectCause.REMOTE)) }
                }
            }).also { it.start() }
        }

        private fun state_(value: String) {
            Calls.state = value
            changed()
        }

        fun end(cause: DisconnectCause) {
            main.removeCallbacks(timeout)
            if (state == STATE_DISCONNECTED) return
            audio?.hangUp()
            context.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
            setDisconnected(cause)
            destroy()
            if (connection === this) connection = null
            Calls.state = "ended"
            changed()
        }
    }

    class CallService : ConnectionService() {
        private fun create(request: ConnectionRequest, incoming: Boolean): Connection {
            val extras = request.extras?.let {
                it.getBundle(TelecomManager.EXTRA_INCOMING_CALL_EXTRAS) ?: it.getBundle(TelecomManager.EXTRA_OUTGOING_CALL_EXTRAS) ?: it
            }
            val call = AuroraConnection(applicationContext, incoming,
                extras?.getString(EXTRA_REASON) ?: "", extras?.getString(EXTRA_TEXT) ?: "")
            connection = call
            state = if (incoming) "ringing" else "calling"
            heard = ""; reply = ""; ended = ""
            if (incoming) call.setRinging() else call.setDialing()
            changed()
            return call
        }

        override fun onCreateIncomingConnection(account: PhoneAccountHandle?, request: ConnectionRequest) =
            create(request, true)

        override fun onCreateOutgoingConnection(account: PhoneAccountHandle?, request: ConnectionRequest): Connection {
            val call = create(request, false)
            (call as AuroraConnection).startAudio()
            startActivity(screen(this, false))
            return call
        }

        override fun onCreateIncomingConnectionFailed(account: PhoneAccountHandle?, request: ConnectionRequest?) {
            connection = null
        }

        override fun onCreateOutgoingConnectionFailed(account: PhoneAccountHandle?, request: ConnectionRequest?) {
            connection = null
            ended = "The phone couldn't start the call."
            changed()
        }
    }
}
