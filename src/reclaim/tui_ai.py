from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static, Switch

from reclaim.ai.provider import DEFAULT_ANTHROPIC_MODEL, DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL
from reclaim.ai.settings import AiSettings
from reclaim.ai.setup import AiEnv, check
from reclaim.config import set_keys
from reclaim.model import Item

_PROVIDER_LABELS = (("Off (no AI)", "none"), ("Ollama (runs locally, private)", "ollama"), ("Anthropic API (sends item metadata)", "anthropic"))
_MODEL_HINT = {"ollama": DEFAULT_OLLAMA_MODEL, "anthropic": DEFAULT_ANTHROPIC_MODEL, "none": ""}
_URL_HINT = {"ollama": DEFAULT_OLLAMA_URL, "anthropic": "", "none": ""}


DEFAULT_CHOICE = "__default__"
OTHER_CHOICE = "__other__"


def model_options(provider: str, models: list[str], selected: str | None) -> list[tuple[str, str]]:
    """Dropdown entries: the provider default, every model the provider lists, and (Ollama) a way to download another."""
    options = [(f"Provider default ({_MODEL_HINT[provider]})" if _MODEL_HINT[provider] else "Provider default", DEFAULT_CHOICE)]
    names = list(models)
    if selected and selected not in names and selected != OTHER_CHOICE:
        options.append((f"{selected} (not found)", selected))
    options += [(name, name) for name in names]
    if provider == "ollama":
        options.append(("Download another model...", OTHER_CHOICE))
    return options


