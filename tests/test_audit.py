import json
from datetime import UTC, datetime

from reclaim.audit import append, default_log_path
from reclaim.executor import Result


def test_append_writes_jsonl(tmp_path):
    log = default_log_path(tmp_path)
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    append(log, Result("a", True, ("rm -rf /x",)), freed_bytes=5, now=now)
    append(log, Result("b", False, ("boom",)), now=now)
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    assert lines[0] == {"ts": "2026-10-09T12:00:00+00:00", "item": "a", "ok": True, "log": ["rm -rf /x"], "freed_bytes": 5}
    assert lines[1]["ok"] is False and lines[1]["freed_bytes"] is None
    assert log == tmp_path / ".local" / "state" / "reclaim" / "log.jsonl"
