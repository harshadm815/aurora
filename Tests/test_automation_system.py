"""Phase 15: automation and device control - the action catalogue, audit trail, undo,
persistent reminders, tidying files, opening programs and scheduled tasks."""

import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from conftest import FakeProvider, release_widgets

from aurora.automation import actions
from aurora.automation.apps import App, AppError, Launcher, load_apps
from aurora.automation.audit import AuditLog
from aurora.automation.files import FileError, Organizer, allowed_folders, find_folder, make_plan
from aurora.automation.reminders import ReminderScheduler
from aurora.automation.schedule import Scheduler, Task, TaskStore, day_set, describe_days
from aurora.automation.store import AutomationStore
from aurora.brain.brain import Brain
from aurora.config import settings
from aurora.events import topics
from aurora.kernel.engine import AuroraEngine
from aurora.kernel.permissions import Permission, PermissionManager
from aurora.routing.intent import Intent
from aurora.routing.router import IntentRouter
from aurora.tool_plugins.apps import AppsTool
from aurora.tool_plugins.files import FilesTool
from aurora.tool_plugins.registry import default_tools
from aurora.tool_plugins.reminders import RemindersTool
from aurora.tool_plugins.schedule import ScheduleTool
from aurora.tools.result import ToolResult

DEVICES = [
    {"id": "desk_light", "name": "desk light", "kind": "light", "aliases": ["lamp"], "driver": "simulated"},
    {"id": "fan", "name": "fan", "kind": "fan", "driver": "simulated", "schedulable": True},
]


class FakeProcess:
    started: list = []

    def __init__(self, args, **options):
        self.args, self.options, self.alive = args, options, True
        FakeProcess.started.append(self)

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


class HandingOver(FakeProcess):
    """Like Windows' calc.exe: starts another process and exits at once."""

    def __init__(self, args, **options):
        super().__init__(args, **options)
        self.alive = False


def fake_which(name):
    return {"notepad.exe": r"C:\Windows\System32\notepad.exe", "calc.exe": r"C:\Windows\System32\calc.exe",
            "explorer.exe": r"C:\Windows\explorer.exe"}.get(name)


def launcher(popen=FakeProcess, apps=None):
    return Launcher(apps, popen=popen, which=fake_which, wait=0)


@pytest.fixture
def documents():
    folder = settings.DOCUMENTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for name in ["holiday.jpg", "thesis.pdf", "notes.txt", "budget.xlsx", "setup.exe", "odd.xyz", ".hidden.txt"]:
        (folder / name).write_text(f"contents of {name}")
    return folder


@pytest.fixture
def engine(monkeypatch, memory):
    monkeypatch.setattr(settings, "DEVICES", DEVICES)
    monkeypatch.setattr(settings, "SCHEDULE_CHECK_SECONDS", 3600)    # the test drives the scheduler itself
    made = []

    def make(permissions=None, popen=FakeProcess):
        tools = default_tools(memory)
        next(t for t in tools if t.name == "Apps").launcher = launcher(popen)
        engine = AuroraEngine(Brain(FakeProvider()), memory=memory, tools=tools, permissions=permissions)
        engine.start()
        made.append(engine)
        return engine

    yield make
    for engine in made:
        engine.shutdown()


def talk(engine, *messages):
    return [engine.process_message(message) for message in messages]


