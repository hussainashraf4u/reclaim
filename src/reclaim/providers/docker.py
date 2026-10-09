from __future__ import annotations

import json
from typing import Iterator

from reclaim.context import ScanContext
from reclaim.model import Item, Risk, Run
from reclaim.units import parse_size

_GROUP = "Docker"


def _down() -> Item:
    return Item(
        id="docker-down",
        label="Docker (daemon not reachable)",
        group=_GROUP,
        size_bytes=0,
        risk=Risk.SAFE,
        why="Start Docker Desktop to see reclaimable images, build cache and volumes.",
        disabled_reason="Docker is not running",
        report_only=True,
    )


class DockerProvider:
    name = "docker"

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        if ctx.which("docker") is None:
            return
        summary = ctx.run(["docker", "system", "df", "--format", "{{json .}}"], timeout=30)
        if summary is None or summary.returncode != 0:
            yield _down()
            return
        try:
            rows = {
                row["Type"]: row
                for row in (json.loads(line) for line in summary.stdout.splitlines() if line.strip())
            }
        except (ValueError, KeyError, TypeError):
            yield _down()
            return
        note = self._context_note(ctx)
        yield from self._pruneables(ctx, rows, note)
        yield from self._dangling_volumes(ctx, note)

    def _context_note(self, ctx: ScanContext) -> str:
        result = ctx.run(["docker", "context", "show"], timeout=10)
        name = result.stdout.strip() if result is not None and result.returncode == 0 else ""
        if name and name not in ("default", "desktop-linux", "orbstack", "colima"):
            return f" (docker context: {name})"
        return ""

    def _pruneables(self, ctx: ScanContext, rows: dict, note: str) -> Iterator[Item]:
        specs = (
            ("Build Cache", "docker-build-cache", "Docker build cache", Risk.REGENERABLE,
             ("docker", "builder", "prune", "-a", "-f"), "Layers cached from previous builds; rebuilt on demand."),
            ("Images", "docker-images-unused", "Docker images not used by any container", Risk.DATA,
             ("docker", "image", "prune", "-a", "-f"), "Pulled images can be fetched again, but locally built or patched images cannot."),
            ("Containers", "docker-containers-stopped", "Stopped Docker containers", Risk.REGENERABLE,
             ("docker", "container", "prune", "-f"), "Exited containers; their writable layer and run configuration are lost, volumes are kept."),
        )
        for key, item_id, label, risk, argv, why in specs:
            size = parse_size(str(rows.get(key, {}).get("Reclaimable", "")).split(" (")[0])
            if size > 0 and size >= ctx.min_size:
                yield Item(id=item_id, label=label, group=_GROUP, size_bytes=size, risk=risk, why=why + note, actions=(Run(argv),))

    def _dangling_volumes(self, ctx: ScanContext, note: str) -> Iterator[Item]:
        verbose = ctx.run(["docker", "system", "df", "-v", "--format", "json"], timeout=60)
        volumes = None
        if verbose is not None and verbose.returncode == 0:
            try:
                volumes = json.loads(verbose.stdout).get("Volumes") or []
            except (ValueError, AttributeError):
                volumes = None
        if volumes is None:
            yield Item(
                id="docker-volumes-unknown",
                label="Docker volumes (could not be inspected)",
                group=_GROUP,
                size_bytes=0,
                risk=Risk.SAFE,
                why="'docker system df -v' failed or timed out, so unattached volumes are not listed." + note,
                report_only=True,
            )
            return
        for vol in volumes:
            name = vol.get("Name", "")
            size = parse_size(str(vol.get("Size", "")))
            if str(vol.get("Links")) != "0" or not name or size <= 0 or size < ctx.min_size:
                continue
            yield Item(
                id=f"docker-volume:{name}",
                label=f"Docker volume {name} (not attached to any container)",
                group=_GROUP,
                size_bytes=size,
                risk=Risk.DATA,
                why="May hold a database or app data; check before removing." + note,
                actions=(Run(("docker", "volume", "rm", name)),),
            )
