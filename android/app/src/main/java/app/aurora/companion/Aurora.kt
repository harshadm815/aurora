package app.aurora.companion

import android.content.Context
import org.json.JSONObject
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.URL
import java.security.SecureRandom
import java.security.cert.X509Certificate
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/** AURORA's answer: the HTTP status and its JSON (or an error message). */
class Reply(val status: Int, val json: JSONObject) {
    val ok get() = status == 200
    val error: String get() = json.optString("error", "Something went wrong (HTTP $status).")
}

class NotConnected : IOException("Can't reach your PC. Plug in the cable, or be on your home Wi-Fi with AURORA open.")

/**
 * The connection to AURORA on the PC, like an account the phone is signed in to.
 * Remembers the device key, the PC's certificate fingerprint and its Wi-Fi address; signs every
 * request with [PhoneKey]; finds the PC over the USB cable or the home Wi-Fi.
 */
class Aurora(context: Context) {
    private val prefs = context.getSharedPreferences("aurora", Context.MODE_PRIVATE)
    private val random = SecureRandom()

    val paired: Boolean get() = prefs.getString("token", null) != null && PhoneKey.exists()
    val deviceName: String get() = prefs.getString("device", "") ?: ""

    @Volatile var route: String? = prefs.getString("route", null)
        private set

    private val fingerprint get() = prefs.getString("fingerprint", null)
    private val wifiBase get() = Protocol.wifiBase(prefs.getString("wifi_address", null), prefs.getInt("wifi_port", 0))

    // ------------------------------------------------------------------ pairing

    /** Pairs over the USB cable with the code shown on the PC. Throws IOException with a reason. */
    fun pair(code: String, name: String) {
        val publicKey = PhoneKey.create()
        val body = JSONObject().put("code", code).put("name", name).put("public_key", publicKey)
        val reply = send(Protocol.USB_BASE, "POST", "/api/pair", body.toString().toByteArray(), signed = false)
        if (!reply.ok) {
            PhoneKey.delete()
            throw IOException(reply.error)
        }
        val data = reply.json
        prefs.edit()
            .putString("token", data.getString("token"))
            .putString("device", data.optString("device", name))
            .putString("fingerprint", data.getString("fingerprint"))
            .putInt("wifi_port", data.optInt("wifi_port", 0))
            .putInt("discovery_port", data.optInt("discovery_port", 0))
            .putString("wifi_address", data.optString("wifi_address", "").ifBlank { null })
            .putString("route", Protocol.USB_BASE)
            .apply()
        route = Protocol.USB_BASE
    }

    fun forget() {
        prefs.edit().clear().apply()
        PhoneKey.delete()
        route = null
    }

    /** Signs out on both sides (the PC forgets this phone), as far as the PC can be reached. */
    fun signOut() {
        try {
            call("POST", "/api/unpair")
        } catch (error: IOException) {
            // Not reachable: the phone forgets anyway; disconnect it in Settings → Phone on the PC.
        }
        forget()
    }

    // ------------------------------------------------------------------ calls

    fun get(path: String, timeoutMs: Int = 10_000): Reply = call("GET", path, timeoutMs = timeoutMs)

    fun post(path: String, body: JSONObject, timeoutMs: Int = 10_000): Reply =
        call("POST", path, body.toString().toByteArray(), timeoutMs)

    /** Tries the routes in order (last good, USB, home Wi-Fi, then a discovery broadcast). */
    @Synchronized
    fun call(method: String, path: String, body: ByteArray = ByteArray(0), timeoutMs: Int = 10_000): Reply {
        for (base in Protocol.routes(route, wifiBase)) {
            try {
                return sendAndRemember(base, method, path, body, timeoutMs)
            } catch (error: NotConnected) {
                // try the next route
            }
        }
        val found = discover()
        if (found != null) {
            try {
                return sendAndRemember(found, method, path, body, timeoutMs)
            } catch (error: NotConnected) {
                // fall through
            }
        }
        remember(null)
        throw NotConnected()
    }

    private fun sendAndRemember(base: String, method: String, path: String, body: ByteArray, timeoutMs: Int): Reply {
        val reply = send(base, method, path, body, signed = true, timeoutMs = timeoutMs)
        remember(base)
        if (path == "/api/status" || path == "/api/connection") learnWifi(reply)
        return reply
    }

