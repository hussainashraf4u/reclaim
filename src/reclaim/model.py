from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Risk(str, Enum):
    SAFE = "safe"
    REGENERABLE = "regenerable"
    DATA = "data"


@dataclass(frozen=True)
class RemovePath:
    path: Path


@dataclass(frozen=True)
class Run:
    argv: tuple[str, ...]


Action = RemovePath | Run


@dataclass
class Item:
    id: str
    label: str
    group: str
    size_bytes: int
    risk: Risk
    why: str = ""
    actions: tuple[Action, ...] = ()
    disabled_reason: str | None = None
    report_only: bool = False
    busy: tuple[str, ...] = ()
    path: Path | None = None

    @property
    def executable(self) -> bool:
        return bool(self.actions) and not self.report_only and self.disabled_reason is None

    def to_json(self) -> dict:
        actions = []
        for a in self.actions:
            if isinstance(a, RemovePath):
                actions.append({"type": "remove", "path": str(a.path)})
            else:
                actions.append({"type": "run", "argv": list(a.argv)})
        return {
            "id": self.id,
            "label": self.label,
            "group": self.group,
            "size_bytes": self.size_bytes,
            "risk": self.risk.value,
            "why": self.why,
            "actions": actions,
            "disabled_reason": self.disabled_reason,
            "report_only": self.report_only,
            "executable": self.executable,
            "path": str(self.path) if self.path else None,
        }
