package app.aurora.companion

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import org.json.JSONObject
import java.io.DataInputStream
import java.io.IOException
import java.io.OutputStream
import java.net.Socket
import java.net.URLEncoder
import kotlin.concurrent.thread

/**
 * The sound of a call (aurora/mobile/call.py): the microphone goes to the PC in 0.1 s frames, AURORA's
 * voice comes back and is played; the phone's echo canceller keeps AURORA from hearing itself.
 * Events (state, what was heard, AURORA's reply, the end) go to [listener] on a background thread.
 */
class CallAudio(private val context: Context, private val reason: String, private val text: String,
                private val listener: Listener) {
    interface Listener {
        fun onState(state: String) {}
        fun onHeard(text: String) {}
        fun onPartial(text: String) {}
        fun onReply(text: String) {}
        fun onConnected() {}
        fun onEnded(reason: String) {}
        /** Loudness 0-1 for the animations: [mic] your voice (0 while muted), [voice] AURORA's. */
        fun onLevel(mic: Float, voice: Float) {}
        /** The link dropped and the phone is trying again (once). */
        fun onReconnecting() {}
    }

    companion object {
        private const val RATE = 16000
        private val SILENCE = ByteArray(RATE / 10 * 2)
    }

    @Volatile var muted = false
    /** True once the call is with AURORA on the phone (the PC couldn't be reached). */
    @Volatile var offline = false
    @Volatile private var running = false
    private var socket: Socket? = null
    private val sendLock = Any()

    fun start() {
        running = true
        thread(name = "aurora-call") { run() }
    }

    /** Hangs up (tells the PC, then closes). Safe to call more than once. */
    fun hangUp() {
        if (!running) return
        running = false
        thread(name = "aurora-hangup") {
            try { send('J'.code.toByte(), JSONObject().put("hangup", true).toString().toByteArray()) } catch (e: IOException) { }
            try { socket?.close() } catch (e: IOException) { }
        }
    }

    private fun send(kind: Byte, payload: ByteArray) {
        val out: OutputStream = socket?.getOutputStream() ?: return
        synchronized(sendLock) {
            val header = byteArrayOf(kind, (payload.size ushr 24).toByte(), (payload.size ushr 16).toByte(),
                (payload.size ushr 8).toByte(), payload.size.toByte())
            out.write(header + payload)
            out.flush()
        }
    }

    @Volatile private var voiceLevel = 0f

    private fun run() {
        var ended = "ended"
        var attempt = 0
        var callReason = reason
        var micThread: Thread? = null
        try {
            while (true) {
                try {
                    val query = "reason=" + URLEncoder.encode(callReason, "UTF-8") + "&text=" +
                        URLEncoder.encode(text.take(300), "UTF-8")
                    socket = Aurora.get(context).openCall("/api/call?$query")
                    socket!!.soTimeout = 0                         // the PC talks when it has something to say
                    listener.onConnected()
                    micThread = thread(name = "aurora-call-mic") { microphone() }
                    ended = receive()
                    micThread?.join(1000)
                    break
                } catch (error: IOException) {
                    // The link dropped in the middle of the call (Wi-Fi changed, PC busy): try once more.
                    val dropped = running && error !is NotConnected && attempt == 0
                    try { socket?.close() } catch (e: IOException) { }
                    micThread?.join(2000)                         // the old microphone lets go first
                    if (!dropped) throw error
                    attempt++
                    callReason = "reconnect"
                    listener.onReconnecting()
                    Thread.sleep(1500)
                    if (!running) { ended = "ended"; break }
                }
            }
        } catch (error: NotConnected) {
            ended = when {
                attempt > 0 -> "The connection to your PC was lost."
                // The PC can't be reached at all: talk with AURORA on the phone itself (offline plan C).
                LocalCall.possible(context) -> { offline = true; LocalCall(context, listener, { running }, { muted }).run() }
                else -> "Can't reach your PC."
            }
        } catch (error: IOException) {
            ended = if (running) (error.message ?: "The call was cut off.") else "ended"
        } catch (error: InterruptedException) {
            ended = "ended"
        } finally {
            running = false
            try { socket?.close() } catch (e: IOException) { }
            listener.onEnded(ended)
        }
    }

    @SuppressLint("MissingPermission")                    // checked before a call starts
    private fun microphone() {
        val size = maxOf(AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), RATE)
        val record = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, RATE,
            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size)
        val echo = if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(record.audioSessionId) else null
        val noise = if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(record.audioSessionId) else null
        echo?.enabled = true
        noise?.enabled = true
        try {
            record.startRecording()
            val buffer = ByteArray(RATE / 10 * 2)          // 0.1 s
            while (running) {
                var filled = 0
                while (filled < buffer.size && running) {
                    val read = record.read(buffer, filled, buffer.size - filled)
                    if (read <= 0) break
                    filled += read
                }
                send('A'.code.toByte(), if (muted) SILENCE else buffer.copyOf(filled))
                listener.onLevel(if (muted) 0f else Levels.of(buffer, filled), voiceLevel)
                voiceLevel *= 0.6f                          // fades unless AURORA keeps speaking
            }
        } catch (error: IOException) {
            // the call ended
        } finally {
            echo?.release()
            noise?.release()
            record.stop()
            record.release()
        }
    }

    /** Reads the PC's frames until the call ends; returns why it ended. */
    private fun receive(): String {
        val input = DataInputStream(socket!!.getInputStream().buffered())
        var player: AudioTrack? = null
        var rate = 22050
        try {
            while (running) {
                val kind = input.readByte()
                val length = input.readInt()
                if (length < 0 || length > 1_048_576) return "The PC sent something unexpected."
                val payload = ByteArray(length).also { input.readFully(it) }
                when (kind.toInt().toChar()) {
                    'P' -> {
                        val track = player ?: speakerTrack(rate).also { player = it; it.play() }
                        voiceLevel = maxOf(voiceLevel, Levels.of(payload))
                        track.write(payload, 0, payload.size)
                    }
                    'J' -> {
                        val event = JSONObject(String(payload))
                        if (event.has("rate") && event.getInt("rate") != rate) {
                            rate = event.getInt("rate")
                            player?.release(); player = null
                        }
                        if (event.optBoolean("flush")) {           // you interrupted: stop at once
                            player?.pause(); player?.flush(); player?.play()
                        }
                        event.optString("state").takeIf { it.isNotEmpty() }?.let(listener::onState)
                        event.optString("partial").takeIf { it.isNotEmpty() }?.let(listener::onPartial)
                        event.optString("heard").takeIf { it.isNotEmpty() }?.let(listener::onHeard)
                        event.optString("reply").takeIf { it.isNotEmpty() }?.let(listener::onReply)
                        event.optString("error").takeIf { it.isNotEmpty() }?.let { return it }
                        if (event.has("end")) {
                            Thread.sleep(1200)                     // let the goodbye finish playing
                            return event.optString("end")
                        }
                    }
                }
            }
            return "ended"
        } finally {
            player?.release()
        }
    }

    private fun speakerTrack(rate: Int): AudioTrack = AudioTrack.Builder()
        .setAudioAttributes(AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
        .setAudioFormat(AudioFormat.Builder().setSampleRate(rate)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
        .setBufferSizeInBytes(maxOf(AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT), rate * 2))
        .setTransferMode(AudioTrack.MODE_STREAM)
        .build()
}
