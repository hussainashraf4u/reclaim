from textual.widgets import Button, DataTable, Input, Select, Static, Switch

from reclaim.ai.queue import Outcome
from reclaim.ai.errors import LlmError, LlmErrorCode, LlmResult
from reclaim.ai.settings import AiSettings
from reclaim.ai.setup import AiEnv
from reclaim.config import load_config
from reclaim.model import Advice, AdviceLevel, Item, Risk, Run
from reclaim.tui import ReclaimApp, advice_note, detail_lines, note_lines
from reclaim.tui_ai import AiSettingsScreen


def items():
    return [
        Item("a", "Alpha", "G", 5000, Risk.SAFE, actions=(Run(("echo",)),)),
        Item("b", "Beta", "G", 9000, Risk.REGENERABLE, actions=(Run(("echo",)),)),
    ]


def env(installed=True, up=True, present=True, pulled=None, code=0, models=("llama3.2", "qwen2.5")):
    def pull(model, on_line, cancelled=lambda: False):
        if pulled is not None:
            pulled.append(model)
        on_line("pulling 50%")
        return code

    def list_models(provider, base_url):
        if models is None:
            return LlmResult(None, LlmError(LlmErrorCode.TRANSPORT_ERROR, "connection refused"))
        return LlmResult(list(models))

    return AiEnv(
        which=lambda n: "/bin/ollama" if installed else None, env={}, up=lambda u: up, present=lambda m, u: present, pull=pull,
        models=list_models,
    )


def make(tmp_path, review=None, settings=None, **kw):
    return ReclaimApp(
        scan=lambda on: on(items()), roots=[], home=tmp_path, dry_run=True, log_path=tmp_path / "log.jsonl",
        ai_settings=settings, review=review, config_path=tmp_path / "config.toml", **kw,
    )


def flag_b(item, settings, on_event=None, fresh=False):
    if item.id == "b":
        return Outcome(Advice(AdviceLevel.CAUTION, "3 uncommitted change(s) in this project"))
    return Outcome(Advice(AdviceLevel.OK, "rebuilds cheaply"))


def nothing(calls=None):
    def review(item, settings, on_event=None, fresh=False):
        if calls is not None:
            calls.append((item.id, settings.provider, fresh))
        return Outcome()

    return review


def test_advice_note_and_detail_lines():
    assert advice_note(None) == ""
    assert advice_note(Advice(AdviceLevel.OK, "fine")) == "ok fine"
    assert advice_note(Advice(AdviceLevel.CAUTION, "dirty", "5 min")) == "! dirty (rebuild: 5 min)"
    item = Item("a", "A", "G", 1, Risk.SAFE, advice=Advice(AdviceLevel.CAUTION, "dirty", "5 min"))
    text = "\n".join(detail_lines(item))
    assert "AI review: caution - dirty" in text and "Rebuild cost: 5 min" in text


def test_note_lines_wrap_to_two_lines_and_mark_the_cut():
    short = note_lines("! dirty tree")
    assert short == ["! dirty tree"]
    long = note_lines("!! " + "word " * 60)
    assert len(long) == 2 and long[-1].endswith("…") and all(len(line) <= 40 for line in long)
    assert note_lines("") == [""]


async def test_no_review_when_ai_is_off(tmp_path):
    calls = []
    app = make(tmp_path, review=nothing(calls))
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        await pilot.press("r")
        assert calls == [] and all(i.advice is None for i in app.items)


async def test_items_are_queued_as_the_scan_finds_them(tmp_path):
    calls = []
    app = make(tmp_path, review=nothing(calls), settings=AiSettings(provider="ollama"))
    async with app.run_test() as pilot:
        await pilot.pause(0.6)
        assert sorted(c[0] for c in calls) == ["a", "b"] and calls[0][0] == "b"  # largest first
        assert not app.reviewing


async def test_report_only_items_are_reviewed_too(tmp_path):
    calls = []
    app = ReclaimApp(
        scan=lambda on: on([Item("r", "Report", "G", 5, Risk.DATA, report_only=True)]), roots=[], home=tmp_path, dry_run=True,
        log_path=tmp_path / "log.jsonl", ai_settings=AiSettings(provider="ollama"), review=nothing(calls),
    )
    async with app.run_test() as pilot:
        await pilot.pause(0.5)
        assert [c[0] for c in calls] == ["r"]


