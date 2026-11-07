# AURORA Memory System Design

Version: 3.0 (AURORA 0.26.0)
Status: All five memory types implemented.

---

## Purpose

The memory system lets AURORA retain meaningful information over time.

Its goal is **not** to save every conversation. Its goal is to build an
organised, user-controlled knowledge base that helps AURORA understand
the user.

> AURORA should not remember everything. AURORA should remember what matters,
> say so, and let the user decide.

## Design principles

1. Remember important information automatically, and say so.
2. Ignore temporary, uncertain or irrelevant information.
3. Store structured facts, not raw conversations.
4. The user can inspect, correct, pin, expire and delete any memory, and
   stop AURORA learning altogether.
5. Every memory knows where it came from (provenance).
6. Separate memory from reasoning: the language model never decides
   what is stored.
7. Never store sensitive data automatically.
8. Memory contents never appear in the log file.

---

## Memory types

| Type | Purpose | Where | Kept |
|---|---|---|---|
| Working | The current conversation | `Conversation` | Until exit or `clear` |
| Semantic | Facts and preferences about the user | `facts` table, `MemoryManager` | Until deleted (or its expiry) |
| Episodic | What AURORA did for the user, one line per task | `episodes` table, `EpisodeLog` | 30 days (`EPISODE_RETENTION_DAYS`) |
| Procedural | Routines the user taught ("when I say …") | `routines` table, `RoutineStore` | Until deleted |
| Associative | Links between the user, people they mention, and facts about those people | `facts` with an entity other than `User` | Until deleted |

All of it lives in one SQLite file, `aurora_memory.db` (not in git).

---

## Pipeline (semantic and associative)

```
User message (ordinary conversation only, not tool commands)
   │                                   learning paused? → stop
   ▼
FactExtractor      extractor.py   proposes candidate Facts, each with the sentence it came from
   │                              (facts about people only for names already known)
   ▼
MemoryEvaluator    evaluator.py   keep or discard; assigns priority
   │
   ▼
MemoryManager      manager.py     learn(): block list, duplicates, conflicts, provenance
   │
   ▼
MemoryStore        store.py       SQLite, with change history

Before the Brain answers:
AutoMemory.relevant() → name, pinned memories, matches (following links), important memories
```

## 1. Extraction

Rule-based and conservative: a missed fact is cheaper than a wrong one.

| Statement | Fact(s) |
|---|---|
| my name is Harry | Name = Harry |
| I live in Pune · I'm from Nagpur | Lives in · From |
| I like tea, coffee and chess | Likes (three facts) |
| my sister is Priya | Sister = Priya |
| **my sister Priya lives in Delhi** | Sister = Priya, and **Priya**: Lives in = Delhi |
| **Priya likes chess** | **Priya**: Likes = chess (only once Priya is known) |
| **Priya's birthday is 3 March** | **Priya**: Birthday = 3 March |

Facts about other people are only read for names the user has already
introduced as a relationship, so "Monday is from hell" or an unknown
name never becomes a person.

Every candidate carries `source_text`: the sentence it came from.

**Ignored**: questions, hypotheticals ("if", "maybe"), reported speech,
relationships whose value isn't a capitalised name, and *any message
mentioning sensitive data* (passwords, PINs, card or account numbers,
government IDs, API keys, 8+ digit numbers).

## 2. Evaluation

Unchanged: candidates below confidence 0.7, vague, negated or too long
are discarded; priority 5 (Name) … 2 (Favourite …).

## 3. Storage, duplicates and conflicts

`MemoryManager.learn(fact, source, source_text)` returns:

| Result | When |
|---|---|
| `blocked` | The user said not to remember this (block list); nothing is stored |
| `duplicate` | Already known, comparing values without case, articles or punctuation ("the tea." = "tea"). It is **confirmed**: `times_confirmed` + 1, confidence moves halfway to 1 (0.8 → 0.9 → 0.95) |
| `updated` | Single-valued attribute (Name, Lives in, Birthday …) with a new value, per person; or Likes ↔ Dislikes. The change is written to `fact_history` |
| `added` | Anything else |

Told explicitly ("remember that …"), a fact is certain (confidence 1.0).
Edited by the user, it is certain and marked `edited`.

## 4. Provenance

Every fact records:

| Field | Meaning |
|---|---|
| `source` | `automatic` (learned), `explicit` (you asked), `edited` (you changed it), `unknown` (saved before 0.26) |
| `source_text` | The sentence it was learned from (set `MEMORY_KEEP_SOURCE_TEXT = False` to keep none) |
| `created`, `updated` | When it was first stored and last changed |
| `times_confirmed` | How often the user has said it |
| `fact_history` | Every value change: old, new, when and why |

"how do you know where I live?" answers from these:

```
Lives in: Pune: I learned it from something you said on 07 Nov 2026: “I live in Pune”.
```

Deleting a fact deletes its sentence and its history.

## 5. Importance and confidence

