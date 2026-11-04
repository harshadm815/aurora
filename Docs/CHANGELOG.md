# Changelog

## 0.7.0 — Milestone 3: RouteDecision (2026-11-04)

### Added
- `RouteDecision`: intent, confidence, target, read-only arguments,
  reason and ranked alternatives.
- New intents: `remember`, `recall`, `forget`, `clarify` (replacing the
  vague `memory`, `reasoning`, `unknown`).
- Router extracts arguments: calculator expression (including "plus",
  "times", "x", "^"), weather location and time, memory content/query.
- Clarification when two tool candidates are nearly tied.
- Engine routes every message and publishes `routing.decided`;
  `route <message>` command in the terminal.
- Routing thresholds in `settings.py`.
- 56 routing tests.

### Fixed
- Messages containing `-` or `/` (hyphenated words, URLs, dates, phone
  numbers) no longer route to the calculator.
- "I can't remember…" and statements like "my favourite colour is blue"
  are no longer treated as memory commands.
- Indoor/device temperatures and non-questions ("Purple Rain is my
  favourite song") are not routed to weather.

### Not changed
- The Brain still answers every message; decisions are observed only
  until Milestone 4.


## 0.6.0 — Milestone 2: Complete kernel (2026-11-04)

### Added
- `PermissionManager`: deny-by-default, per-component permissions for
  memory, filesystem, network, camera, microphone, sensors, health data
  and hardware control. Grants come from `settings.PERMISSION_GRANTS`.
- Plugins and tools declare `required_permissions`. Plugins missing a
  grant are blocked; tools missing a grant raise `PermissionDeniedError`.
- `KernelContext`: plugins receive the event bus and permissions (not
  the whole engine) and can `publish()` events and `require()` permissions.
- Kernel publishes lifecycle, service, plugin and conversation events
  (`aurora/events/topics.py`).
- `status` command in the terminal; `engine.status()`.
- `engine.register_plugin()`.
- Provider health check: the Brain service reports `Error` if Ollama is
  unreachable or the model isn't installed.

### Changed
- `EventBus` isolates failing subscribers, validates events, supports
  `"*"` wildcard subscriptions and `unsubscribe()`.
- `Event` gains `source` and is immutable.
- Engine `start()` / `shutdown()` are idempotent; plugins shut down in
  reverse order.
- `ToolManager` rejects duplicate tool names.


## 0.5.0 — Milestone 1: Architecture cleanup (2026-11-04)

### Changed
- `BrainService` is now the single owner of the `Brain`; the engine no
  longer creates a second one.
- `ServiceManager` and `PluginManager` isolate failures: a broken
  service is marked `ERROR`, a broken plugin is disabled, and the rest
  keep running. Duplicate names are rejected.
- Model name, version and file paths come from `settings.py`. Paths are
  absolute, so starting AURORA from another folder no longer creates a
  new empty database.
- Diagnostics use Python's `logging` module and go to `Logs/aurora.log`.
  The engine no longer prints; `main.py` owns terminal output.
- `main.py` shuts down cleanly on Ctrl+C / end of input.
- `Brain`, `AuroraEngine`, `MemoryManager` accept injected dependencies.

### Security
- The calculator no longer uses `eval()`. It parses expressions with
  `ast` and accepts only numbers and arithmetic operators, with limits
  on length, exponent and result size.

### Removed
- `kernel/kernel.py` (old duplicate of `AuroraEngine`).
- `kernel/plugins/test_plugins.py` (broken import) and
  `plugins/test_plugins.py` (manual script).
- `memory/models.py` (unused duplicate of `Fact`), `conversation/message.py`
  (unused), `utils/logger.py` (replaced by `logging`).
- Empty placeholders `memory/evaluator.py` and `pipeline/chat_pipeline.py`.

### Added
- pytest suite in `Tests/` (45 tests) using a fake model provider and a
  temporary database.
- `Requirements.txt`, `pyproject.toml`, `README.md`, `Docs/ARCHITECTURE.md`.
- Git repository.
