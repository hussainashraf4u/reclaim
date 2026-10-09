from __future__ import annotations

from typing import Iterator

from reclaim.context import ScanContext
from reclaim.model import Item, Risk

_PREFIX = "com.apple.TimeMachine."


class TimeMachineProvider:
    name = "time_machine"

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        if ctx.which("tmutil") is None:
            return
        result = ctx.run(["tmutil", "listlocalsnapshots", "/"], timeout=30)
        if result is None or result.returncode != 0:
            return
        dates = [
            line.strip()[len(_PREFIX):].removesuffix(".local")
            for line in result.stdout.splitlines()
            if line.strip().startswith(_PREFIX)
        ]
        if not dates:
            return
        yield Item(
            id="time-machine-snapshots",
            label=f"Time Machine: {len(dates)} local snapshots",
            group="System",
            size_bytes=0,
            risk=Risk.DATA,
            why=f"Sizes are not reported by macOS. Remove the oldest with: tmutil deletelocalsnapshots {min(dates)}",
            report_only=True,
        )
