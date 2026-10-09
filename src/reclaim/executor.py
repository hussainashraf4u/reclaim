from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reclaim.context import process_running
from reclaim.model import Action, Item, RemovePath
from reclaim.safety import UnsafePath, check_removable


class NotExecutable(RuntimeError):
    pass


@dataclass(frozen=True)
class Result:
    item_id: str
    ok: bool
    log: tuple[str, ...]
    measured: bool = False


def describe(action: Action) -> str:
    if isinstance(action, RemovePath):
        return f"rm -rf {shlex.quote(str(action.path))}"
    return shlex.join(action.argv)


def _rmtree(path: Path) -> list[str]:
    errors: list[str] = []
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=lambda func, p, exc: errors.append(f"{p}: {exc}"))
    else:
        shutil.rmtree(path, onerror=lambda func, p, info: errors.append(f"{p}: {info[1]}"))
    return errors


def run_item(
    item: Item,
    roots: list[Path],
    home: Path,
    dry_run: bool = False,
    runner=subprocess.run,
    is_running: Callable[[str], bool] = process_running,
) -> Result:
    if not item.executable:
        raise NotExecutable(item.id)
    log: list[str] = []
    measured = True
    for name in item.busy:
        if is_running(name):
            return Result(item.id, False, (f"{name} is running",))
    for action in item.actions:
        try:
            if isinstance(action, RemovePath):
                real = check_removable(action.path, roots, home)
                log.append(describe(action))
                if dry_run:
                    continue
                existed = action.path.is_symlink() or real.exists()
                measured = measured and existed
                if action.path.is_symlink():
                    action.path.unlink()
                elif real.is_dir():
                    errors = _rmtree(real)
                    if errors:
                        log.append(f"{len(errors)} entries could not be removed")
                        log.extend(errors[:3])
                        return Result(item.id, False, tuple(log))
                elif real.exists():
                    real.unlink()
            else:
                log.append(describe(action))
                measured = False
                if dry_run:
                    continue
                proc = runner(list(action.argv), capture_output=True, text=True, errors="replace", timeout=1800)
                if proc.returncode != 0:
                    log.append((proc.stderr or "").strip()[:500])
                    return Result(item.id, False, tuple(log))
        except subprocess.TimeoutExpired:
            log.append("timed out")
            return Result(item.id, False, tuple(log))
        except (UnsafePath, OSError, subprocess.SubprocessError, ValueError) as exc:
            log.append(str(exc))
            return Result(item.id, False, tuple(log))
    return Result(item.id, True, tuple(log), measured=measured and not dry_run)
