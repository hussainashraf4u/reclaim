from __future__ import annotations

import re
from typing import Iterator

from reclaim.context import ScanContext
from reclaim.model import Item, Risk, Run
from reclaim.providers._walk import safe_id
from reclaim.units import parse_size


class OllamaProvider:
    name = "ollama"

    def scan(self, ctx: ScanContext) -> Iterator[Item]:
        if ctx.which("ollama") is None:
            return
        models = ctx.home / ".ollama" / "models"
        if not ctx.is_running("ollama"):
            if models.is_dir():
                yield Item(
                    id="ollama-down",
                    label="Ollama models (server not running)",
                    group="Models",
                    size_bytes=0,
                    risk=Risk.SAFE,
                    why="Start the Ollama server to list models; scanning does not launch it.",
                    disabled_reason="Ollama server is not running",
                    report_only=True,
                )
            return
        result = ctx.run(["ollama", "list"], timeout=20)
        if result is None or result.returncode != 0:
            return
        groups: dict[str, tuple[list[str], int]] = {}
        for line in result.stdout.splitlines()[1:]:
            cols = re.split(r"\s{2,}", line.strip())
            if len(cols) < 3 or not cols[0]:
                continue
            tags, size = groups.get(cols[1], ([], parse_size(cols[2])))
            tags.append(cols[0])
            groups[cols[1]] = (tags, size)
        for tags, size in groups.values():
            if size <= 0 or size < ctx.min_size:
                continue
            extra = f" (also tagged {', '.join(tags[1:])})" if len(tags) > 1 else ""
            yield Item(
                id=f"ollama:{safe_id(tags[0])}",
                label=f"Ollama model {tags[0]}{extra}",
                group="Models",
                size_bytes=size,
                risk=Risk.REGENERABLE,
                why="Downloaded model weights; 'ollama pull' fetches them again.",
                actions=tuple(Run(("ollama", "rm", t)) for t in tags),
                path=models,
            )
