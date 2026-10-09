from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, Risk, Run


class HomebrewProvider:
    """Formulae installed only as dependencies that nothing needs any more (`brew autoremove`)."""

    name = "homebrew"

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        if ctx.which("brew") is None:
            return
        preview = ctx.run(["brew", "autoremove", "--dry-run"], timeout=60)
        if preview is None or preview.returncode != 0:
            return
        names = _formulae(preview.stdout)
        if not names:
            return
        cellar = ctx.run(["brew", "--cellar"], timeout=20)
        base = Path(cellar.stdout.strip()) if cellar and cellar.returncode == 0 and cellar.stdout.strip() else None
        size = sum(ctx.sizer(base / n) for n in names if (base / n).exists()) if base is not None else 0
        if size < ctx.min_size:
            return
        shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
        yield Item(
            id="homebrew-autoremove",
            label=f"Homebrew: {len(names)} unused dependencies",
            group="System",
            size_bytes=size,
            risk=Risk.REGENERABLE,
            why=f"Installed only as dependencies and no longer needed: {shown}. 'brew install' restores any of them.",
            actions=(Run(("brew", "autoremove")),),
        )


def _formulae(text: str) -> list[str]:
    names: list[str] = []
    collecting = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("==>"):
            collecting = "autoremove" in line.lower()
            continue
        if collecting and line and " " not in line:
            names.append(line)
    return names
