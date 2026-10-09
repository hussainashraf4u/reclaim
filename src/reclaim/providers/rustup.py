from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath, Risk
from reclaim.providers._walk import find_files, safe_id

_PIN_FILES = {"rust-toolchain", "rust-toolchain.toml"}


def _channel(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if path.suffix == ".toml" or text.startswith("["):
        try:
            value = tomllib.loads(text).get("toolchain", {}).get("channel")
        except tomllib.TOMLDecodeError:
            return None
        return value if isinstance(value, str) else None
    return text.splitlines()[0].strip() if text else None


def _used_by_settings(home: Path) -> set[str]:
    try:
        data = tomllib.loads((home / ".rustup" / "settings.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return set()
    names = {v for v in (data.get("default_toolchain"),) if isinstance(v, str)}
    overrides = data.get("overrides", {})
    if isinstance(overrides, dict):
        names |= {v for v in overrides.values() if isinstance(v, str)}
    return names


class RustupProvider:
    name = "rustup"

    def roots(self, ctx: ScanContext) -> list[Path]:
        return [ctx.home / ".rustup" / "toolchains"]

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        base = ctx.home / ".rustup" / "toolchains"
        if not base.is_dir():
            return
        keep = _used_by_settings(ctx.home)
        cutoffs: list[Path] = []
        ok = True
        for f in find_files(ctx.project_roots, _PIN_FILES, cutoffs=cutoffs):
            channel = _channel(f)
            if channel is None:
                ok = False
            else:
                keep.add(channel)
        known = ok and not cutoffs and bool(ctx.project_roots)
        for tc in sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink()):
            if any(tc.name == k or tc.name.startswith(k + "-") for k in keep):
                continue
            size = ctx.sizer(tc)
            if size < ctx.min_size:
                continue
            yield Item(
                id=f"rustup:{safe_id(tc.name)}",
                label=f"Rust toolchain {tc.name}",
                group="Language runtimes",
                size_bytes=size,
                risk=Risk.REGENERABLE,
                why="Not the rustup default and not named by any rust-toolchain file; 'rustup toolchain install' brings it back."
                if known
                else "Cannot confirm no project pins this: set project_roots in the config (or fix unreadable rust-toolchain files).",
                actions=(RemovePath(tc),) if known else (),
                report_only=not known,
                path=tc,
            )
