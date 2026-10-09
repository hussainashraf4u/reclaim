from textual.widgets import DataTable

from reclaim.model import Item, Risk, Run
from reclaim.tui import PlanScreen, ReclaimApp


def items():
    return [
        Item(id="a", label="Alpha", group="G", size_bytes=5 * 1024**3, risk=Risk.SAFE, actions=(Run(("echo", "a")),)),
        Item(id="b", label="Beta", group="G", size_bytes=1024**3, risk=Risk.DATA, actions=(Run(("echo", "b")),)),
        Item(id="c", label="Gamma", group="G", size_bytes=1024**2, risk=Risk.SAFE, actions=(Run(("echo", "c")),), disabled_reason="Xcode is running"),
    ]


def feed(on_items):
    on_items(items())


def make(tmp_path, dry_run=True):
    return ReclaimApp(scan=feed, roots=[], home=tmp_path, dry_run=dry_run, log_path=tmp_path / "log.jsonl")


async def test_rows_render_and_space_toggles(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.row_count == 4
        await pilot.press("down")
        await pilot.press("space")
        assert [i.id for i in app.selection.selected] == ["a"]


async def test_a_selects_all_safe_and_c_opens_plan(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, PlanScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, PlanScreen)


async def test_confirm_runs_selected_and_writes_audit_log(tmp_path):
    app = make(tmp_path, dry_run=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("space")
        await pilot.press("c")
        await pilot.pause()
        await pilot.click("#run")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
    rows = (tmp_path / "log.jsonl").read_text().splitlines()
    assert len(rows) == 1 and '"item": "a"' in rows[0] and '"ok": true' in rows[0]


async def test_data_item_needs_typed_confirmation(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.selection.toggle("b")
        await pilot.press("c")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlanScreen) and screen.needed == ["b"]


async def test_overlapping_items_are_deduped_in_the_view(tmp_path):
    from pathlib import Path

    from reclaim.model import RemovePath
    parent = Item(id="p", label="Parent", group="G", size_bytes=100, risk=Risk.SAFE, actions=(RemovePath(Path("/h/.cache/uv")),))
    child = Item(id="c", label="Child", group="G", size_bytes=10, risk=Risk.SAFE, actions=(RemovePath(Path("/h/.cache/uv/wheels")),))

    def scan(on_items):
        on_items([parent])
        on_items([child])

    app = ReclaimApp(scan=scan, roots=[], home=tmp_path, dry_run=True, log_path=tmp_path / "log.jsonl")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 2
        assert [i.id for i in app.items] == ["p"]


async def test_large_folders_covered_by_other_items_are_hidden_in_the_view(tmp_path):
    from pathlib import Path
    cat = Item(id="uv", label="uv", group="Python", size_bytes=100, risk=Risk.SAFE, actions=(Run(("uv", "cache", "clean")),), path=Path("/h/.cache/uv"))
    big = Item(id="large:1", label="uv dir", group="Large folders", size_bytes=100, risk=Risk.SAFE, report_only=True, path=Path("/h/.cache/uv"))

    def scan(on_items):
        on_items([big])
        on_items([cat])

    app = ReclaimApp(scan=scan, roots=[], home=tmp_path, dry_run=True, log_path=tmp_path / "log.jsonl")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert [i.id for i in app.items] == ["uv"]


def grouped_items():
    return [
        Item(id="a", label="Alpha", group="One", size_bytes=3 * 1024**3, risk=Risk.SAFE, actions=(Run(("echo", "a")),)),
        Item(id="b", label="Beta", group="Two", size_bytes=2 * 1024**3, risk=Risk.SAFE, actions=(Run(("echo", "b")),)),
        Item(id="c", label="Gamma", group="Two", size_bytes=1024**3, risk=Risk.DATA, actions=(Run(("echo", "c")),)),
    ]


def make_grouped(tmp_path, **kw):
    kw.setdefault("dry_run", True)
    return ReclaimApp(scan=lambda on: on(grouped_items()), roots=[], home=tmp_path, log_path=tmp_path / "log.jsonl", **kw)


async def test_rows_are_grouped_with_headers_ordered_by_group_size(tmp_path):
    app = make_grouped(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        keys = [row.key.value for row in table.ordered_rows]
        assert keys == ["group:One", "a", "group:Two", "b", "c"]


async def test_space_on_group_header_toggles_the_groups_non_data_items(tmp_path):
    app = make_grouped(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "down", "space")
        assert [i.id for i in app.selection.selected] == ["b"]


async def test_filter_hides_non_matching_items_and_empty_groups(tmp_path):
    app = make_grouped(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.set_filter("gamma")
        await pilot.pause()
        keys = [row.key.value for row in app.query_one(DataTable).ordered_rows]
        assert keys == ["group:Two", "c"]
        app.set_filter("")
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 5


async def test_enter_opens_details_and_escape_closes(tmp_path):
    from reclaim.tui import DetailScreen
    app = make_grouped(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        assert "Alpha" in "\n".join(app.screen.lines)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, DetailScreen)


async def test_plan_shows_expected_freed_total(tmp_path):
    app = make_grouped(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.selection.toggle("a")
        await pilot.press("c")
        await pilot.pause()
        assert any(line.startswith("Expected to free: 3.0 GB") for line in app.screen.lines)


async def test_executing_flag_blocks_selection_and_plan(tmp_path):
    app = make_grouped(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.executing = True
        await pilot.press("down", "space", "a")
        assert app.selection.selected == []
        await pilot.press("c")
        await pilot.pause()
        assert not isinstance(app.screen, PlanScreen)


async def test_cleaned_items_leave_the_list_after_a_real_run(tmp_path):
    app = make_grouped(tmp_path, dry_run=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.selection.toggle("a")
        await pilot.press("c")
        await pilot.pause()
        await pilot.click("#run")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "a" not in [i.id for i in app.items] and app.executing is False


async def test_dry_run_keeps_items_in_the_list(tmp_path):
    app = make_grouped(tmp_path, dry_run=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.selection.toggle("a")
        await pilot.press("c")
        await pilot.pause()
        await pilot.click("#run")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "a" in [i.id for i in app.items]


async def test_scan_errors_are_shown(tmp_path):
    def scan(on_items):
        on_items(grouped_items())
        return ["docker: boom"]

    app = ReclaimApp(scan=scan, roots=[], home=tmp_path, dry_run=True, log_path=tmp_path / "log.jsonl")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.scan_errors == ["docker: boom"]


async def test_ignored_items_are_hidden_but_still_cover_large_folders(tmp_path):
    from pathlib import Path
    cat = Item(id="uv", label="uv", group="Python", size_bytes=100, risk=Risk.SAFE, actions=(Run(("uv", "x")),), path=Path("/h/.cache/uv"))
    big = Item(id="large:1", label="uv dir", group="Large folders", size_bytes=100, risk=Risk.SAFE, report_only=True, path=Path("/h/.cache/uv"))
    app = ReclaimApp(scan=lambda on: on([cat, big]), roots=[], home=tmp_path, dry_run=True,
                     log_path=tmp_path / "log.jsonl", ignore=frozenset({"uv"}))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert [i.id for i in app.items] == []


async def test_real_run_audit_does_not_claim_freed_bytes_for_commands(tmp_path):
    app = make_grouped(tmp_path, dry_run=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.selection.toggle("a")
        await pilot.press("c")
        await pilot.pause()
        await pilot.click("#run")
        await pilot.pause()
        await app.workers.wait_for_complete()
    assert '"freed_bytes": null' in (tmp_path / "log.jsonl").read_text()


async def test_groups_with_cleanable_items_come_before_report_only_groups(tmp_path):
    big_info = Item(id="info", label="Big info", group="Large folders", size_bytes=50 * 1024**3, risk=Risk.DATA, report_only=True)
    small = Item(id="s", label="Small", group="Python", size_bytes=1024**3, risk=Risk.SAFE, actions=(Run(("echo", "s")),))
    medium = Item(id="m", label="Medium", group="Docker", size_bytes=5 * 1024**3, risk=Risk.SAFE, actions=(Run(("echo", "m")),))
    app = ReclaimApp(scan=lambda on: on([big_info, small, medium]), roots=[], home=tmp_path, dry_run=True, log_path=tmp_path / "log.jsonl")
    async with app.run_test() as pilot:
        await pilot.pause()
        keys = [row.key.value for row in app.query_one(DataTable).ordered_rows]
        assert keys == ["group:Docker", "m", "group:Python", "s", "group:Large folders", "info"]
