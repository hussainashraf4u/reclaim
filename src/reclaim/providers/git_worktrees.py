from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, Risk, Run
from reclaim.safety import ARTIFACT_NAMES

_GROUP = "Git worktrees"
_MAX_DEPTH = 2
_TOLERATED = frozenset({".DS_Store", "__pycache__"})


def parse_worktrees(text: str) -> list[dict]:
    blocks: list[dict] = []
    current: dict | None = None
    for line in (text.split("\0") if "\0" in text else text.splitlines()):
        if line.startswith("worktree "):
            current = {"path": line[len("worktree "):], "head": "", "branch": None}
            blocks.append(current)
        elif current is not None and line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif current is not None and line.startswith("branch "):
            current["branch"] = line[len("branch "):].removeprefix("refs/heads/")
    return blocks


def _status(text: str) -> tuple[bool, list[str]]:
    dirty = False
    extras: list[str] = []
    for line in text.splitlines():
        if line.startswith("!! "):
            name = line[3:].rstrip("/").rsplit("/", 1)[-1]
            if name not in ARTIFACT_NAMES and name not in _TOLERATED:
                extras.append(name)
        elif line.strip():
            dirty = True
    return dirty, extras


def _repos(roots: tuple[Path, ...]) -> Iterator[Path]:
    seen: set[Path] = set()
    for root in roots:
        stack = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            if (current / ".git").is_dir():
                if current not in seen:
                    seen.add(current)
                    yield current
                continue
            if depth >= _MAX_DEPTH:
                continue
            try:
                with os.scandir(current) as entries:
                    children = sorted(e.path for e in entries if e.is_dir(follow_symlinks=False) and not e.name.startswith("."))
            except OSError:
                continue
            stack.extend((Path(c), depth + 1) for c in children)


class GitWorktreesProvider:
    name = "git_worktrees"

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        if ctx.which("git") is None:
            return
        for repo in _repos(ctx.project_roots):
            listing = ctx.run(["git", "-C", str(repo), "worktree", "list", "--porcelain", "-z"])
            if listing is None or listing.returncode != 0:
                continue
            for block in parse_worktrees(listing.stdout)[1:]:
                item = self._item(ctx, repo, block)
                if item is not None and item.size_bytes >= ctx.min_size:
                    yield item

    def _item(self, ctx: ScanContext, repo: Path, block: dict) -> Item | None:
        path = Path(block["path"])
        if not path.exists():
            return None
        status = ctx.run(["git", "-C", str(path), "status", "--porcelain", "--ignored"])
        known = status is not None and status.returncode == 0
        dirty, extras = _status(status.stdout) if status is not None and known else (True, [])
        ahead = ctx.run(["git", "-C", str(repo), "rev-list", "--count", f"HEAD..{block['head']}"])
        count = ahead.stdout.strip() if ahead and ahead.returncode == 0 else "?"
        branch = block["branch"] or "detached"
        digest = hashlib.sha1(str(path).encode()).hexdigest()[:8]
        label = f"{repo.name}/{path.name} ({branch}, {count} ahead of the main checkout)"
        size = ctx.sizer(path)
        problem = None
        if dirty:
            problem = "Has uncommitted changes or unknown status; commit or discard them first."
        elif extras:
            problem = "Contains ignored files git would delete: " + ", ".join(extras[:5])
        elif block["branch"] is None:
            refs = ctx.run(["git", "-C", str(repo), "for-each-ref", "--contains", block["head"], "--format=%(refname)"])
            if refs is None or refs.returncode != 0 or not refs.stdout.strip():
                problem = "Detached HEAD has commits on no branch; removing it would lose them."
        if problem:
            return Item(
                id=f"worktree:{digest}", label=label, group=_GROUP, size_bytes=size, risk=Risk.REGENERABLE,
                why=problem, report_only=True, path=path,
            )
        return Item(
            id=f"worktree:{digest}", label=label, group=_GROUP, size_bytes=size, risk=Risk.REGENERABLE,
            why="Removes only the checkout; the branch is kept in git.",
            actions=(Run(("git", "-C", str(repo), "worktree", "remove", str(path))),),
            path=path,
        )
