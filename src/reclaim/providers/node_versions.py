from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath, Risk
from reclaim.providers._walk import find_files, safe_id

HIGHEST = "@highest"
IGNORE = ""
_ALIASES = frozenset({"node", "stable", "latest", "current"})
_NUMERIC = re.compile(r"^v?\d+(\.\d+){0,2}$")
_CODENAMES = {
    "argon": 4, "boron": 6, "carbon": 8, "dubnium": 10, "erbium": 12, "fermium": 14,
    "gallium": 16, "hydrogen": 18, "iron": 20, "jod": 22, "krypton": 24,
}


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in version.lstrip("v").split("."))


def resolve_rule(text: str, home: Path, depth: int = 0) -> str | None:
    t = text.strip()
    if not t or depth > 5 or ".." in t or t.startswith("/"):
        return None
    if _NUMERIC.match(t):
        return t.lstrip("v")
    if t in _ALIASES:
        return HIGHEST
    if t == "system" or t.startswith("iojs"):
        return IGNORE
    alias_file = home / ".nvm" / "alias" / t
    if alias_file.is_file():
        try:
            line = next((l.strip() for l in alias_file.read_text(encoding="utf-8").splitlines() if l.strip()), "")
        except (OSError, UnicodeDecodeError):
            return None
        return resolve_rule(line, home, depth + 1)
    if t.startswith("lts/") and t[4:] in _CODENAMES:
        return str(_CODENAMES[t[4:]])
    return None


def version_kept(rule: str, version: str, highest: str) -> bool:
    if rule == HIGHEST:
        return version == highest
    v = version.lstrip("v")
    return v == rule or v.startswith(rule + ".")


_PIN_FILES = {".nvmrc", ".node-version", ".tool-versions", "package.json"}


def _pins_from(f: Path) -> list[str] | None:
    try:
        text = f.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None if f.name != "package.json" else []
    if f.name == "package.json":
        try:
            node = (json.loads(text).get("volta") or {}).get("node")
        except (ValueError, AttributeError):
            return []
        return [node] if isinstance(node, str) else []
    if f.name == ".tool-versions":
        out = []
        for line in text.splitlines():
            parts = line.split("#")[0].split()
            if len(parts) >= 2 and parts[0] in ("nodejs", "node"):
                out.append(parts[1])
        return out
    lines = [l.strip() for l in text.splitlines()]
    line = next((l for l in lines if l and not l.startswith("#")), "")
    return [line] if line else []


def _read_pins(project_roots: tuple[Path, ...]) -> tuple[list[str], bool]:
    pins: list[str] = []
    cutoffs: list[Path] = []
    ok = True
    for f in find_files(project_roots, _PIN_FILES, cutoffs=cutoffs):
        found = _pins_from(f)
        if found is None:
            ok = False
        else:
            pins.extend(found)
    return pins, ok and not cutoffs


class _Manager:
    def __init__(self, name: str, versions_dir: Path, default: str | None):
        self.name = name
        self.versions_dir = versions_dir
        self.default = default


def _managers(home: Path) -> list[_Manager]:
    out: list[_Manager] = []
    nvm = home / ".nvm"
    default_file = nvm / "alias" / "default"
    default = "default" if default_file.is_file() else None
    out.append(_Manager("nvm", nvm / "versions" / "node", default))
    for label, base in (("fnm", home / "Library" / "Application Support" / "fnm"), ("fnm-xdg", home / ".local" / "share" / "fnm")):
        link = base / "aliases" / "default"
        default = None
        if link.is_symlink():
            target = link.resolve()
            default = target.parent.name if target.name == "installation" else target.name
        out.append(_Manager(label, base / "node-versions", default))
    return out


class NodeVersionsProvider:
    name = "node_versions"

    def roots(self, ctx: ScanContext) -> list[Path]:
        return [m.versions_dir for m in _managers(ctx.home)]

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        managers = [m for m in _managers(ctx.home) if m.versions_dir.is_dir()]
        if not managers:
            return
        pins, pins_ok = _read_pins(ctx.project_roots)
        resolved = [resolve_rule(p, ctx.home) for p in pins]
        pins_known = pins_ok and all(r is not None for r in resolved)
        for manager in managers:
            installed = sorted((p for p in manager.versions_dir.iterdir() if p.is_dir()), key=lambda p: _key(p.name))
            if not installed:
                continue
            highest = installed[-1].name
            default_rule = resolve_rule(manager.default, ctx.home) if manager.default else None
            rules = {r for r in resolved if r}
            if default_rule:
                rules.add(default_rule)
            active = ctx.env.get("NVM_BIN") if manager.name == "nvm" else None
            if active and manager.versions_dir in Path(active).parents:
                rules.add(Path(active).relative_to(manager.versions_dir).parts[0].lstrip("v"))
            known = pins_known and bool(ctx.project_roots) and default_rule is not None
            for version in installed:
                if any(version_kept(rule, version.name, highest) for rule in rules):
                    continue
                size = ctx.sizer(version)
                if size < ctx.min_size:
                    continue
                yield Item(
                    id=f"node:{manager.name}:{safe_id(version.name)}",
                    label=f"Node {version.name} ({manager.name})",
                    group="Node versions",
                    size_bytes=size,
                    risk=Risk.REGENERABLE,
                    why="Not the default and not pinned by any .nvmrc or .node-version; reinstall on demand."
                    if known
                    else "Cannot confirm it is unused: set project_roots, a resolvable default version, and use plain version pins.",
                    actions=(RemovePath(version),) if known else (),
                    report_only=not known,
                    path=version,
                )
