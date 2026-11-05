# AURORA Architecture

Version: 0.9.0
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
   ├── EventBus ─────────────┐
   ├── PermissionManager ────┤── KernelContext (what plugins may use)
   ├── Conversation ── Personality (system prompt)
   ├── IntentRouter ── RouteDecision (published as routing.decided)
   ├── ToolExecutor ── ToolManager (permission-checked)
   │      ├── Calculator
   │      ├── Remember / Recall / Forget ── MemoryManager ── SQLite
   │      └── Weather ── Open-Meteo (network)
   ├── ServiceManager ── publishes service.* events
   │      └── BrainService ── Brain ── OllamaProvider ── Ollama (tinyllama)
   │                                    (timeout + reply length cap)
   ├── AutoMemory ── FactExtractor → MemoryEvaluator → MemoryManager
   └── PluginManager ── receives KernelContext, publishes plugin.* events
          (no plugins registered by default)
```

## Kernel lifecycle

```
start():     services start → plugins initialize → kernel.started
shutdown():  kernel.stopping → plugins shut down → services stop → kernel.stopped
```

Both are idempotent. `BrainService.start()` runs a provider health check,
so if Ollama is down or the model is missing the Brain is marked `Error`
and `status` reports it instead of claiming all systems are operational.

## Events

`EventBus` (`aurora/events/bus.py`) is synchronous publish/subscribe.

- Event names are constants in `aurora/events/topics.py`
  (`kernel.started`, `service.failed`, `plugin.blocked`,
  `conversation.message_received`, …).
- Each `Event` has `name`, `data`, `source` and `timestamp`; empty names
  are rejected.
- Subscribing to `"*"` receives every event.
- A subscriber that raises is logged and skipped. Other subscribers
  still run and the publisher is never interrupted.

## Permissions

`PermissionManager` (`aurora/kernel/permissions.py`) is **deny by default**.

- Permissions: `memory.read`, `memory.write`, `filesystem.read`,
  `filesystem.write`, `network`, `camera`, `microphone`, `sensors`,
  `health_data`, `hardware_control`.
- Grants are per component name and come only from
  `settings.PERMISSION_GRANTS`. An unknown permission name there is an
  error at startup. By default only the built-in tools are granted, each
  with just what it needs (see the Tools table).
- `status` lists every tool as `Ready` or `Blocked (needs ...)`.
- Plugins and tools declare `required_permissions`. A plugin missing any
  of them is **blocked** (never initialized, `plugin.blocked` event,
  warning in the terminal). A tool missing any of them raises
  `PermissionDeniedError` when called.
- At runtime a plugin can re-check with `self.require(Permission.X)`.

## Writing a plugin

```python
from aurora.kernel.permissions import Permission
from aurora.plugins.plugin import BasePlugin


class RoomSensorPlugin(BasePlugin):
    required_permissions = (Permission.SENSORS,)

    def __init__(self):
        super().__init__("RoomSensor")

    def initialize(self):
        self.publish("sensor.ready")      # self.context.events is available

    def shutdown(self):
        pass
