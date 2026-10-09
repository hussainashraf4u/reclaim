from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from reclaim.sizing import dir_size


def process_running(name: str) -> bool:
    try:
        return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0
    except OSError:
        return False


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str


def run_command(argv, cwd=None, timeout=60) -> RunResult | None:
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True, errors="replace", cwd=cwd, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return RunResult(proc.returncode, proc.stdout)


@dataclass
class ScanContext:
    home: Path
    min_size: int = 0
    which: Callable[[str], str | None] = shutil.which
    sizer: Callable[[Path], int] = dir_size
    is_running: Callable[[str], bool] = process_running
    run: Callable[..., RunResult | None] = run_command
    project_roots: tuple[Path, ...] = ()
    stale_days: int = 30
    large_dir_bytes: int = 1000 * 1024 * 1024
    big_file_bytes: int = 500 * 1024 * 1024
    duplicate_min_bytes: int = 100 * 1024 * 1024
    now: Callable[[], float] = time.time
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
