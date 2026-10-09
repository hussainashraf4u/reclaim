from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath, Risk
from reclaim.providers._walk import find_files

QEMU_PROCESSES = ("qemu-system-aarch64", "qemu-system-x86_64")
_OVERLAYS = ("userdata-qemu.img.qcow2", "encryption-key.img.qcow2", "encryptionkey.img.qcow2", "cache.img.qcow2", "snapshots")
_NDK_LINE = re.compile(r'val ndkVersion: String = "([^"]+)"')
_GRADLE_NDK = re.compile(r"ndkVersion\s*=?\s*[\"']([\d.]+)[\"']")
_KOTLIN = Path("packages/flutter_tools/gradle/src/main/kotlin/FlutterExtension.kt")


def required_ndks(home: Path, project_roots: tuple[Path, ...] = ()) -> tuple[set[str], bool]:
    found: set[str] = set()
    ok = True
    versions = home / "fvm" / "versions"
    if versions.is_dir():
        for flutter in sorted(versions.iterdir()):
            try:
                m = _NDK_LINE.search((flutter / _KOTLIN).read_text(encoding="utf-8"))
            except OSError:
                continue
            if m:
                found.add(m.group(1))
    cutoffs: list[Path] = []
    for gradle in find_files(project_roots, {"build.gradle", "build.gradle.kts"}, cutoffs=cutoffs):
        try:
            found.update(_GRADLE_NDK.findall(gradle.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            ok = False
    return found, ok and not cutoffs


def _safe(name: str) -> str:
    if any(c.isspace() or c == "," for c in name):
        return hashlib.sha1(name.encode()).hexdigest()[:8]
    return name


class AndroidProvider:
    name = "android"

    def roots(self, ctx: ScanContext) -> list[Path]:
        return [ctx.home / ".android" / "avd", ctx.home / "Library" / "Android" / "sdk" / "ndk"]

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        yield from self._avds(ctx)
        yield from self._ndks(ctx)

    def _avds(self, ctx: ScanContext) -> Iterator[Item]:
        base = ctx.home / ".android" / "avd"
        if not base.is_dir():
            return
        for avd in sorted(base.glob("*.avd")):
            targets = [avd / name for name in _OVERLAYS if (avd / name).exists()]
            size = sum(ctx.sizer(t) for t in targets)
            if not targets or size < ctx.min_size:
                continue
            running = next((p for p in QEMU_PROCESSES if ctx.is_running(p)), None)
            yield Item(
                id=f"avd:{_safe(avd.stem)}",
                label=f"AVD {avd.stem}: userdata overlay and snapshots",
                group="Android",
                size_bytes=size,
                risk=Risk.DATA,
                why="Wipes installed apps and logins; the AVD config.ini is kept and the first boot is slow.",
                actions=tuple(RemovePath(t) for t in targets),
                disabled_reason=f"{running} is running" if running else None,
                busy=QEMU_PROCESSES,
            )

    def _ndks(self, ctx: ScanContext) -> Iterator[Item]:
        base = ctx.home / "Library" / "Android" / "sdk" / "ndk"
        if not base.is_dir():
            return
        required, pins_ok = required_ndks(ctx.home, ctx.project_roots)
        known = bool(required) and pins_ok
        for ndk in sorted(p for p in base.iterdir() if p.is_dir()):
            if ndk.name in required:
                continue
            size = ctx.sizer(ndk)
            if size < ctx.min_size:
                continue
            yield Item(
                id=f"ndk:{_safe(ndk.name)}",
                label=f"Android NDK {ndk.name}",
                group="Android",
                size_bytes=size,
                risk=Risk.REGENERABLE,
                why="Not required by any installed Flutter version; reinstall with sdkmanager if needed."
                if known
                else "No Flutter install found to check against, so this is shown but not removable.",
                actions=(RemovePath(ndk),) if known else (),
                report_only=not known,
            )
