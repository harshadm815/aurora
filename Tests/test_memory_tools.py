from aurora.knowledge.fact import Fact
from aurora.memory.manager import keywords
from aurora.tool_plugins.memory_tools import ForgetTool, RecallTool, RememberTool


def subject_fact(value="Astrophysics"):
    return Fact("Preference", "User", "Favourite Subject", value, 4)


# --- Keyword recall -------------------------------------------------------

def test_keywords_drop_filler_and_normalize_spelling():
    assert keywords("what is my favourite colour?") == ["favorite", "color"]
    assert keywords("me") == []


def test_recall_requires_all_keywords_for_a_match(memory):
    memory.remember(subject_fact())

    result = memory.recall("favourite subject")
    assert [f.value for f in result.matches] == ["Astrophysics"]

    result = memory.recall("favourite colour")
    assert result.matches == []
    assert [f.value for f in result.related] == ["Astrophysics"]


def test_recall_with_no_keywords_returns_everything_once(memory):
    memory.store.add(subject_fact())
    memory.store.add(subject_fact())  # An old exact duplicate.

    result = memory.recall("me")

    assert [f.value for f in result.matches] == ["Astrophysics"]


# --- Remember -------------------------------------------------------------

def test_remember_stores_a_note(memory):
    result = RememberTool(memory).execute(content="my exam is on Friday")

    assert result.success
    assert "my exam is on Friday" in result.message
    assert [f.value for f in memory.memories()] == ["my exam is on Friday"]


def test_remember_does_not_duplicate(memory):
    tool = RememberTool(memory)
    tool.execute(content="my exam is on Friday")

    result = tool.execute(content="My exam is on Friday")

    assert "already" in result.message
    assert len(memory.memories()) == 1


def test_remember_needs_content(memory):
    assert RememberTool(memory).execute(content="  ").success is False


# --- Recall ---------------------------------------------------------------

def test_recall_lists_matches(memory):
    RememberTool(memory).execute(content="my exam is on Friday")

    result = RecallTool(memory).execute(query="exam")

    assert result.success
    assert "my exam is on Friday" in result.message


def test_recall_says_when_nothing_is_saved(memory):
    result = RecallTool(memory).execute(query="Mars")

    assert result.message == "I don't have anything saved about Mars."


def test_recall_mentions_related_but_not_as_an_answer(memory):
    memory.remember(subject_fact())

    result = RecallTool(memory).execute(query="favourite colour")

    assert result.message.startswith("I don't have anything saved about favourite colour.")
    assert "Favourite Subject: Astrophysics" in result.message


def test_recall_everything(memory):
    memory.remember(subject_fact())

    result = RecallTool(memory).execute(query="me")

    assert result.message.startswith("Here's what I have saved:")


def test_recall_empty_memory(memory):
    assert RecallTool(memory).execute(query="me").message == "I don't have anything saved yet."


# --- Forget ---------------------------------------------------------------

def test_forget_asks_before_deleting(memory):
    RememberTool(memory).execute(content="my exam is on Friday")

    result = ForgetTool(memory).execute(query="my exam")

    assert result.needs_confirmation
    assert "my exam is on Friday" in result.message
    assert len(memory.memories()) == 1  # Nothing deleted yet.

    done = result.confirm()

    assert done.message == "Done. I deleted 1 memory."
    assert memory.memories() == []


def test_forget_removes_exact_duplicates_too(memory):
    memory.store.add(subject_fact())
    memory.store.add(subject_fact())

    result = ForgetTool(memory).execute(query="favourite subject")
    result.confirm()

    assert memory.memories() == []


def test_forget_leaves_unrelated_memories(memory):
    tool = RememberTool(memory)
    tool.execute(content="my exam is on Friday")
    tool.execute(content="my sister is Priya")

    ForgetTool(memory).execute(query="exam").confirm()

    # "my sister is Priya" is stored as a structured fact.
    assert [(f.attribute, f.value) for f in memory.memories()] == [("Sister", "Priya")]


def test_forget_nothing_matching(memory):
    result = ForgetTool(memory).execute(query="Mars")

    assert result.success is False
    assert not result.needs_confirmation
