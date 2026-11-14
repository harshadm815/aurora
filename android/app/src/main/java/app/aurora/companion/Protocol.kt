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

    fun routeName(base: String?): String = when {
        base == null -> "Not connected"
        base == USB_BASE -> "USB cable"
        base.startsWith("https://") -> "Home Wi-Fi"
        else -> "Connected"
    }
}
