from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    ProgressBar,
    RichLog,
    Static,
    Tab,
    Tabs,
)

from reclaim.audit import append, default_log_path
from reclaim.context import ScanContext
from reclaim.executor import Result, describe, run_item
from reclaim.model import Item, Risk
from reclaim.safety import default_roots
from reclaim.scan import dedupe, drop_covered, scan_all
from reclaim.selection import Selection, confirmation_ok, human, summary_text

_GROUP_PREFIX = "group:"

_RISK_STYLE = {Risk.SAFE: "green", Risk.REGENERABLE: "yellow", Risk.DATA: "bold red"}

# (tab id, label, predicate)
_TABS: tuple[tuple[str, str, Callable[[Item], bool]], ...] = (
    ("tab-all", "All", lambda i: True),
    ("tab-safe", "Safe", lambda i: i.risk is Risk.SAFE and not i.report_only),
    ("tab-regenerable", "Regenerable", lambda i: i.risk is Risk.REGENERABLE and not i.report_only),
    ("tab-data", "Data", lambda i: i.risk is Risk.DATA and not i.report_only),
    ("tab-report", "Report-only", lambda i: i.report_only),
)
_TAB_FILTERS = {tab_id: pred for tab_id, _, pred in _TABS}


_SELECTED_STYLE = "#400000 on #ffcccc"
_NOTE_WIDTH = 32


def _pad(text: str, width: int = 0) -> str:
    return f" {text} ".ljust(width) if width else f" {text} "


def risk_text(risk: Risk, dim: bool = False) -> Text:
    style = _RISK_STYLE[risk]
    return Text(risk.value, style=f"dim {style}" if dim else style)


class PlanScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, lines: list[str], needed: list[str], dry_run: bool):
        super().__init__()
        self.lines = lines
        self.needed = needed
        self.dry_run = dry_run

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            title = "Plan (dry-run mode: nothing will be executed)" if self.dry_run else "Plan"
            yield Static(Text(title), classes="title")
            yield VerticalScroll(Static(Text("\n".join(self.lines))))
            if self.needed:
                yield Static(Text(f"Type to confirm: {', '.join(self.needed)}"))
                yield Input(id="confirm")
            with Horizontal():
                yield Button("Run", id="run", variant="error")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            if self.needed and not confirmation_ok(self.query_one(Input).value, self.needed):
                self.notify("Type the listed ids to confirm", severity="warning")
                return
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class DetailScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, lines: list[str]):
        super().__init__()
        self.lines = lines

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield VerticalScroll(Static(Text("\n".join(self.lines))))
            with Horizontal():
                yield Button("Close", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class RunScreen(ModalScreen[None]):
    """Live progress for a clean run. Close stays locked until finish() is called."""

    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, total: int, dry_run: bool):
        super().__init__()
        self.total = total
        self.dry_run = dry_run
        self.entries: list[tuple[str, bool, str]] = []
        self.summary: str | None = None
        self._ready = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            title = "Running (dry-run: nothing is executed)" if self.dry_run else "Running"
            yield Static(Text(title), classes="title")
            yield ProgressBar(total=self.total, show_eta=False, id="progress")
            yield RichLog(id="log", wrap=True, markup=False)
            with Horizontal():
                yield Button("Close", id="close", disabled=True)

    def on_mount(self) -> None:
        self._ready = True
        for entry in self.entries:
            self._show(entry)
        if self.summary is not None:
            self._show_summary()

    def record(self, label: str, ok: bool, detail: str = "") -> None:
        entry = (label, ok, detail)
        self.entries.append(entry)
        if self._ready:
            self._show(entry)

    def finish(self, summary: str) -> None:
        self.summary = summary
        if self._ready:
            self._show_summary()

    def _show(self, entry: tuple[str, bool, str]) -> None:
        label, ok, detail = entry
        self.query_one(ProgressBar).advance(1)
        line = Text("ok      " if ok else "failed  ", style="green" if ok else "bold red")
        line.append(label)
        if detail:
            line.append(f"  {detail}", style="dim")
        self.query_one(RichLog).write(line)

    def _show_summary(self) -> None:
        self.query_one(RichLog).write(Text(self.summary or "", style="bold"))
        self.query_one("#close", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        if self.summary is not None:
            self.dismiss(None)


def detail_lines(item: Item) -> list[str]:
    lines = [item.label, f"Group: {item.group}", f"Size: {human(item.size_bytes)}", f"Risk: {item.risk.value}"]
    if item.path is not None:
        lines.append(f"Path: {item.path}")
    if item.why:
        lines.append(f"Why: {item.why}")
    if item.disabled_reason:
        lines.append(f"Disabled: {item.disabled_reason}")
    if item.report_only:
        lines.append("Report only: shown for information, cannot be cleaned here")
    if item.actions:
        lines.append("Would run:")
        lines.extend(f"  {describe(a)}" for a in item.actions)
    return lines


class ReclaimApp(App):
    CSS_PATH = "tui.tcss"
    TITLE = "reclaim"
    BINDINGS = [
        Binding("1", "show_tab('tab-all')", "All", show=False),
        Binding("2", "show_tab('tab-safe')", "Safe", show=False),
        Binding("3", "show_tab('tab-regenerable')", "Regenerable", show=False),
        Binding("4", "show_tab('tab-data')", "Data", show=False),
        Binding("5", "show_tab('tab-report')", "Report-only", show=False),
        ("space", "toggle", "Select"),
        ("a", "select_safe", "All safe"),
        ("c", "plan", "Plan"),
        ("slash", "focus_filter", "Filter"),
        ("escape", "clear_filter", "Clear filter"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        scan: Callable[[Callable[[list[Item]], None]], list[str] | None],
        roots: list[Path],
        home: Path,
        dry_run: bool = False,
        log_path: Path | None = None,
        ignore: frozenset[str] = frozenset(),
    ):
        super().__init__()
        self._scan = scan
        self.roots = roots
        self.home = home
        self.dry_run = dry_run
        self.log_path = log_path or default_log_path(home)
        self.ignore = ignore
        self._raw: list[Item] = []
        self.items: list[Item] = []
        self.selection = Selection()
        self.executing = False
        self.scan_errors: list[str] = []
        self.filter_text = ""
        self.tab_id = "tab-all"
        self.sub_title = "DRY RUN" if dry_run else ""
        self._run_screen: RunScreen | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tabs(*(Tab(label, id=tab_id) for tab_id, label, _ in _TABS), id="tabs")
        yield Input(id="filter", placeholder="filter (esc to clear)")
        yield DataTable(cursor_type="row", cell_padding=0)
        yield LoadingIndicator(id="scanning")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#filter", Input).display = False
        table = self.query_one(DataTable)
        self._mark_col = table.add_column("   ")
        table.add_columns(" Item ", " Size ", " Risk ", _pad(" Note ", _NOTE_WIDTH))
        self.query_one(DataTable).focus()
        self.run_worker(self._scan_worker, thread=True)

    def _scan_worker(self) -> None:
        try:
            errors = self._scan(lambda batch: self.call_from_thread(self.add_items, batch))
            if errors:
                self.call_from_thread(self._set_errors, list(errors))
        finally:
            self.call_from_thread(self._scan_done)

    def _scan_done(self) -> None:
        self.query_one("#scanning", LoadingIndicator).display = False
        self._update_status()

    def _set_errors(self, errors: list[str]) -> None:
        self.scan_errors = errors
        self.notify(f"{len(errors)} scan error(s): {errors[0]}", severity="warning")
        self._update_status()

    def add_items(self, batch: list[Item]) -> None:
        self._raw = drop_covered(dedupe(self._raw + batch))
        self._refresh_items()

    def _refresh_items(self) -> None:
        self.items = sorted((i for i in self._raw if i.id not in self.ignore), key=lambda i: i.size_bytes, reverse=True)
        self.selection.set_items(self.items)
        self._render_rows()

    def set_filter(self, text: str) -> None:
        self.filter_text = text.strip().lower()
        self._render_rows()

    def _matches(self, item: Item) -> bool:
        if not _TAB_FILTERS[self.tab_id](item):
            return False
        if not self.filter_text:
            return True
        return self.filter_text in f"{item.label} {item.group} {item.id}".lower()

    def _update_tab_labels(self) -> None:
        tabs = self.query_one(Tabs)
        for tab_id, label, pred in _TABS:
            tabs.get_tab(tab_id).label = f"{label} ({sum(1 for i in self.items if pred(i))})"

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one(Tabs).active = tab_id

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab.id in _TAB_FILTERS:
            self.tab_id = event.tab.id
            self._render_rows()

    def _grouped(self) -> list[tuple[str, list[Item]]]:
        groups: dict[str, list[Item]] = {}
        for item in self.items:
            if self._matches(item):
                groups.setdefault(item.group, []).append(item)
        return sorted(
            groups.items(),
            key=lambda kv: (not any(i.executable for i in kv[1]), -sum(i.size_bytes for i in kv[1])),
        )

    def _render_rows(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for group, members in self._grouped():
            total = sum(i.size_bytes for i in members)
            table.add_row("", Text(_pad(f"{group} ({len(members)})"), style="bold"), Text(_pad(human(total)), style="bold"), "", "", key=f"{_GROUP_PREFIX}{group}")
            for item in members:
                table.add_row(*self._cells(item), key=item.id)
        self._update_tab_labels()
        self._update_status()

    def _cells(self, item: Item) -> list[Text]:
        ticked = self.selection.is_ticked(item.id)
        base = _SELECTED_STYLE if ticked else ""
        note = item.disabled_reason or ("report only" if item.report_only else "")
        risk = Text(_pad(item.risk.value), style=base) if ticked else Text.assemble(" ", risk_text(item.risk, dim=item.report_only), " ")
        return [
            Text(" [x] " if ticked else " [ ] ", style=base),
            Text(_pad("  " + item.label), style=base),
            Text(_pad(human(item.size_bytes)), style=base),
            risk,
            Text(_pad(note, _NOTE_WIDTH), style=base),
        ]

    def _update_status(self, extra: str = "") -> None:
        errors = f" | {len(self.scan_errors)} scan error(s)" if self.scan_errors else ""
        text = f"{summary_text(self.items, self.selection.selected_bytes)}{errors} {extra}"
        self.query_one("#status", Static).update(Text(text))

    def _cursor_id(self) -> str | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_toggle(self) -> None:
        if self.executing:
            return
        key = self._cursor_id()
        if key is None:
            return
        if key.startswith(_GROUP_PREFIX):
            members = [i for g, items in self._grouped() if f"{_GROUP_PREFIX}{g}" == key for i in items]
            ids = [i.id for i in members if i.executable and i.risk is not Risk.DATA]
            if not ids:
                self.notify("Nothing in this group can be ticked in bulk", severity="warning")
                return
            self.selection.tick(ids, not all(self.selection.is_ticked(i) for i in ids))
            self._render_rows()
            return
        if self.selection.toggle(key):
            table = self.query_one(DataTable)
            item = next(i for i in self.items if i.id == key)
            for column, cell in zip(table.columns, self._cells(item)):
                table.update_cell(key, column, cell)
            self._update_status()
        else:
            self.notify("Item cannot be cleaned from here", severity="warning")

    def action_select_safe(self) -> None:
        if self.executing:
            return
        self.selection.select_safe()
        self._render_rows()

    def action_focus_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.display = True
        box.focus()

    def action_clear_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.value = ""
        box.display = False
        self.set_filter("")
        self.query_one(DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self.set_filter(event.value)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value
        item = next((i for i in self.items if i.id == key), None)
        if item is not None:
            self.push_screen(DetailScreen(detail_lines(item)))

    def action_plan(self) -> None:
        if self.executing:
            return
        if not self.selection.selected:
            self.notify("Nothing selected", severity="warning")
            return
        lines = self.selection.plan_lines(self.roots, self.home)
        self.push_screen(PlanScreen(lines, self.selection.data_ids, self.dry_run), self._after_plan)

    def _after_plan(self, confirmed: bool | None) -> None:
        if confirmed:
            self.executing = True
            self._run_screen = RunScreen(len(self.selection.selected), self.dry_run)
            self.push_screen(self._run_screen)
            self.run_worker(self._execute, thread=True)

    def _execute(self) -> None:
        free_before = shutil.disk_usage(self.home).free
        cleaned: set[str] = set()
        done = 0
        for item in list(self.selection.selected):
            try:
                result = run_item(item, self.roots, self.home, dry_run=self.dry_run)
            except Exception as exc:
                result = Result(item.id, False, (str(exc),))
            freed = item.size_bytes if result.measured else None
            append(self.log_path, result, freed_bytes=freed)
            done += 1
            if result.ok and not self.dry_run:
                cleaned.add(item.id)
            mark = "ok" if result.ok else "failed: " + (result.log[-1] if result.log else "")
            self.call_from_thread(self._update_status, f"| {done}: {item.label} {mark}")
            if self._run_screen is not None:
                detail = "" if result.ok else (result.log[-1] if result.log else "")
                self.call_from_thread(self._run_screen.record, item.label, result.ok, detail)
        free_after = shutil.disk_usage(self.home).free
        freed_total = max(0, free_after - free_before)
        label = "dry-run, nothing executed" if self.dry_run else f"freed {human(freed_total)} (measured)"
        self.call_from_thread(self._finish_run, cleaned, label)

    def _finish_run(self, cleaned: set[str], label: str) -> None:
        self._raw = [i for i in self._raw if i.id not in cleaned]
        self.selection.clear()
        self.executing = False
        self._refresh_items()
        self._update_status(f"| done: {label}")
        if self._run_screen is not None:
            self._run_screen.finish(label)
            self._run_screen = None


def run_app(ctx: ScanContext, entries, dry_run: bool = False, providers=(), roots=None, ignore=frozenset()) -> None:
    def scan(on_items):
        return scan_all(ctx, entries, providers, on_items=on_items).errors

    ReclaimApp(scan=scan, roots=roots or default_roots(ctx.home), home=ctx.home, dry_run=dry_run, ignore=ignore).run()
