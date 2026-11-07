"""Phase 9: the advanced memory system."""

import logging
import shutil
import sqlite3
from datetime import datetime, timedelta

import pytest

from aurora.brain.brain import Brain
from aurora.config import settings
from aurora.kernel.engine import AuroraEngine
from aurora.knowledge.fact import AUTOMATIC, EDITED, EXPLICIT, UNKNOWN, Fact
from aurora.memory.auto import AutoMemory, describe
from aurora.memory.episodes import EpisodeLog, summarize
from aurora.memory.extractor import FactExtractor
from aurora.memory.manager import MemoryManager, importance, normalize_value, reinforced
from aurora.memory.routines import RoutineStore
from aurora.memory.store import SCHEMA_VERSION, MemoryStore
from aurora.planning.planner import Planner
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent
from aurora.routing.router import IntentRouter
from aurora.tool_plugins.memory_tools import expiry_for
from aurora.tool_plugins.registry import default_tools
from test_weather import client_returning


@pytest.fixture
def manager(memory):
    return memory


def fact(attribute="Likes", value="tea", entity="User", category="Preference", priority=3, confidence=0.8):
    return Fact(category, entity, attribute, value, priority, confidence)


# ============================================================ schema upgrade


OLD_SCHEMA = """CREATE TABLE facts (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL,
    entity TEXT NOT NULL, attribute TEXT NOT NULL, value TEXT NOT NULL, priority INTEGER NOT NULL,
    confidence REAL NOT NULL, created TEXT NOT NULL, last_used TEXT, times_used INTEGER NOT NULL DEFAULT 0)"""


