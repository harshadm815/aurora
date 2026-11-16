package app.aurora.companion

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import org.json.JSONObject
import java.io.File
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * A call with AURORA when the PC can't be reached (offline plan C), entirely on the phone:
 * Vosk hears you (the same small English model the PC uses, copied over the cable), the phone's model
 * answers (LocalBrain), Android's own voice speaks. Same events as a call to the PC (CallAudio.Listener),
 * so the call screen, transcript and after-call summary work unchanged. Talking over AURORA stops it;
 * "goodbye" or 20 s of silence ends the call. Nothing is recorded.
 */
class LocalCall(private val context: Context, private val listener: CallAudio.Listener,
                private val running: () -> Boolean, private val muted: () -> Boolean) {
    companion object {
        const val VOSK_FOLDER = "vosk-model-small-en-us-0.15"
        private const val RATE = 16000
        private const val SILENCE_MS = 20_000L
        private val GOODBYE = Regex("""\b(good ?bye|bye( bye)?|that'?s all|hang up|end (the )?call)\b""", RegexOption.IGNORE_CASE)
        const val GREETING = "Hi, it's AURORA, on your phone. Your PC can't be reached, so I can only talk for now."

        fun voskFolder(context: Context) = File(context.getExternalFilesDir("models"), VOSK_FOLDER)

        /** True when the phone has everything for an offline call: the model and the speech recogniser. */
        fun possible(context: Context): Boolean =
            LocalBrain.available(context) && File(voskFolder(context), "am/final.mdl").isFile
    }

    private var tts: TextToSpeech? = null
    @Volatile private var speaking = false
    @Volatile private var interrupted = false             // you talked over AURORA: what you say next counts
    private var spoken = CountDownLatch(0)
    private val history = mutableListOf<Pair<String, String>>()

    /** Runs the call until it ends; returns why ("goodbye", "silence", "hangup" or a problem). */
    @SuppressLint("MissingPermission")                 // the microphone was allowed before the call started
    fun run(): String {
        val ready = CountDownLatch(1)
        var voice: TextToSpeech? = null
        voice = TextToSpeech(context) { status ->
            if (status == TextToSpeech.SUCCESS) {
                voice?.language = Locale.getDefault()
                voice?.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            }
            ready.countDown()
        }
        tts = voice
        ready.await(5, TimeUnit.SECONDS)
        val model = try { org.vosk.Model(voskFolder(context).path) } catch (error: Exception) {
            return "The phone's speech recogniser couldn't start."
        }
        val recognizer = org.vosk.Recognizer(model, RATE.toFloat())
        val size = maxOf(AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), RATE)
        val record = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, RATE, AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT, size)
        val echo = if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(record.audioSessionId) else null
        val noise = if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(record.audioSessionId) else null
        echo?.enabled = true
        noise?.enabled = true
        try {
            listener.onConnected()
            record.startRecording()
            say(GREETING)
            val buffer = ByteArray(RATE / 10 * 2)                    // 0.1 s
            var lastSpeech = System.currentTimeMillis()
            while (running()) {
                val read = record.read(buffer, 0, buffer.size)
                if (read <= 0) continue
                val silent = muted()
                listener.onLevel(if (silent) 0f else Levels.of(buffer, read), if (speaking) 0.5f else 0f)
                if (silent) continue
                val done = recognizer.acceptWaveForm(buffer, read)
                if (!done) {
                    val partial = JSONObject(recognizer.partialResult).optString("partial")
                    if (partial.isNotBlank()) {
                        lastSpeech = System.currentTimeMillis()
                        listener.onPartial(partial)
                        if (speaking && partial.split(" ").size >= 2) interrupt()   // you talked over AURORA
                    } else if (!speaking && System.currentTimeMillis() - lastSpeech > SILENCE_MS) {
                        say("I'll let you go. Bye for now.", wait = true)
                        return "silence"
                    }
                    continue
                }
                val heard = JSONObject(recognizer.result).optString("text").trim()
                if (heard.isEmpty()) continue
                if (speaking && !interrupted) continue                  // AURORA's own voice coming back: not you
                interrupted = false
                lastSpeech = System.currentTimeMillis()
                listener.onHeard(heard)
                if (GOODBYE.containsMatchIn(heard)) {
                    listener.onReply("Goodbye!")
                    say("Goodbye!", wait = true)
                    return "goodbye"
                }
                listener.onState("thinking")
                val answer = try {
                    LocalBrain.reply(context, history + ("user" to heard), maxTokens = 120)
                } catch (error: Exception) {
                    "Sorry, I couldn't think of an answer just now."
                }.ifBlank { "Sorry, I didn't catch that." }
                history += "user" to heard
                history += "assistant" to answer
                if (!running()) break
                listener.onReply(answer)
                recognizer.reset()
                say(answer)                                          // plays while the microphone keeps listening
            }
            return "hangup"
        } catch (error: Exception) {
            return "The offline call stopped: ${error.message}"
        } finally {
            LocalBrain.stop()
            tts?.stop()
            tts?.shutdown()
            try { record.stop() } catch (e: Exception) { }
            record.release()
            echo?.release()
            noise?.release()
            recognizer.close()
            model.close()
        }
    }

    /** Starts speaking [text]; the microphone keeps listening meanwhile. [wait]: return only when it's said. */
    private fun say(text: String, wait: Boolean = false) {
        val voice = tts ?: return
        val done = CountDownLatch(1)
        spoken = done
        voice.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String?) {}
            override fun onDone(id: String?) = finished(done)
            override fun onStop(id: String?, interrupted: Boolean) = finished(done)
            @Deprecated("Deprecated in Java") override fun onError(id: String?) = finished(done)
        })
        speaking = true
        listener.onState("speaking")
        voice.speak(text, TextToSpeech.QUEUE_FLUSH, null, "aurora-${System.nanoTime()}")
        if (wait) done.await(Math.min(30_000L, 2_000L + text.length * 90L), TimeUnit.MILLISECONDS)
    }

    private fun finished(done: CountDownLatch) {
        done.countDown()
        if (spoken === done) {
            speaking = false
            listener.onState("listening")
        }
    }

    private fun interrupt() {
        interrupted = true
        tts?.stop()
        speaking = false
        listener.onState("listening")
    }
}
