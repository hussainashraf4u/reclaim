from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.model import Item, Risk, Run

_GROUP = "Xcode"


def _size(ctx: ScanContext, device: dict) -> int:
    value = device.get("dataPathSize")
    if isinstance(value, int):
        return value
    path = device.get("dataPath")
    return ctx.sizer(Path(path)) if path else 0


class IosSimProvider:
    name = "ios_sim"

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        if ctx.which("xcrun") is None:
            return
        result = ctx.run(["xcrun", "simctl", "list", "devices", "-j"], timeout=60)
        if result is None or result.returncode != 0:
            return
        try:
            runtimes = json.loads(result.stdout)["devices"]
            if not isinstance(runtimes, dict):
                return
        except (ValueError, KeyError, TypeError):
            return
        unavailable = 0
        unavailable_parent = None
        for runtime, devices in runtimes.items():
            short = runtime.rsplit(".", 1)[-1]
            for device in devices:
                size = _size(ctx, device)
                if device.get("isAvailable") is False or "unavailable" in str(device.get("availability", "")).lower():
                    unavailable += size
                    if device.get("dataPath"):
                        unavailable_parent = Path(device["dataPath"]).parent.parent
                    continue
                if device.get("state") != "Shutdown" or size < max(ctx.min_size, 1):
                    continue
                udid = device.get("udid", "")
                if not udid:
                    continue
                yield Item(
                    id=f"ios-sim:{udid}",
                    label=f"iOS Simulator {device.get('name', udid)} ({short})",
                    group=_GROUP,
                    size_bytes=size,
                    risk=Risk.DATA,
                    why="Holds apps and data installed for testing on this simulator; recreate it in Xcode if needed.",
                    actions=(Run(("xcrun", "simctl", "delete", udid)),),
                    path=Path(device["dataPath"]).parent if device.get("dataPath") else None,
                )
        if unavailable > 0 and unavailable >= ctx.min_size:
            yield Item(
                id="ios-sim-unavailable",
                label="iOS Simulators whose runtime is no longer installed",
                group=_GROUP,
                size_bytes=unavailable,
                risk=Risk.REGENERABLE,
                why="These devices cannot boot; 'simctl delete unavailable' removes them.",
                actions=(Run(("xcrun", "simctl", "delete", "unavailable")),),
                path=unavailable_parent,
            )
