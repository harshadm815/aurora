package app.aurora.companion

/** Small pieces of the screens that don't need Android (unit-tested in FormatTest). */
object Format {
    /** A reply split into text and code blocks (``` fenced), in order. */
    data class Part(val code: Boolean, val text: String)

    fun parts(reply: String): List<Part> {
        val result = mutableListOf<Part>()
        val chunks = reply.split("```")
        val closed = chunks.size % 2 == 1                 // an even count means the last ``` was never closed
        for ((i, chunk) in chunks.withIndex()) {
            val code = i % 2 == 1 && (closed || i != chunks.size - 1)
            val body = if (code) chunk.substringAfter('\n', chunk).trimEnd('\n', ' ')   // without the language line
                       else (if (i % 2 == 1) "```$chunk" else chunk).trim()
            if (body.isNotBlank()) result += Part(code, body)
        }
        return result
    }

    /** "Good morning" / "Good afternoon" / "Good evening" for the hour 0-23. */
    fun greeting(hour: Int): String = when (hour) {
        in 5..11 -> "Good morning"
        in 12..16 -> "Good afternoon"
        in 17..21 -> "Good evening"
        else -> "Hello"
    }

    /** Does a memory match the search box and the chosen category ("" = all)? */
    fun matches(value: String, category: String, query: String, chosen: String): Boolean {
        if (chosen.isNotEmpty() && !category.equals(chosen, ignoreCase = true)) return false
        val words = query.trim().lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
        val haystack = "$value $category".lowercase()
        return words.all { haystack.contains(it) }
    }

    /** One line for an event in Home's "Recent" / Control's notifications. */
    fun event(kind: String, text: String, reply: String, source: String, name: String): String = when (kind) {
        "message" -> (if (source == "phone") "You asked: " else "On the PC: ") + text.take(80)
        "reminder" -> "Reminder: " + text.take(80)
        "call" -> "AURORA called"
        "file" -> "File from your PC: $name"
        else -> kind
    }

    /** "2026-11-15T18:00" → "18:00" today, else "15 Nov 18:00". */
    fun due(iso: String, today: String): String {
        val date = iso.substringBefore("T")
        val time = iso.substringAfter("T", "").take(5)
        if (date == today || time.isEmpty()) return time.ifEmpty { iso }
        val parts = date.split("-")
        val months = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        val month = parts.getOrNull(1)?.toIntOrNull()?.let { months.getOrNull(it - 1) } ?: return "$date $time"
        return "${parts.getOrNull(2)?.trimStart('0')} $month $time"
    }
}
