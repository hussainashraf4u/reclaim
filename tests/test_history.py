import io
import json

from reclaim.audit import append, read_log, summarize
from reclaim.cli import main
from reclaim.context import ScanContext
from reclaim.executor import Result


def test_read_log_skips_garbage_and_summarizes(tmp_path):
    log = tmp_path / "log.jsonl"
    append(log, Result("a", True, ("rm",)), freed_bytes=2048)
    append(log, Result("b", False, ("boom",)))
    with log.open("a") as fh:
        fh.write("not json\n{}\n")
    rows = read_log(log)
    assert [r.item for r in rows] == ["a", "b"]
    total = summarize(rows)
    assert (total.runs, total.failures, total.freed_bytes) == (2, 1, 2048)


def test_read_log_missing_file_is_empty(tmp_path):
    assert read_log(tmp_path / "nope.jsonl") == []


def test_history_command_text_and_json(tmp_path):
    log = tmp_path / ".local" / "state" / "reclaim" / "log.jsonl"
    append(log, Result("npm-cache", True, ()), freed_bytes=5 * 1024**2)
    ctx = ScanContext(home=tmp_path)
    out = io.StringIO()
    assert main(["history"], out=out, ctx=ctx, entries=[], providers=[]) == 0
    assert "npm-cache" in out.getvalue() and "5.0 MB freed in total" in out.getvalue()
    out = io.StringIO()
    main(["history", "--json"], out=out, ctx=ScanContext(home=tmp_path), entries=[], providers=[])
    data = json.loads(out.getvalue())
    assert data["runs"] == 1 and data["freed_bytes"] == 5 * 1024**2


def test_history_with_no_log(tmp_path):
    out = io.StringIO()
    main(["history"], out=out, ctx=ScanContext(home=tmp_path), entries=[], providers=[])
    assert "No cleanups" in out.getvalue()
