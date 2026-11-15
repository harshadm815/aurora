package app.aurora.companion

/** One thing said in a call. */
data class Turn(val speaker: String, val text: String)          // speaker: "you" or "aurora"

/** What was said in the current call, in memory only (never written to the phone's storage). */
class CallTranscript {
    private val turns = mutableListOf<Turn>()

    @Synchronized fun you(text: String) = add("you", text)
    @Synchronized fun aurora(text: String) = add("aurora", text)
    @Synchronized fun clear() = turns.clear()
    @Synchronized fun turns(): List<Turn> = turns.toList()

    private fun add(speaker: String, text: String) {
        val clean = text.trim()
        if (clean.isNotEmpty() && turns.lastOrNull() != Turn(speaker, clean)) turns.add(Turn(speaker, clean))
    }
}

/**
 * The after-call screen's content, worked out on the phone from the transcript: no model call, so it
 * is instant and private. Points are AURORA's answers (first sentence each), tasks are what you asked
 * to be reminded of or done, follow-ups are AURORA's open questions.
 */
class CallSummary(val turns: List<Turn>, val durationMs: Long) {
    companion object {
        private val TASK = Regex("""\b(remind me|set a reminder|add .+ to (my )?(list|tasks|calendar)|don't let me forget|schedule)\b""",
            RegexOption.IGNORE_CASE)
        private val NOT_FACT = Regex("""^(what|who|when|where|why|how|is|are|can|could|will|would|do|does|did|should|please|remind|tell|set|call|open|play|stop)\b""",
            RegexOption.IGNORE_CASE)

        fun duration(ms: Long): String {
            val seconds = (ms / 1000).coerceAtLeast(0)
            return "%d:%02d".format(seconds / 60, seconds % 60)
        }

        fun spokenDuration(ms: Long): String {
            val seconds = (ms / 1000).coerceAtLeast(0)
            return when {
                seconds < 60 -> "$seconds s"
                seconds % 60 == 0L -> "${seconds / 60} min"
                else -> "${seconds / 60} min ${seconds % 60} s"
            }
        }

        fun firstSentence(text: String, limit: Int = 160): String {
            val end = Regex("""[.!?](\s|$)""").find(text)?.range?.first
            val sentence = (if (end != null) text.substring(0, end + 1) else text).trim()
            return if (sentence.length <= limit) sentence else sentence.take(limit - 1).trimEnd() + "…"
        }
    }

    val exchanges: Int get() = turns.count { it.speaker == "you" }

    val points: List<String> get() = turns.filter { it.speaker == "aurora" && !it.text.trimEnd().endsWith("?") }
        .map { firstSentence(it.text) }.distinct().takeLast(5)

    val tasks: List<String> get() = turns.filter { it.speaker == "you" && TASK.containsMatchIn(it.text) }
        .map { it.text.trim().trimEnd('.') }.distinct()

    val followUps: List<String> get() = turns.filter { it.speaker == "aurora" && it.text.trimEnd().endsWith("?") }
        .map { firstSentence(it.text) }.distinct().takeLast(3)

    /** Things you said that could be worth remembering (statements, not questions or commands). */
    val memoryCandidates: List<String> get() = turns.filter { it.speaker == "you" }
        .map { it.text.trim().trimEnd('.') }
        .filter { it.split(" ").size >= 3 && !it.endsWith("?") && !NOT_FACT.containsMatchIn(it) }
        .distinct()

    /** The transcript as text (for "Save transcript"). */
    fun text(title: String): String = buildString {
        appendLine(title)
        appendLine("Length: ${spokenDuration(durationMs)}")
        appendLine()
        for (turn in turns) appendLine((if (turn.speaker == "you") "You: " else "AURORA: ") + turn.text)
    }
}
