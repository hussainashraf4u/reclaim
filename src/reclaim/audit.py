from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from reclaim.executor import Result


def default_log_path(home: Path) -> Path:
    return home / ".local" / "state" / "reclaim" / "log.jsonl"


def append(log_path: Path, result: Result, freed_bytes: int | None = None, now: datetime | None = None) -> None:
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": stamp, "item": result.item_id, "ok": result.ok, "log": list(result.log), "freed_bytes": freed_bytes}
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
