import io
import json

from reclaim import scancache
from reclaim.catalog import parse_entries
from reclaim.cli import main
from reclaim.context import ScanContext
from reclaim.model import Item, Risk


def item(i, size):
    return Item(id=i, label=i, group="G", size_bytes=size, risk=Risk.SAFE)


def test_roundtrip_and_annotate(tmp_path):
    path = tmp_path / "c" / "last.json"
    scancache.save(path, [item("a", 100), item("b", 50)], now=1.0)
    prev = scancache.load(path)
    assert prev == {"a": 100, "b": 50}
    now = [item("a", 160), item("c", 5)]
    scancache.annotate(prev, now)
    assert now[0].delta_bytes == 60 and now[1].delta_bytes is None


def test_load_tolerates_corrupt_cache(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{oops")
    assert scancache.load(path) == {}
    path.write_text(json.dumps({"sizes": []}))
    assert scancache.load(path) == {}


def test_scan_only_reports_delta_on_second_run(tmp_path):
    (tmp_path / "x").mkdir()
    entries = parse_entries('[[item]]\nid="x"\nlabel="X"\ngroup="G"\nrisk="safe"\npath="~/x"\n')
    sizes = iter([1000, 3 * 1024**2])
    ctx = ScanContext(home=tmp_path, which=lambda n: "/x", sizer=lambda p: next(sizes), is_running=lambda n: False)
    main(["--scan-only", "--min-size-mb", "0"], out=io.StringIO(), ctx=ctx, entries=entries, providers=[])
    out = io.StringIO()
    ctx2 = ScanContext(home=tmp_path, which=lambda n: "/x", sizer=lambda p: 3 * 1024**2, is_running=lambda n: False)
    main(["--scan-only", "--min-size-mb", "0"], out=out, ctx=ctx2, entries=entries, providers=[])
    assert "since last scan" in out.getvalue()
