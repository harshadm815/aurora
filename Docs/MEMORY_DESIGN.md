# AURORA Memory System Design

Version: 2.0 (AURORA 0.9.0)
Status: Implemented for semantic memory. Other memory types are planned.

---

## Purpose

The memory system lets AURORA retain meaningful information over time.

Its goal is **not** to save every conversation. Its goal is to build an
organised, user-controlled knowledge base that helps AURORA understand
the user.

> AURORA should not remember everything. AURORA should remember what matters.

## Design principles

1. Remember important information automatically, and say so.
2. Ignore temporary, uncertain or irrelevant information.
3. Store structured facts, not raw sentences.
4. The user can inspect, correct and delete any memory.
5. Separate memory from reasoning: the language model never decides
   what is stored.
6. Never store sensitive data automatically.
7. Every module has one responsibility.

---

## Pipeline

```
User message (ordinary conversation only, not tool commands)
   │
   ▼
FactExtractor      aurora/memory/extractor.py   proposes candidate Facts
   │
   ▼
MemoryEvaluator    aurora/memory/evaluator.py   keep or discard; assigns priority
   │
   ▼
MemoryManager      aurora/memory/manager.py     learn(): duplicates, conflicts
   │
   ▼
MemoryStore        aurora/memory/store.py       SQLite (aurora_memory.db)

Before the Brain answers:
MemoryManager.recall() → AutoMemory.relevant() → system message for the Brain
```

`AutoMemory` (`aurora/memory/auto.py`) connects these to the conversation.
It runs as the component `AutoMemory`, which needs the `memory.read` and
`memory.write` permissions (granted by default in `settings.py`). Remove
the grant to switch automatic memory off completely.

---

## 1. Extraction

Rule-based and conservative. A small local model can't be trusted to
produce reliable structured output, and a missed fact is cheaper than a
wrong one.

| Statement | Fact |
|---|---|
| my name is Harry | Personal / Name = Harry |
| I live in Pune · I'm from Nagpur | Personal / Lives in · From |
| my birthday is 4 November | Personal / Birthday |
| I like tea, coffee and chess | Preference / Likes (three facts) |
| I don't like / hate spicy food | Preference / Dislikes |
| my favourite subject is astrophysics | Preference / Favourite Subject |
| I study physics · I go to IIT Bombay | Education / Studies · Institution |
| I work as an engineer · I work at Infosys | Work / Occupation · Employer |
| my sister is Priya · my best friend's name is Rahul | Relationship / Sister · Best Friend |

Messages are split into clauses ("My name is Harry **and I** live in
Pune" gives two facts). Values stop at "because", "but", "which" and so on.

**Ignored:**

- Questions: "is my name Harry?"
- Hypotheticals and uncertainty: "if", "would", "maybe", "might", "imagine".
- Reported speech: "my friend said my name is Bob".
- Relationships whose value isn't a capitalised name: "my sister is sick".
- **Any message mentioning sensitive data**: passwords, PINs, OTPs, card
  or account numbers, government IDs (SSN, Aadhaar, PAN, passport),
  API keys or tokens, or any run of 8+ digits. Nothing at all is
  extracted from such a message.

## 2. Evaluation

A candidate is discarded if:

- its confidence is below `settings.MEMORY_MIN_CONFIDENCE` (0.7),
- its value is vague ("it", "that", "you", "stuff" …),
- its value is negated ("not Harry"),
- its value is longer than 6 words / 60 characters,
- it is a name containing digits.

Priority (5 = critical … 1 = temporary):

| Priority | Attributes |
|---|---|
| 5 | Name |
| 4 | Lives in, From, Birthday, Studies, Institution, Occupation, Employer, relationships |
| 3 | Likes, Dislikes, notes |
| 2 | Favourite … |

## 3. Storage and conflict resolution

`MemoryManager.learn(fact)` returns `added`, `updated` or `duplicate`:

- **Exact duplicate** (case-insensitive): nothing changes.
- **Single-valued attribute** (Name, Birthday, Lives in, From, Studies,
  Institution, Occupation, Employer, Mother, Father, partner types, Best
  Friend, every Favourite …): a new value **replaces** the old one, and
  old duplicate copies are removed. "my name is Harshad" after "my name
  is Harry" updates the name.
- **Opposites**: "I hate coffee" replaces "Likes: coffee" (and vice versa).
- **Everything else** (Likes, Sister, Friend …) can have several values.

When AURORA saves or updates something automatically it appends a note
to its reply, e.g. `(Remembered Name: Harry)` or
`(Updated Name: Harshad (was Name: Harry))`. Set
`settings.MEMORY_ANNOUNCE = False` to hide these.

## 4. Retrieval

`MemoryManager.recall(query)`:

- Reduces the query to keywords (drops filler words, normalises
  colour/color and favourite/favorite, and stems: lives → live,
  studies/studying/studied → study).
- **matches**: memories containing every keyword.
- **related**: memories containing some of them (shown to the user as
  "related", never as the answer).
- A query with no keywords ("what do you know about me") returns everything.

`AutoMemory.relevant(message)` chooses what the Brain sees, at most
`settings.MEMORY_CONTEXT_LIMIT` (5):

1. the user's name, always;
2. memories matching the message;
3. partial matches;
4. other priority 4+ memories.

They are sent as a system message right after the personality prompt,
labelled as data that may be out of date. They are **not** added to the
saved conversation. Each memory used this way or recalled has its
`last_used` and `times_used` updated.

---

## Fact structure

| Field | Meaning |
|---|---|
| id | Database ID |
| category | Personal, Preference, Education, Work, Relationship, Note |
| entity | Who it's about (currently always `User`) |
| attribute | Name, Lives in, Likes, Sister, Favourite Colour, Note … |
| value | Harry, Pune, tea … |
| priority | 1–5, assigned by the evaluator |
| confidence | 0–1, from the extractor |
| created | When first stored |
| last_used | When last recalled or given to the Brain |
| times_used | How often |

---

## User control

| You say | What happens |
|---|---|
| remember that my exam is on Friday | Stored. If it matches a pattern ("remember that my name is Harry") it becomes a structured fact; otherwise a Note |
| what do you know about me? · who am I? | Lists everything |
| what's my name? · where do I live? · what do I study? | Recall by keyword |
| my name is Harshad | Updates the stored name (announced) |
| forget my exam | Lists matches and asks yes/no before deleting |

---

## Memory types

| Type | Purpose | Status |
|---|---|---|
| Working | The current conversation | Implemented (`Conversation`, cleared on exit or `clear`) |
| Semantic | Facts and preferences | **Implemented** (this document) |
| Episodic | Past events and completed activities | Planned |
| Procedural | How to perform tasks and use tools | Planned |
| Associative | Relationships between facts, people and concepts (knowledge graph) | Planned |

## Known limits

- English, first-person statements only; anything outside the patterns
  above is not learned automatically (use "remember that …").
- Recall is keyword matching, not meaning: "what's my hometown?" won't
  find "From: Nagpur". Semantic search needs embeddings, which is a
  later step and must fit in 4 GB of RAM.
- TinyLlama often ignores or garbles the memory context it is given.
  Direct questions about memories are answered by the Recall tool for
  that reason; a stronger model will make better use of the context.
- Facts about other people are only stored as the user's relationships;
  there is no general knowledge graph yet.
