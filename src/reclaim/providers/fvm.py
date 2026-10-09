from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath, Risk
from reclaim.providers._walk import find_files, safe_id

_PIN_FILES = {".fvmrc", "fvm_config.json"}


def pinned_versions(project_roots: tuple[Path, ...]) -> tuple[set[str], bool]:
    pins: set[str] = set()
    cutoffs: list[Path] = []
    ok = True
    for f in find_files(project_roots, _PIN_FILES, cutoffs=cutoffs):
        if f.name == "fvm_config.json" and f.parent.name != ".fvm":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            value = data.get("flutter") or data.get("flutterSdkVersion")
        except (OSError, ValueError, AttributeError):
            ok = False
            continue
        if not isinstance(value, str):
            ok = False
            continue
        values = [value]
        flavors = data.get("flavors")
        if isinstance(flavors, dict):
            values += [v for v in flavors.values() if isinstance(v, str)]
        for v in values:
            pins.add(v)
            pins.add(v.split("@")[0])
            pins.add(v.split("/")[0])
    return pins, ok and not cutoffs


def _cache_base(ctx: ScanContext) -> Path:
    raw = ctx.env.get("FVM_CACHE_PATH")
    if raw:
        path = Path(raw)
        if path.is_absolute() and ".." not in path.parts and Path(os.path.realpath(ctx.home)) in Path(os.path.realpath(path)).parents:
            return path
    return ctx.home / "fvm"


class FvmProvider:
    name = "fvm"

    def roots(self, ctx: ScanContext) -> list[Path]:
        return [_cache_base(ctx) / "versions"]

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        cache = _cache_base(ctx)
        base = cache / "versions"
        if not base.is_dir():
            return
        pins, ok = pinned_versions(ctx.project_roots)
        known = ok and bool(ctx.project_roots)
        default = cache / "default"
        keep_names = {Path(default.resolve()).name} if default.is_symlink() else set()
        flutter = ctx.which("flutter")
        if flutter:
            real = Path(os.path.realpath(flutter))
            if base in real.parents:
                keep_names.add(real.relative_to(base).parts[0])
        for version in sorted(p for p in base.iterdir() if p.is_dir()):
            if version.name in pins or version.name in keep_names:
                continue
            size = ctx.sizer(version)
            if size < ctx.min_size:
                continue
            yield Item(
                id=f"fvm:{safe_id(version.name)}",
                label=f"Flutter SDK {version.name} (FVM)",
                group="Flutter and Android",
                size_bytes=size,
                risk=Risk.REGENERABLE,
                why="Not pinned by any project .fvmrc and not the global default; fvm install brings it back."
                if known
                else "Cannot confirm no project pins this: set project_roots in the config (or fix unreadable .fvmrc files).",
                actions=(RemovePath(version),) if known else (),
                report_only=not known,
                path=version,
            )
