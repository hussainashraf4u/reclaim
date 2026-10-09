from __future__ import annotations

import json
from pathlib import Path

from reclaim.model import Item


def default_cache_path(home: Path) -> Path:
    return home / ".cache" / "reclaim" / "last_scan.json"


def save(path: Path, items: list[Item], now: float) -> None:
    """Remember item sizes from the last scan. Failures are ignored: the cache is only a convenience."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "ts": now, "sizes": {i.id: i.size_bytes for i in items}}
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def load(path: Path) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        sizes = data["sizes"]
        return {k: v for k, v in sizes.items() if isinstance(k, str) and isinstance(v, int)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def annotate(previous: dict[str, int], items: list[Item]) -> None:
    """Set delta_bytes (size change since the previous scan) on items seen in that scan."""
    for item in items:
        if item.id in previous:
            item.delta_bytes = item.size_bytes - previous[item.id]
