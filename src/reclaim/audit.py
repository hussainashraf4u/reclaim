from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from reclaim.executor import Result


def default_log_path(home: Path) -> Path:
    return home / ".local" / "state" / "reclaim" / "log.jsonl"


def append(log_path: Path, result: Result, freed_bytes: int | None = None, now: datetime | None = None) -> None:
    stamp = (now or datetime.now(UTC)).isoformat()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": stamp, "item": result.item_id, "ok": result.ok, "log": list(result.log), "freed_bytes": freed_bytes}
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


@dataclass(frozen=True)
class HistoryRow:
    ts: str
    item: str
    ok: bool
    freed_bytes: int | None
    log: tuple[str, ...]


@dataclass(frozen=True)
class HistorySummary:
    runs: int
    failures: int
    freed_bytes: int


def read_log(log_path: Path) -> list[HistoryRow]:
    """Read the audit log, skipping lines that are not valid rows."""
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    rows: list[HistoryRow] = []
    for line in lines:
        try:
            raw = json.loads(line)
            freed = raw.get("freed_bytes")
            rows.append(
                HistoryRow(
                    ts=str(raw["ts"]),
                    item=str(raw["item"]),
                    ok=bool(raw["ok"]),
                    freed_bytes=freed if isinstance(freed, int) else None,
                    log=tuple(str(x) for x in raw.get("log", [])),
                )
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return rows


def summarize(rows: list[HistoryRow]) -> HistorySummary:
    return HistorySummary(
        runs=len(rows),
        failures=sum(1 for r in rows if not r.ok),
        freed_bytes=sum(r.freed_bytes or 0 for r in rows if r.ok),
    )
