# AURORA

A modular personal AI assistant that runs a local language model through
[Ollama](https://ollama.com).

Current stage: early prototype. Text conversation with a local model
works; long-term memory, routing and tools exist as separate components
but are not yet connected to the conversation. See
[Docs/ARCHITECTURE.md](Docs/ARCHITECTURE.md).

## Setup (Windows)

1. Install Ollama and pull the model:

   ```
   ollama pull tinyllama
   ```

2. Create the environment and install dependencies:

   ```
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r Requirements.txt
   ```

3. Optional:
   - Put .txt, .md or .pdf files in `Documents/` for AURORA to list,
     search and read. It can't read files anywhere else.
   - To use Wikipedia, set `RESEARCH_CONTACT` in
     `aurora/config/settings.py` to an email address or web page.
     Wikimedia requires this, and it is sent with each lookup.

## Run

```
.venv\Scripts\python.exe main.py
```

Commands: `status` shows system status, `route <message>` shows how a message would be routed, `plan <message>` shows how it would be split into steps, `clear` resets the conversation, `exit` shuts down.
Logs are written to `Logs/aurora.log` (metadata only; never your messages).

## Test

```
.venv\Scripts\python.exe -m pytest
```

## Documentation

- [Architecture](Docs/ARCHITECTURE.md)
- [Memory design](Docs/MEMORY_DESIGN.md)
- [Changelog](Docs/CHANGELOG.md)
