"""Phase 10: knowledge index, answers with citations, summaries, comparison, sources."""

import logging
from datetime import datetime

import pytest

from aurora.brain.brain import Brain
from aurora.config import settings
from aurora.kernel.engine import AuroraEngine, is_factual_question
from aurora.kernel.permissions import PermissionManager
from aurora.knowledge import answers
from aurora.knowledge.index import KnowledgeIndex, chunk, query_terms, split_sentences
from aurora.knowledge.sources import (
    COMPUTED, DOCUMENT, LIVE, MEMORY, MODEL, SENSOR, Source, caption, merge, numbered,
)
from aurora.planning.planner import Planner
from aurora.routing.intent import Intent
from aurora.routing.router import IntentRouter
from aurora.tool_plugins.documents import DocumentsTool, Passage
from aurora.tool_plugins.knowledge import KnowledgeTool
from aurora.tool_plugins.registry import default_tools
from aurora.tool_plugins.research import WikipediaTool
from aurora.tools.result import ToolResult
from aurora.tools.tool_manager import ToolManager
from test_weather import client_returning

NOTES = """# Astronomy notes

Dark matter makes up about 27 percent of the universe. It does not emit or absorb light,
so it is detected only through its gravity. Galaxy rotation curves were the first strong evidence.

Dark energy makes up about 68 percent of the universe and drives its accelerating expansion.
Ordinary matter is only about 5 percent.

The Hubble constant is roughly 70 km/s per megaparsec. Different measurement methods disagree
slightly, which is called the Hubble tension.
"""

LECTURE = """Lecture 4: Black holes

A black hole forms when a massive star collapses at the end of its life. The boundary beyond which
nothing escapes is called the event horizon. Its radius is the Schwarzschild radius.

Hawking radiation means black holes slowly lose mass over extremely long times.

Dark matter makes up about 25 percent of the universe according to older estimates.
"""


def write_pdf(path, pages):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for lines in pages:
        page = writer.add_blank_page(width=612, height=792)
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({l}) Tj T*" for l in lines) + " ET")
                        .encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    with open(path, "wb") as f:
        writer.write(f)


@pytest.fixture
def folder():
    root = settings.DOCUMENTS_DIR          # a temporary folder (conftest)
    root.mkdir(parents=True, exist_ok=True)
    (root / "astronomy_notes.md").write_text(NOTES, encoding="utf-8")
    (root / "lecture.txt").write_text(LECTURE, encoding="utf-8")
    write_pdf(root / "thesis.pdf", [
        ["Thesis chapter 1: Introduction.", "This thesis studies exoplanet atmospheres."],
        ["Chapter 2: Methods.", "We used transit spectroscopy from the James Webb telescope.",
         "The sample contains 42 planets."],
    ])
    return root


@pytest.fixture
def index(folder):
    index = KnowledgeIndex(DocumentsTool(folder))
    index.sync()
    return index


# ============================================================ sources


def test_sources():
    doc = Source(DOCUMENT, "thesis.pdf", "page 2")
    live = Source(LIVE, "Wikipedia, \"Dark matter\"", "https://w/Dark_matter", datetime(2026, 11, 7, 10, 5))
    assert doc.citation() == "thesis.pdf, page 2"
    assert live.citation() == "Wikipedia, \"Dark matter\", https://w/Dark_matter, retrieved 07 Nov 2026 10:05"
    assert Source(MODEL).citation() == "language model (training data, not checked)"
    assert merge([doc], [doc, live], [Source(MODEL)]) == [doc, live, Source(MODEL)]
    assert caption([doc, Source(MODEL)]) == "Sources: your documents: thesis.pdf p. 2 · language model (training data, not checked)"
    assert caption([]) == ""
    assert numbered([doc, live]).splitlines()[1] == "  [1] thesis.pdf, page 2"
    with pytest.raises(ValueError):
        Source("rumour")


# ============================================================ index


def test_sentences_and_terms():
    assert split_sentences("# Title\nFirst one. Second one!\n- item three") == ["Title", "First one.", "Second one!", "item three"]
    assert query_terms("According to my notes, how much of the universe is dark matter?") == [
        "universe", "dark", "matter"]


def test_chunks_keep_pages_and_size():
    passages = [Passage("a.pdf", 1, "word " * 80), Passage("a.pdf", 1, "word " * 80), Passage("a.pdf", 2, "x y z")]
    chunks = chunk(passages)
    assert [page for page, _ in chunks] == [1, 1, 2]
    assert all(len(text.split()) <= 220 for _, text in chunks)


