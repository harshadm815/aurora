"""
Automation: what AURORA may do on its own, what it has done, and how to undo it.

- Scheduled tasks (pause, resume, delete) and reminders (cancel).
- Undo: the most recent reversible action, with what undoing it will do.
- Tidy a folder: preview, then organise (asks first), stop part way, undo.
- Audit trail: every action asked for, approved, declined, done, failed,
  undone or skipped, and who started it (you or a schedule).
- The rules: every action, the permission it needs (and whether it is
  granted), whether AURORA asks first, and how it is undone.

Every button goes through the same checked and audited path as a chat
message (engine.execute_action / confirm_action), so nothing here can do
more than you could ask for in Chat.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from aurora.automation.actions import ACTIONS
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import Panel, label
from aurora.kernel.permissions import Permission
from aurora.routing.intent import Intent

OUTCOME_COLOURS = {"done": "#34d399", "undone": "#60a5fa", "failed": "#f87171", "declined": "#8b9bb4",
                   "cancelled": "#8b9bb4", "skipped": "#fbbf24", "asked": "#8b9bb4"}


def table(columns: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(columns))
    widget.setHorizontalHeaderLabels(columns)
    widget.verticalHeader().setVisible(False)
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setWordWrap(True)
    header = widget.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setStretchLastSection(True)
    return widget


def fill(widget: QTableWidget, rows: list[list[str]], data: list | None = None, colours: list | None = None):
    widget.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))               # the full text when a cell is cut short
            if data is not None and c == 0:
                item.setData(Qt.UserRole, data[r])
            if colours and colours[r] and c == 1:
                item.setForeground(QColor(colours[r]))
            widget.setItem(r, c, item)
    widget.resizeRowsToContents()


class AutomationView(BaseView):
    title = "Automation"
    subtitle = ("Scheduled tasks, reminders, tidy-ups and undo, with a record of everything AURORA did. "
                "Nothing runs unless you approved it.")
    nav_icon = "⚙"

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self.pending_plan = None

        tabs = QTabWidget()
        tabs.addTab(self._tasks_tab(), "Tasks and reminders")
        tabs.addTab(self._files_tab(), "Tidy a folder")
        tabs.addTab(self._audit_tab(), "Audit trail")
        tabs.addTab(self._rules_tab(), "What AURORA may do")
        self.tabs = tabs

        undo = Panel("Undo")
        row = QHBoxLayout()
        self.undo_text = label("Nothing to undo yet.", "Faint", wrap=True)
        self.undo_button = QPushButton("Undo…")
        self.undo_button.clicked.connect(self.undo)
        row.addWidget(self.undo_text, 1)
        row.addWidget(self.undo_button)
        undo.add_layout(row)

        self.body.addWidget(undo)
        self.body.addWidget(tabs, 1)
        self.status = label("", "Faint", wrap=True)
        self.body.addWidget(self.status)

        backend.task_ran.connect(lambda _: self.refresh())
        backend.reminder_due.connect(lambda _: self.refresh())
        backend.reminders_missed.connect(self._missed)

    # --- Tabs ---------------------------------------------------------------------------------

    def _tasks_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Add tasks in Chat, e.g. \"every weekday at 18:30 turn on the desk light\" or "
                               "\"every day at 7 run my morning briefing\". Each is approved once.", "Faint", wrap=True))
        self.tasks = table(["#", "When", "Does", "Next run", "Last result"])
        layout.addWidget(self.tasks, 2)
        buttons = QHBoxLayout()
        self.pause_button = QPushButton("Pause / resume")
        self.pause_button.clicked.connect(self.toggle_task)
        self.delete_button = QPushButton("Delete task")
        self.delete_button.clicked.connect(self.delete_task)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        for button in (self.pause_button, self.delete_button, refresh):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        layout.addWidget(label("Reminders", "SectionTitle"))
        self.reminders = table(["#", "Due", "Reminder"])
        layout.addWidget(self.reminders, 1)
        self.missed_label = label("", "Faint", wrap=True)
        layout.addWidget(self.missed_label)
        cancel = QPushButton("Cancel reminder")
        cancel.clicked.connect(self.cancel_reminder)
        row = QHBoxLayout()
        row.addWidget(cancel)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    def _files_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Moves files in an allowed folder into subfolders. You see every move first; "
                               "nothing is deleted or overwritten, and it can be undone.", "Faint", wrap=True))
        row = QHBoxLayout()
        self.folder_choice = QComboBox()
        self.folder_choice.setMinimumWidth(170)
        self.scheme_choice = QComboBox()
        self.scheme_choice.addItem("by type (Images, PDFs, Notes…)", "type")
        self.scheme_choice.addItem("by month (2026-11…)", "month")
        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self.preview)
        self.organize_button = QPushButton("Organise…")
        self.organize_button.setObjectName("Primary")
        self.organize_button.setEnabled(False)
        self.organize_button.clicked.connect(self.organize)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_tidy)
        self.undo_tidy_button = QPushButton("Undo last tidy-up…")
        self.undo_tidy_button.clicked.connect(self.undo_tidy)
        for widget in (self.folder_choice, self.scheme_choice, self.preview_button, self.organize_button,
                       self.stop_button, self.undo_tidy_button):
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)
        from PySide6.QtWidgets import QPlainTextEdit

        self.plan_text = QPlainTextEdit()
        self.plan_text.setReadOnly(True)
        self.plan_text.setPlaceholderText("Choose a folder and click Preview.")
        layout.addWidget(self.plan_text, 1)
        return page

    def _audit_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.audit_filter = QComboBox()
        self.audit_filter.addItem("Everything", "")
        for prefix, name in (("devices", "Devices"), ("apps", "Programs"), ("files", "Files"),
                             ("reminders", "Reminders"), ("schedule", "Scheduled tasks"), ("memory", "Memory")):
            self.audit_filter.addItem(name, prefix)
        self.audit_filter.currentIndexChanged.connect(lambda _: self.refresh_audit())
        row.addWidget(self.audit_filter)
        row.addStretch(1)
        layout.addLayout(row)
        self.audit = table(["When", "Outcome", "Action", "By", "What"])
        layout.addWidget(self.audit, 1)
        layout.addWidget(label("Kept for 90 days in aurora_automation.db on this computer. AURORA's log file "
                               "only records the action and outcome.", "Faint", wrap=True))
        return page

    def _rules_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Every action AURORA can take. Anything not listed, it can't do. Permissions are "
                               "set in PERMISSION_GRANTS (settings).", "Faint", wrap=True))
        self.rules = table(["Action", "Allowed", "Needs", "Asks first", "In schedules", "Undo"])
        header = self.rules.horizontalHeader()
        for column, width in enumerate((320, 90, 200, 170, 100)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            self.rules.setColumnWidth(column, width)
        layout.addWidget(self.rules, 1)
        return page

    # --- Refreshing ------------------------------------------------------------------------------

    def on_shown(self):
        self.refresh()

    def on_engine_ready(self):
        self.refresh()

    def refresh(self):
        engine = self.backend.engine
        if engine is None:
            return
        self._show_rules(engine)
        schedule = engine.tool_manager.get("Schedule")
        reminders = engine.tool_manager.get("Reminders")
        files = engine.tool_manager.get("Files")

        def work():
            tasks = schedule.tasks.all() if schedule else []
            pending = reminders.scheduler.pending() if reminders else []
            from aurora.automation.files import allowed_folders
            return tasks, pending, allowed_folders() if files else []

        self.backend.run_io(work, self._show)
        self.refresh_audit()
        offer = engine.executor.undo_stack[-1] if engine.executor.undo_stack else None
        self.undo_text.setText(f"Undoing your last action would {offer.label}." if offer else "Nothing to undo yet.")
        self.undo_button.setEnabled(offer is not None)

    def _show(self, result):
        tasks, pending, folders = result
        from datetime import datetime

        now = datetime.now()
        rows = []
        for task in tasks:
            upcoming = task.next_run(now)
            last = "—" if not task.last_run else (
                f"{task.last_run:%d %b %H:%M} " + {True: "done", False: "failed", None: "skipped"}[task.last_success])
            rows.append([task.id, task.when() + ("" if task.active else " (paused)"), task.name,
                         f"{upcoming:%a %d %b %H:%M}" if upcoming else "—", last])
        fill(self.tasks, rows, tasks)
        fill(self.reminders, [[r.id, f"{r.due:%a %H:%M}", r.text] for r in pending], pending)
        current = self.folder_choice.currentData()
        self.folder_choice.clear()
        for folder in folders:
            self.folder_choice.addItem(folder.name, folder.name)
            self.folder_choice.setItemData(self.folder_choice.count() - 1, str(folder), Qt.ToolTipRole)
        if current:
            index = self.folder_choice.findData(current)
            self.folder_choice.setCurrentIndex(max(index, 0))

    def _missed(self, reminders: list):
        if reminders:
            lines = "; ".join(f"{item.get('due', '')[11:16]} {item.get('text', '')}" for item in reminders)
            self.missed_label.setText(f"While AURORA was closed, these were due: {lines}")

    def refresh_audit(self):
        engine = self.backend.engine
        if engine is None:
            return
        prefix = self.audit_filter.currentData() or None
        self.backend.run_io(lambda: engine.executor.audit.recent(200, prefix), self._show_audit)

    def _show_audit(self, entries):
        rows = [[f"{e.time:%d %b %H:%M}", e.outcome, e.action, e.initiated,
                 e.summary + (" (simulated)" if e.simulated and "simulated" not in e.summary else "")]
                for e in entries]
        fill(self.audit, rows, entries, [OUTCOME_COLOURS.get(e.outcome) for e in entries])

    def _show_rules(self, engine):
        rows = []
        for spec in ACTIONS:
            tool = engine.tool_manager.get(spec.tool)
            if tool is None:
                allowed = "not loaded"
            else:
                missing = engine.permissions.missing(spec.tool, tool.required_permissions)
                allowed = "yes" if not missing else "no (needs " + ", ".join(p.value for p in missing) + ")"
            rows.append([spec.does, allowed, ", ".join(spec.permissions) or "—", spec.asks_first,
                         "yes" if spec.schedulable else "no", spec.undo])
        fill(self.rules, rows)

    # --- Actions (same path as chat: checked and audited) ---------------------------------------

    def _run(self, intent: Intent, arguments: dict, done=None):
        engine = self.backend.engine
        if engine is None:
            return

        def finished(result):
            self.status.setText(result.message.splitlines()[0] if result.message else "")
            if result.needs_confirmation:
                self._ask(intent, result, done)
                return
            if done:
                done(result)
            self.refresh()

        self.backend.run_engine(lambda: engine.execute_action(intent, arguments), finished,
                                lambda error: self.status.setText(f"That failed: {error}"))

    def _ask(self, intent: Intent, pending, done=None):
        engine = self.backend.engine
        name = {Intent.FILES: "Files", Intent.SCHEDULE: "Schedule", Intent.REMINDER: "Reminders"}.get(intent, "")
        answer = QMessageBox.question(self, "Please confirm", pending.message.replace(" (yes / no)", ""),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            self.backend.run_engine(lambda: engine.decline_action(name, pending))
            self.status.setText("Okay, nothing was changed.")
            self.stop_button.setEnabled(False)
            return

        def finished(result):
            self.status.setText(result.message.splitlines()[0])
            if done:
                done(result)
            self.refresh()

        self.backend.run_engine(lambda: engine.confirm_action(name, pending), finished,
                                lambda error: self.status.setText(f"That failed: {error}"))

    def _selected(self, widget: QTableWidget):
        row = widget.currentRow()
        if row < 0 or widget.item(row, 0) is None:
            self.status.setText("Select a row first.")
            return None
        return widget.item(row, 0).data(Qt.UserRole)

    def toggle_task(self):
        task = self._selected(self.tasks)
        if task is not None:
            self._run(Intent.SCHEDULE, {"action": "pause" if task.active else "resume", "number": task.id})

    def delete_task(self):
        task = self._selected(self.tasks)
        if task is None:
            return
        answer = QMessageBox.question(self, "Delete task", f"Delete scheduled task {task.id} ({task.when()}, "
                                      f"{task.name})?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self._run(Intent.SCHEDULE, {"action": "delete", "number": task.id})

    def cancel_reminder(self):
        reminder = self._selected(self.reminders)
        if reminder is not None:
            self._run(Intent.REMINDER, {"action": "cancel", "which": str(reminder.id)})

    def undo(self):
        engine = self.backend.engine
        if engine is None:
            return
        offer = engine.undo_offer()
        if offer is None:
            self.status.setText("There's nothing to undo.")
            return
        answer = QMessageBox.question(self, "Undo", offer.message.replace(" (yes / no)", ""),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        def finished(result):
            self.status.setText(result.message.splitlines()[0])
            self.refresh()

        self.backend.run_engine(lambda: engine.confirm_action("Undo", offer), finished)

    # --- Tidy a folder -----------------------------------------------------------------------

    def preview(self):
        folder = self.folder_choice.currentData()
        if not folder:
            self.plan_text.setPlainText("No folder is allowed to be tidied (ORGANIZE_FOLDERS in settings).")
            return
        files = self.backend.tool("Files")
        if files is None:
            return
        missing = self.backend.missing_permissions("Files", files.required_permissions)
        if missing:
            self.plan_text.setPlainText("Tidying is switched off (needs " + ", ".join(p.value for p in missing) + ").")
            return
        from aurora.automation.files import FileError, find_folder, make_plan

        def work():
            try:
                return make_plan(find_folder(folder), self.scheme_choice.currentData())
            except FileError as error:
                return str(error)

        def show(plan):
            if isinstance(plan, str):
                self.plan_text.setPlainText(plan)
                self.organize_button.setEnabled(False)
                return
            lines = [f"{m.source.name}  →  {m.destination.parent.name}/{m.destination.name}" for m in plan.moves]
            head = f"I'd move {plan.summary()}." if plan.moves else f"Nothing to tidy in {plan.folder.name}."
            note = plan.skipped_note()
            self.plan_text.setPlainText("\n".join([head, *(["", note] if note else []), "", *lines]))
            self.organize_button.setEnabled(bool(plan.moves))

        self.backend.run_io(work, show)

    def organize(self):
        self.organize_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        def done(result):
            self.stop_button.setEnabled(False)
            self.plan_text.setPlainText(result.message)

        self._run(Intent.FILES, {"action": "organize", "folder": self.folder_choice.currentData() or "",
                                 "by": self.scheme_choice.currentData()}, done)

    def stop_tidy(self):
        files = self.backend.tool("Files")
        if files is not None:
            files.organizer.cancel()
            self.status.setText("Stopping after the current file…")

    def undo_tidy(self):
        self._run(Intent.FILES, {"action": "undo"}, lambda result: self.plan_text.setPlainText(result.message))
