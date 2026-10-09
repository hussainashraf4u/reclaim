from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, Risk

GROUP_BIG = "Big files"
GROUP_DUPES = "Duplicate files"
_DIRS = ("Documents", "Desktop", "Movies", "Downloads", "Music", "Pictures")
_SKIP = frozenset({"node_modules", ".git", "Library", ".Trash", "Pods", ".venv", "venv", "target", "build"})
_MAX_DEPTH = 5
_MAX_FILES = 200_000
_CHUNK = 1 << 20


def _walk_files(home: Path, min_bytes: int) -> Iterator[tuple[Path, int]]:
    seen = 0
    for name in _DIRS:
        stack = [(home / name, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                with os.scandir(current) as it:
                    entries = list(it)
            except OSError:
                continue
            for e in entries:
                seen += 1
                if seen > _MAX_FILES:
                    return
                try:
                    if e.is_symlink():
                        continue
                    if e.is_file(follow_symlinks=False):
                        size = e.stat(follow_symlinks=False).st_size
                        if size >= min_bytes:
                            yield Path(e.path), size
                    elif e.is_dir(follow_symlinks=False) and e.name not in _SKIP and depth + 1 < _MAX_DEPTH:
                        stack.append((Path(e.path), depth + 1))
                except OSError:
                    continue


def _digest(path: Path, partial: bool, size: int) -> str | None:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            if partial:
                h.update(fh.read(_CHUNK))
                if size > 2 * _CHUNK:
                    fh.seek(size - _CHUNK)
                    h.update(fh.read(_CHUNK))
            else:
                while chunk := fh.read(_CHUNK):
                    h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _item_id(prefix: str, *paths: Path) -> str:
    return f"{prefix}:{hashlib.sha1(''.join(sorted(str(p) for p in paths)).encode()).hexdigest()[:8]}"


class BigFilesProvider:
    """Single large files in personal folders. Report only: these are your own files."""

    name = "big_files"

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        floor = max(ctx.big_file_bytes, ctx.min_size)
        for path, size in sorted(_walk_files(ctx.home, floor), key=lambda t: -t[1]):
            yield Item(
                id=_item_id("bigfile", path),
                label="~/" + str(path.relative_to(ctx.home)),
                group=GROUP_BIG,
                size_bytes=size,
                risk=Risk.DATA,
                why="Large personal file. Reported so you can decide; reclaim never deletes it.",
                report_only=True,
                path=path,
            )


class DuplicatesProvider:
    """Identical large files. Report only; size counts the redundant copies."""

    name = "duplicates"

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        floor = max(ctx.duplicate_min_bytes, 1)
        by_size: dict[int, list[Path]] = defaultdict(list)
        for path, size in _walk_files(ctx.home, floor):
            by_size[size].append(path)
        for size, paths in by_size.items():
            if len(paths) < 2:
                continue
            partial: dict[str, list[Path]] = defaultdict(list)
            for p in paths:
                digest = _digest(p, True, size)
                if digest:
                    partial[digest].append(p)
            for group in partial.values():
                if len(group) < 2:
                    continue
                full: dict[str, list[Path]] = defaultdict(list)
                for p in group:
                    digest = _digest(p, False, size)
                    if digest:
                        full[digest].append(p)
                for same in full.values():
                    if len(same) < 2:
                        continue
                    wasted = size * (len(same) - 1)
                    if wasted < ctx.min_size:
                        continue
                    same = sorted(same)
                    shown = "; ".join("~/" + str(p.relative_to(ctx.home)) for p in same[:4])
                    yield Item(
                        id=_item_id("dup", *same),
                        label=f"{same[0].name} x{len(same)}",
                        group=GROUP_DUPES,
                        size_bytes=wasted,
                        risk=Risk.DATA,
                        why=f"Identical copies: {shown}. Keep one and delete the rest by hand.",
                        report_only=True,
                        path=same[0],
                    )
