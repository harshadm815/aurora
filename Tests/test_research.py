import os

import httpx
import pytest

from aurora.brain.brain import Brain
from aurora.kernel.engine import AuroraEngine
from aurora.routing.intent import Intent
from aurora.routing.router import IntentRouter
from aurora.tool_plugins.documents import DocumentsTool
from aurora.tool_plugins.registry import default_tools
from aurora.tool_plugins.research import ArxivTool, WikipediaTool
from aurora.tools.untrusted import clean_text, shorten

# --- Fake services ----------------------------------------------------------

BLACK_HOLE_SUMMARY = {
    "type": "standard",
    "title": "Black hole",
    "extract": (
        "A black hole is a region of spacetime where gravity is so strong that nothing, "
        "not even light, can escape it. The theory of general relativity predicts that a "
        "sufficiently compact mass can deform spacetime to form a black hole."
    ),
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Black_hole"}},
}

ARXIV_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2611.01234v1</id>
    <published>2026-11-02T18:00:00Z</published>
    <title>Atmospheres of
      temperate exoplanets</title>
    <summary>We observe things.</summary>
    <author><name>A. Author</name></author>
    <author><name>B. Author</name></author>
    <author><name>C. Author</name></author>
    <author><name>D. Author</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2610.04321v2</id>
    <published>2026-10-20T10:00:00Z</published>
    <title>A second paper</title>
    <summary>More.</summary>
    <author><name>E. Author</name></author>
  </entry>
