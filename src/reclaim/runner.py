from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from reclaim.audit import append
from reclaim.executor import Result, run_item
from reclaim.model import Item


@dataclass(frozen=True)
class RunSummary:
    cleaned: frozenset[str]
    failed: int
    freed_bytes: int


def execute_items(
    items: Iterable[Item],
    roots: list,
    home: Path,
    log_path: Path,
    dry_run: bool = False,
    trash_dir: Path | None = None,
    on_result: Callable[[Item, Result], None] | None = None,
) -> RunSummary:
    """Run items one by one, audit each result, and measure the free-space change."""
    free_before = shutil.disk_usage(home).free
    cleaned: set[str] = set()
    failed = 0
    for item in list(items):
        try:
            result = run_item(item, roots, home, dry_run=dry_run, trash_dir=trash_dir)
        except Exception as exc:
            result = Result(item.id, False, (str(exc),))
        append(log_path, result, freed_bytes=item.size_bytes if result.measured else None)
        if result.ok and not dry_run:
            cleaned.add(item.id)
        if not result.ok:
            failed += 1
        if on_result:
            on_result(item, result)
    freed = max(0, shutil.disk_usage(home).free - free_before)
    return RunSummary(frozenset(cleaned), failed, freed)
