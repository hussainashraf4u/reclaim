import io

from reclaim.cli import main
from reclaim.context import RunResult, ScanContext, run_command
from reclaim.model import Item, RemovePath, Risk
from reclaim.providers import all_providers, provider_roots
from reclaim.scan import scan_all


class P:
    name = "p"

    def roots(self, ctx):
        return [ctx.home / "extra"]

    def scan(self, ctx):
        yield Item(id="keep", label="K", group="G", size_bytes=2, risk=Risk.SAFE, actions=(RemovePath(ctx.home / "extra" / "k"),))
        yield Item(id="drop", label="D", group="G", size_bytes=1, risk=Risk.SAFE)


def test_run_command_missing_binary_is_none():
    assert run_command(["definitely-not-a-binary-xyz"]) is None


def test_run_command_returns_result():
    res = run_command(["echo", "hi"])
    assert isinstance(res, RunResult) and res.returncode == 0 and res.stdout.strip() == "hi"


def test_provider_roots_collects_optional_roots(make_ctx):
    ctx = make_ctx()
    assert provider_roots([P(), object()], ctx) == [ctx.home / "extra"]


def test_all_providers_returns_list():
    assert isinstance(all_providers(), list)


def test_scan_all_ignores_ids(make_ctx):
    res = scan_all(make_ctx(), [], [P()], ignore=frozenset({"drop"}))
    assert [i.id for i in res.items] == ["keep"]


def test_main_wires_config_providers_and_roots(tmp_path):
    cfg = tmp_path / ".config" / "reclaim"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text('ignore = ["drop"]\nextra_allowlist = ["~/.cache/allowed"]\nstale_days = 9\n')
    launched = {}
    ctx = ScanContext(home=tmp_path)
    code = main([], out=io.StringIO(), ctx=ctx, entries=[], providers=[P()], launch=lambda **kw: launched.update(kw))
    assert code == 0 and ctx.stale_days == 9
    assert launched["ignore"] == frozenset({"drop"})
    roots = launched["roots"]
    assert tmp_path / "extra" in roots and tmp_path / ".cache" / "allowed" in roots and tmp_path / ".npm" in roots


def test_main_bad_config_exits_2(tmp_path, capsys):
    cfg = tmp_path / ".config" / "reclaim"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text("project_roots = [")
    code = main(["--scan-only"], out=io.StringIO(), ctx=ScanContext(home=tmp_path), entries=[], providers=[])
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("reclaim:") and err.count("\n") == 1