```

Grant it in `settings.py`:

```python
PERMISSION_GRANTS = {"RoomSensor": ["sensors"]}
```

and register it before `engine.start()`:

```python
engine.register_plugin(RoomSensorPlugin())
```

## Request flow (today)

1. `main.py` reads a line from the user.
2. `AuroraEngine.process_message()` appends it to the `Conversation`.
3. If an action is waiting for confirmation, "yes" runs it, "no" cancels
   it, and anything else cancels it and is handled as a new message.
4. Otherwise the `IntentRouter` produces a `RouteDecision`
   (published as `routing.decided`), and:
   - `clarify`: AURORA asks the user to rephrase.
   - A tool intent with a registered tool: the `ToolExecutor` runs it
     and the tool's own message is the reply. **The language model is not
     involved**, so computed or retrieved facts reach the user unchanged.
   - Anything else is conversation. `AutoMemory` learns facts from the
     message (announced in the reply), picks the memories relevant to it,
     and the Brain receives: personality prompt, memory context, history.
     See [MEMORY_DESIGN.md](MEMORY_DESIGN.md).
5. The reply (from a tool or the Brain) is appended to the conversation,
   so the Brain sees tool answers as context in later turns.

## Tools

| Tool | Intent | Needs | What it does |
|---|---|---|---|
| Calculator | `calculator` | none | Safe arithmetic, e.g. `12 * (7 + 3) = 120` |
| Remember | `remember` | `memory.write` | Stores an explicit note ("remember that ...") as a `Note` fact |
| Recall | `recall` | `memory.read` | Keyword lookup. Only memories containing **every** keyword count as an answer; partial matches are listed separately as "related" |
| Forget | `forget` | `memory.read`, `memory.write` | Lists matching memories and **asks for confirmation** before deleting (including exact duplicates) |
| Weather | `weather` | `network` | Open-Meteo: current conditions and today, tomorrow, this week, this weekend |

A tool returns a `ToolResult` (`aurora/tools/result.py`): `success`,
`message` (the reply), `data`, and optionally `confirm`, a function the
kernel only calls after the user says yes. This is the pattern for every
future consequential action (deleting, controlling hardware).

Failures are always reported, never papered over by the language model:

- Missing permission: explains which permission and where to grant it.
- Tool crash: "The X tool failed unexpectedly (...)" and a `tool.failed` event.
- Weather service down, slow, or place not found: says so, and
  "I won't guess the weather."

`WeatherPlugin` (a placeholder) was removed; weather is now a tool.

### Privacy note

The Weather tool sends the place name (then its coordinates) to
open-meteo.com. Nothing else leaves the machine. Remove `"Weather"` from
`PERMISSION_GRANTS` to switch this off.

## Routing

`IntentRouter.route(message)` (`aurora/routing/router.py`) returns a
`RouteDecision` (`aurora/routing/decision.py`):

| Field | Meaning |
|---|---|
| `intent` | `conversation`, `calculator`, `weather`, `remember`, `recall`, `forget`, `clarify` |
| `confidence` | 0.0–1.0 |
| `target` | Component to handle it: `Brain`, `Calculator`, `Weather`, `Memory`, or `None` for clarify |
| `arguments` | Read-only, e.g. `{"expression": "12 * (7 + 3)"}`, `{"location": "Pune", "when": "tomorrow"}`, `{"content": "..."}`, `{"query": "..."}` |
| `reason` | Why, for logs |
| `alternatives` | The other candidates, best first |

How it decides:

1. Strip polite/wake-word prefixes ("Aurora, please can you …").
2. Every rule (calculator, weather, remember, forget, recall) returns a
   scored candidate or nothing. A rule that crashes is logged and skipped.
3. A default `conversation` candidate is added at
   `settings.ROUTE_CONVERSATION_CONFIDENCE` (0.5). A tool only wins if its
   rule is more confident than that.
4. If the two best tool candidates differ by less than
   `settings.ROUTE_CLARIFY_MARGIN` (0.1), the result is `clarify`.
   Explicit memory commands ("remember that …", "forget …") are exempt:
   whatever follows the command is content, not a competing request.

Rules are conservative on purpose. Examples:

| Message | Intent |
|---|---|
| `what is 5 plus 3?` | calculator `5 + 3` (0.95) |
| `I need 3/4 cup of flour` | conversation (calculator kept as a 0.45 alternative) |
| `this is well-known`, `2004-11-04`, `555-1234`, URLs | conversation |
| `what's the weather in Pune tomorrow?` | weather, Pune, tomorrow (0.9) |
| `what is the temperature in my room?` | conversation (indoor; a sensor question, not weather) |
| `remember that my exam is on Friday` | remember (0.95) |
| `I can't remember the formula` | conversation |
| `my favourite colour is blue` | conversation (automatic extraction is Milestone 5) |
| `what is my favourite colour?` | recall (0.75) |
| `forget it` | conversation ("never mind") |

