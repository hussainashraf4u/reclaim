import io
import json

from conftest import FakeRun, ok

from reclaim.ai import setup
from reclaim.ai.advisor import advise, merge
from reclaim.ai.errors import LlmError, LlmErrorCode, LlmResult
from reclaim.ai.provider import AnthropicProvider, FallbackProvider, LlmProvider, OllamaProvider, build_provider, parse_json_object
from reclaim.cli import main
from reclaim.config import load_config, set_keys
from reclaim.model import Advice, AdviceLevel, Item, RemovePath, Risk


class FakeProvider(LlmProvider):
    name, model = "fake", "m1"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def chat_json(self, system_prompt, user_prompt, *, schema=None, on_chunk=None):
        self.calls.append(user_prompt)
        return self.reply


def artifact(tmp_path, name="node_modules", risk=Risk.REGENERABLE):
    project = tmp_path / "proj"
    path = project / name
    path.mkdir(parents=True)
    return Item(id="a1", label="proj/node_modules", group="Project artifacts", size_bytes=5 * 1024**3, risk=risk,
                actions=(RemovePath(path),), path=path)


def git_run(dirty="", days_ago=100):
    last = str(1_800_000_000 - days_ago * 86400)
    return FakeRun({
        ("git", "rev-parse", "--is-inside-work-tree"): ok("true"),
        ("git", "status", "--porcelain"): ok(dirty),
        ("git", "log", "-1", "--format=%ct"): ok(last),
    })


def test_dirty_repo_raises_caution_offline(tmp_path, make_ctx):
    item = artifact(tmp_path)
    (item.path.parent / "package-lock.json").write_text("{}")
    res = advise([item], make_ctx(run=git_run(dirty=" M a.py\n?? b.py\n")))
    assert item.advice.level is AdviceLevel.CAUTION and "2 uncommitted" in item.advice.reason
    assert res.advice == {"a1": item.advice}


def test_missing_lockfile_and_clean_old_repo(tmp_path, make_ctx):
    item = artifact(tmp_path)
    advise([item], make_ctx(run=git_run()))
    assert "no lockfile" in item.advice.reason
    (item.path.parent / "package-lock.json").write_text("{}")
    item.advice = None
    advise([item], make_ctx(run=git_run()))
    assert item.advice is None


def test_recent_commit_is_active(tmp_path, make_ctx):
    item = artifact(tmp_path)
    (item.path.parent / "package-lock.json").write_text("{}")
    advise([item], make_ctx(run=git_run(days_ago=2)))
    assert "active" in item.advice.reason


def test_model_can_raise_but_not_lower(tmp_path, make_ctx):
    item = artifact(tmp_path)
    (item.path.parent / "package-lock.json").write_text("{}")
    reply = LlmResult({"assessments": [{"id": "a1", "level": "keep", "reason": "looks like real work", "rebuild": "10 min"}]})
    advise([item], make_ctx(run=git_run()), FakeProvider(reply))
    assert item.advice.level is AdviceLevel.KEEP and item.advice.source == "llm"
    low = merge(Advice(AdviceLevel.CAUTION, "dirty"), Advice(AdviceLevel.OK, "fine", "1 min", "llm"))
    assert low.level is AdviceLevel.CAUTION and low.reason == "dirty" and low.rebuild == "1 min"


def test_model_cannot_invent_ids_or_levels(tmp_path, make_ctx):
    item = artifact(tmp_path)
    reply = LlmResult({"assessments": [{"id": "/etc", "level": "keep", "reason": "x"}, {"id": "a1", "level": "bogus", "reason": "y"}]})
    res = advise([item], make_ctx(run=git_run()), FakeProvider(reply))
    assert "/etc" not in res.advice and "bogus" not in {a.level.value for a in res.advice.values()}


def test_failure_keeps_heuristics_and_reports_error(tmp_path, make_ctx):
    item = artifact(tmp_path)
    err = LlmError(LlmErrorCode.TRANSPORT_ERROR, "down")
    res = advise([item], make_ctx(run=git_run(dirty=" M a\n")), FakeProvider(LlmResult(None, err)))
    assert res.error is err and item.advice.source == "heuristic"


