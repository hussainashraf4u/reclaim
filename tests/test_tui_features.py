from textual.widgets import DataTable

from reclaim.config import load_config
from reclaim.model import Item, Risk, Run
from reclaim.tui import DetailScreen, DrillScreen, ReclaimApp, detail_lines, history_lines


def items():
    return [
        Item("a", "Alpha", "G", 5000, Risk.SAFE, actions=(Run(("echo",)),), age_days=10),
        Item("b", "Beta", "G", 9000, Risk.REGENERABLE, actions=(Run(("echo",)),), age_days=200),
        Item("c", "Charlie", "G", 1000, Risk.DATA, actions=(Run(("echo",)),)),
    ]


def make(tmp_path, **kw):
    return ReclaimApp(scan=lambda on: on(items()), roots=[], home=tmp_path, dry_run=True, log_path=tmp_path / "log.jsonl", **kw)


def order(app):
    return [i.id for i in app.items]


async def test_sort_cycles_through_modes(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert order(app) == ["b", "a", "c"]
        await pilot.press("s")
        assert app.sort_mode == "age" and order(app) == ["b", "a", "c"]
        await pilot.press("s")
        assert app.sort_mode == "name" and order(app) == ["a", "b", "c"]
        await pilot.press("s")
        assert app.sort_mode == "risk" and order(app) == ["a", "b", "c"]
        await pilot.press("s")
        assert app.sort_mode == "size"


async def test_age_column_is_rendered(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert len(table.columns) == 6
        cells = [str(c) for c in table.get_row("b")]
        assert any("200d" in c for c in cells)


async def test_ignore_key_hides_item_and_saves_config(tmp_path):
    cfg = tmp_path / "config.toml"
    app = make(tmp_path, config_path=cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")  # group row -> first item
        await pilot.press("down")
        key = app._cursor_id()
        await pilot.press("i")
        assert key not in order(app)
    assert load_config(cfg, tmp_path).ignore == {key}


async def test_ignore_without_config_path_warns(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("i")
        assert len(app.items) == 3


async def test_history_screen_opens(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, DetailScreen)


def test_history_lines_empty_and_filled(tmp_path):
    from reclaim.audit import append
    from reclaim.executor import Result

    assert history_lines(tmp_path / "x.jsonl") == ["No cleanups recorded yet."]
    append(tmp_path / "x.jsonl", Result("npm", True, ()), freed_bytes=1024)
    assert "npm" in "\n".join(history_lines(tmp_path / "x.jsonl"))


async def test_drill_screen_lists_children_and_descends(tmp_path):
    root = tmp_path / "root"
    (root / "big").mkdir(parents=True)
    (root / "big" / "f").write_bytes(b"x" * 500)
    (root / "small.txt").write_text("hi")
    sizer = lambda p: sum(f.stat().st_size for f in p.rglob("*") if f.is_file())  # noqa: E731
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = DrillScreen(root, sizer)
        app.push_screen(screen)
        await pilot.pause(0.5)
        table = screen.query_one("#drill-table", DataTable)
        assert table.row_count == 2
        assert [r.value for r in table.rows][0] == "d:big"
        screen._load(root / "big")
        await pilot.pause(0.5)
        assert screen.query_one("#drill-table", DataTable).row_count == 1
        screen.action_up()
        await pilot.pause(0.5)
        assert screen.here == root


async def test_drill_key_needs_a_folder(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("d")
        await pilot.pause()
        assert not isinstance(app.screen, DrillScreen)


def test_detail_lines_show_age_and_delta():
    item = Item("a", "A", "G", 10, Risk.SAFE, age_days=40, delta_bytes=2048)
    text = "\n".join(detail_lines(item))
    assert "40 days" in text and "+2.0 KB" in text


async def test_progress_callback_is_used_when_scan_accepts_it(tmp_path):
    def scan(on_items, on_progress):
        on_progress(1, 2, "docker")
        on_items(items())
        return []

    app = ReclaimApp(scan=scan, roots=[], home=tmp_path, dry_run=True, log_path=tmp_path / "log.jsonl")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.items) == 3
