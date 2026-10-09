import io
import json

from reclaim.catalog import parse_entries
from reclaim.cli import main
from reclaim.context import ScanContext


def test_scan_only_json(tmp_path):
    (tmp_path / "x").mkdir()
    entries = parse_entries('[[item]]\nid="x"\nlabel="X"\ngroup="G"\nrisk="safe"\npath="~/x"\n')
    ctx = ScanContext(home=tmp_path, which=lambda n: "/x", sizer=lambda p: 123, is_running=lambda n: False)
    out = io.StringIO()
    code = main(["--scan-only", "--json", "--min-size-mb", "0"], out=out, ctx=ctx, entries=entries, providers=[])
    data = json.loads(out.getvalue())
    assert code == 0 and data["version"] == 1
    assert data["items"][0]["id"] == "x" and data["items"][0]["size_bytes"] == 123
    assert isinstance(data["free_bytes"], int) and data["errors"] == []


def test_launches_tui_without_scan_only(tmp_path):
    launched = {}
    ctx = ScanContext(home=tmp_path)
    code = main([], out=io.StringIO(), ctx=ctx, entries=[], providers=[], launch=lambda **kw: launched.update(kw))
    assert code == 0 and launched["dry_run"] is False


def test_min_size_flag_sets_context(tmp_path):
    ctx = ScanContext(home=tmp_path)
    main(["--scan-only", "--json", "--min-size-mb", "50"], out=io.StringIO(), ctx=ctx, entries=[], providers=[])
    assert ctx.min_size == 50 * 1024 * 1024
