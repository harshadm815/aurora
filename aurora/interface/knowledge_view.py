"""
Knowledge Centre: research projects (from the research log) and saved
notes.

Your files are on the Documents page (browse, preview, Smart search,
summaries), and questions about your documents are answered in Chat
("what do my notes say about dark matter?"), quoted with their sources.
"""

from PySide6.QtWidgets import QLineEdit, QListWidget, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import label, safe_markdown


def _browser() -> QTextBrowser:
    browser = QTextBrowser()
    browser.setOpenExternalLinks(False)
    browser.setOpenLinks(False)
    return browser


class KnowledgeView(BaseView):
    title = "Knowledge Centre"
    subtitle = ("Your research projects and notes. Files are on the Documents page; ask about your documents "
                "in Chat.")
    nav_icon = "📚"
    scrollable = False

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._research_tab(), "Research projects")
        self.tabs.addTab(self._notes_tab(), "Notes")
        self.body.addWidget(self.tabs, 1)

    # --- Research projects ----------------------------------------------------------

    def _research_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(label("Sessions you saved after \"continue my research on …\" in Chat.", "Faint"))
        split = QSplitter()
        self.projects = QListWidget()
        self.projects.currentRowChanged.connect(self._show_project)
        self.project_detail = _browser()
        split.addWidget(self.projects)
        split.addWidget(self.project_detail)
        split.setSizes([260, 600])
        layout.addWidget(split, 1)
        self._project_data = []
        return tab

    def _load_projects(self):
        tool = self.backend.tool("Research")
        if tool is None:
            return
        self.backend.run_io(tool.log.projects, self._projects_loaded,
                            lambda e: self.project_detail.setPlainText(f"Couldn't read the research log: {e}"))

    def _projects_loaded(self, projects):
        self._project_data = projects
        self.projects.clear()
        for project in projects:
            self.projects.addItem(f"{project.name}  ·  {len(project.sessions)} session(s)")
        if not projects:
            self.project_detail.setMarkdown(
                "No research projects yet.\n\nIn **Chat**, try *continue my research on black hole "
                "cosmogenesis* and answer **yes** to save the session."
            )
        else:
            self.projects.setCurrentRow(0)

    def _show_project(self, row: int):
        if not 0 <= row < len(self._project_data):
            return
        project = self._project_data[row]
        lines = [f"## {safe_markdown(project.name)}", f"Started {project.created:%d %b %Y}.", ""]
        for session in reversed(project.sessions):
            lines.append(f"**{session.time:%d %b %Y %H:%M}** — {safe_markdown(session.summary)}")
            for url in session.papers:
                lines.append(f"- {safe_markdown(url)}")
            lines.append("")
        self.project_detail.setMarkdown("\n".join(lines))

    # --- Notes --------------------------------------------------------------------

    def _notes_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(label("Things you asked AURORA to remember (\"remember that …\"). "
                               "Edit or delete them in the Memory Centre.", "Faint", wrap=True))
        self.note_search = QLineEdit()
        self.note_search.setPlaceholderText("Filter notes…")
        self.note_search.textChanged.connect(self._fill_notes)
        layout.addWidget(self.note_search)
        self.notes = QListWidget()
        layout.addWidget(self.notes, 1)
        self._note_data = []
        return tab

    def _load_notes(self):
        engine = self.backend.engine
        if engine is None:
            return
        self.backend.run_io(lambda: [f for f in engine.memory.memories() if f.category == "Note"],
                            self._notes_loaded)

    def _notes_loaded(self, notes):
        self._note_data = notes
        self._fill_notes()

    def _fill_notes(self):
        query = self.note_search.text().strip().lower()
        self.notes.clear()
        shown = [n for n in self._note_data if not query or query in n.value.lower()]
        for note in shown:
            created = f"{note.created:%d %b %Y}" if note.created else ""
            self.notes.addItem(f"{note.value}    ·  {created}")
        if not shown:
            self.notes.addItem("No notes yet." if not self._note_data else "No notes match.")

    # --- Lifecycle ------------------------------------------------------------------

    def on_engine_ready(self):
        self.on_shown()

    def on_shown(self):
        if self.backend.engine is None:
            return
        self._load_projects()
        self._load_notes()
