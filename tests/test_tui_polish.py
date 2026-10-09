import threading
from pathlib import Path

from textual.widgets import Button, DataTable, Input, ProgressBar, Tabs

from reclaim.model import Item, Risk, Run
from reclaim.tui import ReclaimApp, RunScreen, risk_text


def items():
    return [
        Item(id="a", label="Alpha", group="G", size_bytes=5 * 1024**3, risk=Risk.SAFE, actions=(Run(("echo", "a")),)),
        Item(id="b", label="Beta", group="G", size_bytes=1024**3, risk=Risk.DATA, actions=(Run(("echo", "b")),)),
        Item(id="r", label="Regen", group="G", size_bytes=2 * 1024**3, risk=Risk.REGENERABLE, actions=(Run(("echo", "r")),)),
        Item(id="i", label="Info", group="H", size_bytes=3 * 1024**3, risk=Risk.SAFE, report_only=True),
    ]


def make(tmp_path, scan=None, dry_run=True):
    return ReclaimApp(
        scan=scan or (lambda on: on(items())), roots=[], home=tmp_path, dry_run=dry_run, log_path=tmp_path / "log.jsonl"
    )


def item_ids(app):
    table = app.query_one(DataTable)
    return [k.value for k in table.rows if not k.value.startswith("group:")]


def test_risk_text_colors_each_level_differently():
    styles = {r: str(risk_text(r).style) for r in Risk}
    assert risk_text(Risk.SAFE).plain == "safe"
    assert len(set(styles.values())) == 3


def test_tcss_file_ships_in_the_package():
    assert (Path(__file__).resolve().parents[1] / "src" / "reclaim" / "tui.tcss").is_file()
    assert ReclaimApp.CSS_PATH == "tui.tcss"


async def test_tabs_filter_rows_by_risk(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert set(item_ids(app)) == {"a", "b", "r", "i"}
        app.query_one(Tabs).active = "tab-data"
        await pilot.pause()
        assert item_ids(app) == ["b"]
        app.query_one(Tabs).active = "tab-safe"
        await pilot.pause()
        assert item_ids(app) == ["a"]
        app.query_one(Tabs).active = "tab-report"
        await pilot.pause()
        assert item_ids(app) == ["i"]


async def test_number_keys_switch_tabs(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("4")
        await pilot.pause()
        assert app.query_one(Tabs).active == "tab-data"
        await pilot.press("1")
        await pilot.pause()
        assert app.query_one(Tabs).active == "tab-all"


async def test_tab_and_text_filter_combine(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(Tabs).active = "tab-safe"
        app.set_filter("zzz")
        await pilot.pause()
        assert item_ids(app) == []
        app.set_filter("alp")
        await pilot.pause()
        assert item_ids(app) == ["a"]


async def test_tab_labels_show_counts(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        tabs = app.query_one(Tabs)
        assert "4" in str(tabs.get_tab("tab-all").label)
        assert "1" in str(tabs.get_tab("tab-data").label)


async def test_scan_indicator_visible_during_scan_and_hidden_after(tmp_path):
    release = threading.Event()

    def scan(on_items):
        on_items(items())
        release.wait(5)
        return []

    app = make(tmp_path, scan=scan)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#scanning").display is True
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one("#scanning").display is False


async def test_scan_indicator_hidden_even_when_scan_has_errors(tmp_path):
    app = make(tmp_path, scan=lambda on: ["docker: boom"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one("#scanning").display is False


async def test_dry_run_is_shown_in_header_subtitle(tmp_path):
    assert make(tmp_path, dry_run=True).sub_title == "DRY RUN"
    assert make(tmp_path, dry_run=False).sub_title == ""


async def test_run_screen_tracks_progress_and_unlocks_close(tmp_path):
    app = make(tmp_path, dry_run=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.selection.toggle("a")
        app.selection.toggle("r")
        await pilot.press("c")
        await pilot.pause()
        await pilot.click("#run")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, RunScreen)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert screen.query_one(ProgressBar).progress == 2
        assert len(screen.entries) == 2 and all(ok for _, ok, _ in screen.entries)
        assert screen.query_one("#close", Button).disabled is False
        await pilot.click("#close")
        await pilot.pause()
        assert not isinstance(app.screen, RunScreen)


async def test_run_screen_close_is_locked_while_running(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = RunScreen(total=2, dry_run=True)
        await app.push_screen(screen)
        await pilot.pause()
        assert screen.query_one("#close", Button).disabled is True
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen


async def test_run_screen_records_failures(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = RunScreen(total=1, dry_run=False)
        await app.push_screen(screen)
        screen.record("Alpha", False, "boom")
        screen.finish("freed 0 B (measured)")
        await pilot.pause()
        assert screen.entries == [("Alpha", False, "boom")]
        assert screen.query_one("#close", Button).disabled is False


async def test_filter_input_still_works_with_tabs(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        app.query_one("#filter", Input).value = "beta"
        await pilot.pause()
        assert item_ids(app) == ["b"]


def _row_styles(app, key):
    table = app.query_one(DataTable)
    return [str(table.get_cell(key, col.key).style) for col in table.columns.values()]


async def test_selected_row_is_highlighted_light_red_on_every_cell(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert all("#ffcccc" not in s for s in _row_styles(app, "a"))
        app.selection.toggle("a")
        app._render_rows()
        await pilot.pause()
        assert all("on #ffcccc" in s for s in _row_styles(app, "a"))
        assert all("#ffcccc" not in s for s in _row_styles(app, "r"))


async def test_space_toggle_highlights_and_unhighlights_row_in_place(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.move_cursor(row=1)
        await pilot.pause()
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        await pilot.press("space")
        await pilot.pause()
        assert all("on #ffcccc" in s for s in _row_styles(app, key))
        assert table.cursor_coordinate.row == 1
        await pilot.press("space")
        await pilot.pause()
        assert all("#ffcccc" not in s for s in _row_styles(app, key))


async def test_selected_risk_text_stays_readable_on_light_red(tmp_path):
    app = make(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.selection.toggle("b")
        app._render_rows()
        await pilot.pause()
        risk_style = _row_styles(app, "b")[3]
        assert "on #ffcccc" in risk_style and "green" not in risk_style and "red" not in risk_style.replace("#ffcccc", "")
