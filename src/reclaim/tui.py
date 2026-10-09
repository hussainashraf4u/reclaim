from __future__ import annotations

import inspect
import os
import textwrap
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
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

from reclaim import scancache
from reclaim.ai.queue import Outcome, ReviewQueue
from reclaim.ai.settings import AiSettings
from reclaim.ai.setup import AiEnv
from reclaim.audit import default_log_path, read_log, summarize
from reclaim.config import set_ignore
from reclaim.context import ScanContext
from reclaim.executor import describe
from reclaim.model import Advice, AdviceLevel, Item, Risk
from reclaim.runner import execute_items
from reclaim.safety import default_roots
from reclaim.scan import dedupe, drop_covered, scan_all
from reclaim.selection import Selection, confirmation_ok, human, summary_text
from reclaim.tui_ai import AiPanel, AiSettingsScreen, ReviewActivity, panel_text

_GROUP_PREFIX = "group:"
PANEL_MIN_WIDTH = 110  # narrower terminals start with the AI panel hidden

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


_ADVICE_STYLE = {AdviceLevel.OK: "green", AdviceLevel.CAUTION: "yellow", AdviceLevel.KEEP: "bold red"}
_ADVICE_MARK = {AdviceLevel.OK: "ok", AdviceLevel.CAUTION: "!", AdviceLevel.KEEP: "!!"}

_SELECTED_STYLE = "#400000 on #ffcccc"
_NOTE_WIDTH = 40
NOTE_LINES = 2


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


class DrillScreen(ModalScreen[None]):
    """Read-only, ncdu-style size browser for a folder. It never deletes anything."""

    BINDINGS = [("escape", "close", "Close"), ("backspace", "up", "Up"), ("left", "up", "Up")]

    def __init__(self, root: Path, sizer: Callable[[Path], int]):
        super().__init__()
        self.root = root
        self.here = root
        self.sizer = sizer
        self._generation = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(""), id="drill-title", classes="title")
            yield DataTable(cursor_type="row", id="drill-table")
            yield Static(Text("enter: open folder   backspace: up   esc: close   (read-only)"))

    def on_mount(self) -> None:
        self.query_one("#drill-table", DataTable).add_columns(" Size ", " Share ", " Name ")
        self._load(self.root)

    def _load(self, path: Path) -> None:
        self.here = path
        self._generation += 1
        self.query_one("#drill-title", Static).update(Text(f"{path}  (measuring...)"))
        self.query_one("#drill-table", DataTable).clear()
        gen = self._generation
        self.run_worker(lambda: self._measure(path, gen), thread=True)

    def _measure(self, path: Path, gen: int) -> None:
        rows: list[tuple[int, str, bool]] = []
        try:
            with os.scandir(path) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError:
            children = []
        for entry in children:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    rows.append((self.sizer(Path(entry.path)), entry.name, True))
                elif entry.is_file(follow_symlinks=False):
                    rows.append((entry.stat(follow_symlinks=False).st_size, entry.name, False))
            except OSError:
                continue
        rows.sort(key=lambda r: r[0], reverse=True)
        self.app.call_from_thread(self._show, path, gen, rows)

    def _show(self, path: Path, gen: int, rows: list[tuple[int, str, bool]]) -> None:
        if gen != self._generation:
            return
        total = sum(r[0] for r in rows) or 1
        self.query_one("#drill-title", Static).update(Text(f"{path}  ({human(total)})"))
        table = self.query_one("#drill-table", DataTable)
        table.clear()
        for size, name, is_dir in rows:
            bar = "#" * max(1, round(20 * size / total)) if size else ""
            table.add_row(_pad(human(size)), f" {bar:<20} ", f" {name}{'/' if is_dir else ''} ", key=f"{'d' if is_dir else 'f'}:{name}")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value or ""
        if key.startswith("d:"):
            self._load(self.here / key[2:])

    def action_up(self) -> None:
        if self.here != self.root:
            self._load(self.here.parent)

    def action_close(self) -> None:
        self.dismiss(None)