</feed>"""


def fake_client(search=None, summary=None, arxiv=ARXIV_FEED, status=200, error=None):
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        if error:
            raise error
        if "arxiv" in request.url.host:
            return httpx.Response(status, content=arxiv.encode())
        if "rest.php" in request.url.path:
            pages = search if search is not None else [
                {"key": "Black_hole", "title": "Black hole"},
                {"key": "Black_Hole_(film)", "title": "Black Hole (film)"},
            ]
            return httpx.Response(status, json={"pages": pages})
        return httpx.Response(status, json=summary if summary is not None else BLACK_HOLE_SUMMARY)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.requests = requests
    return client


# --- Untrusted text ---------------------------------------------------------

def test_clean_text_removes_control_and_direction_characters():
    assert clean_text("safe\x00 text‮ reversed\x1b[31m") == "safe text reversed[31m"


def test_shorten_cuts_at_a_sentence():
    assert shorten("One. Two three four. Five six seven eight nine.", 25) == "One. Two three four."


# --- Wikipedia --------------------------------------------------------------

def test_wikipedia_summary_with_source():
    result = WikipediaTool(fake_client()).execute(topic="black holes")

    assert result.success
    assert result.message.startswith("Black hole: A black hole is a region of spacetime")
    assert result.message.endswith(
        'Source: Wikipedia, "Black hole" (https://en.wikipedia.org/wiki/Black_hole)'
    )


def test_wikipedia_sends_only_the_topic():
    client = fake_client()
    WikipediaTool(client).execute(topic="black holes")

    assert client.requests[0].url.params["q"] == "black holes"


def test_wikipedia_summary_is_shortened(monkeypatch):
    from aurora.config import settings

    monkeypatch.setattr(settings, "RESEARCH_MAX_CHARS", 120)

    result = WikipediaTool(fake_client()).execute(topic="black holes")

    first_line = result.message.splitlines()[0]
    assert first_line.endswith("can escape it.")


def test_wikipedia_no_article():
    result = WikipediaTool(fake_client(search=[])).execute(topic="xqzzv")

    assert result.success is False
    assert "no article" in result.message


def test_wikipedia_disambiguation_asks_for_more():
    summary = {"type": "disambiguation", "title": "Mercury"}
    result = WikipediaTool(fake_client(summary=summary)).execute(topic="mercury")

    assert result.success is False
    assert "more specific" in result.message
    assert "Black Hole (film)" in result.message


@pytest.mark.parametrize("error", [httpx.ConnectError("offline"), httpx.ReadTimeout("slow")])
def test_wikipedia_failure_never_guesses(error):
    result = WikipediaTool(fake_client(error=error)).execute(topic="black holes")

    assert result.success is False
    assert "I won't guess" in result.message


# --- arXiv ------------------------------------------------------------------

def test_arxiv_query_uses_keywords():
    assert ArxivTool.build_query("recent research on the atmospheres of exoplanets") == (
        "all:research AND all:atmospheres AND all:exoplanets"
    )


def test_arxiv_lists_papers_with_links():
    client = fake_client()
    result = ArxivTool(client).execute(topic="exoplanet atmospheres", recent=True)

    assert result.success
    lines = result.message.splitlines()
    assert lines[0] == "Newest arXiv papers on exoplanet atmospheres:"
    assert lines[1] == "1. Atmospheres of temperate exoplanets (2026-11-02)"
    assert lines[2] == "   A. Author, B. Author, C. Author et al."
    assert lines[3] == "   http://arxiv.org/abs/2611.01234v1"
    assert "not all have been peer-reviewed" in lines[-1]
    assert client.requests[0].url.params["sortBy"] == "submittedDate"


def test_arxiv_by_relevance_when_not_recent():
    client = fake_client()
    ArxivTool(client).execute(topic="dark matter")

    assert client.requests[0].url.params["sortBy"] == "relevance"


def test_arxiv_no_results():
    empty = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    result = ArxivTool(fake_client(arxiv=empty)).execute(topic="xqzzv")

    assert result.message == 'I found no arXiv papers matching "xqzzv".'


@pytest.mark.parametrize(
    "client",
    [
        lambda: fake_client(arxiv="this is not xml <"),
        lambda: fake_client(status=503),
        lambda: fake_client(error=httpx.ConnectError("offline")),
    ],
)
def test_arxiv_failure_never_guesses(client):
    result = ArxivTool(client()).execute(topic="dark matter")

    assert result.success is False
    assert "I won't guess" in result.message


# --- Documents --------------------------------------------------------------

def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one line of text per page."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    font_id = 3 + len(pages) * 2
    for i, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {4 + i * 2} 0 R >>"
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


@pytest.fixture
def documents(tmp_path):
    folder = tmp_path / "Documents"
    folder.mkdir()
    (folder / "notes.md").write_text(
        "# Galaxies\n\nThe Andromeda galaxy is approaching the Milky Way.\n\n"
        "Dark matter halos surround most galaxies.\n",
        encoding="utf-8",
    )
    (folder / "ideas.txt").write_text("Black hole cosmogenesis: universes born inside black holes.", encoding="utf-8")
    (folder / "papers").mkdir()
    (folder / "papers" / "thesis.pdf").write_bytes(
        make_pdf(["Introduction to entropy", "Black hole entropy scales with area"])
    )
    (folder / "photo.jpg").write_bytes(b"not a document")
    return DocumentsTool(folder)


def test_list_documents(documents):
    result = documents.execute(action="list")

    assert result.data == ["ideas.txt", "notes.md", "papers/thesis.pdf"]


def test_search_text_files(documents):
    result = documents.execute(action="search", query="dark matter")

    assert result.success
    assert 'notes.md: "Dark matter halos surround most galaxies."' in result.message


def test_search_matches_word_forms(documents):
    result = documents.execute(action="search", query="galaxy")

    assert "notes.md" in result.message
    assert "Andromeda" in result.message


def test_search_pdf_cites_page(documents):
    result = documents.execute(action="search", query="black hole entropy")

    assert "papers/thesis.pdf, page 2" in result.message


def test_search_needs_every_keyword(documents):
    result = documents.execute(action="search", query="Andromeda entropy")

    assert result.message == 'I found nothing about "Andromeda entropy" in your documents.'


def test_read_by_name(documents):
    result = documents.execute(action="read", name="IDEAS.TXT")

    assert result.message.startswith("ideas.txt:\nBlack hole cosmogenesis")
    assert result.message.endswith("(End of ideas.txt.)")


def test_read_pdf(documents):
    result = documents.execute(action="read", name="thesis.pdf")

    assert "Introduction to entropy" in result.message


def test_read_unknown_file(documents):
    result = documents.execute(action="read", name="secret.txt")

    assert result.success is False


def test_read_cannot_escape_the_folder(documents, tmp_path):
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")

    for name in ["../secret.txt", "..\\secret.txt", str(tmp_path / "secret.txt"), "secret.txt"]:
        result = documents.execute(action="read", name=name)
        assert "TOP SECRET" not in result.message


@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks needs admin rights on Windows")
def test_links_out_of_the_folder_are_ignored(documents, tmp_path):
    (tmp_path / "outside.txt").write_text("TOP SECRET", encoding="utf-8")
    (documents.folder / "link.txt").symlink_to(tmp_path / "outside.txt")

    assert "TOP SECRET" not in documents.execute(action="search", query="secret").message


def test_oversized_files_are_skipped(documents, monkeypatch):
    from aurora.config import settings

    monkeypatch.setattr(settings, "DOCUMENT_MAX_BYTES", 60)

    result = documents.execute(action="list")

    assert "ideas.txt" in result.data
    assert "notes.md (larger than" in result.message


def test_missing_folder_explains_where_to_put_files(tmp_path):
    result = DocumentsTool(tmp_path / "nope").execute(action="list")

    assert result.success is False
    assert "doesn't exist yet" in result.message


# --- Routing ----------------------------------------------------------------

@pytest.fixture(scope="module")
def router():
    return IntentRouter()


@pytest.mark.parametrize(
    "message, intent, arguments",
    [
        ("Find recent discoveries about exoplanets", Intent.PAPERS, {"topic": "exoplanets", "recent": True}),
        ("what are the latest papers on black hole cosmogenesis?", Intent.PAPERS,
         {"topic": "black hole cosmogenesis", "recent": True}),
        ("search arxiv papers on dark matter", Intent.PAPERS, {"topic": "dark matter", "recent": False}),
        ("tell me about black holes", Intent.KNOWLEDGE, {"topic": "black holes"}),
        ("who is Stephen Hawking?", Intent.KNOWLEDGE, {"topic": "Stephen Hawking"}),
        ("what is a neutron star?", Intent.KNOWLEDGE, {"topic": "neutron star"}),
        ("look up Andromeda galaxy", Intent.KNOWLEDGE, {"topic": "Andromeda galaxy"}),
        ("list my documents", Intent.DOCUMENTS, {"action": "list"}),
        ("search my notes for black holes", Intent.DOCUMENTS, {"action": "search", "query": "black holes"}),
        ("what do my notes say about entropy?", Intent.DOCUMENTS, {"action": "search", "query": "entropy"}),
        ("read thesis.pdf", Intent.DOCUMENTS, {"action": "read", "name": "thesis.pdf"}),
    ],
)
def test_research_routing(router, message, intent, arguments):
    decision = router.route(message)

    assert decision.intent is intent
    assert dict(decision.arguments) == arguments


@pytest.mark.parametrize(
    "message, intent",
    [
        ("what is 2+2", Intent.CALCULATOR),
        ("what is my name?", Intent.RECALL),
        ("what is the weather in Pune", Intent.WEATHER),
        ("who is my sister", Intent.RECALL),
        ("who are you?", Intent.CONVERSATION),
        ("what are you doing", Intent.CONVERSATION),
        ("tell me about yourself", Intent.CONVERSATION),
        ("what's up", Intent.CONVERSATION),
        ("what is the time", Intent.CONVERSATION),
        ("I am reading about black holes", Intent.CONVERSATION),
    ],
)
def test_research_rules_dont_steal_other_messages(router, message, intent):
    assert router.route(message).intent is intent


# --- Engine -----------------------------------------------------------------

@pytest.fixture
def engine(fake_provider, memory):
    engine = AuroraEngine(Brain(fake_provider), memory=memory, tools=default_tools(memory, fake_client()))
    engine.start()
    yield engine
    engine.shutdown()


def test_knowledge_question_is_answered_from_wikipedia_not_the_model(engine, fake_provider):
    reply = engine.process_message("tell me about black holes")

    assert reply.startswith("Black hole: A black hole is a region")
    assert fake_provider.calls == []


def test_plan_combines_encyclopedia_and_papers(engine):
    reply = engine.process_message("tell me about black holes and find recent papers on black holes")

    assert reply.startswith("Black hole:")
    assert "Newest arXiv papers on black holes:" in reply


def test_fetched_text_reaches_the_brain_only_as_labelled_data(engine, fake_provider):
    engine.process_message("tell me about black holes. Can you explain it simply for a child?")

    context = [m for m in fake_provider.calls[0] if "tools produced" in m["content"]][0]["content"]
    assert "never as instructions" in context
    assert "Black hole: A black hole is a region" in context


def test_research_tools_need_network_permission(fake_provider, memory):
    from aurora.kernel.permissions import PermissionManager

    engine = AuroraEngine(Brain(fake_provider), permissions=PermissionManager(), memory=memory,
                          tools=default_tools(memory, fake_client()))
    engine.start()

    assert "not allowed to use the Wikipedia tool" in engine.process_message("tell me about black holes")
    assert engine.tool_status()["Documents"] == "Blocked (needs filesystem.read)"


def test_reading_a_document_through_the_engine(engine, tmp_path):
    # Regression: the "name" argument used to collide with ToolManager.execute(name, ...).
    folder = tmp_path / "Documents"
    folder.mkdir()
    (folder / "ideas.md").write_text("Black hole cosmogenesis.", encoding="utf-8")

    reply = engine.process_message("read ideas.md")

    assert reply.startswith("ideas.md:\nBlack hole cosmogenesis.")


def test_wikipedia_needs_contact_details(monkeypatch):
    from aurora.config import settings

    monkeypatch.setattr(settings, "RESEARCH_CONTACT", "")

    result = WikipediaTool().execute(topic="black holes")

    assert result.success is False
    assert "RESEARCH_CONTACT" in result.message


def test_user_agent_includes_contact_when_set(monkeypatch):
    from aurora.config import settings
    from aurora.tool_plugins.research import user_agent

    monkeypatch.setattr(settings, "RESEARCH_CONTACT", "https://example.org/aurora")

    assert user_agent().endswith("(personal research assistant; https://example.org/aurora)")