    private fun remember(base: String?) {
        route = base
        prefs.edit().putString("route", base).apply()
    }

    private fun learnWifi(reply: Reply) {
        val address = reply.json.optString("wifi_address", "")
        if (address.isNotBlank()) prefs.edit().putString("wifi_address", address).apply()
    }

    /** Asks the home network "AURORA?"; returns the HTTPS address if our PC answers. */
    private fun discover(): String? {
        val port = prefs.getInt("discovery_port", 0)
        if (port <= 0 || fingerprint == null) return null
        return try {
            DatagramSocket().use { socket ->
                socket.broadcast = true
                socket.soTimeout = 1500
                val question = Protocol.DISCOVERY_QUESTION.toByteArray()
                socket.send(DatagramPacket(question, question.size, InetAddress.getByName("255.255.255.255"), port))
                val buffer = ByteArray(256)
                val packet = DatagramPacket(buffer, buffer.size)
                socket.receive(packet)
                val httpsPort = Protocol.discoveryPort(String(buffer, 0, packet.length), fingerprint) ?: return null
                val address = packet.address.hostAddress ?: return null
                prefs.edit().putString("wifi_address", address).putInt("wifi_port", httpsPort).apply()
                Protocol.wifiBase(address, httpsPort)
            }
        } catch (error: IOException) {
            null
        }
    }

    private fun send(base: String, method: String, path: String, body: ByteArray, signed: Boolean,
                     timeoutMs: Int = 10_000): Reply {
        val connection = try {
            URL(base + path).openConnection() as HttpURLConnection
        } catch (error: IOException) {
            throw NotConnected()
        }
        try {
            if (connection is HttpsURLConnection) pin(connection)
            connection.requestMethod = method
            connection.connectTimeout = 3000
            connection.readTimeout = timeoutMs
            connection.useCaches = false
            connection.setRequestProperty("Content-Type", "application/json")
            if (signed) {
                val token = prefs.getString("token", null) ?: throw IOException("This phone isn't paired.")
                val time = (System.currentTimeMillis() / 1000).toString()
                val nonce = ByteArray(12).also { random.nextBytes(it) }.joinToString("") { "%02x".format(it) }
                connection.setRequestProperty("Authorization", "Bearer $token")
                connection.setRequestProperty("X-Aurora-Time", time)
                connection.setRequestProperty("X-Aurora-Nonce", nonce)
                connection.setRequestProperty(
                    "X-Aurora-Signature",
                    PhoneKey.sign(Protocol.canonical(method, path, time, nonce, if (method == "POST") body else ByteArray(0)))
                )
            }
            if (method == "POST") {
                connection.doOutput = true
                connection.setFixedLengthStreamingMode(body.size)
            }
            try {
                connection.connect()                       // nobody there, or not our PC's certificate: not this route
            } catch (error: IOException) {
                throw NotConnected()
            }
            if (method == "POST") connection.outputStream.use { it.write(body) }
            val status = connection.responseCode           // a slow answer (read timeout) is a real error
            if (connection.getHeaderField("Server")?.startsWith("AURORA") != true) throw NotConnected()
            val stream = if (status < 400) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
            val json = try { JSONObject(text) } catch (error: Exception) { JSONObject() }
            if (status == 401 && signed && json.optString("error").contains("isn't paired")) forget()
            return Reply(status, json)
        } finally {
            connection.disconnect()
        }
    }

    /** Accepts only the PC's own certificate, pinned at pairing. */
    private fun pin(connection: HttpsURLConnection) {
        val pinned = fingerprint
        val trust = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
                throw java.security.cert.CertificateException("Not used")

            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                if (chain.isEmpty() || !Protocol.certificateMatches(chain[0].encoded, pinned))
                    throw java.security.cert.CertificateException("Not AURORA's certificate")
            }

            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        val tls = SSLContext.getInstance("TLS")
        tls.init(null, arrayOf(trust), random)
        connection.sslSocketFactory = tls.socketFactory
        connection.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }   // pinned instead
    }
}