def history_lines(log_path: Path) -> list[str]:
    rows = read_log(log_path)
    if not rows:
        return ["No cleanups recorded yet."]
    total = summarize(rows)
    lines = [f"{total.runs} runs, {total.failures} failed, {human(total.freed_bytes)} freed in total (measured items only)", ""]
    for r in reversed(rows[-200:]):
        freed = human(r.freed_bytes) if r.freed_bytes is not None else "-"
        lines.append(f"{r.ts[:19]}  {'ok    ' if r.ok else 'FAILED'}  {freed:>10}  {r.item}")
    return lines


def advice_note(advice: Advice | None) -> str:
    """Everything the review said, on one line, or '' when there is no review."""
    if advice is None:
        return ""
    body = f"{advice.what}: {advice.reason}" if advice.what and advice.reason else advice.what or advice.reason
    text = f"{_ADVICE_MARK[advice.level]} {body}".rstrip()
    return f"{text} (rebuild: {advice.rebuild})" if advice.rebuild else text


def note_lines(text: str) -> list[str]:
    """The note wrapped to the column, at most NOTE_LINES lines, with an ellipsis when it had to be cut."""
    return textwrap.wrap(text, _NOTE_WIDTH - 2, max_lines=NOTE_LINES, placeholder="…") or [""]


