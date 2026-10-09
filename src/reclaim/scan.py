from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Protocol

from reclaim.catalog import CatalogEntry, entry_to_items
from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath


class Provider(Protocol):
    name: str

    def scan(self, ctx: ScanContext) -> Iterable[Item]: ...


@dataclass
class ScanResult:
    items: list[Item] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _remove_paths(item: Item) -> list[Path]:
    return [a.path for a in item.actions if isinstance(a, RemovePath)]


def _located(item: Item) -> list[Path]:
    return [item.path] if item.path is not None else _remove_paths(item)


def dedupe(items: list[Item]) -> list[Item]:
    large = [i for i in items if i.group == "Large folders"]
    rest = [i for i in items if i.group != "Large folders"]

    def depth(item: Item) -> int:
        places = _located(item)
        return min((len(p.parts) for p in places), default=0)

    kept: list[Item] = []
    for item in sorted(rest, key=lambda i: (-i.size_bytes, depth(i))):
        places = _located(item)
        covered = any(
            p == op or op in p.parents
            for other in kept
            for op in _located(other)
            for p in places
        )
        if not covered:
            kept.append(item)
    keep_ids = {id(i) for i in kept} | {id(i) for i in large}
    return [i for i in items if id(i) in keep_ids]


def drop_covered(items: list[Item]) -> list[Item]:
    others = [i for i in items if i.group != "Large folders"]
    out = []
    for item in items:
        if item.group == "Large folders" and item.path is not None:
            p = item.path
            inside = False
            covered = 0
            for other in others:
                places = _located(other)
                if any(p == o or o in p.parents for o in places):
                    inside = True
                    break
                if any(p in o.parents for o in places):
                    covered += other.size_bytes
            if inside or (item.size_bytes > 0 and covered * 2 >= item.size_bytes):
                continue
        out.append(item)
    return out


def scan_all(
    ctx: ScanContext,
    entries: Iterable[CatalogEntry],
    providers: Iterable[Provider] = (),
    on_items: Callable[[list[Item]], None] | None = None,
    max_workers: int = 8,
    ignore: frozenset[str] = frozenset(),
) -> ScanResult:
    jobs: dict = {}
    result = ScanResult()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for entry in entries:
            jobs[pool.submit(entry_to_items, entry, ctx)] = entry.id
        for provider in providers:
            jobs[pool.submit(lambda p=provider: list(p.scan(ctx)))] = provider.name
        for future in as_completed(jobs):
            try:
                batch = list(future.result())
            except Exception as exc:
                result.errors.append(f"{jobs[future]}: {exc}")
                continue
            if batch:
                result.items.extend(batch)
                if on_items:
                    on_items(batch)
    kept = [i for i in drop_covered(dedupe(result.items)) if i.id not in ignore]
    result.items = sorted(kept, key=lambda i: i.size_bytes, reverse=True)
    return result
