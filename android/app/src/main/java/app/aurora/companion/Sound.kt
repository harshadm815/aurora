package app.aurora.companion

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.speech.tts.TextToSpeech
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** Playing AURORA's voice (reminders said aloud), with Android's own voice when the PC can't speak. */
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
