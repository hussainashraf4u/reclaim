from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, Risk

GROUP = "Large folders"
USER_CONTENT = frozenset({
    "Documents", "Desktop", "Movies", "Music", "Pictures", "Public", "Applications", "Library", ".Trash", "Downloads",
})
_LIBRARY_PARTS = ("Application Support", "Caches", "Developer", "Logs")
_EXTRA_DIRS = (Path(".cache"), Path(".local") / "share")


def _children(parent: Path, include_files: bool, skip: frozenset[str] = frozenset()) -> list[Path]:
    try:
        with os.scandir(parent) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        return []
    out = []
    for e in entries:
        try:
            if e.is_symlink() or e.name in skip or e.name == ".DS_Store":
                continue
            if e.is_dir(follow_symlinks=False) or (include_files and e.is_file(follow_symlinks=False)):
                out.append(Path(e.path))
        except OSError:
            continue
    return out


def _why(path: Path) -> str:
    if path.name == "MobileSync":
        return "iPhone and iPad backups. Do not delete unless you are sure you no longer need them."
    return (
        "Large folder that no cleanup rule covers. It may hold your own data "
        "(backups, projects, synced files); inspect it before deleting anything."
    )


def _candidates(home: Path) -> list[Path]:
    found = _children(home, False, USER_CONTENT)
    for part in _LIBRARY_PARTS:
        found += _children(home / "Library" / part, False)
    for rel in _EXTRA_DIRS:
        found += _children(home / rel, False)
    found += _children(home / "Downloads", True)
    return found


class LargeDirsProvider:
    name = "large_dirs"

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        candidates = _candidates(ctx.home)
        if not candidates:
            return
        with ThreadPoolExecutor(max_workers=4) as pool:
            sizes = list(pool.map(ctx.sizer, candidates))
        for path, size in zip(candidates, sizes):
            if size < ctx.large_dir_bytes:
                continue
            shown = "~/" + str(path.relative_to(ctx.home))
            yield Item(
                id=f"large:{hashlib.sha1(str(path).encode()).hexdigest()[:8]}",
                label=shown,
                group=GROUP,
                size_bytes=size,
                risk=Risk.DATA,
                why=_why(path),
                report_only=True,
                path=path,
            )