class AiSettingsScreen(ModalScreen["AiSettings | None"]):
    """Manage the AI advisor. Saving with Ollama selected downloads the model first if it is missing."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, settings: AiSettings, config_path: Path | None, env: AiEnv, cache_path: Path | None = None):
        super().__init__()
        self.settings = settings
        self.config_path = config_path
        self.env = env
        self.cache_path = cache_path
        self._cancel = threading.Event()
        self._dismissed = False
        self._working = False
        self._saving = False
        self._generation = 0
        self._provider = settings.provider

    def compose(self) -> ComposeResult:
        s = self.settings
        with Vertical(id="dialog", classes="tall"):
            yield Static(Text("AI advisor"), classes="title")
            with VerticalScroll(id="settings-form"):
                yield Static("Provider")
                yield Select(_PROVIDER_LABELS, value=s.provider, allow_blank=False, id="ai-provider")
                yield Static("Model (loaded from the provider)")
                yield Select(
                    model_options(s.provider, [], s.model), value=s.model or DEFAULT_CHOICE, allow_blank=False,
                    id="ai-model", disabled=not s.enabled,
                )
                yield Input("", placeholder="model name to download, e.g. qwen2.5:7b", id="ai-model-custom")
                yield Static("Server URL (blank = default; enter reloads models)")
                yield Input(s.base_url or "", placeholder=_URL_HINT[s.provider], id="ai-url")
                with Horizontal(id="ai-share-row"):
                    yield Switch(s.share_names, id="ai-share")
                    yield Static(" Send file and folder names to Anthropic")
                with Horizontal(id="ai-auto-row"):
                    yield Switch(s.auto_review, id="ai-auto")
                    yield Static(" Review items automatically as they are found")
                yield Static(Text(""), id="ai-status")
                yield Static(Text(""), id="ai-pull")
                yield Static(Text("The AI only adds notes. It cannot choose, add or run cleanups."), classes="hint")
            with Horizontal():
                yield Button("Check", id="check")
                yield Button("Clear cache", id="clear-cache")
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#ai-model-custom", Input).display = False
        self.query_one("#ai-share-row").display = self.settings.provider == "anthropic"
        if self.settings.enabled:
            self._run_check()

    # --- reading the form ---
    def _chosen_model(self) -> tuple[str | None, bool]:
        """(model, ok). The dropdown's choice, or the typed name when downloading another."""
        value = str(self.query_one("#ai-model", Select).value)
        if value == DEFAULT_CHOICE:
            return None, True
        if value == OTHER_CHOICE:
            typed = self.query_one("#ai-model-custom", Input).value.strip()
            if not typed:
                self._say("Type the name of the model to download", error=True)
                return None, False
            return typed, True
        return value, True

    def _current(self) -> AiSettings | None:
        model, ok = self._chosen_model()
        if not ok:
            return None
        return AiSettings(
            provider=str(self.query_one("#ai-provider", Select).value),
            model=model,
            base_url=self.query_one("#ai-url", Input).value.strip() or None,
            limit=self.settings.limit,
            auto_review=self.query_one("#ai-auto", Switch).value,
            share_names=self.query_one("#ai-share", Switch).value,
        )

    def _say(self, text: str, error: bool = False, ok: bool = False) -> None:
        style = "bold red" if error else "green" if ok else ""
        self.query_one("#ai-status", Static).update(Text(text, style=style))

    def _set_busy(self, busy: bool) -> None:
        self._working = busy
        for button_id in ("check", "clear-cache", "save"):
            self.query_one(f"#{button_id}", Button).disabled = busy

    def _ui(self, fn, *args) -> None:
        if self._dismissed:
            return
        try:
            self.app.call_from_thread(fn, *args)
        except RuntimeError:
            pass

    def _fill_models(self, provider: str, models: list[str], selected: str | None) -> None:
        select = self.query_one("#ai-model", Select)
        select.set_options(model_options(provider, models, selected))
        select.value = selected if selected else DEFAULT_CHOICE
        select.disabled = provider == "none"

    # --- events ---
    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "ai-model":
            self.query_one("#ai-model-custom", Input).display = event.value == OTHER_CHOICE
            return
        provider = str(event.value)
        if provider == self._provider:
            return
        self._provider = provider
        self.query_one("#ai-share-row").display = provider == "anthropic"
        self.query_one("#ai-url", Input).placeholder = _URL_HINT[provider]
        self.query_one("#ai-pull", Static).update(Text(""))
        self._fill_models(provider, [], None)
        if provider == "none":
            self._generation += 1
            self._say("AI is off. Nothing is sent anywhere and nothing is downloaded.")
        else:
            self._run_check()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "ai-url":
            self._run_check()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button == "check":
            self._run_check()
        elif button == "clear-cache":
            self._clear_cache()
        elif button == "save":
            self._save()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self._cancel.set()
        self._dismissed = True
        self.dismiss(None)

    def _clear_cache(self) -> None:
        try:
            if self.cache_path is not None and self.cache_path.exists():
                self.cache_path.unlink()
            self._say("Cleared saved AI answers. The next review asks the model again.", ok=True)
        except OSError as exc:
            self._say(f"Could not clear the cache: {exc}", error=True)

    # --- work that blocks runs in a thread ---
    def _run_check(self) -> None:
        if self._saving:
            return
        provider = str(self.query_one("#ai-provider", Select).value)
        if provider == "none":
            return
        base_url = self.query_one("#ai-url", Input).value.strip() or None
        choice = str(self.query_one("#ai-model", Select).value)
        if choice == OTHER_CHOICE:
            model = self.query_one("#ai-model-custom", Input).value.strip() or None
        else:
            model = None if choice == DEFAULT_CHOICE else choice
        self._generation += 1
        generation = self._generation
        self._say("Loading models...")
        self.query_one("#ai-model", Select).disabled = True
        self.run_worker(lambda: self._check_worker(generation, provider, model, base_url), thread=True)

    def _check_worker(self, generation: int, provider: str, model: str | None, base_url: str | None) -> None:
        listed = self.env.list_models(provider, base_url)
        ready = check(provider, model, base_url, self.env)
        message = ready.message + (". Save will download it." if ready.needs_download else "")
        if listed.error is not None and (ready.ok or ready.needs_download):
            message += f" (could not list models: {listed.error.detail})"
        self._ui(self._checked, generation, provider, model, listed.value or [], message, ready.ok or ready.needs_download)

    def _checked(self, generation: int, provider: str, model: str | None, models: list[str], message: str, good: bool) -> None:
        if generation != self._generation:
            return
        other = str(self.query_one("#ai-model", Select).value) == OTHER_CHOICE
        self._fill_models(provider, models, OTHER_CHOICE if other else model)
        self._say(message, error=not good, ok=good)

    def _save(self) -> None:
        current = self._current()
        if current is None or self._working:
            return
        self._saving = True
        self._set_busy(True)
        self._say("Checking...")
        self.run_worker(lambda: self._save_worker(current), thread=True)

    def _save_worker(self, s: AiSettings) -> None:
        ready = check(s.provider, s.model, s.base_url, self.env)
        if ready.needs_download:
            model = s.model or DEFAULT_OLLAMA_MODEL
            self._ui(self._say, f"Downloading {model} (esc cancels)...")
            code = self.env.pull(model, lambda line: self._ui(self._pull_line, line), self._cancel.is_set)
            if code != 0:
                self._ui(self._failed, f"Download of {model} failed or was cancelled. Nothing was saved.")
                return
            ready = check(s.provider, s.model, s.base_url, self.env)
        if not ready.ok:
            self._ui(self._failed, ready.message + ". Nothing was saved.")
            return
        if self.config_path is not None:
            try:
                set_keys(self.config_path, s.to_keys())
            except OSError as exc:
                self._ui(self._failed, f"Could not write the config: {exc}")
                return
        self._ui(self._saved, s)

    def _pull_line(self, line: str) -> None:
        self.query_one("#ai-pull", Static).update(Text(line, style="dim"))

    def _failed(self, message: str) -> None:
        self._saving = False
        self._set_busy(False)
        self._say(message, error=True)

    def _saved(self, s: AiSettings) -> None:
        self._dismissed = True
        self.dismiss(replace(s))


MAX_STEPS = 8
MAX_OUTPUT = 8000
MAX_FLAGGED = 20


class ReviewActivity:
    """What the review in flight has done so far, for the panel to show."""

    def __init__(self, item_id: str = "", label: str = "") -> None:
        self.item_id = item_id
        self.label = label
        self.steps: list[str] = []
        self.output = ""
        self.active = True

    def add_step(self, text: str) -> None:
        self.steps.append(text)
        self.steps = self.steps[-MAX_STEPS:]

    def add_chunk(self, text: str) -> None:
        self.output = (self.output + text)[-MAX_OUTPUT:]

    def finish(self) -> None:
        self.active = False
        self.output = ""