def files_in(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


# --- The catalogue: every action defined ----------------------------------------------------

def test_every_action_has_a_tool_and_valid_permissions(memory):
    tools = {tool.name: tool for tool in default_tools(memory)}
    valid = {p.value for p in Permission}
    for spec in actions.ACTIONS:
        assert spec.tool in tools, spec.name
        needed = {p.value for p in tools[spec.tool].required_permissions}
        listed = {p for p in spec.permissions if p in valid}
        assert needed <= listed or spec.name == "schedule.run" and needed <= set(spec.permissions), spec.name
        assert spec.asks_first and spec.undo and spec.does


def test_action_tools_are_granted_by_default():
    for spec in actions.ACTIONS:
        if spec.permissions:
            assert spec.tool in settings.PERMISSION_GRANTS, spec.tool


@pytest.mark.parametrize("tool, arguments, name", [
    ("Devices", {"action": "on"}, "devices.change"), ("Devices", {"action": "set"}, "devices.change"),
    ("Devices", {"action": "status"}, "devices.status"), ("Files", {"action": "organize"}, "files.organize"),
    ("Schedule", {"action": "pause"}, "schedule.change"), ("Schedule", {"action": "add"}, "schedule.add"),
    ("Forget", {}, "memory.forget"), ("Apps", {"action": "open"}, "apps.open"),
])
def test_action_names(tool, arguments, name):
    assert actions.action_name(tool, arguments) == name


def test_actions_that_always_ask_do_ask(engine, documents):
    aurora = engine()
    replies = {
        "files.organize": aurora.process_message("organise my documents by type"),
        "schedule.add": talk(aurora, "no", "every day at 7 remind me to drink water")[1],
    }
    talk(aurora, "no", "open notepad")
    replies["apps.close"] = aurora.process_message("close notepad")
    for name, reply in replies.items():
        assert reply.endswith("(yes / no)"), name
        assert actions.BY_NAME[name].asks_first == actions.ALWAYS


# --- Audit trail -----------------------------------------------------------------------------

def test_audit_records_and_filters():
    audit = AuditLog()
    audit.record("devices.change", "done", "  The   fan is on.\n", "schedule #2", simulated=True)
    audit.record("files.organize", "declined", "x" * 500)
    entries = audit.recent()
    assert [e.action for e in entries] == ["files.organize", "devices.change"]
    assert entries[1].summary == "The fan is on." and entries[1].initiated == "schedule #2" and entries[1].simulated
    assert len(entries[0].summary) == 300
    assert [e.action for e in audit.recent(action="devices")] == ["devices.change"]
    assert "[schedule #2]" in entries[1].line() and "(simulated)" in entries[1].line()
    with pytest.raises(ValueError):
        audit.record("x", "approved?", "y")


def test_audit_retention(monkeypatch):
    audit = AuditLog()
    with audit.store.connect() as connection:
        connection.execute("INSERT INTO audit (time, action, outcome, summary, initiated) VALUES (?, 'old', 'done', '', 'you')",
                           ((datetime.now() - timedelta(days=91)).isoformat(),))
    audit.record("new", "done", "")
    assert [e.action for e in audit.recent()] == ["new"]


def test_audit_never_logs_summaries(caplog):
    with caplog.at_level(logging.INFO, logger="aurora.automation.audit"):
        AuditLog().record("reminders.add", "done", "remind me about my secret appointment")
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "reminders.add done" in logged and "secret" not in logged


def test_conversation_actions_are_audited(engine):
    aurora = engine()
    talk(aurora, "turn on the desk light", "yes", "turn on the fan", "no", "turn off the desk light",
         "what's the weather", "is the fan on?", "list my devices")
    outcomes = [(e.action, e.outcome) for e in reversed(aurora.executor.audit.recent())]
    assert outcomes == [("devices.change", "asked"), ("devices.change", "done"),
                        ("devices.change", "asked"), ("devices.change", "declined"),
                        ("devices.change", "asked"), ("devices.change", "cancelled")]   # status and lists aren't actions
    assert aurora.executor.audit.recent()[-2].simulated


# --- Undo -------------------------------------------------------------------------------------

def test_undo_a_device_change(engine):
    aurora = engine()
    assert aurora.process_message("undo that") == aurora.NOTHING_TO_UNDO
    talk(aurora, "set the desk light to 40", "yes", "set the desk light to 80", "yes")
    assert aurora.process_message("undo that") == "Shall I undo that: put the desk light back (on (brightness 40%))? (yes / no)"
    assert aurora.process_message("yes").startswith("The desk light is on (brightness 40%)")
    assert aurora.process_message("undo that").startswith("Shall I undo that: put the desk light back (off)")
    assert aurora.process_message("yes").startswith("The desk light is off")
    assert aurora.process_message("undo") == aurora.NOTHING_TO_UNDO
    undone = [e for e in aurora.executor.audit.recent() if e.outcome == "undone"]
    assert len(undone) == 2 and undone[0].action == "devices.change"


def test_declined_undo_can_be_asked_again(engine):
    aurora = engine()
    talk(aurora, "turn on the fan", "yes", "undo that", "no")
    assert aurora.process_message("undo that").startswith("Shall I undo that: put the fan back (off)")


def test_undo_needs_the_permission_at_undo_time(engine):
    aurora = engine()
    talk(aurora, "turn on the fan", "yes", "undo that")
    aurora.permissions.revoke("Devices", "hardware_control")
    assert aurora.process_message("yes") == "I can't undo that: the Devices tool needs the 'hardware_control' permission."


def test_no_undo_when_nothing_changed(engine):
    aurora = engine()
    talk(aurora, "turn off the fan", "yes")          # it was already off
    assert aurora.process_message("undo that") == aurora.NOTHING_TO_UNDO


# --- Reminders kept on disk --------------------------------------------------------------------

class ManualTimer:
    def __init__(self, delay, function, args=()):
        self.delay, self.function, self.args, self.cancelled, self.daemon = delay, function, args, False, False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.function(*self.args)


def test_reminders_survive_a_restart():
    now = datetime(2026, 11, 8, 12, 0)
    first = ReminderScheduler(timer_factory=ManualTimer, clock=lambda: now)
    first.add("stretch", now + timedelta(minutes=10))
    first.add("call home", now + timedelta(hours=5))
    first.stop()                                          # AURORA closes
    later = now + timedelta(hours=1)
    shown = []
    second = ReminderScheduler(notify=shown.append, timer_factory=ManualTimer, clock=lambda: later)
    missed = second.restore()
    assert [r.text for r in missed] == ["stretch"]
    assert [r.text for r in second.pending()] == ["call home"]
    assert second.restore() == []                         # reported once
    second.pending()[0].timer.fire()
    assert shown == ["⏰ Reminder: call home"]
    third = ReminderScheduler(timer_factory=ManualTimer, clock=lambda: later)
    assert third.restore() == [] and third.pending() == []   # shown and missed ones don't come back


def test_reminder_listeners_and_cancelling_one():
    now = datetime(2026, 11, 8, 12, 0)
    scheduler = ReminderScheduler(timer_factory=ManualTimer, clock=lambda: now)
    heard = []
    scheduler.listeners.append(lambda reminder: heard.append(reminder.text))
    tool = RemindersTool(scheduler)
    tool.execute(text="stretch", seconds=600)
    tool.execute(text="stretch your legs", seconds=900)
    tool.execute(text="call home", seconds=1200)
    assert tool.execute(action="cancel", which="stretch").message.startswith("Several reminders match")
    assert tool.execute(action="cancel", which="call").message == "Cancelled the reminder for 12:20: call home"
    assert tool.execute(action="cancel", which="9").message.startswith("I can't find a reminder matching \"9\"")
    assert tool.execute(action="cancel", which="1").success
    scheduler.pending()[0].timer.fire()
    assert heard == ["stretch your legs"]


def test_reminder_can_be_undone():
    now = datetime(2026, 11, 8, 12, 0)
    tool = RemindersTool(ReminderScheduler(timer_factory=ManualTimer, clock=lambda: now))
    result = tool.execute(text="stretch", seconds=600)
    assert result.undo_label == "cancel the reminder for 12:10"
    assert result.undo().message == "Reminder cancelled." and not result.undo().success


def test_engine_reports_missed_reminders_at_start(engine):
    store = AutomationStore()
    with store.connect() as connection:
        connection.execute("INSERT INTO reminders (text, due, created, status) VALUES ('take medicine', ?, ?, 'pending')",
                           ((datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds"), datetime.now().isoformat()))
    seen = []
    from aurora.events.bus import EventBus

    original = EventBus.__init__

    def capturing(self):
        original(self)
        self.subscribe(topics.REMINDERS_MISSED, seen.append)

    EventBus.__init__ = capturing
    try:
        engine()
    finally:
        EventBus.__init__ = original
    assert seen and seen[0].data["reminders"][0]["text"] == "take medicine"


# --- Tidying files --------------------------------------------------------------------------------

def test_plan_by_type(documents):
    plan = make_plan(find_folder(""), "type")
    assert {(m.source.name, m.destination.parent.name) for m in plan.moves} == {
        ("holiday.jpg", "Images"), ("thesis.pdf", "PDFs"), ("notes.txt", "Notes"), ("budget.xlsx", "Spreadsheets")}
    assert plan.skipped_note() == ("Left where they are: 1 hidden file, 1 file of another type, "
                                   "1 program, shortcut or system file.")
    assert plan.summary() == "4 files in Documents into 4 folders: Images (1), Notes (1), PDFs (1), Spreadsheets (1)"


def test_plan_by_month_and_name_clashes(documents):
    stamp = datetime(2026, 3, 14, 10, 0).timestamp()
    os.utime(documents / "thesis.pdf", (stamp, stamp))
    (documents / "PDFs").mkdir()
    (documents / "PDFs" / "thesis.pdf").write_text("an older thesis")
    by_type = {m.source.name: m.destination.name for m in make_plan(documents, "type").moves}
    assert by_type["thesis.pdf"] == "thesis (2).pdf"
    by_month = {m.source.name: m.destination.parent.name for m in make_plan(documents, "month").moves}
    assert by_month["thesis.pdf"] == "2026-03"


def test_only_allowed_folders(documents, tmp_path, monkeypatch):
    with pytest.raises(FileError, match="only allowed to tidy: Documents"):
        find_folder("downloads")
    elsewhere = tmp_path / "Elsewhere"
    elsewhere.mkdir()
    with pytest.raises(FileError, match="not allowed to tidy Elsewhere"):
        make_plan(elsewhere)
    monkeypatch.setattr(settings, "ORGANIZE_FOLDERS", [documents, elsewhere])
    assert find_folder("elsewhere") == elsewhere.resolve() and len(allowed_folders()) == 2
    monkeypatch.setattr(settings, "ORGANIZE_MAX_FILES", 2)
    with pytest.raises(FileError, match="only tidy up to 2"):
        make_plan(documents)


def test_organize_and_undo_never_lose_anything(documents):
    before = {p.name: p.read_text() for p in documents.iterdir() if p.is_file()}
    organizer = Organizer()
    plan = make_plan(documents)
    outcome = organizer.execute(plan)
    assert outcome.moved == 4 and not outcome.error
    assert files_in(documents) == [".hidden.txt", "Images/holiday.jpg", "Notes/notes.txt", "PDFs/thesis.pdf",
                                   "Spreadsheets/budget.xlsx", "odd.xyz", "setup.exe"]
    # Undo works from a fresh Organizer (as after a restart): the moves are on disk.
    batch, count, folder = Organizer().last_batch()
    assert (count, folder) == (4, "Documents")
    back, problems = Organizer().undo(batch)
    assert (back, problems) == (4, [])
    assert {p.name: p.read_text() for p in documents.iterdir() if p.is_file()} == before
    assert not (documents / "Images").exists()            # folders it created are removed when empty
    assert Organizer().last_batch() is None


def test_tidy_stops_at_the_first_problem_and_can_be_undone(documents):
    plan = make_plan(documents)
    (documents / "notes.txt").write_text("changed after the preview")
    outcome = Organizer().execute(plan)
    assert outcome.moved == 2 and outcome.error == "notes.txt couldn't be moved (it changed after the plan was made)"
    assert (documents / "notes.txt").read_text() == "changed after the preview"
    tool = FilesTool()
    reply = tool._report(plan, outcome)
    assert reply.message.startswith("Moved 2 of 4 files in Documents, then stopped: notes.txt")
    assert "Nothing was lost" in reply.message and reply.undo is not None
    assert reply.undo().message == "Moved 2 files back in Documents."


def test_tidy_refuses_to_overwrite_and_can_be_stopped(documents):
    plan = make_plan(documents)
    (documents / "Images").mkdir()
    (documents / "Images" / "holiday.jpg").write_text("appeared after the preview")
    outcome = Organizer().execute(plan)
    assert "Images/holiday.jpg already exists" in outcome.error
    assert (documents / "Images" / "holiday.jpg").read_text() == "appeared after the preview"

    organizer = Organizer()
    original = organizer._move

    def move_then_stop(*args):
        original(*args)
        organizer.cancel()

    organizer._move = move_then_stop
    later = organizer.execute(make_plan(documents))
    assert later.cancelled and later.moved == 1


def test_undo_reports_what_it_cannot_put_back(documents):
    organizer = Organizer()
    outcome = organizer.execute(make_plan(documents))
    (documents / "thesis.pdf").write_text("a new file with the old name")
    (documents / "Notes" / "notes.txt").unlink()
    back, problems = organizer.undo(outcome.batch)
    assert back == 2
    assert sorted(problems) == ["notes.txt is no longer in Notes", "thesis.pdf already exists again in Documents"]
    assert (documents / "thesis.pdf").read_text() == "a new file with the old name"


def test_tidy_through_conversation(engine, documents):
    aurora = engine()
    preview = aurora.process_message("organise my documents by type")
    assert preview.startswith("I'd move 4 files in Documents into 4 folders")
    assert files_in(documents)[0] == ".hidden.txt" and "holiday.jpg" in files_in(documents)   # nothing moved yet
    assert aurora.process_message("yes") == 'Moved 4 files in Documents into 4 folders. Say "undo that" to put them back.'
    restarted = engine()                                    # undo survives a restart
    assert restarted.process_message("undo my last tidy-up").startswith("Shall I move the 4 files")
    assert restarted.process_message("yes") == "Moved 4 files back in Documents."
    assert "holiday.jpg" in files_in(documents)
    assert restarted.process_message("organise my downloads").startswith("I'm only allowed to tidy: Documents")


def test_tidy_needs_write_permission(engine, documents):
    aurora = engine(PermissionManager({"Files": ["filesystem.read"]}))
    assert "filesystem.write" in aurora.process_message("organise my documents")


# --- Programs ----------------------------------------------------------------------------------

def test_app_list_validation():
    with pytest.raises(ValueError, match="needs a command"):
        load_apps([{"name": "Bad", "command": "notepad.exe"}])
    apps = load_apps()
    assert {a.name for a in apps} >= {"Notepad", "Calculator", "File Explorer"}


def test_finding_and_checking_programs(tmp_path):
    runner = launcher()
    assert runner.find("the text editor").name == "Notepad"
    with pytest.raises(AppError, match="I can only open programs on my list"):
        runner.find("powershell")
    assert runner.command(runner.find("explorer")) == [r"C:\Windows\explorer.exe", str(settings.DOCUMENTS_DIR)]
    missing = App("Paint", (), ("mspaint.exe",))
    with pytest.raises(AppError, match=r"can't find Paint \(mspaint.exe\) on this PC"):
        runner.command(missing)
    with pytest.raises(AppError, match="can't find Tool at"):
        runner.command(App("Tool", (), (str(tmp_path / "nothing.exe"),)))


def test_opening_uses_the_fixed_command_without_a_shell():
    FakeProcess.started.clear()
    tool = AppsTool(launcher())
    result = tool.execute(action="open", app="notepad")
    assert result.message == "Opened Notepad." and result.undo is not None
    process = FakeProcess.started[-1]
    assert process.args == [r"C:\Windows\System32\notepad.exe"] and process.options["shell"] is False


def test_programs_that_hand_over_are_not_claimed():
    tool = AppsTool(launcher(HandingOver))
    result = tool.execute(action="open", app="calculator")
    assert "close it yourself" in result.message and result.undo is None
    assert tool.execute(action="close", app="calculator").message.startswith("I only close programs I opened")


def test_closing_asks_and_only_closes_its_own():
    tool = AppsTool(launcher())
    assert not tool.execute(action="close", app="notepad").success
    tool.execute(action="open", app="notepad")
    ask = tool.execute(action="close", app="notepad")
    assert ask.message == "Shall I close Notepad? Unsaved work in it will be lost. (yes / no)"
    assert ask.confirm().message == "Closed Notepad."
    assert not FakeProcess.started[-1].alive


def test_confirm_setting_for_programs():
    runner = launcher(apps=[App("Notepad", (), ("notepad.exe",), confirm=True)])
    result = AppsTool(runner).execute(action="open", app="notepad")
    assert result.message == "Shall I open Notepad? (yes / no)" and result.needs_confirmation


@pytest.mark.parametrize("message", ["open notepad & del *", "open cmd", "run powershell", "start notepad.exe /c x"])
def test_unknown_or_decorated_programs_are_not_routed(message):
    assert IntentRouter().route(message).intent is not Intent.APPS


def test_apps_need_their_permission(engine):
    aurora = engine(PermissionManager({}))
    assert "apps.launch" in aurora.process_message("open notepad")


# --- Scheduled tasks --------------------------------------------------------------------------------

def test_days():
    assert day_set("weekdays") == {0, 1, 2, 3, 4} and day_set("mon,thu") == {0, 3}
    assert describe_days("weekends") == "every weekend" and describe_days("mon,thu") == "every Monday and Thursday"
    with pytest.raises(ValueError):
        day_set("someday")


def test_occurrences():
    task = Task(1, "x", "reminder", {}, "weekdays", "18:30", datetime(2026, 11, 1))
    friday_night = datetime(2026, 11, 6, 20, 0)            # a Friday
    assert task.latest_occurrence(friday_night) == datetime(2026, 11, 6, 18, 30)
    assert task.next_run(friday_night) == datetime(2026, 11, 9, 18, 30)           # Monday
    assert task.latest_occurrence(datetime(2026, 11, 8, 12, 0)) == datetime(2026, 11, 6, 18, 30)   # Sunday


def test_task_store_validation():
    tasks = TaskStore()
    with pytest.raises(ValueError):
        tasks.add("x", "shell", {}, "daily", "07:00")
    with pytest.raises(ValueError):
        tasks.add("x", "reminder", {}, "daily", "25:00")
    task = tasks.add("x", "reminder", {"text": "hi"}, "daily", "7:05")
    assert task.at == "07:05" and tasks.set_active(task.id, False) and not tasks.get(task.id).active
    assert tasks.delete(task.id) and not tasks.delete(task.id)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def scheduler_with(task_kwargs, runner, now):
    tasks = TaskStore()
    task = tasks.add("say hi", "reminder", {"text": "hi"}, now=now - timedelta(days=1), **task_kwargs)
    clock = Clock(now)
    runs = []
    return Scheduler(tasks, runner, on_run=runs.append, clock=clock), task, clock, runs


def test_tasks_run_once_within_the_grace_period():
    calls = []
    scheduler, task, clock, runs = scheduler_with(
        {"days": "daily", "at": "07:00"}, lambda t: calls.append(t.id) or ToolResult(True, "ok"),
        datetime(2026, 11, 9, 7, 2))
    scheduler.check()
    scheduler.check()
    assert calls == [task.id] and runs[0].success is True
    clock.now = datetime(2026, 11, 10, 7, 0, 30)
    scheduler.check()
    assert calls == [task.id, task.id]


def test_missed_runs_are_skipped_not_run_late():
    calls = []
    scheduler, task, clock, runs = scheduler_with(
        {"days": "daily", "at": "07:00"}, lambda t: calls.append(t) or ToolResult(True, "ok"),
        datetime(2026, 11, 9, 11, 0))
    scheduler.check()
    assert calls == [] and runs[0].success is None
    assert runs[0].message == "Skipped the 07:00 run: AURORA wasn't running then."
    scheduler.check()
    assert len(runs) == 1


def test_new_tasks_wait_for_their_next_time():
    tasks = TaskStore()
    now = datetime(2026, 11, 9, 9, 0)
    tasks.add("say hi", "reminder", {"text": "hi"}, "daily", "07:00", now=now)
    calls = []
    Scheduler(tasks, lambda t: calls.append(t), clock=lambda: now).check()
    assert calls == []


def test_failing_tasks_are_paused(monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULE_GRACE_MINUTES", 60 * 24 * 7)
    def boom(task):
        raise RuntimeError("device gone")

    scheduler, task, clock, runs = scheduler_with({"days": "daily", "at": "07:00"}, boom, datetime(2026, 11, 9, 7, 1))
    for day in range(3):
        clock.now = datetime(2026, 11, 9 + day, 7, 1)
        scheduler.check()
    assert [r.success for r in runs] == [False, False, False] and runs[-1].paused
    assert "Paused after 3 failures in a row" in runs[-1].message
    assert "RuntimeError" in runs[0].message
    assert not scheduler.tasks.get(task.id).active
    clock.now = datetime(2026, 11, 12, 7, 1)
    scheduler.check()
    assert len(runs) == 3                                   # paused: not run


def test_scheduler_thread_starts_and_stops(monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULE_CHECK_SECONDS", 0.01)
    checked = threading.Event()
    scheduler = Scheduler(TaskStore(), lambda t: None)
    scheduler.check = lambda: checked.set() or []
    scheduler.start()
    assert checked.wait(5)
    scheduler.stop()
    assert not scheduler.running


def test_schedule_tool_checks_before_asking(engine):
    aurora = engine()
    reply = lambda message: aurora.process_message(message)
    assert reply("every weekday at 18:30 turn on the desk light").startswith("The desk light isn't marked as schedulable")
    assert reply("every day turn off the fan").startswith("At what time?")
    assert reply("every day at 7 run my morning briefing").startswith("I don't know a routine called")
    assert reply("every day at 7 make coffee").startswith("I can schedule reminders")
    assert reply("every weekday at 7:30 set the fan to 9").startswith("The fan speed must be between 1 and 3")
    assert reply("what tasks are scheduled").startswith("You have no scheduled tasks.")
    assert TaskStore().all() == []


def test_schedule_add_list_pause_delete(engine):
    aurora = engine()
    ask = aurora.process_message("every weekday at 18:30 turn on the fan")
    assert ask == ("Shall I add a scheduled task: every weekday at 18:30, turn the fan on (simulated device)? It will "
                   "run at that time without asking each time; you can pause or delete it whenever you like. (yes / no)")
    assert aurora.process_message("yes") == "Scheduled task 1 added: every weekday at 18:30, turn the fan on (simulated device)."
    talk(aurora, "every evening at 9 remind me to take my medicine", "yes")
    listing = aurora.process_message("what tasks are scheduled")
    assert "1. every weekday at 18:30: turn the fan on (simulated device); next " in listing
    assert "2. every day at 21:00: remind you: take my medicine; next " in listing
    assert aurora.process_message("pause task 2").startswith("Paused scheduled task 2")
    assert "(paused)" in aurora.process_message("what tasks are scheduled")
    assert aurora.process_message("resume task 2").startswith("Resumed scheduled task 2")
    assert aurora.process_message("delete task 1") == "Deleted scheduled task 1."
    assert aurora.process_message("delete task 7").startswith("There's no scheduled task 7")
    assert aurora.process_message("delete all scheduled tasks") == "Shall I delete all 1 scheduled tasks? (yes / no)"
    assert aurora.process_message("yes") == "Deleted 1 scheduled task."
    assert [e.action for e in aurora.executor.audit.recent() if e.outcome == "done"][:3] == [
        "schedule.change", "schedule.change", "schedule.change"]


def test_routine_tasks(engine):
    aurora = engine()
    talk(aurora, "when I say morning briefing, check my sensors")
    assert aurora.process_message("every day at 7 run my morning briefing").startswith(
        "Shall I add a scheduled task: every day at 07:00, run your routine “morning briefing”")
    aurora.process_message("yes")
    task = TaskStore().all()[0]
    result = aurora.run_task(task)
    assert result.success and result.message.startswith("Running your routine “morning briefing”")


def test_run_task_checks_permissions_every_time(engine):
    aurora = engine()
    talk(aurora, "every day at 7 turn on the fan", "yes")
    task = TaskStore().all()[0]
    assert aurora.run_task(task).message.startswith("The fan is on")
    aurora.permissions.revoke("Devices", "hardware_control")
    assert aurora.run_task(task).message == "Not run: it needs the 'hardware_control' permission."
    aurora.permissions.revoke("Schedule", "automation")
    assert aurora.run_task(task).message.startswith("Not run: scheduled tasks are switched off")
    runs = [e for e in aurora.executor.audit.recent() if e.action == "schedule.run"]
    assert [e.outcome for e in runs] == ["failed", "failed", "done"] and runs[0].initiated == "schedule #1"


def test_device_no_longer_schedulable_is_refused_at_run_time(engine):
    aurora = engine()
    talk(aurora, "every day at 7 turn on the fan", "yes")
    devices = aurora.tool_manager.get("Devices")
    devices.controller.find("fan").schedulable = False         # you changed your mind in settings
    result = aurora.run_task(TaskStore().all()[0])
    assert not result.success and "isn't marked as schedulable" in result.message
    assert devices.controller.state(devices.controller.find("fan")).on is False


def test_a_task_that_leaves_a_question_cannot_replace_yours(engine):
    aurora = engine()
    talk(aurora, "every day at 9 remind me to stretch", "yes")
    assert aurora.process_message("turn on the desk light").endswith("(yes / no)")

    def sneaky(task):
        aurora.pending = ("Forget", ToolResult(True, "Delete everything?", confirm=lambda: ToolResult(True, "gone")))
        return ToolResult(True, "done")

    aurora._task_result = sneaky
    aurora.run_task(TaskStore().all()[0])
    assert aurora.process_message("yes").startswith("Done. The desk light is on")


def test_scheduled_device_change_can_be_undone(engine):
    aurora = engine()
    talk(aurora, "every day at 7 turn on the fan", "yes")
    aurora.run_task(TaskStore().all()[0])
    assert aurora.process_message("undo that").startswith("Shall I undo that: put the fan back (off)")
    assert aurora.process_message("yes").startswith("The fan is off")


def test_run_task_never_steals_a_pending_question(engine):
    aurora = engine()
    talk(aurora, "every day at 9 remind me to stretch", "yes")
    assert aurora.process_message("turn on the desk light").endswith("(yes / no)")
    assert aurora.run_task(TaskStore().all()[0]).message == "⏰ Reminder: stretch"
    assert aurora.process_message("yes").startswith("Done. The desk light is on")


def test_engine_reports_task_runs(engine):
    aurora = engine()
    talk(aurora, "every day at 9 remind me to stretch", "yes")
    seen = []
    aurora.events.subscribe(topics.TASK_RAN, seen.append)
    task = TaskStore().all()[0]
    with TaskStore().store.connect() as connection:
        connection.execute("UPDATE tasks SET created = ?", ((datetime.now() - timedelta(days=2)).isoformat(),))
    scheduler = aurora.service_manager.services["Automation"].scheduler
    moment = task.latest_occurrence(datetime.now())
    scheduler.clock = lambda: moment + timedelta(minutes=1)
    scheduler.check()
    assert seen[0].data["message"] == "⏰ Reminder: stretch" and seen[0].data["success"] is True


# --- Routing ------------------------------------------------------------------------------------

@pytest.mark.parametrize("message, intent, arguments", [
    ("every weekday at 18:30 turn on the desk light", Intent.SCHEDULE,
     {"action": "add", "days": "weekdays", "at": "18:30", "kind": "device", "device": "the desk light",
      "device_action": "on", "value": None}),
    ("every evening at 9 remind me to take my medicine", Intent.SCHEDULE,
     {"action": "add", "days": "daily", "at": "21:00", "kind": "reminder", "text": "take my medicine"}),
    ("remind me to stretch every day at 3 pm", Intent.SCHEDULE,
     {"action": "add", "days": "daily", "at": "15:00", "kind": "reminder", "text": "stretch"}),
    ("on mondays at 8 am check my sensors", Intent.SCHEDULE,
     {"action": "add", "days": "mon", "at": "08:00", "kind": "sensors", "quantity": "all"}),
    ("pause task 2", Intent.SCHEDULE, {"action": "pause", "number": 2}),
    ("open notepad", Intent.APPS, {"action": "open", "app": "Notepad"}),
    ("close the calculator", Intent.APPS, {"action": "close", "app": "Calculator"}),
    ("tidy up my documents by month", Intent.FILES, {"action": "organize", "folder": "my documents", "by": "month"}),
    ("undo my last tidy-up", Intent.FILES, {"action": "undo"}),
    ("undo that", Intent.UNDO, {}),
    ("cancel reminder 2", Intent.REMINDER, {"action": "cancel", "which": "2"}),
])
def test_routing(message, intent, arguments):
    decision = IntentRouter().route(message)
    assert decision.intent is intent and dict(decision.arguments) == arguments


@pytest.mark.parametrize("message, intent", [
    ("turn on the desk light", Intent.DEVICES), ("what is the temperature in my room every day", Intent.SENSORS),
    ("undo that memory", Intent.MEMORY_CONTROL), ("open notes.pdf", Intent.DOCUMENTS),
    ("start a timer for 5 minutes", Intent.REMINDER),
])
def test_routing_leaves_other_requests_alone(message, intent):
    assert IntentRouter().route(message).intent is intent


def test_a_schedule_never_switches_a_device_now(engine):
    aurora = engine()
    aurora.process_message("every day at 7 turn on the fan")
    devices = aurora.tool_manager.get("Devices")
    fan = devices.controller.find("fan")
    assert devices.controller.state(fan).on is False
    aurora.process_message("no")
    assert TaskStore().all() == []


def test_routines_cannot_contain_automation(engine):
    aurora = engine()
    for steps in ("organise my documents", "every day at 7 turn on the fan", "undo that"):
        assert "can't include" in aurora.process_message(f"when I say tidy time, {steps}")


# --- Command Centre -----------------------------------------------------------------------------

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from aurora.config import preferences  # noqa: E402
from aurora.events.event import Event  # noqa: E402
from aurora.interface.backend import Backend, describe_event, wait_for  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def backend(qapp, memory, tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    monkeypatch.setattr(settings, "DEVICES", DEVICES)
    monkeypatch.setattr(settings, "SCHEDULE_CHECK_SECONDS", 3600)

    def make():
        tools = default_tools(memory)
        next(t for t in tools if t.name == "Apps").launcher = launcher()
        return AuroraEngine(Brain(FakeProvider()), memory=memory, tools=tools)

    b = Backend(engine_factory=make)
    b.start()
    assert wait_for(lambda: b.engine is not None, 10)
    yield b
    b.shutdown()
    release_widgets()


def test_activity_wording():
    assert describe_event(Event(topics.TASK_RAN, {"id": 3, "success": None})).text == "Scheduled task 3 skipped"
    assert describe_event(Event(topics.REMINDERS_MISSED, {"reminders": [{}, {}]})).text == \
        "2 reminders fell due while AURORA was closed"
    assert describe_event(Event(topics.REMINDER_DUE, {"text": "secret"})).text == "Reminder shown"


def page(backend):
    from aurora.interface.automation_view import AutomationView

    view = AutomationView(backend)
    view.refresh()
    return view


def test_automation_page_lists_and_controls_tasks(backend, monkeypatch):
    engine = backend.engine
    talk(engine, "every day at 7 turn on the fan", "yes", "remind me in 2 hours to call home")
    view = page(backend)
    assert wait_for(lambda: view.tasks.rowCount() == 1 and view.reminders.rowCount() == 1, 10)
    assert view.tasks.item(0, 2).text() == "turn the fan on (simulated device)"
    assert view.rules.rowCount() == len(actions.ACTIONS) and view.rules.item(0, 1).text() == "yes"
    assert "cancel the reminder" in view.undo_text.text()
    view.tasks.selectRow(0)
    view.toggle_task()
    assert wait_for(lambda: "(paused)" in (view.tasks.item(0, 1).text() if view.tasks.item(0, 1) else ""), 10)
    assert wait_for(lambda: view.audit.rowCount() > 0 and view.audit.item(0, 2).text() == "schedule.change", 10)
    assert view.audit.item(0, 3).text() == "you"


def test_automation_page_tidies_with_confirmation(backend, documents, monkeypatch):
    view = page(backend)
    assert wait_for(lambda: view.folder_choice.count() == 1, 10)
    view.preview()
    assert wait_for(lambda: view.plan_text.toPlainText().startswith("I'd move 4 files"), 10)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    view.organize()
    assert wait_for(lambda: view.status.text() == "Okay, nothing was changed.", 10)
    assert "holiday.jpg" in files_in(documents)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    view.organize()
    assert wait_for(lambda: view.plan_text.toPlainText().startswith("Moved 4 files"), 10)
    view.undo()
    assert wait_for(lambda: view.status.text() == "Moved 4 files back in Documents.", 10)
    outcomes = [e.outcome for e in backend.engine.executor.audit.recent(action="files")]
    assert outcomes == ["undone", "done", "asked", "declined", "asked"]


def test_tray_shows_reminders_and_task_runs(backend):
    from PySide6.QtGui import QIcon

    from aurora.interface.windows.tray import Tray

    tray = Tray(backend, QIcon(), None)
    backend.reminder_due.emit({"text": "stretch"})
    backend.reminders_missed.emit([{"text": "call home", "due": "2026-11-08T17:00"}])
    backend.task_ran.emit({"id": 2, "success": False, "message": "The fan didn't answer."})
    assert tray.notified == ["⏰ stretch", "While AURORA was closed: 17:00 call home", "The fan didn't answer."]
