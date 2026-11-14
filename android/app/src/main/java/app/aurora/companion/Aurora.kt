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

/** AURORA's answer: the HTTP status and its JSON (or an error message); [audio] for /api/speak. */
class Reply(val status: Int, val json: JSONObject, val audio: ByteArray? = null) {
    val ok get() = status == 200
    val error: String get() = json.optString("error", "Something went wrong (HTTP $status).")
}

class NotConnected : IOException("Can't reach your PC. Plug in the cable, or be on your home Wi-Fi with AURORA open.")

/**
 * The connection to AURORA on the PC, like an account the phone is signed in to.
 * Remembers the device key, the PC's certificate fingerprint and its Wi-Fi address; signs every
 * request with [PhoneKey]; finds the PC over the USB cable or the home Wi-Fi.
 */
class Aurora private constructor(context: Context) {
    companion object {
        @Volatile private var shared: Aurora? = null

        /** One connection for the whole app (the screens and the background service). */
        fun get(context: Context): Aurora =
            shared ?: synchronized(this) { shared ?: Aurora(context.applicationContext).also { shared = it } }
    }

    val prefs = context.getSharedPreferences("aurora", Context.MODE_PRIVATE)
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
        Live.routeChanged(null)
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

    /** What was said in [wav] (16-bit mono), recognised on the PC. */
    fun hear(wav: ByteArray): Reply = call("POST", "/api/voice", wav, 60_000, "audio/wav")

    /** [text] in AURORA's voice (a WAV made on the PC), or null if the PC can't speak now. */
    fun say(text: String): ByteArray? = try {
        call("POST", "/api/speak", JSONObject().put("text", text).toString().toByteArray(), 60_000).audio
    } catch (error: IOException) {
        null
    }

    /**
     * Opens a call (GET [path] upgraded to the call stream, see aurora/mobile/call.py) on the first
     * route that works. Returns the connected socket, positioned after the server's 101 answer.
     */
    fun openCall(path: String): java.net.Socket {
        for (base in Protocol.routes(route, wifiBase)) {
            try { return connectCall(base, path).also { remember(base) } } catch (error: NotConnected) { }
        }
        val found = discover()
        if (found != null) {
            try { return connectCall(found, path).also { remember(found) } } catch (error: NotConnected) { }
        }
        remember(null)
        throw NotConnected()
    }

    private fun connectCall(base: String, path: String): java.net.Socket {
        val url = URL(base)
        val socket: java.net.Socket = try {
            if (base.startsWith("https://")) {
                (pinnedContext().socketFactory.createSocket() as javax.net.ssl.SSLSocket).apply {
                    connect(java.net.InetSocketAddress(url.host, url.port), 3000)
                    soTimeout = 10_000
                    startHandshake()                       // not our PC's certificate: fails here
                }
            } else {
                java.net.Socket().apply { connect(java.net.InetSocketAddress(url.host, url.port), 3000) }
            }
        } catch (error: IOException) {
            throw NotConnected()
        }
        try {
            socket.soTimeout = 30_000
            val token = prefs.getString("token", null) ?: throw IOException("This phone isn't paired.")
            val time = (System.currentTimeMillis() / 1000).toString()
            val nonce = ByteArray(12).also { random.nextBytes(it) }.joinToString("") { "%02x".format(it) }
            val signature = PhoneKey.sign(Protocol.canonical("GET", path, time, nonce, ByteArray(0)))
            val request = "GET $path HTTP/1.1\r\nHost: ${url.host}:${url.port}\r\nAuthorization: Bearer $token\r\n" +
                "X-Aurora-Time: $time\r\nX-Aurora-Nonce: $nonce\r\nX-Aurora-Signature: $signature\r\n" +
                "Connection: Upgrade\r\nUpgrade: aurora-call\r\n\r\n"
            socket.getOutputStream().apply { write(request.toByteArray()); flush() }
            val input = socket.getInputStream()
            val head = StringBuilder()
            while (!head.endsWith("\r\n\r\n")) {
                val byte = input.read()
                if (byte < 0) throw NotConnected()
                head.append(byte.toChar())
                if (head.length > 8192) throw IOException("Bad answer from the PC.")
            }
            val status = head.lineSequence().first().split(" ").getOrNull(1)?.toIntOrNull() ?: 0
            if (!head.contains("Server: AURORA")) throw NotConnected()
            if (status != 101) {
                val length = Regex("Content-Length: (\\d+)", RegexOption.IGNORE_CASE).find(head)?.groupValues?.get(1)?.toInt() ?: 0
                val body = ByteArray(length).also { var read = 0; while (read < length) { val n = input.read(it, read, length - read); if (n < 0) break; read += n } }
                val message = try { JSONObject(String(body)).optString("error") } catch (error: Exception) { "" }
                if (status == 401 && message.contains("isn't paired")) forget()
                throw IOException(message.ifBlank { "The PC couldn't start the call (HTTP $status)." })
            }
            return socket
        } catch (error: IOException) {
            socket.close()
            throw error
        }
    }

