package app.aurora.companion

import java.security.MessageDigest

/**
 * The parts of talking to AURORA that don't need Android, so they can be unit-tested on the PC.
 * Must match aurora/mobile/signing.py and server.py.
 */
object Protocol {
    const val USB_BASE = "http://localhost:8765"
    const val DISCOVERY_QUESTION = "AURORA?"

    fun sha256Hex(data: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }

    /** METHOD \n PATH \n TIME \n NONCE \n SHA-256(BODY): what the phone's key signs. */
    fun canonical(method: String, path: String, time: String, nonce: String, body: ByteArray): ByteArray =
        listOf(method.uppercase(), path, time, nonce, sha256Hex(body)).joinToString("\n").toByteArray(Charsets.UTF_8)

    /** The same, for a body sent in pieces: [bodyHash] is its SHA-256 (hex), worked out beforehand. */
    fun canonicalHash(method: String, path: String, time: String, nonce: String, bodyHash: String): ByteArray =
        listOf(method.uppercase(), path, time, nonce, bodyHash.lowercase()).joinToString("\n").toByteArray(Charsets.UTF_8)

    /** True only for the exact certificate AURORA showed at pairing (SHA-256 of its DER bytes). */
    fun certificateMatches(der: ByteArray, pinned: String?): Boolean =
        !pinned.isNullOrBlank() && MessageDigest.isEqual(
            sha256Hex(der).toByteArray(), pinned.lowercase().toByteArray()
        )

    fun wifiBase(address: String?, port: Int): String? =
        if (address.isNullOrBlank() || port <= 0) null else "https://$address:$port"

    /** Where to try, best first: the route that worked last time, then USB, then home Wi-Fi. */
    fun routes(lastGood: String?, wifi: String?): List<String> =
        listOfNotNull(lastGood, USB_BASE, wifi).distinct()

    /** The PC's answer to a discovery broadcast: its HTTPS port if the fingerprint prefix matches ours. */
    fun discoveryPort(answer: String, pinned: String?): Int? {
        val port = Regex("\"port\"\\s*:\\s*(\\d+)").find(answer)?.groupValues?.get(1)?.toIntOrNull()
        val prefix = Regex("\"fp\"\\s*:\\s*\"([0-9a-f]+)\"").find(answer)?.groupValues?.get(1)
        if (port == null || prefix == null || pinned == null || prefix.length < 8) return null
        return if (pinned.lowercase().startsWith(prefix)) port else null
    }

    /** A 16-bit mono WAV file around [pcm] (what /api/voice expects). */
    fun wav(pcm: ByteArray, rate: Int): ByteArray {
        val out = java.io.ByteArrayOutputStream(44 + pcm.size)
        fun int(v: Int) = out.write(byteArrayOf(v.toByte(), (v shr 8).toByte(), (v shr 16).toByte(), (v shr 24).toByte()))
        fun short(v: Int) = out.write(byteArrayOf(v.toByte(), (v shr 8).toByte()))
        out.write("RIFF".toByteArray()); int(36 + pcm.size); out.write("WAVE".toByteArray())
        out.write("fmt ".toByteArray()); int(16); short(1); short(1); int(rate); int(rate * 2); short(2); short(16)
        out.write("data".toByteArray()); int(pcm.size); out.write(pcm)
        return out.toByteArray()
    }

    /** PCM and sample rate from a 16-bit mono WAV (what /api/speak returns), or null. */
    fun pcm(wav: ByteArray): Pair<ByteArray, Int>? {
        fun int(at: Int) = (wav[at].toInt() and 0xff) or ((wav[at + 1].toInt() and 0xff) shl 8) or
            ((wav[at + 2].toInt() and 0xff) shl 16) or ((wav[at + 3].toInt() and 0xff) shl 24)
        if (wav.size < 44 || String(wav, 0, 4) != "RIFF" || String(wav, 8, 4) != "WAVE") return null
        var at = 12
        var rate = 0
        while (at + 8 <= wav.size) {
            val id = String(wav, at, 4)
            val size = int(at + 4)
            if (id == "fmt ") rate = int(at + 12)
            if (id == "data") {
                val end = minOf(wav.size, at + 8 + size)
                return if (rate > 0) wav.copyOfRange(at + 8, end) to rate else null
            }
            at += 8 + size + (size and 1)
        }
        return null
    }

    /** "Remind me in 10 minutes to …" for the reminder screen's snooze button. */
    fun snooze(text: String, minutes: Int = 10): String = "remind me in $minutes minutes to ${text.trim().trimEnd('.')}"

    fun routeName(base: String?): String = when {
        base == null -> "Not connected"
        base == USB_BASE -> "USB cable"
        base.startsWith("https://") -> "Home Wi-Fi"
        else -> "Connected"
    }
}
