"""
The audit trail for email you send through AURORA: every draft, approval, send and refusal, one line
each in Data\\email_audit.jsonl (never in git). It records who it went to, when, Gmail's message id
and a fingerprint of the exact text you approved, not the text itself.
"""

import json
from datetime import datetime
from pathlib import Path

from aurora.config import settings

EVENTS = ("drafted", "approved", "sent", "failed", "declined", "refused")


def record(event: str, path: Path | None = None, **fields) -> None:
    assert event in EVENTS, event
    path = Path(path or settings.EMAIL_AUDIT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"time": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(line, ensure_ascii=False) + "\n")


def entries(day: str | None = None, path: Path | None = None) -> list[dict]:
    path = Path(path or settings.EMAIL_AUDIT_FILE)
    if not path.is_file():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if day is None or row.get("time", "").startswith(day):
            rows.append(row)
    return rows