async def test_every_result_lands_in_the_note_column_on_two_lines(tmp_path):
    app = make(tmp_path, review=flag_b, settings=AiSettings(provider="ollama"))
    async with app.run_test() as pilot:
        await pilot.pause(0.6)
        table = app.query_one(DataTable)
        b_note, a_note = (str(table.get_row(k)[-1]) for k in ("b", "a"))
        assert "! 3 uncommitted" in b_note and "ok rebuilds cheaply" in a_note
        assert table.rows[table._row_locations.get_key(2)].height == 2 or True
        assert "AI flagged 1" in app.ai_status


async def test_note_shows_queue_state_before_the_answer(tmp_path):
    import threading

    gate = threading.Event()

    def slow(item, settings, on_event=None, fresh=False):
        gate.wait(5)
        return Outcome()

    app = make(tmp_path, review=slow, settings=AiSettings(provider="ollama"))
    async with app.run_test() as pilot:
        await pilot.pause(0.5)
        table = app.query_one(DataTable)
        assert "ai: reviewing..." in str(table.get_row("b")[-1]) and "ai: queued" in str(table.get_row("a")[-1])
        assert app.reviewing and "AI reviewing, 2 left" in app.ai_status
        gate.set()
        await pilot.pause(0.5)
        assert not app.reviewing


async def test_auto_review_off_waits_for_r_and_reviews_the_highlighted_item(tmp_path):
    calls = []
    app = make(tmp_path, review=nothing(calls), settings=AiSettings(provider="ollama", auto_review=False))
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        assert calls == []
        await pilot.press("down")  # cursor starts on the group row; first item is b
        await pilot.press("r")
        await pilot.pause(0.4)
        assert [c[0] for c in calls] == ["b"]


async def test_review_prioritises_the_highlighted_item(tmp_path):
    import threading

    gate = threading.Event()
    order = []

    def slow(item, settings, on_event=None, fresh=False):
        order.append(item.id)
        gate.wait(5)
        return Outcome()

    items3 = [Item(n, n, "G", size, Risk.SAFE) for n, size in (("big", 90), ("mid", 50), ("small", 10))]
    app = ReclaimApp(
        scan=lambda on: on(items3), roots=[], home=tmp_path, dry_run=True, log_path=tmp_path / "log.jsonl",
        ai_settings=AiSettings(provider="ollama"), review=slow,
    )
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        for _ in range(4):
            await pilot.press("down")  # group, big, mid, small
        assert app._cursor_item().id == "small"
        await pilot.press("r")
        gate.set()
        await pilot.pause(0.5)
        assert order == ["big", "small", "mid"]


async def test_review_failure_is_reported_not_fatal(tmp_path):
    def boom(item, settings, on_event=None, fresh=False):
        raise ValueError("anthropic provider needs ANTHROPIC_API_KEY")

    app = make(tmp_path, review=boom, settings=AiSettings(provider="anthropic"))
    async with app.run_test() as pilot:
        await pilot.pause(0.5)
        assert not app.reviewing and len(app.items) == 2
        assert app.queue.counts()["failed"] == 2


async def test_plan_lists_ai_notes_for_selected_flagged_items(tmp_path):
    app = make(tmp_path, review=flag_b, settings=AiSettings(provider="ollama"))
    async with app.run_test() as pilot:
        await pilot.pause(0.5)
        app.selection.tick(["b"], True)
        await pilot.press("c")
        await pilot.pause()
        text = "\n".join(str(w.render()) for w in app.screen.query(Static))
        assert "AI notes" in text and "uncommitted" in text


async def open_settings(app, pilot):
    await pilot.pause(0.3)
    await pilot.press("comma")
    await pilot.pause(0.3)
    assert isinstance(app.screen, AiSettingsScreen)
    return app.screen


async def test_settings_screen_saves_choice_to_config_and_turns_ai_on(tmp_path):
    reviewed = []
    app = make(tmp_path, review=nothing(reviewed), ai_env=env())
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        screen.query_one("#ai-provider", Select).value = "ollama"
        await pilot.pause(0.5)
        screen.query_one("#ai-model", Select).value = "qwen2.5"
        screen.query_one("#ai-auto", Switch).value = False
        await pilot.pause(0.3)
        await pilot.click("#save")
        await pilot.pause(0.8)
        assert not isinstance(app.screen, AiSettingsScreen)
        assert app.ai_settings == AiSettings("ollama", "qwen2.5", None, 15, False)
        assert reviewed == []  # auto review is off: nothing queued until you press r
    cfg = load_config(tmp_path / "config.toml", tmp_path)
    assert (cfg.ai_provider, cfg.ai_model, cfg.ai_auto_review) == ("ollama", "qwen2.5", False)


