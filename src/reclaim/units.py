from __future__ import annotations

import re

_SIZE = re.compile(r"^\s*([\d.]+)\s*([kKMGT]?B)")
_UNITS = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}


def parse_size(text: str) -> int:
    m = _SIZE.match(text or "")
    if not m:
        return 0
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])
