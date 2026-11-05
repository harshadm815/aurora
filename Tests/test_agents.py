from datetime import datetime

import httpx
import pytest

from aurora.agents.base import Agent, Blackboard, Coordinator
from aurora.agents.research_agents import paper_id
from aurora.agents.research_log import ResearchLog
from aurora.brain.brain import Brain
from aurora.kernel.engine import AuroraEngine
from aurora.kernel.permissions import PermissionManager
from aurora.knowledge.fact import Fact
from aurora.tool_plugins.registry import default_tools

from conftest import FakeProvider

TOPIC = "black hole cosmogenesis"

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2611.00001v1</id>
    <published>2026-11-03T10:00:00Z</published>
    <title>Entropy across the bounce in black hole cosmogenesis</title>
    <summary>We study entropy at the bounce.</summary>
    <author><name>A. Author</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2610.00002v2</id>
    <published>2026-10-01T10:00:00Z</published>
    <title>Black hole cosmogenesis and dark energy</title>
    <summary>A different angle.</summary>
    <author><name>B. Author</name></author>
  </entry>
</feed>"""

SUMMARY = {
    "type": "standard", "title": "Black hole cosmology",
    "extract": "Black hole cosmology is a cosmological model in which the observable universe is the interior of a black hole.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Black_hole_cosmology"}},
}


def services(arxiv_down=False):
    def handler(request):
        if "arxiv" in request.url.host:
            if arxiv_down:
                raise httpx.ConnectError("offline")
            return httpx.Response(200, content=FEED.encode())
        if "rest.php" in request.url.path:
            return httpx.Response(200, json={"pages": [{"key": "Black_hole_cosmology", "title": "Black hole cosmology"}]})
        return httpx.Response(200, json=SUMMARY)
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def notes(tmp_path):
    folder = tmp_path / "Documents"
    folder.mkdir()
    (folder / "ideas.md").write_text(
        "Black hole cosmogenesis\n\n"
        "Every black hole cosmogenesis event could give birth to a new universe. "
        "Open question: how does entropy behave across the bounce?\n",
        encoding="utf-8",
    )
    return folder


@pytest.fixture
def make_engine(memory, notes):
    def make(permissions=None, arxiv_down=False):
        provider = FakeProvider()
        engine = AuroraEngine(Brain(provider), permissions=permissions, memory=memory,
                              tools=default_tools(memory, services(arxiv_down)))
        engine.progress = []
        engine.tool_manager.get("Research").progress = engine.progress.append
        engine.start()
        engine.provider = provider
        return engine
    return make


# --- A full session -------------------------------------------------------------

def test_first_session(make_engine, memory):
    memory.learn(Fact("Education", "User", "Studies", "black hole cosmogenesis", 4))
    engine = make_engine()

    report = engine.process_message("AURORA, continue my research on black hole cosmogenesis")

    assert report.startswith("Research session: black hole cosmogenesis\nThis is a new research topic for me.")
    assert "From your memory:\n  - Studies: black hole cosmogenesis" in report
    assert 'From your notes (1 passage):\n  - ideas.md: "Every black hole cosmogenesis event' in report
    assert "Open questions in your notes:\n  - how does entropy behave across the bounce?\n" in report
    assert ('2. For your open question "how does entropy behave across the bounce?", start with '
            '"Entropy across the bounce in black hole cosmogenesis".') in report
    assert "Background (Wikipedia):\n  Black hole cosmology: Black hole cosmology is" in report
    assert "Newest matching papers (arXiv), 2 new:" in report
    assert "  1. [NEW] Entropy across the bounce in black hole cosmogenesis (2026-11-03)" in report
    assert "     Related to your notes: entropy, bounce" in report
    assert ('1. Read "Entropy across the bounce in black hole cosmogenesis": it mentions '
            "entropy, bounce, which appear in your notes.") in report
    assert "Start a research log for this topic" in report
    assert "Sources checked: your research log, your memory, your documents, Wikipedia, arXiv." in report
    assert "nothing was written by the language model" in report
    assert report.endswith("Save this session to your research log? (yes / no)")

    # The language model was never asked.
    assert engine.provider.calls == []
    assert [line.split(":")[0] for line in engine.progress] == [
        "🧠  Memory agent", "📄  Documents agent", "🔭  Research agent",
        "🧩  Analyst agent", "✅  Reviewer agent",
    ]


def test_saving_and_the_next_session(make_engine):
    engine = make_engine()
    engine.process_message("continue my research on black hole cosmogenesis")

    assert engine.process_message("yes") == (
        'Saved to your research log for "black hole cosmogenesis" (1 session(s) so far).'
    )

    report = engine.process_message("what's new in my research on black hole cosmogenesis")

    assert "Last session:" in report and "(today). Looked at 2 recent papers (2 new)" in report
    assert "Newest matching papers (arXiv), 0 new since your last session:" in report
    assert "[NEW]" not in report
    assert "Start a research log" not in report


def test_not_saving(make_engine):
    engine = make_engine()
    engine.process_message("continue my research on black hole cosmogenesis")

    assert engine.process_message("no") == "Okay, I won't do that."
    assert ResearchLog().projects() == []


def test_list_projects(make_engine):
    engine = make_engine()
    assert engine.process_message("list my research projects").startswith("You have no research projects yet")

    engine.process_message("continue my research on black hole cosmogenesis")
    engine.process_message("yes")

    assert engine.process_message("list my research projects").startswith(
        "Your research projects:\n  - black hole cosmogenesis (1 session(s), last session"
    )


# --- When things go wrong -------------------------------------------------------

def test_unreachable_source_is_reported_not_hidden(make_engine):
    report = make_engine(arxiv_down=True).process_message("continue my research on black hole cosmogenesis")

    assert "Newest matching papers" not in report
    assert "⚠ arXiv: I couldn't reach arXiv." in report
    assert "This report is incomplete" in report
    assert "Background (Wikipedia):" in report      # The rest still works.


def test_agents_respect_permissions(make_engine):
    grants = PermissionManager({
        "Research": ["memory.read", "memory.write"], "Recall": ["memory.read"],
        "Documents": ["filesystem.read"], "arXiv": ["network"],
    })

    report = make_engine(permissions=grants).process_message("continue my research on black hole cosmogenesis")

    assert "⚠ Wikipedia is switched off (needs the 'network' permission)." in report
    assert "Background (Wikipedia)" not in report


def test_research_needs_its_own_permission(make_engine):
    engine = make_engine(permissions=PermissionManager({"Wikipedia": ["network"]}))

    assert "not allowed to use the Research tool" in engine.process_message(
        "continue my research on black hole cosmogenesis")


def test_one_failing_agent_does_not_stop_the_others():
    class Broken(Agent):
        name = "Broken agent"
        def run(self, board):
            raise RuntimeError("boom")

    class Works(Agent):
        name = "Working agent"
        def run(self, board):
            board.put("done", True)

    board = Coordinator([Broken(), Works()]).run(Blackboard("x"))

    assert board.get("done") is True
    assert board.problems == ["The broken agent failed (RuntimeError)."]


def test_research_sessions_are_never_part_of_a_plan(make_engine):
    engine = make_engine()

    reply = engine.process_message("what is 2+2 and continue my research on black hole cosmogenesis")

    assert reply.startswith("2+2 = 4")
    assert "For a research session, ask me in a separate message" in reply


# --- Research log ---------------------------------------------------------------

def test_log_matches_projects_by_keywords():
    log = ResearchLog()
    log.record("black hole cosmogenesis", "first", ["http://arxiv.org/abs/1v1"], datetime(2026, 11, 1))

    assert log.find("Black hole cosmogenesis").name == "black hole cosmogenesis"
    assert log.find("cosmogenesis of black holes") is not None
    assert log.find("dark matter") is None

    log.record("cosmogenesis black hole", "second", ["http://arxiv.org/abs/2v1"])
    project = log.find(TOPIC)
    assert [s.summary for s in project.sessions] == ["first", "second"]
    assert project.seen_papers == {"http://arxiv.org/abs/1v1", "http://arxiv.org/abs/2v1"}


def test_paper_versions_count_as_the_same_paper():
    assert paper_id("http://arxiv.org/abs/2610.00002v1") == paper_id("http://arxiv.org/abs/2610.00002v2")


# --- Found in a live run ----------------------------------------------------------

def test_whole_note_is_used_when_it_mentions_the_topic(make_engine, notes):
    # Only the heading contains the topic; the hypothesis and question don't.
    (notes / "ideas.md").write_text(
        "Black hole cosmogenesis\n\n"
        "Hypothesis: every black hole may give birth to a new universe through a bounce.\n\n"
        "Open question: how does entropy behave across the bounce?\n",
        encoding="utf-8",
    )

    report = make_engine().process_message("continue my research on black hole cosmogenesis")

    assert 'ideas.md: "Hypothesis: every black hole may give birth' in report
    assert "Open questions in your notes:\n  - how does entropy behave across the bounce?" in report


def test_off_topic_wikipedia_article_is_left_out(make_engine, monkeypatch):
    off_topic = dict(SUMMARY, title="Chronology of the universe",
                     extract="The chronology of the universe describes its history and future.")
    monkeypatch.setitem(SUMMARY, "title", off_topic["title"])
    monkeypatch.setitem(SUMMARY, "extract", off_topic["extract"])

    report = make_engine().process_message("continue my research on black hole cosmogenesis")

    assert "Background (Wikipedia)" not in report
    assert ("Wikipedia's closest article (\"Chronology of the universe\") isn't about this topic, "
            "so I left it out.") in report
