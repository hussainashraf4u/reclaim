from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable, Iterator

SKIP_DIRS = frozenset({
    "node_modules", "build", "Pods", ".dart_tool", "target", "venv", ".venv", ".git", ".gradle", ".idea",
})
_ALLOWED_HIDDEN = frozenset({".fvm"})
_PROJECT_MARKERS = (".git", "pubspec.yaml", "package.json")


def safe_id(name: str) -> str:
    if any(c.isspace() or c == "," for c in name):
        return hashlib.sha1(name.encode()).hexdigest()[:8]
    return name


def find_files(
    roots: Iterable[Path], names: set[str], max_depth: int = 6, cutoffs: list[Path] | None = None
) -> Iterator[Path]:
    for root in roots:
        stack = [(Path(root), 0)]
        while stack:
            current, depth = stack.pop()
            try:
                with os.scandir(current) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False):
                        if entry.name in names:
                            yield Path(entry.path)
                        continue
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if entry.name in _ALLOWED_HIDDEN:
                        stack.append((Path(entry.path), depth + 1))
                        continue
                    if entry.name in SKIP_DIRS or entry.name.startswith("."):
                        continue
                    if depth + 1 < max_depth:
                        stack.append((Path(entry.path), depth + 1))
                    elif cutoffs is not None and any(os.path.exists(os.path.join(entry.path, m)) for m in _PROJECT_MARKERS):
                        cutoffs.append(Path(entry.path))
                except OSError:
                    continue
