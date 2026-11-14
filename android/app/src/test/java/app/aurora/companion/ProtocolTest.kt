package app.aurora.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    @Test
    fun canonical_matches_the_pc() {
        // Same layout as aurora/mobile/signing.py canonical(): METHOD, path, time, nonce, sha256(body).
        val text = String(Protocol.canonical("post", "/api/chat", "1700000000", "abc123def", "{}".toByteArray()))
        assertEquals(
            "POST\n/api/chat\n1700000000\nabc123def\n" +
                "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a", text
        )
    }

    @Test
    fun a_streamed_file_signs_its_checksum_the_same_way() {
        // A file's request signs the checksum worked out beforehand: identical to signing the bytes.
        val bytes = "{}".toByteArray()
        val streamed = Protocol.canonicalHash("POST", "/api/file?name=a.txt", "1", "n0nce123", Protocol.sha256Hex(bytes).uppercase())
        assertEquals(String(Protocol.canonical("POST", "/api/file?name=a.txt", "1", "n0nce123", bytes)), String(streamed))
    }

    @Test
    fun empty_body_hash() {
        assertEquals("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", Protocol.sha256Hex(ByteArray(0)))
    }

    @Test
    fun only_the_pinned_certificate_matches() {
        val der = "certificate".toByteArray()
        val pin = Protocol.sha256Hex(der)
        assertTrue(Protocol.certificateMatches(der, pin))
        assertTrue(Protocol.certificateMatches(der, pin.uppercase()))
        assertFalse(Protocol.certificateMatches("other".toByteArray(), pin))
        assertFalse(Protocol.certificateMatches(der, null))
        assertFalse(Protocol.certificateMatches(der, ""))
    }

    @Test
    fun routes_try_the_last_good_one_first() {
        val wifi = "https://192.168.43.10:8766"
        assertEquals(listOf(Protocol.USB_BASE, wifi), Protocol.routes(null, wifi))
        assertEquals(listOf(wifi, Protocol.USB_BASE), Protocol.routes(wifi, wifi))
        assertEquals(listOf(Protocol.USB_BASE), Protocol.routes(Protocol.USB_BASE, null))
        assertNull(Protocol.wifiBase(null, 8766))
        assertNull(Protocol.wifiBase("192.168.43.10", 0))
    }

    @Test
    fun discovery_answers_must_match_our_pc() {
        val pin = "0123456789abcdef" + "0".repeat(48)
        assertEquals(8766, Protocol.discoveryPort("""{"aurora": 1, "port": 8766, "fp": "0123456789abcdef"}""", pin))
        assertNull(Protocol.discoveryPort("""{"aurora": 1, "port": 8766, "fp": "ffffffffffffffff"}""", pin))
        assertNull(Protocol.discoveryPort("""{"aurora": 1, "port": 8766, "fp": "0123"}""", pin))
        assertNull(Protocol.discoveryPort("nonsense", pin))
        assertNull(Protocol.discoveryPort("""{"port": 8766, "fp": "0123456789abcdef"}""", null))
    }

    @Test
    fun wav_round_trip() {
        val pcm = ByteArray(3200) { (it % 7).toByte() }
        val file = Protocol.wav(pcm, 16000)
        assertEquals(44 + 3200, file.size)
        val (back, rate) = Protocol.pcm(file)!!
        assertEquals(16000, rate)
        assertTrue(back.contentEquals(pcm))
        assertNull(Protocol.pcm("not a wav file at all, not even close".toByteArray()))
    }

    @Test
    fun snooze_sentence() {
        assertEquals("remind me in 10 minutes to drink water", Protocol.snooze(" drink water. "))
    }

    @Test
    fun route_names() {
        assertEquals("USB cable", Protocol.routeName(Protocol.USB_BASE))
        assertEquals("Home Wi-Fi", Protocol.routeName("https://192.168.43.10:8766"))
        assertEquals("Not connected", Protocol.routeName(null))
    }
}
