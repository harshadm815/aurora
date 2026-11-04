# AURORA Architecture

Version: 0.5.0
Status: Describes what exists in the code today. Planned components are listed separately at the end.

---

## Overview

AURORA is a modular personal AI assistant. A small application-level
kernel (not an operating-system kernel) starts, coordinates and stops
independent components. Each component has one responsibility and can be
tested on its own.

```
main.py  (terminal UI: all printing and input)
   │
   ▼
AuroraEngine  (aurora/kernel/engine.py)
   ├── Conversation ── Personality (system prompt)
   ├── ServiceManager
   │      └── BrainService ── Brain ── OllamaProvider ── Ollama (tinyllama)
   └── PluginManager  (no plugins registered yet)
```

## Request flow (today)

1. `main.py` reads a line from the user.
2. `AuroraEngine.process_message()` appends it to the `Conversation`.
3. The engine sends the full history (system prompt + turns) to the `Brain`.
4. The `Brain` forwards it to its provider (`OllamaProvider`).
5. The reply is appended to the conversation and returned to `main.py`.

Memory, routing and tools are **not** in this flow yet.

## Packages

| Package | Files | Responsibility | Used by the engine? |
|---|---|---|---|
| `aurora.config` | `settings.py` | App name, version, model, absolute paths, log level | Yes |
| `aurora.utils` | `log.py` | `setup_logging()`: file log at `Logs/aurora.log`, warnings to console | Yes (`main.py`) |
| `aurora.kernel` | `engine.py`, `service.py`, `service_manager.py`, `lifecycle.py` | Orchestration; service lifecycle (`STOPPED → STARTING → RUNNING → STOPPING`, or `ERROR`) | Yes |
| `aurora.services` | `brain_service.py` | `BrainService` is the **only** owner of the `Brain` | Yes |
| `aurora.brain` | `brain.py` | Reasoning/language, delegates to a provider | Yes |
| `aurora.providers` | `base_provider.py`, `ollama_provider.py` | Model backends behind one `chat(messages)` interface | Yes |
| `aurora.conversation` | `conversation.py` | Working memory: message history for the current session | Yes |
| `aurora.personality` | `personality.py` | AURORA's system prompt | Yes |
| `aurora.plugins` | `plugin.py`, `plugin_manager.py`, `weather_plugin.py` | Plugin lifecycle. `WeatherPlugin` is a placeholder with no data source | Manager only |
| `aurora.memory` | `store.py`, `manager.py` | Long-term memory in SQLite: add, search, edit, delete, exact-duplicate check | No |
| `aurora.knowledge` | `fact.py`, `extractor.py` | `Fact` data model; rule-based `FactExtractor` (two patterns) | No |
| `aurora.routing` | `intent.py`, `router.py` | Keyword-based `IntentRouter` returning an `Intent` enum | No |
| `aurora.tools` | `tool.py`, `tool_manager.py` | Tool interface and registry | No |
| `aurora.tool_plugins` | `calculator.py` | Calculator using a safe AST evaluator (no `eval`) | No |
| `aurora.events` | `event.py`, `bus.py` | Synchronous publish/subscribe | No |

## Design rules

1. **One owner per component.** The Brain is created once and owned by
   `BrainService`; the engine reaches it through `engine.brain`.
2. **Dependency injection.** `Brain`, `BrainService`, `AuroraEngine`,
   `MemoryManager` and `MemoryStore` accept their dependencies as
   arguments, so tests can pass fakes (see `Tests/conftest.py`).
3. **Failures are isolated.** A service that fails to start is marked
   `ERROR`; a plugin that fails to initialize is disabled. Others continue.
4. **The engine never prints.** Terminal output belongs to `main.py`;
   everything else uses `logging.getLogger(__name__)`.
5. **No hardcoded configuration.** Model names, paths and versions come
   from `aurora/config/settings.py`. Paths are absolute, so running from any
   folder uses the same database and log.
6. **Untrusted input is never executed.** The calculator only accepts
   numbers and arithmetic operators, with limits on length, exponents and
   result size.

## Data

- `aurora_memory.db` (project root): SQLite table `facts`
  (`id, category, entity, attribute, value, priority, confidence,
  created, last_used, times_used`). Not committed to git.
- `Logs/aurora.log`: application log. Not committed to git.

## Testing

```
.venv\Scripts\python.exe -m pytest
```

Tests live in `Tests/`, use a fake provider instead of Ollama and a
temporary database instead of `aurora_memory.db`.

## Known gaps (next milestones)

| Gap | Milestone |
|---|---|
| Router returns a bare enum; `-` or `/` anywhere routes to the calculator | 3: RouteDecision |
| Router, tools and memory are not called from the engine | 4: Tool Execution |
| Fact extraction is two string rules and splits on the first "is"/"like" in the sentence | 5: Intelligent Memory |
| No automatic memory retrieval, evaluation, or conflict resolution | 5: Intelligent Memory |
| Event bus is synchronous with no error isolation; nothing publishes events | 2: Complete Kernel |
| No permission system for plugins and tools | 2: Complete Kernel |
