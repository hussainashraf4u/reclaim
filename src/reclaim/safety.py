from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class UnsafePath(ValueError):
    pass


_TREE_ROOTS = (
    ".cache",
    ".npm",
    ".bun",
    ".gradle",
    ".Trash",
    "Library/Caches",
    "Library/Logs",
    "Library/pnpm",
    "Library/Developer/Xcode/DerivedData",
    "Library/Developer/Xcode/Archives",
    "Library/Developer/Xcode/iOS DeviceSupport",
    "Library/Application Support/Claude/vm_bundles",
    "Library/Developer/Xcode/watchOS DeviceSupport",
    "Library/Developer/Xcode/tvOS DeviceSupport",
    ".gem/ruby",
    ".local/share/Trash/files",
)

_EXACT_ROOTS = (
    ".cargo/registry",
    ".cargo/git",
    ".m2/repository",
    ".pub-cache/hosted",
    ".pub-cache/git",
    ".fastlane",
    "fvm/cache.git",
    "Library/Developer/CoreSimulator/Caches",
    "Library/Developer/Xcode/UserData/Previews",
    "Library/Application Support/Code/CachedExtensionVSIXs",
    "Library/Application Support/Code/CachedData",
    "Library/Application Support/Cursor/CachedExtensionVSIXs",
    "Library/Application Support/Cursor/CachedData",
    ".expo/versions",
    ".nuget/packages",
    ".bundle/cache",
    ".ivy2/cache",
    ".lmstudio/models",
    ".local/share/pnpm/store",
)


ARTIFACT_NAMES = frozenset({
    "node_modules", ".next", ".turbo", "target", "build", ".dart_tool", ".venv", "venv", "Pods",
})


@dataclass(frozen=True)
class ScopedRoot:
    path: Path
    names: frozenset[str]


def default_roots(home: Path) -> list[Path]:
    return [home / name for name in _TREE_ROOTS + _EXACT_ROOTS]


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _lexical(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _allowed(path: Path, roots: list, exact: set[Path], norm) -> bool:
    for root in roots:
        if isinstance(root, ScopedRoot):
            if norm(root.path) in path.parents and path.name in root.names:
                return True
            continue
        base = norm(root)
        if base in path.parents or (path == base and base in exact):
            return True
    return False


def check_removable(path: Path, roots: list, home: Path) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raise UnsafePath(f"not absolute: {raw}")
    if ".." in raw.parts:
        raise UnsafePath(f"contains '..': {raw}")
    real = _real(raw)
    real_home = _real(home)
    if real == Path(real.anchor) or real == real_home or real in real_home.parents:
        raise UnsafePath(f"refusing home or ancestor: {real}")
    if not _allowed(real, roots, {_real(home / n) for n in _EXACT_ROOTS}, _real):
        raise UnsafePath(f"outside allowlist: {real}")
    lexical = _lexical(raw)
    if not _allowed(lexical, roots, {_lexical(home / n) for n in _EXACT_ROOTS}, _lexical):
        raise UnsafePath(f"outside allowlist (link location): {lexical}")
    return real