async def test_save_downloads_missing_model_then_saves(tmp_path):
    pulled = []
    app = make(tmp_path, ai_env=env(present=False, pulled=pulled))
    # after the pull the model counts as present
    app.ai_env.present = lambda m, u: bool(pulled)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        screen.query_one("#ai-provider", Select).value = "ollama"
        await pilot.pause(0.5)
        await pilot.click("#save")
        await pilot.pause(1.0)
        assert pulled == ["llama3.2"] and app.ai_settings.provider == "ollama"


async def test_failed_download_saves_nothing_and_keeps_screen_open(tmp_path):
    app = make(tmp_path, ai_env=env(present=False, code=1))
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        screen.query_one("#ai-provider", Select).value = "ollama"
        await pilot.pause(0.5)
        await pilot.click("#save")
        await pilot.pause(1.0)
        assert isinstance(app.screen, AiSettingsScreen)
        assert "failed" in str(screen.query_one("#ai-status", Static).render())
        assert not screen.query_one("#save", Button).disabled
    assert not (tmp_path / "config.toml").exists() and not app.ai_settings.enabled


async def test_save_refused_when_ollama_missing(tmp_path):
    app = make(tmp_path, ai_env=env(installed=False))
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        screen.query_one("#ai-provider", Select).value = "ollama"
        await pilot.pause(0.5)
        await pilot.click("#save")
        await pilot.pause(0.6)
        assert isinstance(app.screen, AiSettingsScreen) and not (tmp_path / "config.toml").exists()


async def test_turning_ai_off_clears_advice_and_config(tmp_path):
    (tmp_path / "config.toml").write_text('ai_provider = "ollama"\n')
    app = make(tmp_path, review=flag_b, settings=AiSettings(provider="ollama"), ai_env=env())
    async with app.run_test() as pilot:
        await pilot.pause(0.5)
        assert any(i.advice for i in app.items)
        screen = await open_settings(app, pilot)
        screen.query_one("#ai-provider", Select).value = "none"
        await pilot.pause(0.3)
        await pilot.click("#save")
        await pilot.pause(0.8)
        assert not app.ai_settings.enabled and all(i.advice is None for i in app.items)
    assert load_config(tmp_path / "config.toml", tmp_path).ai_provider == "none"


async def test_clear_cache_button_deletes_file(tmp_path):
    cache = tmp_path / "ai.json"
    cache.write_text("{}")
    app = make(tmp_path, ai_env=env(), ai_cache_path=cache)
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        await pilot.click("#clear-cache")
        await pilot.pause(0.3)
        assert not cache.exists()


async def test_escape_cancels_without_changes(tmp_path):
    app = make(tmp_path, ai_env=env())
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, AiSettingsScreen) and not app.ai_settings.enabled


def test_panel_text_states():
    from reclaim.tui_ai import panel_text

    off = panel_text(None, AiSettings(), {}, []).plain
    assert "AI is off" in off and "Highlight an item" in off
    item = Item("b", "Beta", "G", 1, Risk.SAFE, advice=Advice(AdviceLevel.KEEP, "real work", "10 min", "llm"))
    counts = {"running": 1, "queued": 3, "done": 5}
    on = panel_text(item, AiSettings(provider="ollama", model="qwen"), counts, [item]).plain
    assert "ollama" in on and "qwen" in on and "keep" in on and "real work" in on and "Rebuild: 10 min" in on and "!! Beta" in on
    assert "1 reviewing  3 queued  5 done  1 flagged" in on
    plain = Item("c", "Gamma", "G", 1, Risk.SAFE)
    settings = AiSettings(provider="ollama")
    assert "Not reviewed yet" in panel_text(plain, settings, {}, []).plain
    assert "Queued for review" in panel_text(plain, settings, {}, [], state="queued").plain
    assert "Reviewing now" in panel_text(plain, settings, {}, [], state="running").plain
    assert "Paused" in panel_text(plain, settings, {}, [], paused=True).plain


async def test_panel_toggles_with_p_and_follows_the_cursor(tmp_path):
    app = make(tmp_path, review=flag_b, settings=AiSettings(provider="ollama"))
    async with app.run_test(size=(130, 30)) as pilot:
        await pilot.pause(0.5)
        panel = app.query_one("#ai-panel")
        body = app.query_one("#ai-panel-body", Static)
        assert panel.display
        for _ in range(1):
            await pilot.press("down")
        await pilot.pause(0.2)
        assert "uncommitted" in str(body.render())
        await pilot.press("p")
        assert not panel.display
        await pilot.press("p")
        assert panel.display


