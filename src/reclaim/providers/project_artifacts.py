from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath, Risk
from reclaim.safety import ScopedRoot

_GROUP = "Project artifacts"
_MAX_DEPTH = 6
ARTIFACTS: dict[str, tuple[str, ...]] = {
    "node_modules": ("package.json",),
    ".next": ("package.json",),
    ".turbo": ("package.json",),
    "target": ("Cargo.toml", "pom.xml"),
    "build": ("pubspec.yaml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"),
    ".dart_tool": ("pubspec.yaml",),
    ".venv": ("pyproject.toml", "requirements.txt"),
    "venv": ("pyproject.toml", "requirements.txt"),
    "Pods": ("Podfile",),
}


def _holds_running_python(path: Path) -> bool:
    prefix = os.path.realpath(sys.prefix)
    real = os.path.realpath(path)
    return prefix == real or prefix.startswith(real + os.sep)


def _candidates(root: Path) -> Iterator[Path]:
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as entries:
                found = sorted(entries, key=lambda e: e.name)
        except OSError:
            continue
        for entry in found:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if entry.name in ARTIFACTS:
                yield Path(entry.path)
            elif not entry.name.startswith(".") and depth + 1 < _MAX_DEPTH:
                stack.append((Path(entry.path), depth + 1))


class ProjectArtifactsProvider:
    name = "project_artifacts"

    def roots(self, ctx: ScanContext) -> list[ScopedRoot]:
        return [ScopedRoot(root, frozenset(ARTIFACTS)) for root in ctx.project_roots]

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        cutoff = ctx.now() - ctx.stale_days * 86400
        found = sorted(((root, path) for root in ctx.project_roots for path in _candidates(root)), key=lambda rp: str(rp[1]))
        if not found:
            return
        with ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(lambda rp: self._item(ctx, rp[0], rp[1], cutoff), found))
        for item in items:
            if item is not None:
                yield item

    def _item(self, ctx: ScanContext, root: Path, path: Path, cutoff: float) -> Item | None:
        parent = path.parent
        markers = [parent / m for m in ARTIFACTS[path.name] if (parent / m).exists()]
        if not markers or _holds_running_python(path):
            return None
        try:
            mtime = max([path.stat().st_mtime] + [m.stat().st_mtime for m in markers])
        except OSError:
            return None
        if mtime > cutoff:
            return None
        check = ctx.run(["git", "-C", str(parent), "check-ignore", "-q", path.name])
        if check is None or check.returncode != 0:
            return None
        size = ctx.sizer(path)
        if size < ctx.min_size:
            return None
        age = int((ctx.now() - mtime) // 86400)
        digest = hashlib.sha1(str(path).encode()).hexdigest()[:8]
        return Item(
            id=f"artifact:{digest}",
            label=f"{path.name}: {path.relative_to(root)} ({age}d old)",
            group=_GROUP,
            size_bytes=size,
            risk=Risk.REGENERABLE,
            why="Build or dependency output of a project not touched recently; rebuilt by the project's tooling.",
            actions=(RemovePath(path),),
            path=path,
            age_days=age,
        )
