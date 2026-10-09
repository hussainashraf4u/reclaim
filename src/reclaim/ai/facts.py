from __future__ import annotations

import os
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath
from reclaim.providers.large_dirs import GROUP as LARGE_DIRS_GROUP

_LOCKFILES = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "Cargo.lock", "poetry.lock",
    "uv.lock", "Pipfile.lock", "pubspec.lock", "Podfile.lock", "Gemfile.lock", "go.sum",
)
# Artifacts whose contents depend on exact dependency versions.
_NEEDS_LOCKFILE = frozenset({"node_modules", ".venv", "venv", "Pods", "target"})


@dataclass(frozen=True)
class Facts:
    """Plain facts about an item's project, gathered locally. Never file contents."""

    artifact: str | None = None
    git_repo: bool | None = None
    git_dirty_files: int | None = None
    days_since_commit: int | None = None
    has_lockfile: bool | None = None
    profile: dict | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def located(item: Item) -> Path | None:
    if item.path is not None:
        return item.path
    for action in item.actions:
        if isinstance(action, RemovePath):
            return action.path
    return None


PROFILE_ENTRIES = 2000
PROFILE_SECONDS = 2.0
TOP_CHILDREN = 12
TOP_EXTENSIONS = 8
_MARKER_NAMES = frozenset({
    ".git", "package.json", "Cargo.toml", "pyproject.toml", "go.mod", "pubspec.yaml", "Package.swift", "Info.plist",
    "Dockerfile", "Gemfile", "build.gradle",
})
_MARKER_SUFFIXES = (".xcodeproj", ".xcworkspace", ".vmdk", ".vdi", ".qcow2", ".sqlite", ".db", ".app")
# Parts of a profile that carry file or folder names; withheld from remote providers unless the user opts in.
NAME_KEYS = ("top_children", "markers")


def profile(path: Path, now: float, entries: int = PROFILE_ENTRIES, seconds: float = PROFILE_SECONDS) -> dict | None:
    """A bounded, local look inside a folder: where the bytes are, what kinds of files, how old. Never reads contents."""
    if not path.is_dir():
        return None
    deadline = time.monotonic() + seconds
    per_child: Counter[str] = Counter()
    extensions: Counter[str] = Counter()
    oldest = newest = None
    markers: list[str] = []
    seen = files = 0
    truncated = False
    stack = [(path, "")]
    while stack and not truncated:
        current, top = stack.pop()
        try:
            with os.scandir(current) as it:
                listing = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in listing:
            seen += 1
            if seen > entries or time.monotonic() > deadline:
                truncated = True
                break
            owner = top or e.name
            try:
                if e.is_symlink():
                    continue
                if not top and (e.name in _MARKER_NAMES or e.name.endswith(_MARKER_SUFFIXES)) and len(markers) < 10:
                    markers.append(e.name)
                if e.is_dir(follow_symlinks=False):
                    stack.append((Path(e.path), owner))
                elif e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    files += 1
                    per_child[owner] += st.st_size
                    extensions[Path(e.name).suffix.lower() or "(none)"] += 1
                    oldest = st.st_mtime if oldest is None else min(oldest, st.st_mtime)
                    newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
            except OSError:
                continue
    days = lambda t: None if t is None else max(0, int((now - t) // 86400))  # noqa: E731
    return {
        "files_sampled": files,
        "truncated": truncated,
        "extensions": dict(extensions.most_common(TOP_EXTENSIONS)),
        "newest_days": days(newest),
        "oldest_days": days(oldest),
        "top_children": [{"name": n, "size_mb": b // (1024 * 1024)} for n, b in per_child.most_common(TOP_CHILDREN)],
        "markers": markers,
    }


def gather(item: Item, ctx: ScanContext) -> Facts:
    path = located(item)
    if item.group == LARGE_DIRS_GROUP and path is not None:
        return Facts(profile=profile(path, ctx.now()))
    if item.group != "Project artifacts" or path is None:
        return Facts()
    project = path.parent
    lockfile = any((project / name).exists() for name in _LOCKFILES) if path.name in _NEEDS_LOCKFILE else None
    inside = ctx.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=project, timeout=10)
    if inside is None or inside.returncode != 0:
        return Facts(artifact=path.name, git_repo=False if inside is not None else None, has_lockfile=lockfile)
    dirty = ctx.run(["git", "status", "--porcelain"], cwd=project, timeout=15)
    last = ctx.run(["git", "log", "-1", "--format=%ct"], cwd=project, timeout=10)
    days = None
    if last is not None and last.returncode == 0 and last.stdout.strip().isdigit():
        days = max(0, int((ctx.now() - int(last.stdout.strip())) // 86400))
    return Facts(
        artifact=path.name,
        git_repo=True,
        git_dirty_files=len(dirty.stdout.splitlines()) if dirty is not None and dirty.returncode == 0 else None,
        days_since_commit=days,
        has_lockfile=lockfile,
    )
