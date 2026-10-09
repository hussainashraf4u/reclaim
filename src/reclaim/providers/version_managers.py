from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath, Risk
from reclaim.providers._walk import find_files, safe_id

GROUP = "Language runtimes"


@dataclass(frozen=True)
class ManagerSpec:
    key: str
    label: str
    versions: tuple[str, ...]  # path below home to the dir holding installed versions
    global_files: tuple[str, ...]  # files below home naming the global default
    pin_files: tuple[str, ...]  # per-project files naming a version
    layout: str = "flat"  # flat: versions/<ver>; tools: installs/<tool>/<ver>


SPECS = (
    ManagerSpec("pyenv", "Python", (".pyenv", "versions"), (".pyenv/version",), (".python-version",)),
    ManagerSpec("rbenv", "Ruby", (".rbenv", "versions"), (".rbenv/version",), (".ruby-version",)),
    ManagerSpec("asdf", "asdf", (".asdf", "installs"), (".tool-versions",), (".tool-versions",), "tools"),
    ManagerSpec(
        "mise", "mise", (".local", "share", "mise", "installs"), (".tool-versions",), (".tool-versions", ".mise.toml", "mise.toml"), "tools"
    ),
    ManagerSpec("sdkman", "SDKMAN", (".sdkman", "candidates"), (), (".sdkmanrc",), "tools"),
)

_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


def _tokens(text: str) -> set[str]:
    return {t for t in _VERSION.findall(text)}


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _pins(spec: ManagerSpec, ctx: ScanContext) -> tuple[set[str], bool]:
    """Every version string some config names, and whether all pin files were readable."""
    pins: set[str] = set()
    ok = True
    for rel in spec.global_files:
        text = _read(ctx.home / rel)
        if text is not None:
            pins |= _tokens(text)
    cutoffs: list[Path] = []
    for f in find_files(ctx.project_roots, set(spec.pin_files), cutoffs=cutoffs):
        text = _read(f)
        if text is None:
            ok = False
            continue
        pins |= _tokens(text)
    return pins, ok and not cutoffs


def _is_pinned(version: str, pins: set[str]) -> bool:
    return any(version == p or version.startswith(p + ".") for p in pins)


def _children(path: Path) -> list[Path]:
    try:
        return sorted(p for p in path.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return []


class VersionManagersProvider:
    name = "version_managers"

    def roots(self, ctx: ScanContext) -> list[Path]:
        return [ctx.home.joinpath(*s.versions) for s in SPECS]

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        for spec in SPECS:
            base = ctx.home.joinpath(*spec.versions)
            if not base.is_dir():
                continue
            pins, ok = _pins(spec, ctx)
            known = ok and bool(ctx.project_roots)
            yield from self._scan_manager(spec, base, pins, known, ctx)

    def _scan_manager(self, spec: ManagerSpec, base: Path, pins: set[str], known: bool, ctx: ScanContext) -> Iterator[Item]:
        if spec.layout == "tools":
            entries = [(tool.name, v) for tool in _children(base) for v in _children(tool)]
        else:
            entries = [("", v) for v in _children(base)]
        for tool, version in entries:
            if version.name in {"current", "system"} or _is_pinned(version.name, pins):
                continue
            size = ctx.sizer(version)
            if size < ctx.min_size:
                continue
            label = f"{tool} {version.name}".strip() if tool else f"{spec.label} {version.name}"
            yield Item(
                id=f"{spec.key}:{safe_id(tool + '-' if tool else '')}{safe_id(version.name)}",
                label=f"{label} ({spec.key})",
                group=GROUP,
                size_bytes=size,
                risk=Risk.REGENERABLE,
                why=f"Not the global default and not pinned by any project; {spec.key} install brings it back."
                if known
                else "Cannot confirm no project pins this: set project_roots in the config (or fix unreadable pin files).",
                actions=(RemovePath(version),) if known else (),
                report_only=not known,
                path=version,
            )