def test_old_database_is_upgraded_without_losing_anything(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(OLD_SCHEMA)
    connection.execute("INSERT INTO facts (category, entity, attribute, value, priority, confidence, created, "
                       "last_used, times_used) VALUES ('Personal', 'User', 'Name', 'Harry', 5, 0.95, "
                       "'2026-11-01T10:00:00', NULL, 3)")
    connection.commit()
    connection.close()
    original = path.read_bytes()

    store = MemoryStore(path)
    [old] = store.all()
    assert (old.attribute, old.value, old.priority, old.times_used) == ("Name", "Harry", 5, 3)
    assert old.source == UNKNOWN and old.times_confirmed == 1 and not old.pinned
    backup = tmp_path / f"old.db.before-schema-{SCHEMA_VERSION}.bak"
    assert backup.read_bytes() == original
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connection.close()
    assert {"fact_history", "blocked", "episodes", "routines", "memory_settings"} <= tables

    MemoryStore(path)                              # a second open changes nothing
    assert len(MemoryStore(path).all()) == 1
    assert len(list(tmp_path.glob("*.bak"))) == 1


def test_new_database_gets_no_backup(tmp_path):
    MemoryStore(tmp_path / "new.db")
    assert not list(tmp_path.glob("*.bak"))


# ============================================================ semantic memory


@pytest.mark.parametrize("a,b", [("tea", "Tea"), ("tea", "the tea."), ("Pune", " pune! "), ("a cat", "cat")])
def test_normalized_duplicates(a, b):
    assert normalize_value(a) == normalize_value(b)


def test_repeating_a_fact_raises_confidence(manager):
    first = manager.learn(fact(confidence=0.8))
    again = manager.learn(fact(value="the tea.", confidence=0.8))
    assert again.action == "duplicate" and again.fact.id == first.fact.id
    assert again.fact.confidence == reinforced(0.8) == 0.9 and again.fact.times_confirmed == 2
    assert len(manager.memories()) == 1


def test_explicitly_told_facts_are_certain(manager):
    manager.learn(fact(confidence=0.8))
    result = manager.learn(fact(confidence=0.8), source=EXPLICIT, source_text="remember that I like tea")
    stored = manager.get(result.fact.id)
    assert stored.confidence == 1.0 and stored.source == EXPLICIT


def test_provenance_is_recorded(manager):
    result = manager.learn(fact("Lives in", "Pune", category="Personal"), source=AUTOMATIC,
                           source_text="I live in Pune")
    stored = manager.get(result.fact.id)
    assert stored.source == AUTOMATIC and stored.source_text == "I live in Pune"
    assert "I learned it from something you said" in manager.provenance(stored)
    assert "“I live in Pune”" in manager.provenance(stored)


def test_source_text_is_not_kept_when_switched_off(manager, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_KEEP_SOURCE_TEXT", False)
    result = manager.learn(fact(), source_text="I like tea")
    assert manager.get(result.fact.id).source_text is None


def test_updates_and_edits_are_kept_in_history(manager):
    first = manager.learn(fact("Name", "Harry", category="Personal"), source_text="my name is Harry")
    manager.learn(fact("Name", "Harshad", category="Personal"), source_text="my name is Harshad")
    manager.edit(first.fact.id, "Harsh")
    stored = manager.get(first.fact.id)
    assert stored.value == "Harsh" and stored.source == EDITED and stored.confidence == 1.0
    history = manager.store.history(stored.id)
    assert [(h["old"], h["new"], h["reason"]) for h in history] == [
        ("Harry", "Harshad", "replaced by a newer statement"), ("Harshad", "Harsh", "edited by you")]
    text = manager.provenance(stored)
    assert "You set it yourself" in text and "Harry → Harshad" in text and "Harshad → Harsh" in text


def test_deleting_removes_the_history(manager):
    first = manager.learn(fact("Name", "Harry", category="Personal"))
    manager.edit(first.fact.id, "Harsh")
    manager.forget(first.fact.id)
    assert manager.store.history(first.fact.id) == []


def test_edit_rejects_empty_values(manager):
    first = manager.learn(fact())
    assert not manager.edit(first.fact.id, "   ")


def test_blocked_facts_are_never_learned(manager):
    manager.block(fact(value="Tea"))
    assert manager.learn(fact(value="the tea")).action == "blocked"
    assert manager.memories() == []
    [entry] = manager.blocked()
    manager.unblock(entry["id"])
    assert manager.learn(fact()).action == "added"


def test_pinned_and_expiring_memories(manager):
    pinned = manager.learn(fact("Note", "exam", category="Note")).fact
    temporary = manager.learn(fact("Note", "plumber", category="Note")).fact
    manager.pin(pinned.id)
    manager.set_expiry(temporary.id, datetime.now() + timedelta(hours=1))
    assert manager.memories()[0].id == pinned.id                 # pinned first
    assert manager.cleanup(datetime.now()) == 0
    assert manager.cleanup(datetime.now() + timedelta(hours=2)) == 1
    assert [f.id for f in manager.memories()] == [pinned.id]
    manager.set_expiry(pinned.id, datetime.now())                 # an expiry unpins
    assert not manager.get(pinned.id).pinned
    manager.pin(pinned.id)                                        # pinning clears the expiry
    assert manager.get(pinned.id).expires is None


def test_a_pinned_memory_never_expires_even_with_an_old_expiry(manager):
    stored = fact("Note", "keep me", category="Note")
    stored.pinned = True
    stored.expires = datetime.now() - timedelta(days=1)
    manager.store.add(stored)
    assert manager.cleanup() == 0 and len(manager.memories()) == 1


def test_importance():
    low = fact(priority=2, confidence=0.7)
    high = fact(priority=5, confidence=0.95)
    used = fact(priority=2, confidence=0.7)
    used.times_used = 10
    assert importance(high) > importance(used) > importance(low)
    low.pinned = True
    assert importance(low) == 1.0


def test_recall_uses_synonyms(manager):
    manager.learn(fact("From", "Nagpur", category="Personal"))
    manager.learn(fact("Occupation", "engineer", category="Work"))
    assert [f.value for f in manager.recall("my hometown").matches] == ["Nagpur"]
    assert [f.value for f in manager.recall("my job").matches] == ["engineer"]


def test_recall_follows_links_between_people(manager):
    manager.learn(fact("Sister", "Priya", category="Relationship"))
    manager.learn(fact("Lives in", "Delhi", entity="Priya", category="Personal"))
    manager.learn(fact("Lives in", "Pune", category="Personal"))
    found = manager.recall("where does my sister live").matches
    assert [(f.entity, f.value) for f in found] == [("Priya", "Delhi")]
    about = manager.recall("Priya").matches
    assert {(f.entity, f.attribute) for f in about} == {("User", "Sister"), ("Priya", "Lives in")}
    assert manager.people() == {"Priya": ["Sister"]}


def test_entity_facts_resolve_conflicts_per_person(manager):
    manager.learn(fact("Lives in", "Delhi", entity="Priya", category="Personal"))
    manager.learn(fact("Lives in", "Pune", category="Personal"))
    result = manager.learn(fact("Lives in", "Mumbai", entity="Priya", category="Personal"))
    assert result.action == "updated" and result.previous.value == "Delhi"
    assert {(f.entity, f.value) for f in manager.memories()} == {("Priya", "Mumbai"), ("User", "Pune")}


# ============================================================ extraction


@pytest.mark.parametrize("message,people,expected", [
    ("My sister Priya lives in Delhi", (), [("User", "Sister", "Priya"), ("Priya", "Lives in", "Delhi")]),
    ("Priya likes chess and tea", ("Priya",), [("Priya", "Likes", "chess"), ("Priya", "Likes", "tea")]),
    ("Priya's birthday is 3 March", ("Priya",), [("Priya", "Birthday", "3 March")]),
    ("my brother is Arjun. Arjun studies law", (), [("User", "Brother", "Arjun"), ("Arjun", "Studies", "law")]),
    ("Priya likes chess", (), []),                       # unknown name: not a person AURORA knows
    ("Monday is from hell", ("Priya",), []),
    ("Rahul works at Infosys", ("Priya",), []),
])
def test_facts_about_other_people(message, people, expected):
    found = FactExtractor().extract(message, people)
    assert [(f.entity, f.attribute, f.value) for f in found] == expected


def test_extracted_facts_carry_their_sentence():
    found = FactExtractor().extract("My name is Harry and I live in Pune")
    assert [f.source_text for f in found] == ["My name is Harry", "I live in Pune"]


def test_sensitive_messages_still_teach_nothing():
    assert FactExtractor().extract("My sister Priya's password is hunter2") == []


# ============================================================ automatic memory


def test_paused_learning_learns_nothing(manager):
    auto = AutoMemory(manager)
    manager.set_learning_paused(True)
    assert auto.learn_from("I like tea") == [] and manager.memories() == []
    manager.set_learning_paused(False)
    assert [r.action for r in auto.learn_from("I like tea")] == ["added"]
    assert auto.last_results and auto.last_results[0].fact.source == AUTOMATIC


def test_describe_names_other_people():
    assert describe(fact("Lives in", "Delhi", entity="Priya")) == "Priya — Lives in: Delhi"
    assert describe(fact("Sister", "Priya")) == "Sister: Priya"


def test_context_includes_pinned_memories(manager):
    auto = AutoMemory(manager, context_limit=3)
    note = manager.learn(fact("Note", "allergic to nuts", category="Note", priority=3)).fact
    manager.pin(note.id)
    assert any(f.value == "allergic to nuts" for f in auto.relevant("what should I cook?"))


# ============================================================ expiry phrases


@pytest.mark.parametrize("span,delta", [
    ("3 days", timedelta(days=3)), ("an hour", timedelta(hours=1)), ("two weeks", timedelta(weeks=2)),
])
def test_expiry_for_durations(span, delta):
    now = datetime(2026, 11, 7, 9, 0)
    assert expiry_for(span, now) == now + delta


def test_expiry_for_named_spans():
    now = datetime(2026, 11, 7, 9, 0)            # a Saturday
    assert expiry_for("today", now) == datetime(2026, 11, 7, 23, 59, 59)
    assert expiry_for("tomorrow", now) == datetime(2026, 11, 8, 23, 59, 59)
    assert expiry_for("this week", now) == datetime(2026, 11, 8, 23, 59, 59)
    assert expiry_for("next century", now) is None


# ============================================================ episodes


def decision(intent, **arguments):
    return RouteDecision(intent, 0.9, "x", arguments, "test")


@pytest.mark.parametrize("d,summary", [
    (decision(Intent.WEATHER, location="Pune"), "Checked the weather in Pune"),
    (decision(Intent.WEATHER, location="Pune", when="tomorrow"), "Checked the weather in Pune (tomorrow)"),
    (decision(Intent.CALCULATOR, expression="2+2"), "Did a calculation"),
    (decision(Intent.NUMBERS, operations=("mean", "median"), numbers=(1, 2, 3)),
     "Calculated the average and median of 3 numbers"),
    (decision(Intent.KNOWLEDGE, topic="Black holes"), "Looked up Black holes on Wikipedia"),
    (decision(Intent.DOCUMENTS, action="read", name="diary.txt"), "Worked with your documents"),
    (decision(Intent.HEALTH), None),
    (decision(Intent.CONVERSATION), None),
    (decision(Intent.REMEMBER, content="secret"), None),
    (decision(Intent.RECALL, query="x"), None),
])
def test_episode_summaries(d, summary):
    assert summarize(d) == summary


def test_episode_log(tmp_path):
    log = EpisodeLog(tmp_path / "m.db")
    now = datetime.now()
    log.record("weather", "Checked the weather in Pune", when=now - timedelta(days=40))
    log.record("weather", "Checked the weather in Delhi", when=now - timedelta(days=1))
    log.record("numbers", "Calculated the average of 3 numbers", success=False)
    assert [e.summary for e in log.recent(contains=["weather"])] == [
        "Checked the weather in Delhi", "Checked the weather in Pune"]
    assert log.cleanup(now) == 1 and log.count() == 2
    log.set_enabled(False)
    assert log.record("x", "Did a calculation") is None and log.count() == 2
    log.set_enabled(True)
    assert log.clear() == 2 and log.count() == 0


def test_episode_summaries_are_short(tmp_path):
    log = EpisodeLog(tmp_path / "m.db")
    log.record("knowledge", "x" * 500)
    assert len(log.recent()[0].summary) <= 120


# ============================================================ routines


def test_routine_store(tmp_path):
    store = RoutineStore(tmp_path / "m.db")
    routine, replaced = store.save("Morning Briefing!", "weather in Pune")
    assert routine.name == "morning briefing" and not replaced
    _, replaced = store.save("morning briefing", "weather in Delhi")
    assert replaced and store.get("MORNING BRIEFING").steps == "weather in Delhi"
    assert store.match("Morning briefing.").name == "morning briefing"
    assert store.match("run my morning briefing routine").name == "morning briefing"
    assert store.match("morning") is None
    store.mark_run("morning briefing")
    assert store.get("morning briefing").times_run == 1
    assert store.delete("morning briefing") and store.all() == []


def test_routine_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ROUTINE_MAX", 2)
    store = RoutineStore(tmp_path / "m.db")
    store.save("a one", "x")
    store.save("b two", "y")
    with pytest.raises(ValueError):
        store.save("c three", "z")


# ============================================================ routing


@pytest.mark.parametrize("message,intent,arguments", [
    ("don't remember that", Intent.MEMORY_CONTROL, {"action": "undo"}),
    ("stop learning", Intent.MEMORY_CONTROL, {"action": "pause"}),
    ("start learning again", Intent.MEMORY_CONTROL, {"action": "resume"}),
    ("stop recording activity", Intent.MEMORY_CONTROL, {"action": "episodes_off"}),
    ("clear my activity history", Intent.MEMORY_CONTROL, {"action": "clear_episodes"}),
    ("forget my activity history", Intent.MEMORY_CONTROL, {"action": "clear_episodes"}),
    ("what did we do today?", Intent.EPISODES, {"period": "today"}),
    ("when did I last check the weather?", Intent.EPISODES, {"what": "check the weather", "latest": True}),
    ("what routines do you know?", Intent.ROUTINES, {"action": "list"}),
    ("forget the routine focus", Intent.ROUTINES, {"action": "delete", "name": "focus"}),
    ("when I say morning briefing, check the weather in Pune and what is 6 * 7", Intent.ROUTINES,
     {"action": "create", "name": "morning briefing", "steps": "check the weather in Pune and what is 6 * 7"}),
    ("how do you know where I live?", Intent.RECALL, {"query": "where I live", "mode": "provenance"}),
    ("where does my sister live?", Intent.RECALL, {"query": "sister live"}),
    ("always remember that my exam is on Friday", Intent.REMEMBER, {"content": "my exam is on Friday", "pin": True}),
    ("remember for today that the plumber comes at 5", Intent.REMEMBER,
     {"content": "the plumber comes at 5", "keep_for": "today"}),
    ("remember that I parked on level 3 for 2 days", Intent.REMEMBER,
     {"content": "I parked on level 3", "keep_for": "2 days"}),
    ("forget my exam", Intent.FORGET, {"query": "my exam"}),
    ("forget it", Intent.CONVERSATION, {}),
])
def test_memory_commands_route(message, intent, arguments):
    d = IntentRouter().route(message)
    assert d.intent is intent and dict(d.arguments) == arguments


def test_planner_keeps_routine_definitions_whole():
    plan = Planner().plan("when I say morning briefing, check the weather in Pune and what is 6 * 7")
    assert len(plan.steps) == 1 and plan.steps[0].decision.intent is Intent.ROUTINES


# ============================================================ engine


@pytest.fixture
def engine(fake_provider, memory):
    engine = AuroraEngine(Brain(fake_provider), memory=memory, tools=default_tools(memory, client_returning()))
    engine.start()
    yield engine
    engine.shutdown()


def test_dont_remember_that_undoes_and_blocks(engine, memory):
    engine.process_message("I like tea")
    reply = engine.process_message("don't remember that")
    assert "I've forgotten Likes: tea" in reply
    assert memory.memories() == []
    engine.process_message("I like tea")
    assert memory.memories() == []                    # blocked


def test_dont_remember_that_restores_an_updated_value(engine, memory):
    engine.process_message("my name is Harry")
    engine.process_message("my name is Bob")
    engine.process_message("don't remember that")
    assert [f.value for f in memory.memories()] == ["Harry"]


def test_undo_only_applies_to_the_previous_message(engine, memory):
    engine.process_message("I like tea")
    engine.process_message("what is 2 + 2")
    assert "nothing to take back" in engine.process_message("don't remember that")
    assert [f.value for f in memory.memories()] == ["tea"]


def test_pause_and_resume_learning(engine, memory):
    engine.process_message("stop learning")
    engine.process_message("I like tea")
    assert memory.memories() == []
    engine.process_message("remember that I like coffee")       # explicit still works
    assert [f.value for f in memory.memories()] == ["coffee"]
    engine.process_message("start learning again")
    engine.process_message("I like tea")
    assert {f.value for f in memory.memories()} == {"coffee", "tea"}


def test_pinned_and_temporary_memories_through_messages(engine, memory):
    assert "pinned" in engine.process_message("always remember that my exam is on Friday")
    assert "then I'll forget it" in engine.process_message("remember for 2 days that I parked on level 3")
    kinds = {f.value: (f.pinned, f.expires is not None, f.source) for f in memory.memories()}
    assert kinds == {"my exam is on Friday": (True, False, EXPLICIT), "I parked on level 3": (False, True, EXPLICIT)}


def test_provenance_question(engine):
    engine.process_message("I live in Pune")
    reply = engine.process_message("how do you know where I live?")
    assert "Lives in: Pune" in reply and "“I live in Pune”" in reply


def test_episodes_are_recorded_for_tools_only(engine):
    engine.process_message("hello there")
    engine.process_message("I like tea")
    engine.process_message("what's the weather in Pune")
    engine.process_message("what is the average of 4, 8 and 15")
    summaries = [e.summary for e in engine.episodes.recent()]
    assert summaries == ["Calculated the average of 3 numbers", "Checked the weather in Pune"]
    reply = engine.process_message("what did we do today?")
    assert "Checked the weather in Pune" in reply and "hello" not in reply


def test_when_did_i_last(engine):
    engine.process_message("what's the weather in Pune")
    assert "Checked the weather in Pune" in engine.process_message("when did I last check the weather?")
    assert "don't have any record" in engine.process_message("when did I last look up black holes?")


def test_activity_history_controls(engine):
    engine.process_message("what's the weather in Pune")
    engine.process_message("stop recording activity")
    engine.process_message("what is 2 + 2")
    assert engine.episodes.count() == 1
    assert "Should I go ahead" in engine.process_message("clear my activity history")
    engine.process_message("yes")
    assert engine.episodes.count() == 0
    engine.process_message("start recording activity")
    engine.process_message("what is 2 + 2")
    assert engine.episodes.count() == 1


def test_device_episode_is_recorded_only_after_confirmation(engine):
    engine.process_message("turn on the desk light")
    assert engine.episodes.count() == 0
    engine.process_message("yes")
    assert [e.summary for e in engine.episodes.recent()] == ["Controlled a device"]


def test_routines_end_to_end(engine):
    reply = engine.process_message("when I say morning briefing, check the weather in Pune and what is 6 * 7")
    assert "When you say “morning briefing”" in reply
    reply = engine.process_message("morning briefing")
    assert reply.startswith("Running your routine “morning briefing”:")
    assert "Pune, Maharashtra" in reply and "6 * 7 = 42" in reply
    assert engine.routines.get("morning briefing").times_run == 1
    assert "morning briefing" in engine.process_message("what routines do you know?")
    assert "Should I go ahead" in engine.process_message("delete the routine morning briefing")
    engine.process_message("yes")
    assert engine.routines.all() == []


@pytest.mark.parametrize("message", [
    "when I say lights, turn on the desk light",
    "when I say wipe, forget my exam",
    "when I say what is 2 + 2, check the weather",            # the name already means something
])
def test_unsafe_routines_are_refused(engine, message):
    reply = engine.process_message(message)
    assert engine.routines.all() == []
    assert "Routines can't" in reply or "already means something" in reply


def test_memory_tools_are_permission_checked(fake_provider, memory):
    from aurora.kernel.permissions import PermissionManager

    engine = AuroraEngine(Brain(fake_provider), memory=memory, permissions=PermissionManager({}),
                          tools=default_tools(memory, client_returning()))
    engine.start()
    assert "not allowed" in engine.process_message("stop learning")
    assert not memory.learning_paused
    engine.shutdown()


def test_memory_contents_never_reach_the_log(engine, caplog):
    # AURORA's log file receives the "aurora" loggers only (see aurora/utils/log.py).
    caplog.set_level(logging.DEBUG, logger="aurora")
    engine.process_message("My sister Priya lives in Zanzibar")
    engine.process_message("how do you know where Priya lives?")
    engine.process_message("always remember that my locker code is behind the clock")
    engine.process_message("what's the weather in Pune")
    engine.process_message("what did we do today?")
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("aurora"))
    assert "Memory added" in ours                       # logging did happen
    for secret in ("Zanzibar", "Priya", "locker", "Pune"):
        assert secret not in ours
