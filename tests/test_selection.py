from reclaim.model import Item, RemovePath, Risk, Run
from reclaim.safety import default_roots
from reclaim.selection import Selection, confirmation_ok, human, summary_text


def mk(id, risk=Risk.SAFE, size=1000, actions=None, **kw):
    acts = (Run(("echo", id)),) if actions is None else actions
    return Item(id=id, label=id.upper(), group="G", size_bytes=size, risk=risk, actions=acts, **kw)


def test_select_safe_only_ticks_safe_executable():
    sel = Selection([mk("a"), mk("b", Risk.REGENERABLE), mk("c", Risk.DATA), mk("d", disabled_reason="busy"), mk("e", actions=())])
    sel.select_safe()
    assert [i.id for i in sel.selected] == ["a"]


def test_toggle_refuses_non_executable():
    sel = Selection([mk("a", report_only=True), mk("b")])
    assert sel.toggle("a") is False and sel.toggle("b") is True
    assert sel.is_ticked("b") and not sel.is_ticked("a")
    assert sel.toggle("b") is True and not sel.is_ticked("b")


def test_selected_bytes_and_data_ids():
    sel = Selection([mk("a", size=10), mk("c", Risk.DATA, size=90)])
    sel.toggle("a")
    sel.toggle("c")
    assert sel.selected_bytes == 100 and sel.data_ids == ["c"]


def test_plan_lines_show_literal_commands_and_flag_unsafe(tmp_path):
    cache = tmp_path / ".npm" / "_cacache"
    cache.mkdir(parents=True)
    victim = tmp_path / "Documents"
    victim.mkdir()
    sel = Selection([
        mk("a", actions=(RemovePath(cache),)),
        mk("b", actions=(RemovePath(victim),)),
    ])
    sel.toggle("a")
    sel.toggle("b")
    lines = sel.plan_lines(default_roots(tmp_path), tmp_path)
    text = "\n".join(lines)
    assert f"rm -rf {cache}" in text and "BLOCKED" in text
    assert cache.exists() and victim.exists()


def test_confirmation_ok():
    assert confirmation_ok("trash, xcode-archives:A", ["trash", "xcode-archives:A"])
    assert not confirmation_ok("trash", ["trash", "x"])
    assert confirmation_ok("", [])


def test_human():
    assert human(0) == "0 B" and human(1536) == "1.5 KB" and human(5 * 1024**3) == "5.0 GB"


def test_summary_counts_only_executable_as_reclaimable():
    items = [mk("a", size=10**9), mk("b", size=2 * 10**9, report_only=True), mk("c", size=10**9, disabled_reason="busy")]
    text = summary_text(items, 0)
    assert text.startswith("3 items") and "reclaimable 953.7 MB" in text and "report-only 2.8 GB" in text