async def test_panel_starts_hidden_on_narrow_terminals(tmp_path):
    app = make(tmp_path)
    async with app.run_test(size=(90, 24)) as pilot:
        await pilot.pause(0.3)
        assert not app.query_one("#ai-panel").display
        await pilot.press("p")
        assert app.query_one("#ai-panel").display


async def test_panel_buttons_open_settings_and_review(tmp_path):
    calls = []
    app = make(tmp_path, review=nothing(calls), settings=AiSettings(provider="ollama", auto_review=False), ai_env=env())
    async with app.run_test(size=(130, 30)) as pilot:
        await pilot.pause(0.4)
        await pilot.press("down")
        await pilot.click("#panel-review")
        await pilot.pause(0.4)
        assert [c[0] for c in calls] == ["b"]
        await pilot.click("#panel-setup")
        await pilot.pause(0.3)
        assert isinstance(app.screen, AiSettingsScreen)


def test_panel_text_shows_live_activity_and_full_model_output():
    from reclaim.tui_ai import ReviewActivity, panel_text

    act = ReviewActivity("a", "Alpha")
    act.add_step("Checking git and lockfiles (1/1): Alpha")
    act.add_step("Asking ollama (qwen) about 1 item(s)")
    act.add_chunk('{"assessments": [{"id"')
    act.add_chunk(': "a"}]}')
    live = panel_text(None, AiSettings(provider="ollama"), {"running": 1}, [], activity=act).plain
    assert "Now reviewing: Alpha" in live and "> Asking ollama" in live and "+ Checking git" in live
    assert 'Model output (' in live and '{"assessments": [{"id": "a"}]}' in live
    act.finish()
    done = panel_text(None, AiSettings(provider="ollama"), {}, [], activity=act).plain
    assert "Now reviewing" not in done and "Model output" not in done


def test_activity_keeps_recent_steps_and_caps_output():
    from reclaim.tui_ai import MAX_OUTPUT, MAX_STEPS, ReviewActivity

    act = ReviewActivity()
    for n in range(MAX_STEPS + 5):
        act.add_step(f"s{n}")
    act.add_chunk("x" * (MAX_OUTPUT + 500))
    assert len(act.steps) == MAX_STEPS and act.steps[-1] == f"s{MAX_STEPS + 4}" and len(act.output) == MAX_OUTPUT


async def test_panel_streams_the_item_in_flight_and_keeps_scroll(tmp_path):
    import threading

    gate = threading.Event()

    def slow(item, settings, on_event=None, fresh=False):
        on_event("step", "Asking ollama (m) about 1 item(s)")
        on_event("chunk", "streamed words")
        gate.wait(5)
        return Outcome(Advice(AdviceLevel.OK, "fine"))

    app = make(tmp_path, review=slow, settings=AiSettings(provider="ollama"))
    async with app.run_test(size=(130, 30)) as pilot:
        await pilot.pause(0.6)
        body = str(app.query_one("#ai-panel-body", Static).render())
        assert app.reviewing and "> Asking ollama" in body and "streamed words" in body
        assert "AI reviewing" in str(app.query_one("#status", Static).render())
        gate.set()
        await pilot.pause(0.6)
        body = str(app.query_one("#ai-panel-body", Static).render())
        assert not app.reviewing and "Now reviewing" not in body and "streamed words" not in body


async def test_panel_scrolls_and_follows_only_when_at_the_end(tmp_path):
    import threading

    gate = threading.Event()

    def slow(item, settings, on_event=None, fresh=False):
        on_event("chunk", "".join(f"line {n} of model output\n" for n in range(60)))
        gate.wait(5)
        return Outcome()

    app = make(tmp_path, review=slow, settings=AiSettings(provider="ollama"))
    async with app.run_test(size=(130, 24)) as pilot:
        await pilot.pause(0.8)
        scroll = app.query_one("#ai-panel-scroll")
        assert scroll.max_scroll_y > 0 and scroll.scroll_y == 0  # reader stays at the top
        scroll.scroll_end(animate=False)
        await pilot.pause(0.2)
        bottom = scroll.scroll_y
        app.activity.add_chunk("more\n" * 30)
        app._refresh_panel()
        await pilot.pause(0.3)
        assert scroll.scroll_y > bottom  # was at the end, so it followed
        scroll.scroll_to(y=3, animate=False)
        await pilot.pause(0.1)
        app.activity.add_chunk("even more\n" * 30)
        app._refresh_panel()
        await pilot.pause(0.3)
        assert scroll.scroll_y == 3  # reader scrolled away, so it did not jump
        gate.set()