_LEVEL_STYLE = {"ok": "green", "caution": "yellow", "keep": "bold red"}
_STATE_TEXT = {
    "queued": "Queued for review. Press Review to do this one next.",
    "running": "Reviewing now...",
    "failed": "The model could not review this one; offline checks only. Press Review to retry.",
}


def _privacy_line(settings: AiSettings) -> str:
    if settings.provider == "anthropic":
        return "Sent to Anthropic: " + ("sizes, extensions and file or folder names" if settings.share_names else "sizes and extensions only")
    return "Stays on this machine"


def panel_text(
    item: Item | None,
    settings: AiSettings,
    counts: dict[str, int],
    flagged: list[Item],
    state: str | None = None,
    activity: ReviewActivity | None = None,
    paused: bool = False,
) -> Text:
    """Body of the floating AI panel: queue state, the highlighted item's full review, the live stream, what was flagged."""
    out = Text()
    if not settings.enabled:
        out.append("AI is off\n", style="bold")
        out.append("Nothing is sent or downloaded. Press Setup to choose a provider.\n", style="dim")
    else:
        out.append(f"{settings.provider}", style="bold green")
        out.append(f"  {settings.model or 'default model'}\n", style="dim")
        out.append(_privacy_line(settings) + "\n", style="dim")
        busy = counts.get("running", 0) + counts.get("queued", 0)
        out.append(
            f"{counts.get('running', 0)} reviewing  {counts.get('queued', 0)} queued  "
            f"{counts.get('done', 0)} done  {len(flagged)} flagged\n",
            style="yellow" if busy else "",
        )
        if paused:
            out.append("Paused after repeated failures. Press Review to resume.\n", style="bold red")
    out.append("\n")
    if item is None:
        out.append("Highlight an item to see its review.\n", style="dim")
    else:
        out.append(f"{item.label}\n", style="bold")
        advice = item.advice
        if advice is not None:
            if advice.what:
                out.append("What it is: ", style="bold")
                out.append(advice.what)
                out.append(f"  ({advice.confidence} confidence)\n" if advice.confidence else "\n", style="dim")
            out.append(f"{advice.level.value}", style=_LEVEL_STYLE[advice.level.value])
            out.append(f"  ({advice.source})\n", style="dim")
            out.append(f"{advice.reason}\n")
            if advice.rebuild:
                out.append(f"Rebuild: {advice.rebuild}\n", style="dim")
        elif not settings.enabled:
            out.append("No AI review (AI is off).\n", style="dim")
        else:
            out.append(f"{_STATE_TEXT.get(state or '', 'Not reviewed yet.')}\n", style="dim")
        if advice is not None and state in ("queued", "running"):
            out.append(f"{_STATE_TEXT[state]}\n", style="dim")
    if activity is not None and activity.active:
        out.append(f"\nNow reviewing: {activity.label}\n", style="bold")
        for n, step in enumerate(activity.steps):
            current = n == len(activity.steps) - 1
            out.append(f" {'>' if current else '+'} {step}\n", style="yellow" if current else "dim")
        if activity.output:
            out.append(f"\nModel output ({len(activity.output)} chars)\n", style="bold")
            out.append(f"{activity.output}\n", style="dim italic")
    if flagged:
        out.append("\nFlagged\n", style="bold")
        for flagged_item in flagged[:MAX_FLAGGED]:
            level = flagged_item.advice.level.value if flagged_item.advice else ""
            out.append(f" {'!!' if level == 'keep' else '!'} {flagged_item.label}\n", style=_LEVEL_STYLE.get(level, ""))
            if flagged_item.advice and flagged_item.advice.reason:
                out.append(f"   {flagged_item.advice.reason}\n", style="dim")
        if len(flagged) > MAX_FLAGGED:
            out.append(f" +{len(flagged) - MAX_FLAGGED} more\n", style="dim")
    return out


class AiPanel(Vertical):
    """Floats over the right edge of the main screen. Toggle with `p`."""

    def compose(self) -> ComposeResult:
        yield Static(Text("AI advisor"), classes="title")
        yield VerticalScroll(Static(Text(""), id="ai-panel-body"), id="ai-panel-scroll")
        with Horizontal(id="ai-panel-buttons"):
            yield Button("Review", id="panel-review")
            yield Button("Setup", id="panel-setup")

    def show(self, text: Text, reset: bool = False) -> None:
        """Replace the body. Keeps the scroll position; sticks to the end only if the reader is already there."""
        scroll = self.query_one("#ai-panel-scroll", VerticalScroll)
        following = scroll.max_scroll_y > 0 and scroll.scroll_y >= scroll.max_scroll_y - 1
        self.query_one("#ai-panel-body", Static).update(text)
        if reset:
            scroll.scroll_home(animate=False)
        elif following:
            self.call_after_refresh(scroll.scroll_end, animate=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "panel-review":
            self.app.action_ai_review()
        else:
            self.app.action_ai_settings()
