"""
"Save to Knowledge": the page's reader-mode text as a Markdown file in
Documents/<BROWSER_SAVED_FOLDER>/, with its address and the date.

Knowledge indexes it with your other documents on its next request, so
"what do my documents say about …" can quote it and cite the file. The
file is yours: open, edit or delete it like any document. An existing
file is never overwritten; a second save gets a new name ("… (2).md").
"""

import re
from datetime import datetime
from pathlib import Path

from aurora.browser.reader import ReaderPage
from aurora.config import settings

MAX_NAME = 80
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class NothingToSave(ValueError):
    pass


def folder() -> Path:
    return Path(settings.DOCUMENTS_DIR) / settings.BROWSER_SAVED_FOLDER


def file_name(title: str) -> str:
    """A safe Windows file name from a page title."""
    name = " ".join(_UNSAFE.sub(" ", title or "").split()).strip(" .")[:MAX_NAME].strip(" .")
    if not name or name.lower() in _RESERVED:
        name = "Web page"
    return name


def document(page: ReaderPage, saved: datetime) -> str:
    lines = [f"# {page.title or page.url}", "", f"Source: {page.url}  ", f"Saved: {saved:%d %B %Y %H:%M}", ""]
    if page.description:
        lines += [f"> {page.description}", ""]
    lines.append(page.markdown())
    return "\n".join(lines).rstrip() + "\n"


def save(page: ReaderPage, saved: datetime | None = None) -> Path:
    """Writes the page into the saved-pages folder and returns the new file's path."""
    if not page.blocks:
        raise NothingToSave("There's no readable text on this page to save.")
    target = folder()
    target.mkdir(parents=True, exist_ok=True)
    root = Path(settings.DOCUMENTS_DIR).resolve()
    if not target.resolve().is_relative_to(root):
        raise ValueError("The saved-pages folder must be inside the Documents folder.")
    base = file_name(page.title)
    path = target / f"{base}.md"
    number = 2
    while path.exists():
        path = target / f"{base} ({number}).md"
        number += 1
    with open(path, "x", encoding="utf-8") as handle:      # "x": never replaces a file
        handle.write(document(page, saved or datetime.now()))
    return path
