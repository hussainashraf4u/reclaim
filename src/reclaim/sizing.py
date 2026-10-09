from __future__ import annotations

import os
import stat
import threading
from pathlib import Path


def _allocated(st: os.stat_result) -> int:
    return st.st_blocks * 512


def dir_size(path: Path) -> int:
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return _allocated(st)
    total = _allocated(st)
    stack = [str(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        s = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    total += _allocated(s)
                    if stat.S_ISDIR(s.st_mode):
                        stack.append(entry.path)
        except OSError:
            continue
    return total


class TreeSizer:
    def __init__(self, record_depth: int = 3):
        self._cache: dict[str, int] = {}
        self._lock = threading.Lock()
        self._record_depth = record_depth

    def __call__(self, path: Path) -> int:
        key = str(path)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        try:
            st = os.lstat(key)
        except OSError:
            return 0
        if not stat.S_ISDIR(st.st_mode):
            return _allocated(st)
        total = self._walk(key, _allocated(st))
        return total

    def _walk(self, root: str, root_size: int) -> int:
        dirs: list[tuple[str, int, int]] = [(root, -1, 0)]
        totals: list[int] = [root_size]
        i = 0
        while i < len(dirs):
            current, _, depth = dirs[i]
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            s = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if stat.S_ISDIR(s.st_mode):
                            dirs.append((entry.path, i, depth + 1))
                            totals.append(_allocated(s))
                        else:
                            totals[i] += _allocated(s)
            except OSError:
                pass
            i += 1
        for idx in range(len(dirs) - 1, 0, -1):
            totals[dirs[idx][1]] += totals[idx]
        with self._lock:
            for idx, (path, _, depth) in enumerate(dirs):
                if depth <= self._record_depth:
                    self._cache[path] = totals[idx]
        return totals[0]