Type `route <message>` in the terminal to see a decision without sending
the message to the Brain.

## Packages

| Package | Files | Responsibility | Used by the engine? |
|---|---|---|---|
| `aurora.config` | `settings.py` | App name, version, model, absolute paths, log level | Yes |
| `aurora.utils` | `log.py` | `setup_logging()`: file log at `Logs/aurora.log`, warnings to console | Yes (`main.py`) |
| `aurora.kernel` | `engine.py`, `service.py`, `service_manager.py`, `lifecycle.py`, `permissions.py`, `context.py` | Orchestration; service lifecycle (`STOPPED → STARTING → RUNNING → STOPPING`, or `ERROR`); permissions; the context handed to plugins | Yes |
| `aurora.services` | `brain_service.py` | `BrainService` is the **only** owner of the `Brain` | Yes |
| `aurora.brain` | `brain.py` | Reasoning/language, delegates to a provider | Yes |
| `aurora.providers` | `base_provider.py`, `ollama_provider.py` | Model backends behind one `chat(messages)` interface | Yes |
| `aurora.conversation` | `conversation.py` | Working memory: message history for the current session | Yes |
| `aurora.personality` | `personality.py` | AURORA's system prompt | Yes |
| `aurora.plugins` | `plugin.py`, `plugin_manager.py` | Plugin lifecycle with permission checks | Yes (none registered) |
| `aurora.memory` | `store.py`, `manager.py`, `extractor.py`, `evaluator.py`, `auto.py` | Long-term memory: extraction, evaluation, conflict resolution, keyword recall with stemming, context for the Brain | Yes |
| `aurora.knowledge` | `fact.py` | The `Fact` data model | Yes |
| `aurora.routing` | `intent.py`, `decision.py`, `router.py` | Scored rule-based `IntentRouter` returning a structured `RouteDecision` | Yes (observed only) |
| `aurora.tools` | `tool.py`, `tool_manager.py`, `result.py`, `executor.py` | Tool interface, permission-checked registry, `ToolResult`, intent-to-tool execution | Yes |
| `aurora.tool_plugins` | `calculator.py`, `memory_tools.py`, `weather.py`, `registry.py` | The built-in tools and `default_tools()` | Yes |
| `aurora.events` | `event.py`, `bus.py`, `topics.py` | Publish/subscribe with error isolation; standard event names | Yes |

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
   **Logs never contain user content**: no messages, memory values,
   place names, tool arguments or model replies. Only metadata
   (intents, tool and attribute names, ids, counts, error types).
   `Tests/test_log_privacy.py` enforces this.
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
- `Logs/aurora.log`: application log (metadata only, no user content). Not committed to git.

## Testing

```
.venv\Scripts\python.exe -m pytest
```

Tests live in `Tests/`, use a fake provider instead of Ollama and a
temporary database instead of `aurora_memory.db`.

## Known gaps (next milestones)

| Gap | Milestone |
|---|---|
| No internet search or file/document reading tools | 7: Knowledge and Research |
| Recall matches words, not meaning ("hometown" won't find "From: Nagpur") | Semantic search, when it fits in 4 GB RAM |
| Only semantic memory; no episodic, procedural or associative (knowledge graph) memory | Later memory work |
| TinyLlama often ignores or garbles the memory context it is given | A stronger model (hardware-dependent) |
| One tool per message; no multi-step requests ("weather in Pune and Delhi") | 6: Planning Engine |
| Routing is rule-based English only; no LLM fallback for unusual phrasing | Later, once a model good at structured output is available |
| Permission grants are static config; no prompt to ask the user at runtime | Later, with physical-action tools (11) |
| Event bus is synchronous; a slow subscriber delays the publisher | When voice/sensors need it (8, 10) |
