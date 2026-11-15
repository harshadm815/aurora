package app.aurora.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RedesignTest {
    // --- Chat formatting

    @Test
    fun code_blocks_are_split_from_text() {
        val parts = Format.parts("Here you go:\n```kotlin\nval x = 1\nprintln(x)\n```\nThat prints 1.")
        assertEquals(listOf(
            Format.Part(false, "Here you go:"),
            Format.Part(true, "val x = 1\nprintln(x)"),
            Format.Part(false, "That prints 1."),
        ), parts)
    }

    @Test
    fun plain_text_and_unclosed_fences_are_kept() {
        assertEquals(listOf(Format.Part(false, "Just words.")), Format.parts("Just words."))
        val unclosed = Format.parts("Start\n```\nnever closed")
        assertEquals(Format.Part(false, "Start"), unclosed[0])
        assertFalse(unclosed[1].code)
        assertTrue(unclosed[1].text.contains("never closed"))
    }

    // --- Memory search

    @Test
    fun memory_search_matches_every_word_and_the_category() {
        assertTrue(Format.matches("Likes green tea", "Preference", "green tea", ""))
        assertTrue(Format.matches("Likes green tea", "Preference", "TEA pref", ""))
        assertFalse(Format.matches("Likes green tea", "Preference", "coffee", ""))
        assertTrue(Format.matches("Lives in Pune", "Place", "", "place"))
        assertFalse(Format.matches("Lives in Pune", "Place", "", "Preference"))
    }

    // --- Home

    @Test
    fun greetings_follow_the_time_of_day() {
        assertEquals("Good morning", Format.greeting(8))
        assertEquals("Good afternoon", Format.greeting(14))
        assertEquals("Good evening", Format.greeting(19))
        assertEquals("Hello", Format.greeting(2))
    }

    @Test
    fun due_times_are_short_for_today() {
        assertEquals("18:00", Format.due("2026-11-15T18:00", "2026-11-15"))
        assertEquals("16 Nov 09:30", Format.due("2026-11-16T09:30", "2026-11-15"))
    }

    // --- Call

    @Test
    fun call_states_drive_the_core() {
        assertEquals(CoreState.CONNECTING, CoreStates.of("connecting", muted = false, connected = false))
        assertEquals(CoreState.LISTENING, CoreStates.of("listening", muted = false, connected = true))
        assertEquals(CoreState.THINKING, CoreStates.of("thinking", muted = false, connected = true))
        assertEquals(CoreState.SPEAKING, CoreStates.of("speaking", muted = false, connected = true))
        assertEquals(CoreState.MUTED, CoreStates.of("listening", muted = true, connected = true))
        assertEquals(CoreState.RECONNECTING, CoreStates.of("reconnecting", muted = false, connected = false))
        assertEquals(CoreState.DISCONNECTED, CoreStates.of("ended", muted = false, connected = false))
        assertEquals("Muted: AURORA can't hear you", CoreStates.label(CoreState.MUTED))
    }

    @Test
    fun loudness_of_silence_and_speech() {
        assertEquals(0f, Levels.of(ByteArray(3200)), 0.001f)
        val loud = ByteArray(3200) { i -> if (i % 2 == 1) 0x30 else 0 }           // 12288 every sample
        assertTrue(Levels.of(loud) > 0.9f)
    }

    @Test
    fun transcript_skips_repeats_and_blanks() {
        val t = CallTranscript()
        t.you("What's the weather?"); t.you("What's the weather?"); t.aurora("  "); t.aurora("Sunny, 24 degrees.")
        assertEquals(listOf(Turn("you", "What's the weather?"), Turn("aurora", "Sunny, 24 degrees.")), t.turns())
        t.clear()
        assertTrue(t.turns().isEmpty())
    }

    @Test
    fun after_call_summary() {
        val summary = CallSummary(listOf(
            Turn("you", "What's on today?"),
            Turn("aurora", "You have a dentist appointment at 4. Nothing else."),
            Turn("you", "Remind me to buy milk at 6."),
            Turn("aurora", "Okay, I'll remind you to buy milk at 18:00."),
            Turn("you", "My sister's birthday is on the 3rd of March"),
            Turn("aurora", "Shall I remember that?"),
        ), durationMs = 125_000)
        assertEquals(3, summary.exchanges)
        assertEquals(listOf("You have a dentist appointment at 4.", "Okay, I'll remind you to buy milk at 18:00."), summary.points)
        assertEquals(listOf("Remind me to buy milk at 6"), summary.tasks)
        assertEquals(listOf("Shall I remember that?"), summary.followUps)
        assertEquals(listOf("My sister's birthday is on the 3rd of March"), summary.memoryCandidates)    // not questions or commands
        assertEquals("2:05", CallSummary.duration(125_000))
        assertEquals("2 min 5 s", CallSummary.spokenDuration(125_000))
        val text = summary.text("AURORA call")
        assertTrue(text.contains("You: Remind me to buy milk at 6.") && text.contains("AURORA: Shall I remember that?"))
    }
}