def test_sync_adds_updates_and_removes(folder):
    index = KnowledgeIndex(DocumentsTool(folder))
    report = index.sync()
    assert sorted(report.added) == ["astronomy_notes.md", "lecture.txt", "thesis.pdf"]
    assert index.sync().unchanged == 3 and not index.sync().changed
    (folder / "lecture.txt").write_text(LECTURE + "\nNeutron stars are very dense.\n", encoding="utf-8")
    assert index.sync().updated == ["lecture.txt"]
    (folder / "astronomy_notes.md").unlink()
    assert index.sync().removed == ["astronomy_notes.md"]
    assert [d["name"] for d in index.documents_indexed()] == ["lecture.txt", "thesis.pdf"]
    assert index.search("Neutron stars")[0].document == "lecture.txt"


def test_pdf_pages_are_indexed(index):
    thesis = next(d for d in index.documents_indexed() if d["name"] == "thesis.pdf")
    assert thesis["pages"] == 2
    assert {h.page for h in index.passages_of("thesis.pdf")} == {1, 2}


def test_damaged_files_dont_stop_indexing(folder):
    (folder / "broken.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    (folder / "photo.png").write_bytes(b"\x89PNG")
    report = KnowledgeIndex(DocumentsTool(folder)).sync()
    assert report.failed == ["broken.pdf"]
    assert "photo.png" not in report.added and len(report.added) == 3


def test_search_handles_odd_queries(index):
    for query in ['"dark" OR matter*', "NEAR(", "a:b", "-", "", "the of and"]:
        index.search(query)                   # never raises
    assert index.search("") == []


def test_search_can_be_limited_to_one_document(index):
    hits = index.search("dark matter", document="lecture.txt")
    assert hits and {h.document for h in hits} == {"lecture.txt"}


def test_find_document(index):
    assert index.find_document("thesis") == ["thesis.pdf"]
    assert index.find_document("THESIS.PDF") == ["thesis.pdf"]
    assert index.find_document("nothing") == []


# ============================================================ answers


def test_answer_quotes_and_cites(index):
    result = answers.answer(index, "which telescope was used?")
    assert result.status == "found"
    assert result.quotes[0].text == "We used transit spectroscopy from the James Webb telescope."
    assert result.sources == [Source(DOCUMENT, "thesis.pdf", "page 2")]
    text = result.text()
    assert "“We used transit spectroscopy from the James Webb telescope.” [1]" in text
    assert "[1] thesis.pdf, page 2" in text


@pytest.mark.parametrize("question", [
    "what is the event horizon", "how many planets are in the sample", "what is the Hubble tension",
    "how much of the universe is dark matter", "what is Hawking radiation",
])
def test_quotes_are_exact_sentences_from_the_documents(index, folder, question):
    """Nothing is invented: every quoted sentence appears in the cited document."""
    result = answers.answer(index, question)
    assert result.status == "found"
    for quote in result.quotes:
        passages = " ".join(" ".join(h.text.split()) for h in index.passages_of(quote.document))
        assert quote.text in passages


def test_unanswerable_questions_are_not_guessed(index):
    result = answers.answer(index, "what is the capital of France?")
    assert result.status == "not_found" and result.quotes == [] and result.sources == []
    assert "I won't guess" in result.text()


def test_a_loosely_related_question_is_not_answered(index):
    """'universe' appears in the documents, 'capital' doesn't: no answer, not a random quote."""
    result = answers.answer(index, "what is the capital of the universe?")
    assert result.status == "not_found" and result.quotes == []


def test_unchanged_documents_are_not_reread(index, monkeypatch):
    import aurora.knowledge.index as index_module

    hashed = []
    real = index_module.hashlib.sha256
    monkeypatch.setattr(index_module.hashlib, "sha256", lambda data=b"": hashed.append(1) or real(data))
    index.sync()
    assert hashed == []                     # size and time unchanged: no reading, no hashing


def test_answer_without_documents_or_question(tmp_path):
    empty = KnowledgeIndex(DocumentsTool(tmp_path / "none"))
    assert answers.answer(empty, "anything").status == "no_documents"


def test_disagreement_between_documents_is_noted(index):
    result = answers.answer(index, "how much of the universe is dark matter?")
    assert {q.document for q in result.quotes} == {"astronomy_notes.md", "lecture.txt"}
    assert result.notes == ["the sources give different figures: 27 percent (astronomy_notes.md) vs "
                            "25 percent (lecture.txt)."]


def test_different_figures_in_one_document_are_not_a_disagreement():
    quotes = [answers.Quote("Dark matter is 27 percent.", "a.md", None, 0.9),
              answers.Quote("Dark energy is 68 percent.", "a.md", None, 0.9)]
    assert answers.disagreements(quotes) == []


def test_answer_limited_to_a_document(index):
    result = answers.answer(index, "how much of the universe is dark matter", document="lecture.txt")
    assert {q.document for q in result.quotes} == {"lecture.txt"}


# ============================================================ summaries and comparison


def test_summarize_document(index):
    summary = answers.summarize_document(index, "thesis.pdf")
    assert [q.text for q in summary.quotes] == ["This thesis studies exoplanet atmospheres.",
                                                "We used transit spectroscopy from the James Webb telescope."]
    assert "[2] thesis.pdf, page 2" in summary.text()


def test_summarize_topic(index):
    summary = answers.summarize_topic(index, "dark matter")
    assert summary.quotes and all("matter" in q.text.lower() for q in summary.quotes)


def test_compare_documents(index):
    comparison = answers.compare(index, "dark matter", ["astronomy_notes.md", "lecture.txt"])
    assert [len(side.quotes) for side in comparison.sides] == [1, 1]
    assert "27 percent (astronomy_notes.md) vs 25 percent (lecture.txt)" in comparison.notes[0]


def test_compare_with_a_live_source(index):
    live = Source(LIVE, "Wikipedia, \"Dark matter\"", "https://w")
    text = "Dark matter is a hypothetical form of matter. Dark matter makes up about 27 percent of the universe."
    comparison = answers.compare(index, "dark matter", ["astronomy_notes.md", "wikipedia"],
                                 fetch_live=lambda topic: (text, live))
    assert comparison.sides[1].source == live and comparison.notes == []
    assert "I found no conflicting figures" in comparison.text()


def test_compare_reports_a_failed_live_source(index):
    def offline(topic):
        raise RuntimeError("I couldn't reach Wikipedia.")
    comparison = answers.compare(index, "dark matter", ["lecture.txt", "wikipedia"], fetch_live=offline)
    assert comparison.sides[1].problem == "I couldn't reach Wikipedia." and comparison.sides[1].quotes == []


# ============================================================ tool


def test_knowledge_tool(folder):
    tool = KnowledgeTool(DocumentsTool(folder))
    result = tool.execute(action="ask", question="how many planets are in the sample?")
    assert result.success and result.sources == [Source(DOCUMENT, "thesis.pdf", "page 2")]
    assert "3 document(s)" in tool.execute(action="status").message
    assert tool.execute(action="summarize", document="thesis").success
    assert tool.execute(action="ask", question="x", document="nothing").message.startswith("I can't find a document")
    assert not tool.execute(action="compare", topic="dark matter", sides=("lecture.txt",)).success
    assert "Re-indexed" in tool.execute(action="reindex").message


def test_knowledge_tool_without_a_folder(tmp_path):
    assert "doesn't exist" in KnowledgeTool(DocumentsTool(tmp_path / "missing")).execute().message


def test_wikipedia_comparison_goes_through_permissions(folder):
    tools = ToolManager(PermissionManager({"Knowledge": ["filesystem.read"]}))   # no network for Wikipedia
    tool = KnowledgeTool(DocumentsTool(folder))
    tools.register(tool)
    tools.register(WikipediaTool(client_returning()))
    tool.attach_tools(tools)
    result = tool.execute(action="compare", topic="dark matter", sides=("documents", "wikipedia"))
    assert "Your documents" in result.message
    assert "network" in result.message                       # the permission problem is shown


# ============================================================ routing


@pytest.mark.parametrize("message,arguments", [
    ("what do my notes say about dark matter?", {"action": "ask", "question": "dark matter"}),
    ("according to my notes, how much of the universe is dark matter?",
     {"action": "ask", "question": "how much of the universe is dark matter"}),
    ("in thesis.pdf, how many planets are in the sample?",
     {"action": "ask", "question": "how many planets are in the sample", "document": "thesis.pdf"}),
    ("according to my thesis, which telescope was used?",
     {"action": "ask", "question": "which telescope was used", "document": "thesis"}),
    ("summarize thesis.pdf", {"action": "summarize", "document": "thesis.pdf"}),
    ("summarize what my notes say about black holes", {"action": "summarize", "topic": "black holes"}),
    ("compare astronomy_notes.md and lecture.txt on dark matter",
     {"action": "compare", "topic": "dark matter", "sides": ("astronomy_notes.md", "lecture.txt")}),
    ("compare what my documents and Wikipedia say about dark matter",
     {"action": "compare", "topic": "dark matter", "sides": ("documents", "Wikipedia")}),
    ("what's in my knowledge base?", {"action": "status"}),
    ("re-index my documents", {"action": "reindex"}),
])
def test_document_questions_route_to_the_knowledge_tool(message, arguments):
    decision = IntentRouter().route(message)
    assert decision.intent is Intent.KNOWLEDGE_BASE and dict(decision.arguments) == arguments


@pytest.mark.parametrize("message,intent", [
    ("search my documents for galaxy", Intent.DOCUMENTS),
    ("read lecture.txt", Intent.DOCUMENTS),
    ("what is dark matter", Intent.KNOWLEDGE),
    ("compare apples and oranges", Intent.CONVERSATION),
    ("summarize our conversation", Intent.CONVERSATION),
    ("what's the temperature in my room", Intent.SENSORS),            # "my room" isn't a document
    ("is there any motion in my room?", Intent.SENSORS),
    ("what's the weather in my city?", Intent.WEATHER),
])
def test_other_messages_keep_their_routes(message, intent):
    assert IntentRouter().route(message).intent is intent


def test_planner_keeps_document_questions_whole():
    plan = Planner().plan("according to my notes, how much of the universe is dark matter?")
    assert len(plan.steps) == 1 and plan.steps[0].decision.intent is Intent.KNOWLEDGE_BASE


# ============================================================ engine: where answers come from


@pytest.mark.parametrize("message,factual", [
    ("who wrote Hamlet?", True), ("when did the Roman empire fall", True),
    ("how are you?", False), ("what should I wear?", False), ("tell me a joke", False), ("hello", False),
])
def test_factual_question_detection(message, factual):
    assert is_factual_question(message) is factual


@pytest.fixture
def engine(fake_provider, memory, folder):
    engine = AuroraEngine(Brain(fake_provider), memory=memory, tools=default_tools(memory, client_returning()))
    engine.start()
    yield engine
    engine.shutdown()


def kinds(engine):
    return [s.kind for s in engine.last_sources]


def test_document_answers_name_their_sources(engine, fake_provider):
    reply = engine.process_message("in thesis.pdf, how many planets are in the sample?")
    assert "“The sample contains 42 planets.” [1]" in reply and "[1] thesis.pdf, page 2" in reply
    assert engine.last_sources == [Source(DOCUMENT, "thesis.pdf", "page 2")]
    assert fake_provider.calls == []                     # the language model wasn't involved


def test_model_answers_are_labelled(engine):
    reply = engine.process_message("who wrote Hamlet?")
    assert "comes from my language model's training" in reply and kinds(engine) == [MODEL]
    assert "language model's training" not in engine.process_message("how are you?")


def test_memory_is_named_as_a_source(engine):
    engine.process_message("my name is Harry")
    engine.process_message("tell me something nice")
    assert kinds(engine) == [MEMORY, MODEL]


@pytest.mark.parametrize("message,expected", [
    ("what's the weather in Pune", [LIVE]),
    ("what is the average of 4, 8 and 15", [COMPUTED]),
    ("what is 2 + 2", [COMPUTED]),
    ("what's the weather in Pune and double the temperature", [LIVE, COMPUTED]),
    ("search my documents for event horizon", [DOCUMENT]),
    ("what's the temperature in my room", [SENSOR]),
])
def test_tool_answers_name_their_sources(engine, message, expected):
    engine.process_message(message)
    assert kinds(engine) == expected


def test_sources_are_reset_for_each_message(engine):
    engine.process_message("what is 2 + 2")
    engine.process_message("what do my notes say about the capital of France?")
    assert engine.last_sources == []


def test_document_text_never_reaches_the_log(engine, caplog):
    caplog.set_level(logging.DEBUG, logger="aurora")
    engine.process_message("according to my notes, how much of the universe is dark matter?")
    engine.process_message("summarize thesis.pdf")
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("aurora"))
    assert "Knowledge index" in ours
    for secret in ("27 percent", "James Webb", "exoplanet", "universe"):
        assert secret not in ours