def detail_lines(item: Item) -> list[str]:
    lines = [item.label, f"Group: {item.group}", f"Size: {human(item.size_bytes)}", f"Risk: {item.risk.value}"]
    if item.path is not None:
        lines.append(f"Path: {item.path}")
    if item.age_days is not None:
        lines.append(f"Age: {item.age_days} days untouched")
    if item.delta_bytes:
        sign = "+" if item.delta_bytes > 0 else "-"
        lines.append(f"Since last scan: {sign}{human(abs(item.delta_bytes))}")
    if item.why:
        lines.append(f"Why: {item.why}")
    if item.advice is not None:
        if item.advice.what:
            confidence = f" ({item.advice.confidence} confidence)" if item.advice.confidence else ""
            lines.append(f"What it is: {item.advice.what}{confidence}")
        lines.append(f"AI review: {item.advice.level.value} - {item.advice.reason}")
        if item.advice.rebuild:
            lines.append(f"Rebuild cost: {item.advice.rebuild}")
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
        ("c", "plan", "Plan"),
        Binding("comma", "ai_settings", "AI setup", key_display=","),
        ("slash", "focus_filter", "Filter"),
        ("q", "quit", "Quit"),
        ("a", "select_safe", "All safe"),
        ("s", "cycle_sort", "Sort"),
        ("i", "ignore_item", "Ignore"),
        ("h", "history", "History"),
        ("d", "drill", "Browse"),
        ("r", "ai_review", "Review"),
        ("p", "toggle_ai_panel", "Panel"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
    ]

    def __init__(
        self,
        scan: Callable[..., list[str] | None],
        roots: list[Path],
        home: Path,
        dry_run: bool = False,
        log_path: Path | None = None,
        ignore: frozenset[str] = frozenset(),
        use_trash: bool = False,
        config_path: Path | None = None,
        sizer: Callable[[Path], int] | None = None,
        previous: dict[str, int] | None = None,
        ai_settings: AiSettings | None = None,
        ai_env: AiEnv | None = None,
        review: Callable[..., object] | None = None,
        ai_cache_path: Path | None = None,
    ):
        super().__init__()
        self.ai_settings = ai_settings or AiSettings()
        self.ai_env = ai_env
        self._review = review
        self.ai_cache_path = ai_cache_path
        self.activity: ReviewActivity | None = None
        self._last_chunk_ui = 0.0
        self._pending_chunks: list[str] = []
        self._panel_key: str | None = None
        self._note_col = None
        self.queue = ReviewQueue(self._review_one, self._queue_emit) if review is not None else None
        self.scanning = True
        self.use_trash = use_trash
        self.config_path = config_path
        self.sizer = sizer
        self.previous = previous or {}
        self.sort_mode = "size"
        self.progress = ""
        self._scan = scan
        self.roots = roots
        self.home = home
        self.dry_run = dry_run
        self.log_path = log_path or default_log_path(home)
        self.ignore = set(ignore)
        self._raw: list[Item] = []
        self.items: list[Item] = []
        self.selection = Selection()
        self.executing = False
        self.scan_errors: list[str] = []
        self.filter_text = ""
        self.tab_id = "tab-all"
        self.sub_title = "DRY RUN" if dry_run else ""
        self._run_screen: RunScreen | None = None

    def query_one(self, *args, **kwargs):  # type: ignore[override]
        """Always the main screen: scan and review results keep arriving while a dialog is open."""
        return self.screen_stack[0].query_one(*args, **kwargs)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tabs(*(Tab(label, id=tab_id) for tab_id, label, _ in _TABS), id="tabs")
        yield Input(id="filter", placeholder="filter (esc to clear)")
        yield DataTable(cursor_type="row", cell_padding=0)
        yield LoadingIndicator(id="scanning")
        yield Static(id="status")
        yield AiPanel(id="ai-panel")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#filter", Input).display = False
        self.query_one("#ai-panel", AiPanel).display = self.size.width >= PANEL_MIN_WIDTH
        self._refresh_panel()
        table = self.query_one(DataTable)
        self._mark_col = table.add_column("   ")
        *_, self._note_col = table.add_columns(" Item ", " Size ", " Age ", " Risk ", _pad(" Note ", _NOTE_WIDTH))
        self.query_one(DataTable).focus()
        self.run_worker(self._scan_worker, thread=True)

    def _scan_worker(self) -> None:
        try:
            feed = lambda batch: self.call_from_thread(self.add_items, batch)  # noqa: E731
            if len(inspect.signature(self._scan).parameters) >= 2:
                errors = self._scan(feed, lambda done, total, name: self.call_from_thread(self.set_progress, done, total, name))
            else:
                errors = self._scan(feed)
            if errors:
                self.call_from_thread(self._set_errors, list(errors))
        finally:
            self.call_from_thread(self._scan_done)

    def set_progress(self, done: int, total: int, name: str) -> None:
        self.progress = f"| scanning {done}/{total} ({name}) "
        self._update_status()

    def _scan_done(self) -> None:
        self.progress = ""
        self.scanning = False
        self.query_one("#scanning", LoadingIndicator).display = False
        self._update_status()

    # --- AI advisor ---
    @property
    def reviewing(self) -> bool:
        return self.queue is not None and not self.queue.idle()

    @property
    def ai_status(self) -> str:
        if not self.ai_settings.enabled:
            return "| AI off (, to set up) "
        counts = self.queue.counts() if self.queue is not None else {}
        left = counts.get("queued", 0) + counts.get("running", 0)
        flagged = sum(1 for i in self._raw if i.advice is not None and i.advice.level is not AdviceLevel.OK)
        if self.queue is not None and self.queue.paused:
            return f"| AI paused, {left} waiting "
        return f"| AI reviewing, {left} left, {flagged} flagged " if left else f"| AI flagged {flagged} "

    def action_toggle_ai_panel(self) -> None:
        panel = self.query_one("#ai-panel", AiPanel)
        panel.display = not panel.display
        self._refresh_panel()

    def _cursor_item(self) -> Item | None:
        key = self._cursor_id()
        return next((i for i in self.items if i.id == key), None)

    def _refresh_panel(self) -> None:
        panel = self.query_one("#ai-panel", AiPanel)
        if not panel.display:
            return
        item = self._cursor_item()
        key = item.id if item is not None else None
        reset = key != self._panel_key
        self._panel_key = key
        flagged = [i for i in self.items if i.advice is not None and i.advice.level is not AdviceLevel.OK]
        queue = self.queue
        panel.show(
            panel_text(
                item,
                self.ai_settings,
                queue.counts() if queue is not None else {},
                flagged,
                queue.state(item.id) if queue is not None and item is not None else None,
                self.activity,
                queue.paused if queue is not None else False,
            ),
            reset=reset,
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._refresh_panel()

    def action_ai_review(self) -> None:
        """Review the highlighted item next."""
        if not self.ai_settings.enabled or self.queue is None:
            self.notify("AI is off. Press , to set it up", severity="warning")
            return
        item = self._cursor_item()
        if item is None:
            self.notify("Highlight an item to review", severity="warning")
            return
        outcome = self.queue.bump(item)
        self.notify(
            {"running": "Already reviewing this item", "queued": "Moved to the front of the queue"}.get(
                outcome, "Reviewing again, ignoring saved answers"
            )
        )
        self._update_note(item)
        self._update_status()
        self._refresh_panel()

    def _enqueue(self, items: list[Item]) -> None:
        if self.queue is None or not self.ai_settings.enabled or not self.ai_settings.auto_review:
            return
        for item in items:
            if item.id not in self.ignore:
                self.queue.put(item)

    # Queue callbacks run on the worker thread; each hops to the UI thread.
    def _review_one(self, item: Item, on_event: Callable[[str, str], None], fresh: bool) -> Outcome:
        assert self._review is not None
        result = self._review(replace(item), self.ai_settings, on_event, fresh)
        return result if isinstance(result, Outcome) else Outcome()

    def _queue_emit(self, event: str, item_id: str, text: str, outcome: Outcome | None) -> None:
        if event == "chunk":
            # Tokens arrive far faster than the screen needs: forward them a few times a second.
            self._pending_chunks.append(text)
            now = time.monotonic()
            if now - self._last_chunk_ui < 0.1:
                return
            self._last_chunk_ui = now
            text = "".join(self._pending_chunks)
            self._pending_chunks.clear()
        elif event in ("start", "done"):
            self._pending_chunks.clear()
        try:
            self.call_from_thread(self._queue_event, event, item_id, text, outcome)
        except RuntimeError:  # app is closing
            pass

    def _queue_event(self, event: str, item_id: str, text: str, outcome: Outcome | None) -> None:
        try:
            self._apply_queue_event(event, item_id, text, outcome)
        except NoMatches:  # the screen is already being torn down
            pass

    def _apply_queue_event(self, event: str, item_id: str, text: str, outcome: Outcome | None) -> None:
        item = next((i for i in self._raw if i.id == item_id), None)
        if event == "start" and item is not None:
            self.activity = ReviewActivity(item.id, item.label)
        elif event in ("step", "chunk"):
            if self.activity is not None and self.activity.item_id == item_id:
                (self.activity.add_step if event == "step" else self.activity.add_chunk)(text)
        elif event == "done":
            if self.activity is not None and self.activity.item_id == item_id:
                self.activity.finish()
            if item is not None and outcome is not None:
                item.advice = outcome.advice
        elif event == "paused":
            self.notify(f"AI review paused after repeated failures: {text}. Press r to resume.", severity="warning")
        if item is not None and event in ("start", "done"):
            self._update_note(item)
        if event != "chunk":
            self._update_status()
        self._refresh_panel()

    def action_ai_settings(self) -> None:
        if self.executing:
            return
        self.push_screen(
            AiSettingsScreen(self.ai_settings, self.config_path, self.ai_env or AiEnv(), self.ai_cache_path),
            self._after_ai_settings,
        )

    def _after_ai_settings(self, new: AiSettings | None) -> None:
        if new is None:
            return
        self.ai_settings = new
        self.activity = None
        if self.queue is not None:
            self.queue.clear()
        if not new.enabled:
            for item in self._raw:
                item.advice = None
            self.notify("AI advisor turned off")
        else:
            self.notify(f"AI advisor on ({new.provider})")
            self._enqueue(self._raw)
        self._render_rows()

    def _set_errors(self, errors: list[str]) -> None:
        self.scan_errors = errors
        self.notify(f"{len(errors)} scan error(s): {errors[0]}", severity="warning")
        self._update_status()

    def add_items(self, batch: list[Item]) -> None:
        scancache.annotate(self.previous, batch)
        self._raw = drop_covered(dedupe(self._raw + batch))
        known = {i.id: i for i in self._raw}
        self._enqueue([known[i.id] for i in batch if i.id in known])
        self._refresh_items()

    def _refresh_items(self) -> None:
        visible = [i for i in self._raw if i.id not in self.ignore]
        if self.sort_mode == "age":
            self.items = sorted(visible, key=lambda i: (i.age_days is None, -(i.age_days or 0), -i.size_bytes))
        elif self.sort_mode == "name":
            self.items = sorted(visible, key=lambda i: i.label.lower())
        elif self.sort_mode == "risk":
            order = {Risk.SAFE: 0, Risk.REGENERABLE: 1, Risk.DATA: 2}
            self.items = sorted(visible, key=lambda i: (order[i.risk], -i.size_bytes))
        else:
            self.items = sorted(visible, key=lambda i: i.size_bytes, reverse=True)
        self.selection.set_items(self.items)
        self._render_rows()

    def action_cycle_sort(self) -> None:
        modes = ("size", "age", "name", "risk")
        self.sort_mode = modes[(modes.index(self.sort_mode) + 1) % len(modes)]
        self.notify(f"Sorted by {self.sort_mode}")
        self._refresh_items()

    def action_ignore_item(self) -> None:
        key = self._cursor_id()
        item = next((i for i in self.items if i.id == key), None)
        if item is None or self.executing:
            return
        if self.config_path is None:
            self.notify("No config path: cannot save the ignore list", severity="warning")
            return
        self.ignore.add(item.id)
        try:
            set_ignore(self.config_path, self.ignore)
        except OSError as exc:
            self.ignore.discard(item.id)
            self.notify(f"Could not write config: {exc}", severity="error")
            return
        self.notify(f"Ignoring {item.id} (saved to config)")
        self._refresh_items()

    def action_history(self) -> None:
        self.push_screen(DetailScreen(history_lines(self.log_path)))

    def action_drill(self) -> None:
        key = self._cursor_id()
        item = next((i for i in self.items if i.id == key), None)
        if item is None or item.path is None or not item.path.is_dir():
            self.notify("Pick an item with a folder to browse", severity="warning")
            return
        self.push_screen(DrillScreen(item.path, self.sizer or (lambda p: 0)))

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
            tab = tabs.get_tab(tab_id)
            if tab is not None:
                tab.label = f"{label} ({sum(1 for i in self.items if pred(i))})"

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
            table.add_row("", Text(_pad(f"{group} ({len(members)})"), style="bold"), Text(_pad(human(total)), style="bold"), "", "", "", key=f"{_GROUP_PREFIX}{group}")
            height = NOTE_LINES if self.ai_settings.enabled else 1
            for item in members:
                table.add_row(*self._cells(item), height=height, key=item.id)
        self._update_tab_labels()
        self._update_status()
        self._refresh_panel()

    def _note_cell(self, item: Item) -> Text:
        ticked = self.selection.is_ticked(item.id)
        style = _SELECTED_STYLE if ticked else ""
        state = self.queue.state(item.id) if self.queue is not None and self.ai_settings.enabled else None
        if item.advice is not None:
            text, style = advice_note(item.advice), style or _ADVICE_STYLE[item.advice.level]
        elif item.disabled_reason or item.report_only:
            text = item.disabled_reason or "report only"
        elif state in ("queued", "running"):
            text, style = f"ai: {'reviewing...' if state == 'running' else 'queued'}", style or "dim"
        elif item.delta_bytes:
            text = f"{'+' if item.delta_bytes > 0 else '-'}{human(abs(item.delta_bytes))} since last scan"
        else:
            text = ""
        lines = note_lines(text) if self.ai_settings.enabled else [text[: _NOTE_WIDTH - 3] + "…" if len(text) > _NOTE_WIDTH - 2 else text]
        return Text("\n".join(_pad(line, _NOTE_WIDTH) for line in lines), style=style)

    def _update_note(self, item: Item) -> None:
        """Redraw just this row's note, so the cursor and scroll position stay put."""
        if self._note_col is None:
            return
        try:
            self.query_one(DataTable).update_cell(item.id, self._note_col, self._note_cell(item))
        except Exception:  # row not on screen (filtered out or mid re-render)
            pass

    def _cells(self, item: Item) -> list[Text]:
        ticked = self.selection.is_ticked(item.id)
        base = _SELECTED_STYLE if ticked else ""
        risk = Text(_pad(item.risk.value), style=base) if ticked else Text.assemble(" ", risk_text(item.risk, dim=item.report_only), " ")
        return [
            Text(" [x] " if ticked else " [ ] ", style=base),
            Text(_pad("  " + item.label), style=base),
            Text(_pad(human(item.size_bytes)), style=base),
            Text(_pad(f"{item.age_days}d" if item.age_days is not None else ""), style=base),
            risk,
            self._note_cell(item),
        ]

    def _update_status(self, extra: str = "") -> None:
        errors = f" | {len(self.scan_errors)} scan error(s)" if self.scan_errors else ""
        text = f"{summary_text(self.items, self.selection.selected_bytes)}{errors} {self.progress}{self.ai_status}{extra}"
        self.query_one("#status", Static).update(Text(text))

    def _cursor_id(self) -> str | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_toggle(self) -> None:  # type: ignore[override]
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
        lines = self.selection.plan_lines(self.roots, self.home, self._trash_dir())
        flagged = [i for i in self.selection.selected if i.advice is not None and i.advice.level is not AdviceLevel.OK]
        if flagged:
            lines = [*lines, "", "AI notes (advice only):", *(f"  [{i.advice.level.value}] {i.label}: {i.advice.reason}" for i in flagged if i.advice)]
        self.push_screen(PlanScreen(lines, self.selection.data_ids, self.dry_run), self._after_plan)

    def _trash_dir(self) -> Path | None:
        return self.home / ".Trash" if self.use_trash else None

    def _after_plan(self, confirmed: bool | None) -> None:
        if confirmed:
            self.executing = True
            self._run_screen = RunScreen(len(self.selection.selected), self.dry_run)
            self.push_screen(self._run_screen)
            self.run_worker(self._execute, thread=True)

    def _execute(self) -> None:
        done = 0

        def on_result(item: Item, result) -> None:
            nonlocal done
            done += 1
            mark = "ok" if result.ok else "failed: " + (result.log[-1] if result.log else "")
            self.call_from_thread(self._update_status, f"| {done}: {item.label} {mark}")
            if self._run_screen is not None:
                detail = "" if result.ok else (result.log[-1] if result.log else "")
                self.call_from_thread(self._run_screen.record, item.label, result.ok, detail)

        summary = execute_items(
            list(self.selection.selected),
            self.roots,
            self.home,
            self.log_path,
            dry_run=self.dry_run,
            trash_dir=self._trash_dir(),
            on_result=on_result,
        )
        label = "dry-run, nothing executed" if self.dry_run else f"freed {human(summary.freed_bytes)} (measured)"
        if self.use_trash and not self.dry_run and any(i.risk is Risk.DATA for i in self.selection.selected):
            label += "; data items are in the Trash"
        self.call_from_thread(self._finish_run, set(summary.cleaned), label)

    def _finish_run(self, cleaned: set[str], label: str) -> None:
        self._raw = [i for i in self._raw if i.id not in cleaned]
        self.selection.clear()
        self.executing = False
        self._refresh_items()
        self._update_status(f"| done: {label}")
        if self._run_screen is not None:
            self._run_screen.finish(label)
            self._run_screen = None


def run_app(
    ctx: ScanContext,
    entries,
    dry_run: bool = False,
    providers=(),
    roots=None,
    ignore=frozenset(),
    use_trash: bool = False,
    config_path: Path | None = None,
    ai_settings: AiSettings | None = None,
) -> None:
    from reclaim.ai.advisor import advise_one
    from reclaim.ai.provider import build_provider
    from reclaim.ai.setup import default_cache_path as ai_cache

    ai_cache_path = ai_cache(ctx.home)

    def review(item, settings, on_event, fresh):
        try:
            provider = build_provider(settings.provider, settings.model, settings.base_url, ctx.env)
        except ValueError as exc:
            return Outcome(None, str(exc))
        result = advise_one(item, ctx, provider, ai_cache_path, on_event, use_cache=not fresh, share_names=settings.share_names)
        return Outcome(result.advice.get(item.id), str(result.error) if result.error else None)

    cache_path = scancache.default_cache_path(ctx.home)
    previous = scancache.load(cache_path)

    def scan(on_items, on_progress=None):
        result = scan_all(ctx, entries, providers, on_items=on_items, on_progress=on_progress)
        scancache.save(cache_path, result.items, ctx.now())
        return result.errors

    ReclaimApp(
        scan=scan,
        roots=roots or default_roots(ctx.home),
        home=ctx.home,
        dry_run=dry_run,
        ignore=ignore,
        use_trash=use_trash,
        config_path=config_path,
        sizer=ctx.sizer,
        previous=previous,
        ai_settings=ai_settings,
        ai_env=AiEnv(which=ctx.which, env=ctx.env),
        review=review,
        ai_cache_path=ai_cache_path,
    ).run()
