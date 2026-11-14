package app.aurora.companion

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.speech.tts.TextToSpeech
import java.io.ByteArrayOutputStream
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * Recording while you hold the talk button (16 kHz mono, nothing saved), and playing AURORA's voice.
 * The microphone is open only between [Recorder.start] and [Recorder.stop].
 */
class Recorder {
    companion object {
        const val RATE = 16000
        const val MAX_SECONDS = 30
    }

    @Volatile private var recording = false
    private var worker: Thread? = null
    private val audio = ByteArrayOutputStream()

    val active get() = recording

    /** Starts recording; [onLevel] gets the loudness (0..1) a few times a second. */
    @android.annotation.SuppressLint("MissingPermission")     // checked by the caller before starting
    fun start(onLevel: (Float) -> Unit = {}) {
        if (recording) return
        audio.reset()
        val size = maxOf(AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), RATE)
        val record = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE,
            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size)
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            throw IllegalStateException("The microphone isn't available.")
        }
        recording = true
        record.startRecording()
        worker = thread(name = "aurora-record") {
            val buffer = ByteArray(RATE / 5)               // 0.1 s
            try {
                while (recording && audio.size() < RATE * 2 * MAX_SECONDS) {
                    val read = record.read(buffer, 0, buffer.size)
                    if (read <= 0) break
                    audio.write(buffer, 0, read)
                    var peak = 0
                    for (i in 0 until read - 1 step 2) {
                        val sample = (buffer[i].toInt() and 0xff) or (buffer[i + 1].toInt() shl 8)
                        peak = maxOf(peak, kotlin.math.abs(sample.toShort().toInt()))
                    }
                    onLevel(peak / 32768f)
                }
            } finally {
                record.stop()
                record.release()
                recording = false
            }
        }
    }

    /** Stops and returns the recording as a WAV file (empty if too short to be speech). */
    fun stop(): ByteArray {
        recording = false
        worker?.join(2000)
        worker = null
        val pcm = audio.toByteArray()
        return if (pcm.size < RATE / 2) ByteArray(0) else Protocol.wav(pcm, RATE)   // under 0.25 s: a slip
    }
}

object Speech {
    @Volatile private var stopped = false

    /** Stops a reminder being spoken (the reminder screen's buttons). */
    fun stopReminder() {
        stopped = true
    }

    /** Plays a WAV and waits until it ends. [alarm]: through the alarm volume (reminders). */
    fun play(wav: ByteArray, alarm: Boolean = false): Boolean {
        stopped = false
        val (pcm, rate) = Protocol.pcm(wav) ?: return false
        val attributes = AudioAttributes.Builder()
            .setUsage(if (alarm) AudioAttributes.USAGE_ALARM else AudioAttributes.USAGE_ASSISTANT)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
        val track = AudioTrack.Builder()
            .setAudioAttributes(attributes)
            .setAudioFormat(AudioFormat.Builder().setSampleRate(rate)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setBufferSizeInBytes(pcm.size)
            .setTransferMode(AudioTrack.MODE_STATIC)
            .build()
        return try {
            track.write(pcm, 0, pcm.size)
            track.play()
            val end = System.currentTimeMillis() + pcm.size / 2 * 1000L / rate + 200
            while (System.currentTimeMillis() < end && !stopped) Thread.sleep(50)
            true
        } catch (error: Exception) {
            false
        } finally {
            track.stop()
            track.release()
        }
    }

    /** When the PC can't make AURORA's voice: Android's own voice (on the phone), and waits. */
    fun fallback(context: Context, text: String, alarm: Boolean = false) {
        val ready = CountDownLatch(1)
        val done = CountDownLatch(1)
        var tts: TextToSpeech? = null
        tts = TextToSpeech(context.applicationContext) { status ->
            if (status == TextToSpeech.SUCCESS) ready.countDown() else done.countDown()
        }
        try {
            if (!ready.await(5, TimeUnit.SECONDS)) return
            tts.language = Locale.getDefault()
            tts.setAudioAttributes(AudioAttributes.Builder()
                .setUsage(if (alarm) AudioAttributes.USAGE_ALARM else AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            tts.setOnUtteranceProgressListener(object : android.speech.tts.UtteranceProgressListener() {
                override fun onStart(id: String?) {}
                override fun onDone(id: String?) = done.countDown()
                @Deprecated("Deprecated in Java") override fun onError(id: String?) = done.countDown()
            })
            tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "aurora")
            done.await(60, TimeUnit.SECONDS)
        } finally {
            tts.shutdown()
        }
    }

    /** AURORA's voice from the PC if possible, else the phone's. Blocking: call off the main thread. */
    fun say(context: Context, text: String, alarm: Boolean = false) {
        val wav = Aurora.get(context).say(text)
        if (wav == null || !play(wav, alarm)) fallback(context, text, alarm)
    }
}