def test_payload_redacts_home_and_cache_avoids_second_call(tmp_path, make_ctx):
    item = artifact(tmp_path)
    reply = LlmResult({"assessments": [{"id": "a1", "level": "ok", "reason": "cache", "rebuild": "2 min"}]})
    provider, cache = FakeProvider(reply), tmp_path / "cache.json"
    ctx = make_ctx(run=git_run())
    advise([item], ctx, provider, cache)
    advise([item], ctx, provider, cache)
    assert len(provider.calls) == 1
    assert str(tmp_path) not in provider.calls[0] and "~/proj/node_modules" in provider.calls[0]


def test_ollama_and_anthropic_wire_format():
    seen = {}

    def post(url, headers, body, timeout):
        seen.update(url=url, headers=dict(headers), body=body)
        return {"message": {"content": '```json\n{"a": 1}\n```'}, "content": [{"type": "text", "text": '{"b": 2}'}]}

    assert OllamaProvider("m", "http://h:1/", post).chat_json("s", "u", schema={"t": 1}).value == {"a": 1}
    assert seen["url"] == "http://h:1/api/chat" and seen["body"]["format"] == {"t": 1} and seen["body"]["stream"] is False
    assert AnthropicProvider("k", "m", post).chat_json("s", "u").value == {"b": 2}
    assert seen["headers"]["x-api-key"] == "k"


def test_provider_errors_are_returned_not_raised():
    def boom(*a):
        raise TimeoutError("slow")

    res = OllamaProvider("m", post=boom).chat_json("s", "u")
    assert res.error.code is LlmErrorCode.TIMEOUT
    assert OllamaProvider("m", post=lambda *a: {"message": {"content": "no json"}}).chat_json("s", "u").error.code is LlmErrorCode.MALFORMED_RESPONSE


def test_fallback_routes_to_secondary():
    bad = FakeProvider(LlmResult(None, LlmError(LlmErrorCode.UNKNOWN, "x")))
    good = FakeProvider(LlmResult({"ok": 1}))
    assert FallbackProvider(bad, good).chat_json("s", "u").value == {"ok": 1}


def test_build_provider():
    assert build_provider("none", None, None, {}) is None
    assert build_provider("ollama", None, None, {}).name == "ollama"
    try:
        build_provider("anthropic", None, None, {})
    except ValueError as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)
    else:
        raise AssertionError


def test_parse_json_object():
    assert parse_json_object('noise {"a": {"b": 1}} tail') == {"a": {"b": 1}}
    assert parse_json_object("[1]") is None and parse_json_object("{bad") is None


def fake_env(installed=True, up=True, present=False, pulled=None, code=0):
    def pull(model, on_line, cancelled=lambda: False):
        if pulled is not None:
            pulled.append(model)
        on_line("pulling 50%")
        return code

    return setup.AiEnv(which=lambda n: "/bin/ollama" if installed else None, env={}, up=lambda u: up, present=lambda m, u: present, pull=pull)


def test_enable_ollama_downloads_only_after_confirm():
    pulled = []

    def run(confirm, **kw):
        return setup.enable_ollama("m", "u", confirm=lambda q: confirm, env=fake_env(pulled=pulled, **kw), on_line=lambda s: None)

    assert not run(True, installed=False).ok and not run(True, up=False).ok
    assert not run(False).ok and pulled == []
    assert run(True, present=True).ok and pulled == []
    assert run(True).ok and pulled == ["m"]


def test_enable_failed_pull_is_failure():
    assert not setup.enable_ollama("m", "u", confirm=lambda q: True, env=fake_env(code=1), on_line=lambda s: None).ok


def test_check_reports_each_state():
    assert setup.check("none", None, None, fake_env()).ok
    assert not setup.check("ollama", "m", None, fake_env(installed=False)).ok
    assert not setup.check("ollama", "m", None, fake_env(up=False)).ok
    missing = setup.check("ollama", "m", None, fake_env())
    assert not missing.ok and missing.needs_download
    assert setup.check("ollama", "m", None, fake_env(present=True)).ok
    assert not setup.check("anthropic", None, None, fake_env()).ok
    assert setup.check("anthropic", None, None, setup.AiEnv(env={"ANTHROPIC_API_KEY": "k"})).ok


