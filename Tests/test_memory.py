from aurora.knowledge.fact import Fact


def make_fact(value="Python"):
    return Fact(
        category="Preference",
        entity="User",
        attribute="Favourite Language",
        value=value,
        priority=4,
    )


def test_remember_returns_id(memory):
    memory_id = memory.remember(make_fact())

    assert isinstance(memory_id, int)
    assert len(memory.memories()) == 1


def test_exact_duplicate_is_not_stored_twice(memory):
    first = memory.remember(make_fact())
    second = memory.remember(make_fact())

    assert first == second
    assert len(memory.memories()) == 1


def test_duplicate_check_ignores_case(memory):
    first = memory.remember(make_fact("Python"))
    second = memory.remember(make_fact("python"))

    assert first == second


def test_search_finds_by_value(memory):
    memory.remember(make_fact("Python"))

    results = memory.search("pyth")

    assert [fact.value for fact in results] == ["Python"]


def test_edit_changes_value(memory):
    memory_id = memory.remember(make_fact("Python"))

    assert memory.edit(memory_id, "Java") is True
    assert [fact.value for fact in memory.search("Java")] == ["Java"]
    assert memory.search("Python") == []


def test_edit_missing_memory_returns_false(memory):
    assert memory.edit(999, "Java") is False


def test_forget_deletes_memory(memory):
    memory_id = memory.remember(make_fact())

    assert memory.forget(memory_id) is True
    assert memory.memories() == []
    assert memory.forget(memory_id) is False


def test_memories_ordered_by_priority(memory):
    low = make_fact("Low")
    low.priority = 1
    high = make_fact("High")
    high.priority = 5

    memory.remember(low)
    memory.remember(high)

    assert [fact.value for fact in memory.memories()] == ["High", "Low"]
