from conftest import FakeRun, ok

from reclaim.model import Risk, Run
from reclaim.providers.ollama import OllamaProvider

TABLE = (
    "NAME                       ID              SIZE      MODIFIED    \n"
    "nomic-embed-text:latest    0a109f422b47    274 MB    3 weeks ago    \n"
    "qwen3.5:9b-mlx             203e30078279    8.9 GB    4 weeks ago    \n"
    "gemma4:12b-mlx             ded7a2735003    7.7 GB    4 weeks ago    \n"
)
CMD = ("ollama", "list")


def run(make_ctx, table, **kw):
    kw.setdefault("is_running", lambda n: n == "ollama")
    ctx = make_ctx(run=FakeRun(table), **kw)
    return {i.id: i for i in OllamaProvider().scan(ctx)}


def test_models_become_items(make_ctx):
    got = run(make_ctx, {CMD: ok(TABLE)})
    assert set(got) == {"ollama:nomic-embed-text:latest", "ollama:qwen3.5:9b-mlx", "ollama:gemma4:12b-mlx"}
    item = got["ollama:qwen3.5:9b-mlx"]
    assert item.size_bytes == 8_900_000_000 and item.risk is Risk.REGENERABLE
    assert item.actions == (Run(("ollama", "rm", "qwen3.5:9b-mlx")),)
    assert all(" " not in i for i in got)


def test_min_size_filters(make_ctx):
    assert set(run(make_ctx, {CMD: ok(TABLE)}, min_size=5 * 10**9)) == {"ollama:qwen3.5:9b-mlx", "ollama:gemma4:12b-mlx"}


def test_failures_and_header_only_yield_nothing(make_ctx):
    assert run(make_ctx, {}) == {}
    assert run(make_ctx, {CMD: ok("", code=1)}) == {}
    assert run(make_ctx, {CMD: ok(TABLE.splitlines()[0] + "\n")}) == {}
    assert list(OllamaProvider().scan(make_ctx(which=lambda n: None))) == []


def test_malformed_lines_are_skipped(make_ctx):
    assert run(make_ctx, {CMD: ok("NAME  ID  SIZE  MODIFIED\nbroken line\n")}) == {}


def test_items_carry_models_path_so_large_dirs_dedupe(make_ctx, tmp_path):
    got = run(make_ctx, {CMD: ok(TABLE)})
    assert all(i.path == tmp_path / ".ollama" / "models" for i in got.values())


SHARED = (
    "NAME              ID              SIZE      MODIFIED\n"
    "llama3:latest     aaaa11112222    4.7 GB    1 week ago\n"
    "llama3:8b         aaaa11112222    4.7 GB    1 week ago\n"
    "other:1b          bbbb33334444    1.3 GB    2 weeks ago\n"
)


def test_tags_with_the_same_id_are_one_item_removing_every_tag(make_ctx):
    got = run(make_ctx, {CMD: ok(SHARED)})
    assert len(got) == 2
    item = got["ollama:llama3:latest"]
    assert item.size_bytes == 4_700_000_000
    assert item.actions == (Run(("ollama", "rm", "llama3:latest")), Run(("ollama", "rm", "llama3:8b")))
    assert "llama3:8b" in item.label


def test_server_not_running_never_calls_ollama_and_explains(make_ctx, tmp_path):
    (tmp_path / ".ollama" / "models").mkdir(parents=True)
    runner = FakeRun({CMD: ok(TABLE)})
    ctx = make_ctx(run=runner, is_running=lambda n: False)
    items = list(OllamaProvider().scan(ctx))
    assert runner.calls == []
    (item,) = items
    assert item.id == "ollama-down" and item.report_only and item.disabled_reason == "Ollama server is not running"


def test_server_not_running_and_no_models_dir_yields_nothing(make_ctx):
    assert list(OllamaProvider().scan(make_ctx(is_running=lambda n: False))) == []