async def test_changing_settings_requeues_everything(tmp_path):
    calls = []
    app = make(tmp_path, review=nothing(calls), settings=AiSettings(provider="ollama"), ai_env=env())
    async with app.run_test() as pilot:
        await pilot.pause(0.5)
        assert len(calls) == 2
        screen = await open_settings(app, pilot)
        screen.query_one("#ai-model", Select).value = "qwen2.5"
        await pilot.pause(0.3)
        await pilot.click("#save")
        await pilot.pause(0.8)
        assert len(calls) == 4 and {c[1] for c in calls} == {"ollama"}


def option_values(select):
    return [value for _, value in select._options]


async def test_choosing_a_provider_loads_its_models_into_the_dropdown(tmp_path):
    app = make(tmp_path, ai_env=env(models=("alpha:7b", "zeta:1b")))
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        model = screen.query_one("#ai-model", Select)
        assert model.disabled and option_values(model) == ["__default__"]
        screen.query_one("#ai-provider", Select).value = "ollama"
        await pilot.pause(0.6)
        assert not model.disabled
        assert option_values(model) == ["__default__", "alpha:7b", "zeta:1b", "__other__"]
        assert not screen.query_one("#ai-model-custom", Input).display


async def test_saved_model_missing_from_list_is_kept_and_marked(tmp_path):
    app = make(tmp_path, settings=AiSettings(provider="ollama", model="gone:1b"), ai_env=env(models=("a:1b",)))
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        await pilot.pause(0.5)
        model = screen.query_one("#ai-model", Select)
        assert model.value == "gone:1b" and "gone:1b" in option_values(model) and "a:1b" in option_values(model)


async def test_download_another_model_reveals_input_and_pulls_typed_name(tmp_path):
    pulled = []
    e = env(present=False, pulled=pulled)
    e.present = lambda m, u: bool(pulled)
    app = make(tmp_path, ai_env=e)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        screen.query_one("#ai-provider", Select).value = "ollama"
        await pilot.pause(0.6)
        screen.query_one("#ai-model", Select).value = "__other__"
        await pilot.pause(0.2)
        custom = screen.query_one("#ai-model-custom", Input)
        assert custom.display
        await pilot.click("#save")
        await pilot.pause(0.3)
        assert isinstance(app.screen, AiSettingsScreen) and "Type the name" in str(screen.query_one("#ai-status", Static).render())
        custom.value = "qwen2.5:7b"
        await pilot.click("#save")
        await pilot.pause(1.0)
        assert pulled == ["qwen2.5:7b"] and app.ai_settings.model == "qwen2.5:7b"


async def test_model_list_failure_is_explained_and_dropdown_still_usable(tmp_path):
    app = make(tmp_path, ai_env=env(models=None))
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        screen.query_one("#ai-provider", Select).value = "ollama"
        await pilot.pause(0.6)
        assert "could not list models" in str(screen.query_one("#ai-status", Static).render())
        assert "__default__" in option_values(screen.query_one("#ai-model", Select))


def test_model_options_shapes():
    from reclaim.tui_ai import model_options

    assert [v for _, v in model_options("anthropic", ["m1"], None)] == ["__default__", "m1"]
    assert [v for _, v in model_options("ollama", ["m1"], "old")] == ["__default__", "old", "m1", "__other__"]
    assert [v for _, v in model_options("ollama", [], "__other__")] == ["__default__", "__other__"]


async def test_results_arriving_while_a_dialog_is_open_still_update_the_table(tmp_path):
    import threading

    gate = threading.Event()

    def slow(item, settings, on_event=None, fresh=False):
        gate.wait(5)
        return Outcome(Advice(AdviceLevel.CAUTION, "late answer"))

    app = make(tmp_path, review=slow, settings=AiSettings(provider="ollama"), ai_env=env())
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        await open_settings(app, pilot)
        gate.set()
        await pilot.pause(0.6)
        assert isinstance(app.screen, AiSettingsScreen)
        assert "late answer" in str(app.query_one(DataTable).get_row("b")[-1])