def test_pull_streaming_reports_progress_lines_and_strips_ansi():
    import io

    class Proc:
        stdout = io.BufferedReader(io.BytesIO(b"pulling manifest\r\x1b[2Kpulling 10%\rpulling 90%\nsuccess\n"))

        def wait(self):
            return 0

        def terminate(self):
            self.stopped = True

    lines = []
    assert setup.pull_streaming("m", lines.append, popen=lambda *a, **k: Proc()) == 0
    assert lines == ["pulling manifest", "pulling 10%", "pulling 90%", "success"]
    proc = Proc()
    proc.stdout = io.BufferedReader(io.BytesIO(b"a\nb\n"))
    assert setup.pull_streaming("m", lambda s: None, cancelled=lambda: True, popen=lambda *a, **k: proc) == 1 and proc.stopped


def test_set_keys_and_config_roundtrip(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('min_size_mb = 10\nai_provider = "none"\n')
    set_keys(cfg, {"ai_provider": "ollama", "ai_model": "qwen"})
    loaded = load_config(cfg, tmp_path)
    assert (loaded.ai_provider, loaded.ai_model, loaded.min_size_mb) == ("ollama", "qwen", 10)
    set_keys(cfg, {"ai_provider": None, "ai_model": None})
    assert load_config(cfg, tmp_path).ai_provider == "none" and "min_size_mb" in cfg.read_text()


def test_cli_ai_enable_writes_config_and_disable_removes(tmp_path, make_ctx, monkeypatch):
    monkeypatch.setattr(setup, "daemon_up", lambda u, timeout=3.0: True)
    monkeypatch.setattr(setup, "has_model", lambda m, u: True)
    cfg = tmp_path / "c.toml"
    out = io.StringIO()
    assert main(["ai", "enable", "--model", "qwen", "--config", str(cfg)], out=out, ctx=make_ctx(), entries=[], providers=[]) == 0
    assert load_config(cfg, tmp_path).ai_provider == "ollama"
    main(["ai", "disable", "--config", str(cfg)], out=io.StringIO(), ctx=make_ctx(), entries=[], providers=[])
    assert load_config(cfg, tmp_path).ai_provider == "none"


def test_cli_enable_fails_without_ollama(tmp_path, make_ctx):
    cfg = tmp_path / "c.toml"
    rc = main(["ai", "enable", "--config", str(cfg)], out=io.StringIO(), ctx=make_ctx(which=lambda n: None), entries=[], providers=[])
    assert rc != 0 and not cfg.exists()


def test_cli_advise_offline(tmp_path, make_ctx):
    from reclaim.catalog import parse_entries

    (tmp_path / "x").mkdir()
    entries = parse_entries('[[item]]\nid="x"\nlabel="X"\ngroup="G"\nrisk="data"\npath="~/x"\n[item.action]\ntype="remove"\n')
    out = io.StringIO()
    main(["advise", "--min-size-mb", "0", "--no-cache", "--config", str(tmp_path / "none.toml")], out=out, ctx=make_ctx(), entries=entries, providers=[])
    text = out.getvalue()
    assert "AI is off" in text


def test_ollama_streams_chunks_and_reassembles_reply():
    lines = [
        json.dumps({"message": {"content": '{"a":'}, "done": False}),
        "",
        json.dumps({"message": {"content": " 1}"}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True}),
    ]
    seen = {}

    def stream(url, headers, body, timeout):
        seen["body"] = body
        yield from lines

    pieces = []
    res = OllamaProvider("m", stream=stream).chat_json("s", "u", on_chunk=pieces.append)
    assert res.value == {"a": 1} and pieces == ['{"a":', " 1}"] and seen["body"]["stream"] is True


def test_anthropic_streams_sse_text_deltas():
    def delta(text):
        return "data: " + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})

    lines = ["event: message_start", "data: " + json.dumps({"type": "message_start"}), delta('{"b"'), "", delta(": 2}"),
             "data: " + json.dumps({"type": "message_stop"})]
    pieces = []
    res = AnthropicProvider("k", stream=lambda *a: iter(lines)).chat_json("s", "u", on_chunk=pieces.append)
    assert res.value == {"b": 2} and pieces == ['{"b"', ": 2}"]