    // ------------------------------------------------------------------ files (aurora/mobile/files.py)

    /** Runs [block] on the first route where the PC answers (as [call] does). */
    private fun <T> onAnyRoute(block: (String) -> T): T {
        for (base in Protocol.routes(route, wifiBase)) {
            try { return block(base).also { remember(base) } } catch (error: NotConnected) { }
        }
        val found = discover()
        if (found != null) {
            try { return block(found).also { remember(found) } } catch (error: NotConnected) { }
        }
        remember(null)
        throw NotConnected()
    }

    /**
     * Sends a file to the PC's "From phone" folder. [sha256] (hex) is signed with the request, so the PC
     * keeps the file only if every byte arrives as it was; [open] gives the file's bytes ([size] of them).
     */
    fun upload(name: String, size: Long, sha256: String, open: () -> java.io.InputStream,
               progress: (Long) -> Unit = {}): Reply = onAnyRoute { base ->
        val path = "/api/file?name=" + java.net.URLEncoder.encode(name, "UTF-8").replace("+", "%20")
        val connection = signedConnection(base, "POST", path, 120_000, sha256)
        try {
            connection.setRequestProperty("Content-Type", "application/octet-stream")
            connection.setRequestProperty("X-Aurora-Content-Sha256", sha256)
            connection.doOutput = true
            connection.setFixedLengthStreamingMode(size)
            try { connection.connect() } catch (error: IOException) { throw NotConnected() }
            connection.outputStream.use { out ->
                open().use { input ->
                    val buffer = ByteArray(64 * 1024)
                    var sent = 0L
                    while (true) {
                        val n = input.read(buffer)
                        if (n < 0) break
                        out.write(buffer, 0, n)
                        sent += n
                        progress(sent)
                    }
                }
            }
            reply(connection, signed = true)
        } finally {
            connection.disconnect()
        }
    }

    /** Fetches a file the PC offered (a "file" event); [save] gets its name, size and bytes. */
    fun <T> download(id: String, save: (name: String, size: Long, input: java.io.InputStream) -> T): T = onAnyRoute { base ->
        val path = "/api/file?id=" + java.net.URLEncoder.encode(id, "UTF-8")
        val connection = signedConnection(base, "GET", path, 60_000, null)
        try {
            try { connection.connect() } catch (error: IOException) { throw NotConnected() }
            val status = connection.responseCode
            if (connection.getHeaderField("Server")?.startsWith("AURORA") != true) throw NotConnected()
            if (status != 200) throw IOException(reply(connection, signed = true).error)
            val name = java.net.URLDecoder.decode(connection.getHeaderField("X-Aurora-Name") ?: "file", "UTF-8")
            connection.inputStream.use { save(name, connection.contentLengthLong, it) }
        } finally {
            connection.disconnect()
        }
    }

