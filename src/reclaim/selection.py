from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from reclaim.executor import run_item
from reclaim.model import Item, Risk


def human(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


def summary_text(items: Iterable[Item], selected_bytes: int) -> str:
    items = list(items)
    reclaimable = sum(i.size_bytes for i in items if i.executable)
    report = sum(i.size_bytes for i in items if not i.executable)
    return (
        f"{len(items)} items, reclaimable {human(reclaimable)}, report-only {human(report)}"
        f" | selected {human(selected_bytes)}"
    )


def confirmation_ok(typed: str, needed: list[str]) -> bool:
    tokens = {t for t in re.split(r"[,\s]+", typed.strip()) if t}
    return all(n in tokens for n in needed)


class Selection:
    def __init__(self, items: Iterable[Item] = ()):
        self._items: dict[str, Item] = {}
        self._ticked: set[str] = set()
        self.add(items)

    def add(self, items: Iterable[Item]) -> None:
        for item in items:
            self._items[item.id] = item

    def set_items(self, items: Iterable[Item]) -> None:
        self._items = {item.id: item for item in items}
        self._ticked &= set(self._items)

    def is_ticked(self, item_id: str) -> bool:
        return item_id in self._ticked

    def toggle(self, item_id: str) -> bool:
        item = self._items.get(item_id)
        if item is None or not item.executable:
            return False
        self._ticked.symmetric_difference_update({item_id})
        return True

    def tick(self, item_ids: Iterable[str], value: bool) -> None:
        for item_id in item_ids:
            item = self._items.get(item_id)
            if item is None or not item.executable:
                continue
            if value:
                self._ticked.add(item_id)
            else:
                self._ticked.discard(item_id)

    def select_safe(self) -> None:
        for item in self._items.values():
            if item.executable and item.risk is Risk.SAFE:
                self._ticked.add(item.id)

    def clear(self) -> None:
        self._ticked.clear()

    @property
    def selected(self) -> list[Item]:
        return [i for i in self._items.values() if i.id in self._ticked]

    @property
    def selected_bytes(self) -> int:
        return sum(i.size_bytes for i in self.selected)

    @property
    def data_ids(self) -> list[str]:
        return [i.id for i in self.selected if i.risk is Risk.DATA]

    def plan_lines(self, roots: list[Path], home: Path, trash_dir: Path | None = None) -> list[str]:
        lines: list[str] = []
        expected = 0
        for item in self.selected:
            tag = f"  id: {item.id}" if item.risk is Risk.DATA else ""
            lines.append(f"[{item.risk.value}] {item.label} ({human(item.size_bytes)}){tag}")
            result = run_item(item, roots, home, dry_run=True, trash_dir=trash_dir)
            for entry in result.log:
                lines.append(f"    {entry}")
            if result.ok:
                expected += item.size_bytes
            else:
                lines.append("    BLOCKED: will not run")
        lines.append(f"Expected to free: {human(expected)} (estimate)")
        return lines
