package app.aurora.companion

import android.app.Activity
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import java.io.ByteArrayInputStream
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread

/**
 * "AURORA (PC)" in the phone's share menu: sends what you shared to the PC's Documents › From phone
 * folder (never over an existing file). Photos, documents, anything; shared text becomes a .txt file.
 */
class ShareActivity : Activity() {
    private class Item(val name: String, val open: () -> java.io.InputStream)

    private lateinit var title: TextView
    private lateinit var status: TextView
    private lateinit var bar: ProgressBar
    private lateinit var done: Button
    @Volatile private var working = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val dp = resources.displayMetrics.density
        fun px(v: Int) = (v * dp).toInt()
        title = TextView(this).apply {
            text = "Sending to your PC"
            textSize = 20f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(getColor(R.color.text))
        }
        status = TextView(this).apply {
            textSize = 15f
            setTextColor(getColor(R.color.muted))
            setPadding(0, px(10), 0, px(10))
        }
        bar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { max = 1000 }
        done = Button(this).apply {
            text = "Close"
            isAllCaps = false
            setOnClickListener { finish() }
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(px(28), px(28), px(28), px(28))
            addView(title)
            addView(status)
            addView(bar, LinearLayout.LayoutParams(-1, -2))
            addView(done, LinearLayout.LayoutParams(-1, -2).apply { topMargin = px(20) })
        })
        val aurora = Aurora.get(this)
        if (!aurora.paired) {
            show("This phone isn't paired with AURORA. Open the AURORA app to pair it first.", finished = true)
            return
        }
        val items = items(intent)
        if (items.isEmpty()) {
            show("There was nothing to send.", finished = true)
            return
        }
        send(aurora, items)
    }

    override fun onBackPressed() {
        if (working) moveTaskToBack(true) else super.onBackPressed()     // keeps sending in the background
    }

    private fun items(intent: Intent): List<Item> {
        val uris = mutableListOf<Uri>()
        @Suppress("DEPRECATION")
        when (intent.action) {
            Intent.ACTION_SEND -> (if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
                                   else intent.getParcelableExtra(Intent.EXTRA_STREAM) as? Uri)?.let { uris += it }
            Intent.ACTION_SEND_MULTIPLE -> (if (Build.VERSION.SDK_INT >= 33) intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
                                            else intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM))?.let { uris += it }
        }
        val items = uris.take(20).map { uri ->
            Item(Files.describe(this, uri).first) {
                contentResolver.openInputStream(uri) ?: throw IOException("The phone couldn't read that file.")
            }
        }.toMutableList()
        val text = intent.getStringExtra(Intent.EXTRA_TEXT)
        if (items.isEmpty() && !text.isNullOrBlank()) {
            val bytes = text.toByteArray(Charsets.UTF_8)
            val stamp = SimpleDateFormat("yyyy-MM-dd HHmm", Locale.getDefault()).format(Date())
            items += Item("Shared text $stamp.txt") { ByteArrayInputStream(bytes) }
        }
        return items
    }

    private fun send(aurora: Aurora, items: List<Item>) {
        working = true
        done.text = "Hide"
        done.setOnClickListener { moveTaskToBack(true) }
        thread(name = "aurora-share") {
            val saved = mutableListOf<String>()
            var problem: String? = null
            for ((index, item) in items.withIndex()) {
                val label = if (items.size > 1) "${item.name} (${index + 1} of ${items.size})" else item.name
                try {
                    runOnUiThread { show("Checking $label…"); bar.isIndeterminate = true }
                    val (sha256, size) = Files.checksum(item.open)
                    if (size == 0L) throw IOException("${item.name} is empty.")
                    runOnUiThread { show("Sending $label…"); bar.isIndeterminate = false; bar.progress = 0 }
                    val reply = aurora.upload(item.name, size, sha256, item.open) { sent ->
                        runOnUiThread { bar.progress = (sent * 1000 / size).toInt() }
                    }
                    if (!reply.ok) throw IOException(reply.error)
                    saved += reply.json.optString("saved", item.name)
                } catch (error: IOException) {
                    problem = "${item.name}: ${error.message ?: "it couldn't be sent."}"
                    break
                } catch (error: SecurityException) {
                    problem = "${item.name}: the app you shared from didn't let AURORA read it."
                    break
                }
            }
            runOnUiThread {
                working = false
                bar.isIndeterminate = false
                bar.progress = if (problem == null) 1000 else bar.progress
                val folder = "Documents › From phone"
                val message = when {
                    problem != null && saved.isEmpty() -> problem!!
                    problem != null -> "Sent ${saved.size}, then stopped. $problem"
                    saved.size == 1 -> "Saved on your PC in $folder as ${saved[0]}."
                    else -> "Saved ${saved.size} files on your PC in $folder."
                }
                title.text = if (problem == null) "Sent to your PC" else "Not sent"
                show(message, finished = true)
            }
        }
    }

    private fun show(message: String, finished: Boolean = false) {
        status.text = message
        if (finished) {
            done.text = "Close"
            done.setOnClickListener { finish() }
        }
    }
}
