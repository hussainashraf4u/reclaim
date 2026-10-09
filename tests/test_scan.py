from pathlib import Path

from reclaim.catalog import parse_entries
from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath, Risk
from reclaim.scan import dedupe, scan_all


def mk(id, size, path=None):
    return Item(id=id, label=id, group="G", size_bytes=size, risk=Risk.SAFE,
                actions=(RemovePath(Path(path)),) if path else ())


class Good:
    name = "good"

    def scan(self, ctx):
        yield mk("g1", 5)
        yield mk("g2", 50)


class Bad:
    name = "bad"

    def scan(self, ctx):
        raise RuntimeError("daemon down")


def ctx(tmp_path):
    return ScanContext(home=tmp_path, which=lambda n: "/x", sizer=lambda p: 7, is_running=lambda n: False)


def test_sorted_by_size_desc_and_errors_isolated(tmp_path):
    res = scan_all(ctx(tmp_path), [], [Good(), Bad()])
    assert [i.id for i in res.items] == ["g2", "g1"]
    assert res.errors == ["bad: daemon down"]


def test_on_items_streams_batches(tmp_path):
    seen = []
    scan_all(ctx(tmp_path), [], [Good()], on_items=seen.append)
    assert sum(len(b) for b in seen) == 2


def test_catalog_entries_are_scanned(tmp_path):
    (tmp_path / "x").mkdir()
    entries = parse_entries('[[item]]\nid="x"\nlabel="X"\ngroup="G"\nrisk="safe"\npath="~/x"\n')
    res = scan_all(ctx(tmp_path), entries)
    assert [i.id for i in res.items] == ["x"] and res.items[0].size_bytes == 7


def test_dedupe_keeps_larger_parent_only():
    parent = mk("p", 100, "/h/.cache/uv")
    child = mk("c", 10, "/h/.cache/uv/wheels")
    same = mk("s", 100, "/h/.cache/uv")
    other = mk("o", 5, "/h/.npm")
    out = dedupe([parent, child, same, other])
    assert [i.id for i in out] == ["p", "o"]
