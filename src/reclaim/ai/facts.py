from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath

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

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def located(item: Item) -> Path | None:
    if item.path is not None:
        return item.path
    for action in item.actions:
        if isinstance(action, RemovePath):
            return action.path
    return None


def gather(item: Item, ctx: ScanContext) -> Facts:
    path = located(item)
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
