"""
Read-only access to the user's documents in ONE folder
(``settings.DOCUMENTS_DIR``, by default I:\\Aurora\\Documents).

- list:   which documents there are
- search: passages that mention every keyword, with file (and page) citations
- read:   the beginning of one document, chosen by name

Safety:
- Only .txt, .md and .pdf files inside the folder are ever opened.
- Files are chosen from the folder's own listing; a path from the user
  is never opened directly, so "..\\..\\secret.txt" can't escape.
- Links pointing outside the folder are ignored; oversized files are skipped.
- File contents are untrusted text: shown to the user, never obeyed.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from aurora.config import settings
from aurora.kernel.permissions import Permission
from aurora.memory.manager import keywords
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool
from aurora.tools.untrusted import clean_text, one_line, shorten

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".txt", ".md", ".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS
MAX_PDF_PAGES = 300
MAX_RESULTS = 5


@dataclass
class Passage:
    file: str
    page: int | None   # PDF page number (1-based); None for text files.
    text: str

    @property
    def citation(self) -> str:
        return f"{self.file}, page {self.page}" if self.page else self.file


def _stem_prefixes(words: list[str]) -> list[str]:
    # keywords() already stems; match word starts so "galax" finds "galaxies".
    return [w[:-1] if len(w) > 5 else w for w in words]


class DocumentsTool(BaseTool):

    required_permissions = (Permission.FILESYSTEM_READ,)

    def __init__(self, folder: Path | None = None, analyzer=None):
        super().__init__("Documents")
        self._folder = folder
        self._analyzer = analyzer   # VisionAnalyzer, created when first needed.
        self._cache: dict[tuple[str, float], list[Passage]] = {}

    @property
    def folder(self) -> Path:
        return Path(self._folder or settings.DOCUMENTS_DIR)

    # --- Files ----------------------------------------------------------------

    def files(self) -> tuple[list[Path], list[str]]:
        """Readable documents, and notes about anything skipped."""
        root = self.folder.resolve()
        found, skipped = [], []

        if not root.is_dir():
            return [], []

        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in EXTENSIONS or not path.is_file():
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(root):
                skipped.append(f"{path.name} (points outside the Documents folder)")
                continue
            if resolved.stat().st_size > settings.DOCUMENT_MAX_BYTES:
                skipped.append(f"{path.name} (larger than {settings.DOCUMENT_MAX_BYTES // 1_000_000} MB)")
                continue
            found.append(resolved)

        return found, skipped

    def _name(self, path: Path) -> str:
        return path.relative_to(self.folder.resolve()).as_posix()

    def passages(self, path: Path) -> list[Passage]:
        key = (str(path), path.stat().st_mtime)
        if key in self._cache:
            return self._cache[key]

        name = self._name(path)
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            passages = []   # Images aren't searched; "read <image>" analyses them.
        elif path.suffix.lower() == ".pdf":
            passages = self._pdf_passages(path, name)
        else:
            text = clean_text(path.read_text(encoding="utf-8", errors="replace"))
            passages = [Passage(name, None, p) for p in re.split(r"\n\s*\n", text) if p.strip()]

        self._cache[key] = passages
        return passages

    @staticmethod
    def _pdf_passages(path: Path, name: str) -> list[Passage]:
        from pypdf import PdfReader  # Imported lazily: only needed for PDFs.

        reader = PdfReader(str(path))
        passages = []
        for number, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
            text = clean_text(page.extract_text() or "")
            for paragraph in re.split(r"\n\s*\n", text):
                if paragraph.strip():
                    passages.append(Passage(name, number, paragraph))
        return passages

    # --- Actions --------------------------------------------------------------

    def execute(self, action: str = "list", query: str = "", name: str = "", **_) -> ToolResult:
        if not self.folder.is_dir():
            return ToolResult(
                False,
                f"Your documents folder doesn't exist yet. Create {self.folder} "
                "and put .txt, .md, .pdf or image files in it.",
            )

        try:
            if action == "search":
                return self.search(query)
            if action == "read":
                return self.read(name)
            return self.list()
        except Exception as error:
            logger.exception("Documents %s failed", action)
            return ToolResult(False, f"I couldn't read your documents ({type(error).__name__}).")

    def read_image(self, path: Path) -> ToolResult:
        """Objects and text in an image, analysed on this computer."""
        import cv2
        import numpy as np

        from aurora.vision.analyzer import VisionAnalyzer
        from aurora.vision.camera import VisionUnavailable

        image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return ToolResult(False, f"I can't open {self._name(path)} as an image.")

        try:
            self._analyzer = self._analyzer or VisionAnalyzer()
            objects, _ = self._analyzer.objects(image)
            text, lines = self._analyzer.text(image)
        except VisionUnavailable as error:
            return ToolResult(False, str(error))

        message = f"{self._name(path)}: {objects}"
        if lines:
            message += "\n" + text
        return ToolResult(True, message, {"objects": objects, "text": lines})

    def list(self) -> ToolResult:
        files, skipped = self.files()
        if not files:
            note = f" ({len(skipped)} skipped)" if skipped else ""
            return ToolResult(True, f"There are no documents or images in {self.folder}{note}.", [])

        lines = [f"  - {self._name(p)} ({p.stat().st_size / 1024:.0f} KB)" for p in files]
        message = f"Your documents ({len(files)}):\n" + "\n".join(lines)
        if skipped:
            message += "\nSkipped: " + "; ".join(skipped)
        return ToolResult(True, message, [self._name(p) for p in files])

    def search(self, query: str) -> ToolResult:
        words = _stem_prefixes(keywords(query))
        if not words:
            return ToolResult(False, "What should I search your documents for?")

        files, _ = self.files()
        hits = []
        for path in files:
            for passage in self.passages(path):
                lowered = passage.text.lower()
                if all(word in lowered for word in words):
                    hits.append(passage)

        if not hits:
            return ToolResult(True, f"I found nothing about \"{one_line(query)}\" in your documents.", [])

        lines = [
            f"  - {p.citation}: \"{shorten(one_line(p.text), 240)}\""
            for p in hits[:MAX_RESULTS]
        ]
        more = f"\n  (and {len(hits) - MAX_RESULTS} more)" if len(hits) > MAX_RESULTS else ""
        return ToolResult(
            True,
            f"Found in your documents ({len(hits)} passage{'s' if len(hits) != 1 else ''}):\n"
            + "\n".join(lines) + more,
            hits,
        )

    def read(self, name: str) -> ToolResult:
        files, _ = self.files()
        wanted = name.strip().lower()

        exact = [p for p in files if self._name(p).lower() == wanted or p.name.lower() == wanted]
        partial = [p for p in files if wanted and wanted in p.name.lower()]
        candidates = exact or partial

        if not candidates:
            return ToolResult(False, f"I can't find a document called \"{one_line(name)}\". Say \"list my documents\" to see them.")
        if len(candidates) > 1:
            names = ", ".join(self._name(p) for p in candidates[:5])
            return ToolResult(False, f"Several documents match: {names}. Which one?")

        path = candidates[0]
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            return self.read_image(path)

        passages = self.passages(path)
        text = "\n\n".join(p.text for p in passages)
        if not text:
            return ToolResult(False, f"{self._name(path)} has no readable text (it may be a scanned PDF).")

        excerpt = shorten(text, settings.DOCUMENT_READ_CHARS)
        footer = (
            f"\n\n(Showing the start of {self._name(path)}: {len(excerpt)} of {len(text)} characters. "
            "Ask me to search it for something specific.)"
            if len(excerpt) < len(text) else f"\n\n(End of {self._name(path)}.)"
        )
        return ToolResult(True, f"{self._name(path)}:\n{excerpt}{footer}", text)
