from __future__ import annotations

from collections.abc import Iterator

from reclaim.context import ScanContext
from reclaim.model import Item, Risk

# (id, label, path below home, how to shrink it)
_VMS = (
    ("colima", "Colima VM disk", (".colima",), "Run 'colima delete' to remove the VM, or 'docker system prune' inside it first."),
    ("lima", "Lima VM disks", (".lima",), "Run 'limactl delete <name>' for instances you no longer use."),
    ("orbstack", "OrbStack data", (".orbstack",), "Run 'orb reset' or prune images and volumes in OrbStack."),
    (
        "podman",
        "Podman machine disks",
        (".local", "share", "containers", "podman", "machine"),
        "Run 'podman machine rm' for machines you no longer use.",
    ),
)


class VmDisksProvider:
    """Container VM disk images. Report only: they hold images, volumes and databases."""

    name = "vm_disks"

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        for key, label, parts, advice in _VMS:
            path = ctx.home.joinpath(*parts)
            if not path.is_dir():
                continue
            size = ctx.sizer(path)
            if size < ctx.min_size:
                continue
            yield Item(
                id=f"vm:{key}",
                label=label,
                group="Containers",
                size_bytes=size,
                risk=Risk.DATA,
                why=f"May contain images, volumes and databases. {advice}",
                report_only=True,
                path=path,
            )
