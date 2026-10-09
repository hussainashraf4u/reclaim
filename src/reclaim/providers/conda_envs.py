from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterator

from reclaim.context import ScanContext
from reclaim.model import Item, Risk, Run

_GROUP = "Python"


def _json(ctx: ScanContext, argv: list[str]) -> dict | None:
    result = ctx.run(argv, timeout=60)
    if result is None or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class CondaEnvsProvider:
    name = "conda_envs"

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        if ctx.which("conda") is None:
            return
        info = _json(ctx, ["conda", "info", "--json"])
        listing = _json(ctx, ["conda", "env", "list", "--json"])
        if info is None or listing is None:
            return
        excluded = {
            os.path.realpath(info[k]) for k in ("root_prefix", "active_prefix") if isinstance(info.get(k), str) and info[k]
        }
        cutoff = ctx.now() - ctx.stale_days * 86400
        for raw in listing.get("envs") or []:
            if not isinstance(raw, str) or os.path.realpath(raw) in excluded:
                continue
            item = self._env(ctx, Path(raw), cutoff)
            if item is not None:
                yield item
        dirs = [Path(p) for p in info.get("pkgs_dirs") or [] if isinstance(p, str)]
        size = sum(ctx.sizer(d) for d in dirs)
        if dirs and size > 0 and size >= ctx.min_size:
            yield Item(
                id="conda-pkgs",
                label="conda package cache",
                group=_GROUP,
                size_bytes=size,
                risk=Risk.REGENERABLE,
                why="Upper bound: includes files shared with environments that 'conda clean -a' keeps.",
                actions=(Run(("conda", "clean", "-a", "-y")),),
                path=dirs[0],
            )

    def _env(self, ctx: ScanContext, path: Path, cutoff: float) -> Item | None:
        meta = path / "conda-meta"
        try:
            mtime = (meta if meta.exists() else path).stat().st_mtime
        except OSError:
            return None
        if mtime > cutoff:
            return None
        size = ctx.sizer(path)
        if size < ctx.min_size:
            return None
        age = int((ctx.now() - mtime) // 86400)
        return Item(
            id=f"conda-env:{hashlib.sha1(str(path).encode()).hexdigest()[:8]}",
            label=f"conda env {path.name} (no package changes in {age}d)",
            group=_GROUP,
            size_bytes=size,
            risk=Risk.DATA,
            why="No package was installed or removed recently, but the environment may still be in daily use.",
            actions=(Run(("conda", "env", "remove", "-y", "-p", str(path))),),
            path=path,
        )
