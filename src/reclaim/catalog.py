from __future__ import annotations

import glob as globmod
import hashlib
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath, Risk, Run


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    label: str
    group: str
    risk: Risk
    why: str
    path: str | None
    glob: str | None
    clean_run: tuple[str, ...] | None
    needs: str | None
    busy: tuple[str, ...]


def parse_entries(text: str) -> list[CatalogEntry]:
    data = tomllib.loads(text)
    out: list[CatalogEntry] = []
    for raw in data.get("item", []):
        for key in ("id", "label", "group", "risk"):
            if key not in raw:
                raise CatalogError(f"missing '{key}' in {raw}")
        try:
            risk = Risk(raw["risk"])
        except ValueError:
            raise CatalogError(f"bad risk '{raw['risk']}' in {raw['id']}") from None
        if ("path" in raw) == ("glob" in raw):
            raise CatalogError(f"{raw['id']}: exactly one of path/glob required")
        pattern = raw.get("path") or raw.get("glob")
        if not isinstance(pattern, str) or not (pattern.startswith("~/") or pattern.startswith("/")):
            raise CatalogError(f"{raw['id']}: path/glob must start with ~/ or /")
        clean = raw.get("clean", {}).get("run")
        out.append(
            CatalogEntry(
                id=raw["id"],
                label=raw["label"],
                group=raw["group"],
                risk=risk,
                why=raw.get("why", ""),
                path=raw.get("path"),
                glob=raw.get("glob"),
                clean_run=tuple(clean) if clean else None,
                needs=raw.get("needs"),
                busy=tuple(raw.get("busy", [])),
            )
        )
    return out


def load_catalog(directory: Path | None = None) -> list[CatalogEntry]:
    source = directory if directory else resources.files("reclaim").joinpath("catalog_data")
    files = sorted((f for f in source.iterdir() if f.name.endswith(".toml")), key=lambda f: f.name)
    entries: list[CatalogEntry] = []
    for f in files:
        entries.extend(parse_entries(f.read_text(encoding="utf-8")))
    ids = [e.id for e in entries]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise CatalogError(f"duplicate ids: {sorted(dupes)}")
    return entries


def _expand(entry: CatalogEntry, home: Path) -> list[tuple[str, str, Path]]:
    pattern = entry.path or entry.glob or ""
    full = str(home / pattern[2:]) if pattern.startswith("~/") else pattern
    if entry.glob:
        return [
            (f"{entry.id}:{hashlib.sha1(Path(p).name.encode()).hexdigest()[:8]}", f"{entry.label}: {Path(p).name}", Path(p))
            for p in sorted(globmod.glob(full))
        ]
    p = Path(full)
    return [(entry.id, entry.label, p)] if p.exists() else []


def entry_to_items(entry: CatalogEntry, ctx: ScanContext) -> list[Item]:
    if entry.needs and ctx.which(entry.needs) is None:
        return []
    busy = next((n for n in entry.busy if ctx.is_running(n)), None)
    items: list[Item] = []
    for item_id, label, path in _expand(entry, ctx.home):
        size = ctx.sizer(path)
        if size < ctx.min_size:
            continue
        actions = (Run(entry.clean_run),) if entry.clean_run else (RemovePath(path),)
        items.append(
            Item(
                id=item_id,
                label=label,
                group=entry.group,
                size_bytes=size,
                risk=entry.risk,
                why=entry.why,
                actions=actions,
                disabled_reason=f"{busy} is running" if busy else None,
                busy=entry.busy,
                path=path,
            )
        )
    return items