def test_stream_error_line_becomes_classified_error():
    lines = [json.dumps({"error": "model not found"})]
    res = OllamaProvider("m", stream=lambda *a: iter(lines)).chat_json("s", "u", on_chunk=lambda p: None)
    assert res.error is not None and "model not found" in res.error.detail


def test_advise_reports_steps_and_streamed_chunks(tmp_path, make_ctx):
    item = artifact(tmp_path)
    reply = LlmResult({"assessments": [{"id": "a1", "level": "ok", "reason": "fine", "rebuild": ""}]})

    class Streaming(FakeProvider):
        def chat_json(self, system_prompt, user_prompt, *, schema=None, on_chunk=None):
            on_chunk("{par")
            on_chunk("tial")
            return reply

    events = []
    advise([item], make_ctx(run=git_run()), Streaming(reply), None, 15, lambda kind, text: events.append((kind, text)))
    steps = [t for k, t in events if k == "step"]
    assert any("Checking git" in t for t in steps) and any("Asking fake (m1)" in t for t in steps) and steps[-1].startswith("Done")
    assert [t for k, t in events if k == "chunk"] == ["{par", "tial"]


def test_advise_reports_model_failure_step(tmp_path, make_ctx):
    item = artifact(tmp_path)
    events = []
    advise([item], make_ctx(run=git_run()), FakeProvider(LlmResult(None, LlmError(LlmErrorCode.TIMEOUT, "slow"))), None, 15,
           lambda k, t: events.append(t))
    assert any("Model failed" in t and "keeping offline" in t for t in events)


def test_list_models_per_provider():
    from reclaim.ai.provider import list_models

    seen = {}

    def get(url, headers, timeout):
        seen["url"], seen["headers"] = url, dict(headers)
        if "tags" in url:
            return {"models": [{"name": "b:1"}, {"name": "a:1"}]}
        return {"data": [{"id": "claude-x"}, {"id": "claude-y"}]}

    assert list_models("ollama", "http://h:1/", {}, get).value == ["a:1", "b:1"] and seen["url"] == "http://h:1/api/tags"
    assert list_models("anthropic", None, {"ANTHROPIC_API_KEY": "k"}, get).value == ["claude-x", "claude-y"]
    assert seen["headers"]["x-api-key"] == "k"


def test_list_models_failures_are_returned():
    from reclaim.ai.provider import list_models

    res = list_models("anthropic", None, {}, lambda *a: {})
    assert res.error.code is LlmErrorCode.AUTH_OR_CONFIG_ERROR

    def boom(*a):
        raise OSError("refused")

    assert list_models("ollama", None, {}, boom).error.code is LlmErrorCode.TRANSPORT_ERROR
    assert list_models("ollama", None, {}, lambda *a: {"nope": 1}).error.code is LlmErrorCode.MALFORMED_RESPONSE


def test_advise_one_reviews_report_only_items_and_can_skip_the_cache(tmp_path, make_ctx):
    from reclaim.ai.advisor import advise_one

    item = artifact(tmp_path)
    item.report_only = True
    reply = LlmResult({"assessments": [{"id": "a1", "level": "caution", "reason": "r", "rebuild": "2 min"}]})
    provider, cache = FakeProvider(reply), tmp_path / "cache.json"
    ctx = make_ctx(run=git_run())
    assert advise([item], ctx, provider, cache).advice == {}  # batch review skips report-only
    first = advise_one(item, ctx, provider, cache)
    assert first.advice["a1"].level is AdviceLevel.CAUTION and item.advice == first.advice["a1"]
    advise_one(item, ctx, provider, cache)
    assert len(provider.calls) == 1  # saved answer reused
    advise_one(item, ctx, provider, cache, use_cache=False)
    assert len(provider.calls) == 2  # Review button forces a fresh answer
    assert len(json.loads(provider.calls[0])["items"]) == 1