`importance(fact)` (0–1) = 0.6 × priority/5 + 0.2 × confidence
+ 0.1 × usage (times used, up to 10) + 0.1 × repetition. Pinned
memories are 1. It orders memories when there's room for only a few
(the Brain's context, related results).

## 6. Retrieval

`MemoryManager.recall(query)`:
- **Keywords:** filler words are removed *first*, then the rest are
  stemmed (lives → live, studies → study).
- **Synonyms:** "hometown" finds From, "job" finds Occupation and
  Employer, "college" finds Institution, "bday" finds Birthday, and so on.
- **Links between people are followed:** "where does my sister live"
  finds Sister = Priya, then Priya's "Lives in: Delhi".
- **Results:** `matches` contain every keyword; `related` contain some of
  them.

`AutoMemory.relevant(message)` gives the Brain at most
`MEMORY_CONTEXT_LIMIT` (5) memories. They are chosen in this order:
1. the user's name;
2. pinned memories;
3. matches;
4. partial matches;
5. other important memories.

## 7. Episodic memory

One line per **completed tool task**, built from the request's metadata
and never from the message text:

```
07 Nov 10:42  Checked the weather in Pune
07 Nov 10:43  Calculated the average of 6 numbers
07 Nov 11:05  Looked up Black holes on Wikipedia
07 Nov 11:20  Controlled a device            (recorded only after you confirmed)
```

**Not recorded:**
- ordinary conversation;
- memory commands;
- health readings;
- what the camera saw;
- document names.

Failed tasks are marked "(didn't work)". Episodes are deleted after 30
days.

| You say | What happens |
|---|---|
| what did we do today? · show my activity | Lists episodes (today / yesterday / this week / last week / recently) |
| when did I last check the weather? | The most recent matching episode |
| stop recording activity · start recording activity | Switches recording off / on (kept in the database) |
| clear my activity history | Asks yes/no, then deletes all episodes |

## 8. Procedural memory: routines

```
when I say morning briefing, check the weather in Pune and what is 6 * 7
→ Okay. When you say “morning briefing”, I'll: check the weather in Pune and what is 6 * 7
morning briefing            (or "run my morning briefing")
→ Running your routine “morning briefing”: …weather…  6 * 7 = 42
```

How routines work:
- **Stored as request text, not frozen tool calls.** Each run goes through
  routing, permissions, planning and workflows, exactly as if typed.
- **Refused when saved:**
  - anything needing a confirmation (devices, deleting memories, research
    sessions);
  - memory commands;
  - other routines;
  - names that already mean something ("what is 2 + 2").
- **Checked again** before every run.
- **Limit:** at most 20 routines (`ROUTINE_MAX`).
- **Other commands:** "what routines do you know?" lists them; "delete the
  routine …" asks yes/no first.

## 9. User control (retention)

| You say | What happens |
|---|---|
| remember that my exam is on Friday | Stored (explicit, certain) |
| **always remember that** … | Stored and **pinned**: never expires, always in context |
| **remember for today / for 3 days / until tomorrow that** … | Stored with an **expiry**; deleted automatically afterwards |
| **don't remember that** | Takes back what the *previous* message taught: added facts are deleted, updated ones restored, and all are put on the **block list** so they aren't learned again |
| **stop learning** · **start learning again** | Pauses / resumes automatic learning; explicit "remember that …" still works |
| forget my exam | Lists matches and asks yes/no before deleting |
| forget everything | Lists everything and asks yes/no |
| how do you know …? · why do you think …? | Provenance |
| what do you know about me / about Priya? | Lists memories |

The **Memory Centre** (Command Centre) shows every memory with:
- **About:** you, or the person it's about;
- **Source:** learned, you asked, you edited, or earlier;
- **Kept:** always, until a date, or until deleted;
- confidence, priority and usage.

Its controls:
- **Buttons:** Add note, Edit value, Pin / unpin, How do you know?,
  Delete, and Delete & don't learn again.
- **Switches:** learning, and keeping the activity history.
- **Lists:** the activity history (with Clear all), routines (with
  Delete) and the block list (with Allow again).

## 10. Database and upgrades

| Table | Contents |
|---|---|
| `facts` | Semantic and associative memories, with provenance and retention columns |
| `fact_history` | Value changes |
| `blocked` | Things not to learn again |
| `episodes` | Activity history |
| `routines` | Routines (name, request text, runs) |
| `memory_settings` | The switches (learning paused, activity on) |

The schema version is SQLite's `user_version` (now 1). Upgrading from
the original schema (0) only **adds** columns and tables; existing
memories keep every value and get source `unknown`. The file is first
copied to `aurora_memory.db.before-schema-1.bak` (git ignores it).
Expired memories and old episodes are removed when AURORA starts.

## Known limits

- **Language:** English, first-person statements only (plus simple
  statements about people already known); everything else needs
  "remember that …".
- **Recall:** keywords with stems and a fixed synonym list, not meaning
  (no embeddings yet).
- **Links:** only person → fact through a relationship; no general
  concept graph.
- **Episodes:** say *that* something was done, never *what the answer
  was*.
- **Routines:** run their steps in order with no conditions ("only if
  it's raining").