    /** A signed request, not yet connected ([bodyHash]: the signed SHA-256 of a streamed body). */
    private fun signedConnection(base: String, method: String, path: String, timeoutMs: Int, bodyHash: String?): HttpURLConnection {
        val connection = try {
            URL(base + path).openConnection() as HttpURLConnection
        } catch (error: IOException) {
            throw NotConnected()
        }
        if (connection is HttpsURLConnection) pin(connection)
        connection.requestMethod = method
        connection.connectTimeout = 3000
        connection.readTimeout = timeoutMs
        connection.useCaches = false
        connection.setRequestProperty("Connection", "close")
        val token = prefs.getString("token", null) ?: throw IOException("This phone isn't paired.")
        val time = (System.currentTimeMillis() / 1000).toString()
        val nonce = ByteArray(12).also { random.nextBytes(it) }.joinToString("") { "%02x".format(it) }
        connection.setRequestProperty("Authorization", "Bearer $token")
        connection.setRequestProperty("X-Aurora-Time", time)
        connection.setRequestProperty("X-Aurora-Nonce", nonce)
        connection.setRequestProperty("X-Aurora-Signature", PhoneKey.sign(
            if (bodyHash != null) Protocol.canonicalHash(method, path, time, nonce, bodyHash)
            else Protocol.canonical(method, path, time, nonce, ByteArray(0))))
        return connection
    }

    private fun reply(connection: HttpURLConnection, signed: Boolean): Reply {
        val status = connection.responseCode
        if (connection.getHeaderField("Server")?.startsWith("AURORA") != true) throw NotConnected()
        val stream = if (status < 400) connection.inputStream else connection.errorStream
        val text = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
        val json = try { JSONObject(text) } catch (error: Exception) { JSONObject() }
        if (status == 401 && signed && json.optString("error").contains("isn't paired")) forget()
        return Reply(status, json)
    }

    /** Tries the routes in order (last good, USB, home Wi-Fi, then a discovery broadcast). */
    fun call(method: String, path: String, body: ByteArray = ByteArray(0), timeoutMs: Int = 10_000,
             type: String = "application/json"): Reply {
        for (base in Protocol.routes(route, wifiBase)) {
            try {
                return sendAndRemember(base, method, path, body, timeoutMs, type)
            } catch (error: NotConnected) {
                // try the next route
            }
        }
        val found = discover()
        if (found != null) {
            try {
                return sendAndRemember(found, method, path, body, timeoutMs, type)
            } catch (error: NotConnected) {
                // fall through
            }
        }
        remember(null)
        throw NotConnected()
    }

    private fun sendAndRemember(base: String, method: String, path: String, body: ByteArray, timeoutMs: Int,
                                type: String): Reply {
        val reply = send(base, method, path, body, signed = true, timeoutMs = timeoutMs, type = type)
        remember(base)
        if (path == "/api/status" || path == "/api/connection") learnWifi(reply)
        return reply
    }

    private fun remember(base: String?) {
        if (route != base) {
            route = base
            prefs.edit().putString("route", base).apply()
            Live.routeChanged(base)
        }
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
                     timeoutMs: Int = 10_000, type: String = "application/json"): Reply {
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
            connection.setRequestProperty("Content-Type", type)
            // A fresh connection every time: AURORA closes idle ones after 30 s, and Android would otherwise
            // reuse a closed one ("unexpected end of stream"), which it doesn't retry for messages and voice.
            connection.setRequestProperty("Connection", "close")
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
            if (status == 200 && connection.contentType?.startsWith("audio/") == true) {
                return Reply(status, JSONObject(), stream.use { it.readBytes() })
            }
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
        connection.sslSocketFactory = pinnedContext().socketFactory
        connection.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }   // pinned instead
    }

    private fun pinnedContext(): SSLContext {
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
        return tls
    }
}
