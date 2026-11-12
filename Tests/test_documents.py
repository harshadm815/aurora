"""
AURORA Documents (the Explorer-style Documents page): places, file
operations, favourites and recent files, Smart search, and the page.
Everything happens in the test's temporary folders; nothing goes to the
real Recycle Bin.
"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from aurora.config import settings
from aurora.explorer import operations
from aurora.explorer.operations import ExplorerError
from aurora.explorer.places import AURORA, is_aurora_document, place_of, places
from aurora.explorer.store import ExplorerStore


@pytest.fixture
def folders(tmp_path, monkeypatch):
    documents = Path(settings.DOCUMENTS_DIR)
    (documents / "Research" / "Cosmology").mkdir(parents=True)
    (documents / "Research" / "Cosmology" / "cosmogenesis.md").write_text(
        "# Black hole cosmogenesis\n\nThe equations relate the parent black hole mass to the expansion rate "
        "of the child universe.\n", encoding="utf-8")
    (documents / "Research" / "notes.txt").write_text("Dark matter makes up about 27 percent of the universe.",
                                                      encoding="utf-8")
    pictures = tmp_path / "Pictures"
    pictures.mkdir()
    (pictures / "moon.png").write_bytes(b"not really a png")
    monkeypatch.setattr(settings, "EXPLORER_PLACES", [("Pictures", str(pictures))])
    return SimpleNamespace(documents=documents, pictures=pictures, outside=tmp_path / "Outside", places=places())


# --- Places -------------------------------------------------------------------------------

def test_places_are_aurora_documents_then_configured_ones(folders):
    assert [(p.name, p.kind) for p in folders.places] == [("AURORA Documents", AURORA), ("Pictures", "user")]
    assert place_of(folders.documents / "Research" / "notes.txt", folders.places).name == "AURORA Documents"
    assert place_of(folders.pictures / "moon.png", folders.places).name == "Pictures"
    assert place_of(folders.outside, folders.places) is None
    assert is_aurora_document(folders.documents / "Research" / "notes.txt")
    assert not is_aurora_document(folders.pictures / "moon.png")


def test_missing_configured_places_are_left_out(folders, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "EXPLORER_PLACES", [("Gone", str(tmp_path / "nope")), ("Pictures", str(folders.pictures))])
    assert [p.name for p in places()] == ["AURORA Documents", "Pictures"]


# --- Operations ---------------------------------------------------------------------------

def test_new_folder_and_document_never_overwrite(folders):
    research = folders.documents / "Research"
    first = operations.new_folder(research, "Drafts", folders.places)
    second = operations.new_folder(research, "Drafts", folders.places)
    assert first.name == "Drafts" and second.name == "Drafts (2)"
    note = operations.new_document(research, "Ideas", folders.places)
    again = operations.new_document(research, "Ideas.md", folders.places)
    assert note.name == "Ideas.md" and again.name == "Ideas (2).md"
    assert note.read_text(encoding="utf-8") == "# Ideas\n\n"


@pytest.mark.parametrize("name, problem", [
    ("", "empty"), ("a/b", "contain"), ('what?', "contain"), ("CON", "reserves"), ("com1.txt", "reserves"),
    ("trailing.", "dot or a space"), ("x" * 201, "too long"),
])
def test_bad_names_are_refused_with_the_reason(name, problem):
    with pytest.raises(ExplorerError, match=problem):
        operations.check_name(name)


def test_rename(folders):
    notes = folders.documents / "Research" / "notes.txt"
    renamed = operations.rename(notes, "dark matter.txt", folders.places)
    assert renamed.name == "dark matter.txt" and not notes.exists()
    operations.new_document(folders.documents / "Research", "taken.txt", folders.places)
    with pytest.raises(ExplorerError, match="already"):
        operations.rename(renamed, "taken.txt", folders.places)
    assert operations.rename(renamed, "Dark Matter.txt", folders.places).name == "Dark Matter.txt"   # case only


def test_paste_copies_and_moves_without_overwriting(folders):
    research = folders.documents / "Research"
    notes = research / "notes.txt"
    copied = operations.paste([notes], research, cut=False, places=folders.places)
    assert copied[0].name == "notes (2).txt" and notes.exists()
    moved = operations.paste([research / "Cosmology"], folders.pictures, cut=True, places=folders.places)
    assert moved[0] == folders.pictures / "Cosmology" and (moved[0] / "cosmogenesis.md").exists()
    assert not (research / "Cosmology").exists()
    with pytest.raises(ExplorerError, match="inside itself"):
        operations.paste([folders.pictures / "Cosmology"], folders.pictures / "Cosmology", cut=True,
                         places=folders.places)


def test_nothing_outside_the_places_can_be_changed(folders):
    folders.outside.mkdir()
    secret = folders.outside / "system.ini"
    secret.write_text("x")
    for action in (lambda: operations.new_folder(folders.outside, "x", folders.places),
                   lambda: operations.rename(secret, "y.ini", folders.places),
                   lambda: operations.paste([secret], folders.documents, False, folders.places),
                   lambda: operations.paste([folders.documents / "Research" / "notes.txt"], folders.outside, False,
                                            folders.places),
                   lambda: operations.to_recycle_bin([secret], folders.places, mover=lambda p: True)):
        with pytest.raises(ExplorerError, match="isn't in one of"):
            action()
    assert secret.read_text() == "x"


def test_delete_goes_to_the_recycle_bin(folders):
    recycled = []
    notes = folders.documents / "Research" / "notes.txt"
    moved = operations.to_recycle_bin([notes], folders.places, mover=lambda p: recycled.append(p) or True)
    assert moved == [notes] and recycled == [notes]
    with pytest.raises(ExplorerError, match="one of the places"):
        operations.to_recycle_bin([folders.pictures], folders.places, mover=lambda p: True)
    with pytest.raises(ExplorerError, match="Couldn't move"):
        operations.to_recycle_bin([folders.pictures / "moon.png"], folders.places, mover=lambda p: False)


def test_info_types_sizes_search_and_preview(folders):
    notes = folders.documents / "Research" / "notes.txt"
    details = operations.info(notes)
    assert details.type == "Text document" and details.size == notes.stat().st_size and not details.is_dir
    assert operations.info(folders.documents / "Research").type == "File folder"
    assert operations.type_name(Path("x.weird")) == "WEIRD file"
    assert operations.human_size(2048) == "2.0 KB" and operations.human_size(None) == ""
    assert operations.search_names(folders.documents, "COSMO") == [
        folders.documents / "Research" / "Cosmology", folders.documents / "Research" / "Cosmology" / "cosmogenesis.md"]
    assert operations.preview_text(notes).startswith("Dark matter")
    assert operations.preview_text(folders.pictures / "moon.png") is None


# --- Favourites and recent ----------------------------------------------------------------

def test_favourites_and_recent(folders):
    store = ExplorerStore()
    notes = folders.documents / "Research" / "notes.txt"
    store.set_favourite(notes, True)
    store.set_favourite(notes, True)
    assert store.favourites() == [notes] and store.is_favourite(notes)
    store.opened(notes)
    store.opened(folders.pictures / "moon.png")
    store.opened(notes)
    assert store.recent() == [notes, folders.pictures / "moon.png"]
    store.forget(notes)
    assert store.favourites() == [] and store.recent() == [folders.pictures / "moon.png"]
    (folders.pictures / "moon.png").unlink()
    assert store.recent() == []                        # missing files aren't listed


# --- Smart search (the Knowledge tool's find action) ---------------------------------------

def test_smart_search_finds_documents_by_content(folders):
    from aurora.tool_plugins.knowledge import KnowledgeTool
    tool = KnowledgeTool()
    result = tool.execute(action="find", question="black hole cosmogenesis equations")
    assert result.success and result.data[0][0] == "Research/Cosmology/cosmogenesis.md"
    assert "parent black hole mass" in result.data[0][2]
    nothing = tool.execute(action="find", question="unicorn recipes")
    assert nothing.data == [] and "No document" in nothing.message


# --- The page -------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp_documents():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(qapp_documents, folders, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    from aurora.interface import documents_view
    from aurora.tool_plugins.knowledge import KnowledgeTool

    knowledge = KnowledgeTool()

    class Tools:
        def get(self, name):
            return knowledge if name == "Knowledge" else None

        def execute(self, name, **arguments):
            return knowledge.execute(**arguments)

    class Backend(QObject):
        engine_ready = Signal()
        engine = SimpleNamespace(tool_manager=Tools())

        def run_io(self, function, on_result=None, on_error=None):
            try:
                value = function()
            except Exception as error:
                on_error(error)
            else:
                on_result(value)

    names = []
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (names.pop(0), True)))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    recycled = []
    view = documents_view.DocumentsView(Backend(), recycle=lambda p: recycled.append(p) or True)
    view.names, view.recycled = names, recycled
    yield view
    from conftest import release_widgets
    release_widgets()


def _select(page, path: Path):
    page.details.setCurrentIndex(page.model.index(str(path)))


def test_page_opens_on_home_with_places(page, folders):
    texts = [page.results.item(i).text() for i in range(page.results.count())]
    assert "PLACES" in texts and any("AURORA Documents" in t for t in texts)
    nav = [page.nav.item(i).text() for i in range(page.nav.count())]
    assert nav[:3] == ["⌂  Home", "🕘  Recent", "★  Favourites"] and nav[-1].endswith("Pictures")


def test_page_navigates_with_breadcrumbs_and_up(page, folders):
    page.open_folder(folders.documents / "Research" / "Cosmology")
    crumbs = [page.crumbs.itemAt(i).widget().text() for i in range(page.crumbs.count())
              if page.crumbs.itemAt(i).widget() is not None]
    assert crumbs == ["AURORA Documents", " › ", "Research", " › ", "Cosmology"]
    page.up()
    assert page.folder == folders.documents / "Research"
    page.up()
    page.up()                                                     # stops at the place's top
    assert page.folder == folders.documents
    page.open_folder(folders.outside)
    assert "isn't in one of the places" in page.status.text() and page.folder == folders.documents
    for mode in ("Large icons", "List", "Details"):
        page.set_mode(mode)
    assert page.stack.currentWidget() is page.details


def test_page_file_operations(page, folders):
    research = folders.documents / "Research"
    page.open_folder(research)
    page.names.append("Drafts")
    page.new_folder()
    assert (research / "Drafts").is_dir()
    page.names.append("Plan.md")
    page.new_document()
    assert (research / "Plan.md").is_file()
    _select(page, research / "notes.txt")
    assert page.selected() == [research / "notes.txt"]
    page.copy(cut=False)
    page.open_folder(research / "Drafts")
    page.paste()
    assert (research / "Drafts" / "notes.txt").is_file() and (research / "notes.txt").is_file()
    page.open_folder(research)
    _select(page, research / "Plan.md")
    page.names.append("Roadmap.md")
    page.rename()
    assert (research / "Roadmap.md").is_file()
    _select(page, research / "Roadmap.md")
    page.delete()
    assert page.recycled == [research / "Roadmap.md"] and "Recycle Bin" in page.status.text()


def test_page_preview_favourite_and_summary(page, folders):
    notes = folders.documents / "Research" / "notes.txt"
    page.open_folder(notes.parent)
    _select(page, notes)
    assert page.preview_title.text() == "notes.txt"
    assert page.preview_text.toPlainText().startswith("Dark matter")
    assert page.summary_button.isEnabled()
    page.toggle_favourite()
    assert page.store.is_favourite(notes) and page.favourite_button.text().startswith("★")
    assert page.store.recent() == [notes]
    page.summarise()
    assert "27 percent" in page.summary_view.toPlainText()
    page.open_folder(folders.pictures)
    _select(page, folders.pictures / "moon.png")
    assert not page.summary_button.isEnabled()                   # outside AURORA's Documents folder


def test_page_name_filter_name_search_and_smart_search(page, folders):
    page.open_folder(folders.documents)
    page.search_box.setText("cosmo")
    page.search()
    assert page.special.startswith("Search:") and page.results.count() == 2
    page.smart.setChecked(True)
    page.search_box.setText("child universe expansion")
    page.search()
    first = page.results.item(0)
    assert first.data(256) == folders.documents / "Research" / "Cosmology" / "cosmogenesis.md"
    assert "parent black hole mass" in first.text()
