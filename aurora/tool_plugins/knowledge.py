"""
Knowledge tool: questions, summaries and comparisons over the user's
documents, with citations.

    ask        "what do my notes say about dark matter?"
               "according to thesis.pdf, how many planets are in the sample?"
    summarize  "summarize thesis.pdf" · "summarize what my notes say about black holes"
    compare    "compare astronomy_notes.md and lecture.txt on dark matter"
               "compare what my documents and Wikipedia say about dark matter"
    status     "what's in my knowledge base?"
    reindex    "re-index my documents"

The index is brought up to date (changed files only) before each request.
Comparisons with Wikipedia call the Wikipedia tool through the tool
manager, so its own network permission is still checked.
"""

import logging
from datetime import datetime

from aurora.kernel.permissions import Permission
from aurora.knowledge import answers
from aurora.knowledge.index import KnowledgeIndex
from aurora.knowledge.sources import LIVE, Source
from aurora.tool_plugins.documents import DocumentsTool
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

logger = logging.getLogger(__name__)

ALL_DOCUMENTS = "documents"


class KnowledgeTool(BaseTool):

    required_permissions = (Permission.FILESYSTEM_READ,)

    def __init__(self, documents: DocumentsTool | None = None, index: KnowledgeIndex | None = None):
        super().__init__("Knowledge")
        self._documents = documents
        self._index = index
        self.tools = None            # ToolManager, for Wikipedia (attach_tools)

    def attach_tools(self, tools) -> None:
        self.tools = tools

    @property
    def index(self) -> KnowledgeIndex:
        if self._index is None:
            self._index = KnowledgeIndex(self._documents or DocumentsTool())
        return self._index

    def execute(self, action: str = "ask", question: str = "", topic: str = "", document: str = "",
                sides=(), **_) -> ToolResult:
        if not self.index.documents.folder.is_dir():
            return ToolResult(False, f"Your documents folder doesn't exist yet. Create {self.index.documents.folder} "
                                     "and put .txt, .md or .pdf files in it.")
        try:
            report = self.index.sync()
        except Exception as error:
            logger.exception("Knowledge index update failed")
            return ToolResult(False, f"I couldn't update the document index ({type(error).__name__}).")

        if action == "reindex":
            self.index.clear()
            report = self.index.sync()
            return self._status(f"Re-indexed your documents: {len(report.added)} document(s).", report)
        if action == "status":
            return self._status("", report)

        name, problem = self._document(document)
        if problem:
            return ToolResult(False, problem)

        if action == "summarize":
            summary = (answers.summarize_document(self.index, name) if name
                       else answers.summarize_topic(self.index, topic or question))
            return ToolResult(bool(summary.quotes), summary.text(), summary, sources=summary.sources)

        if action == "compare":
            return self._compare(topic or question, list(sides))

        result = answers.answer(self.index, question, document=name)
        return ToolResult(result.status in ("found", "partial"), result.text(question), result, sources=result.sources)

    # ------------------------------------------------------------- helpers

    def _document(self, wanted: str) -> tuple[str | None, str | None]:
        """The indexed document the user named, or None for all of them."""
        if not wanted or wanted.lower() in (ALL_DOCUMENTS, "notes", "files", "docs"):
            return None, None
        found = self.index.find_document(wanted)
        if not found:
            return None, f"I can't find a document called \"{wanted}\". Say \"list my documents\" to see them."
        if len(found) > 1:
            return None, f"Several documents match \"{wanted}\": {', '.join(found[:5])}. Which one?"
        return found[0], None

    def _status(self, heading: str, report) -> ToolResult:
        documents = self.index.documents_indexed()
        if not documents:
            return ToolResult(True, (heading + "\n" if heading else "")
                              + "Your knowledge base is empty: there are no .txt, .md or .pdf documents yet.", [])
        def plural(count, word):
            return f"{count} {word}{'' if count == 1 else 's'}"

        lines = [f"  - {d['name']} ("
                 + (plural(d["pages"], "page") + ", " if d["pages"] else "")
                 + plural(d["passages"], "passage") + ")" for d in documents]
        text = (heading + "\n" if heading else "") + \
            f"Your knowledge base has {len(documents)} document(s), {self.index.passage_count()} passages:\n" + \
            "\n".join(lines)
        if report.failed:
            text += "\nCouldn't read: " + ", ".join(report.failed)
        return ToolResult(True, text, documents)

    def _wikipedia(self, topic: str):
        if self.tools is None or self.tools.get("Wikipedia") is None:
            raise RuntimeError("Wikipedia isn't available")
        result = self.tools.execute("Wikipedia", topic=topic)
        if not result.success or not isinstance(result.data, dict) or not result.data.get("extract"):
            raise RuntimeError(result.message.split("\n")[0])
        data = result.data
        title = data.get("title") or topic
        url = (data.get("content_urls") or {}).get("desktop", {}).get("page", "")
        return data["extract"], Source(LIVE, f"Wikipedia, \"{title}\"", url, datetime.now())

    def _compare(self, topic: str, sides: list[str]) -> ToolResult:
        if len(sides) != 2 or not topic:
            return ToolResult(False, "Tell me two sources and a topic, for example: "
                                     "\"compare notes.md and lecture.txt on dark matter\".")
        resolved = []
        for side in sides:
            if side.lower() in ("wikipedia", "wiki"):
                resolved.append("wikipedia")
                continue
            if side.lower() in (ALL_DOCUMENTS, "my documents", "my notes", "notes", "my files"):
                resolved.append(ALL_DOCUMENTS)
                continue
            name, problem = self._document(side)
            if problem:
                return ToolResult(False, problem)
            resolved.append(name)
        comparison = answers.compare(self.index, topic, [s for s in resolved], fetch_live=self._wikipedia)
        for side in comparison.sides:
            if side.name == ALL_DOCUMENTS:
                side.name = "Your documents"
        sources = []
        for side in comparison.sides:
            for quote in side.quotes:
                source = side.source or quote.source
                if source not in sources:
                    sources.append(source)
        ok = any(side.quotes for side in comparison.sides)
        return ToolResult(ok, comparison.text(), comparison, sources=sources)
