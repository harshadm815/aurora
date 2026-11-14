package app.aurora.companion

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.security.MessageDigest

/** Files between the phone and the PC (aurora/mobile/files.py). */
object Files {
    const val MAX_BYTES = 500L * 1024 * 1024          // as MOBILE_MAX_FILE_BYTES on the PC
    const val FOLDER = "AURORA"

    fun mimeOf(name: String): String =
        MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substringAfterLast('.', "").lowercase())
            ?: "application/octet-stream"

    /** A shared item's name and size (size -1 when the sharing app doesn't say). */
    fun describe(context: Context, uri: Uri): Pair<String, Long> {
        var name = uri.lastPathSegment?.substringAfterLast('/') ?: "shared file"
        var size = -1L
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)
            ?.use { cursor ->
                if (cursor.moveToFirst()) {
                    val n = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    val s = cursor.getColumnIndex(OpenableColumns.SIZE)
                    if (n >= 0 && !cursor.isNull(n)) name = cursor.getString(n)
                    if (s >= 0 && !cursor.isNull(s)) size = cursor.getLong(s)
                }
            }
        return name to size
    }

    /** SHA-256 (hex) and the real length of what [open] gives: read once before sending. */
    fun checksum(open: () -> InputStream): Pair<String, Long> {
        val digest = MessageDigest.getInstance("SHA-256")
        var length = 0L
        open().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val n = input.read(buffer)
                if (n < 0) break
                digest.update(buffer, 0, n)
                length += n
                if (length > MAX_BYTES) throw IOException("That file is too big (the limit is 500 MB).")
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) } to length
    }

    /**
     * Saves a file from the PC in Downloads/AURORA (Android picks "name (1)" if it exists, so nothing is
     * overwritten). A file that arrives short is removed. Returns where it is, for opening.
     */
    fun saveDownload(context: Context, name: String, size: Long, input: InputStream): Uri {
        if (size > MAX_BYTES) throw IOException("That file is too big for the phone (the limit is 500 MB).")
        if (Build.VERSION.SDK_INT >= 29) {
            val resolver = context.contentResolver
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, name)
                put(MediaStore.MediaColumns.MIME_TYPE, mimeOf(name))
                put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/" + FOLDER)
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            }
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: throw IOException("The phone couldn't make room for the file.")
            try {
                val written = resolver.openOutputStream(uri)?.use { copy(input, it) }
                    ?: throw IOException("The phone couldn't save the file.")
                if (size >= 0 && written != size) throw IOException("The file didn't arrive completely. Send it again.")
                resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
                return uri
            } catch (error: IOException) {
                resolver.delete(uri, null, null)
                throw error
            }
        }
        // Android 8-9: the app's own Downloads folder (no storage permission needed).
        val folder = File(context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), FOLDER).apply { mkdirs() }
        var target = File(folder, name)
        var n = 1
        while (target.exists()) target = File(folder, name.substringBeforeLast('.', name) + " (${n++})" +
            (if (name.contains('.')) "." + name.substringAfterLast('.') else ""))
        try {
            val written = target.outputStream().use { copy(input, it) }
            if (size >= 0 && written != size) throw IOException("The file didn't arrive completely. Send it again.")
        } catch (error: IOException) {
            target.delete()
            throw error
        }
        return Uri.fromFile(target)
    }

    private fun copy(input: InputStream, out: java.io.OutputStream): Long {
        val buffer = ByteArray(64 * 1024)
        var total = 0L
        while (true) {
            val n = input.read(buffer)
            if (n < 0) break
            out.write(buffer, 0, n)
            total += n
            if (total > MAX_BYTES) throw IOException("That file is too big for the phone.")
        }
        return total
    }
}
