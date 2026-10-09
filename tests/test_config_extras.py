import io

import pytest

from reclaim.cli import main
from reclaim.config import ConfigError, load_config, set_ignore
from reclaim.context import ScanContext


def test_new_keys_load_and_validate(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text("use_trash = true\nbig_file_mb = 10\nduplicate_min_mb = 5\n")
    c = load_config(cfg, tmp_path)
    assert c.use_trash and c.big_file_mb == 10 and c.duplicate_min_mb == 5
    cfg.write_text('use_trash = "yes"\n')
    with pytest.raises(ConfigError):
        load_config(cfg, tmp_path)


def test_set_ignore_creates_replaces_and_preserves(tmp_path):
    cfg = tmp_path / "sub" / "config.toml"
    set_ignore(cfg, {"b", "a"})
    assert cfg.read_text() == 'ignore = ["a", "b"]\n'
    cfg.write_text('min_size_mb = 10\nignore = [\n  "x",\n]\nstale_days = 5\n')
    set_ignore(cfg, {"y"})
    c = load_config(cfg, tmp_path)
    assert c.ignore == {"y"} and c.min_size_mb == 10 and c.stale_days == 5


def test_ignore_and_unignore_commands(tmp_path):
    ctx = lambda: ScanContext(home=tmp_path)  # noqa: E731
    main(["ignore", "foo", "bar"], out=io.StringIO(), ctx=ctx(), entries=[], providers=[])
    assert load_config(tmp_path / ".config" / "reclaim" / "config.toml", tmp_path).ignore == {"foo", "bar"}
    main(["unignore", "foo"], out=io.StringIO(), ctx=ctx(), entries=[], providers=[])
    assert load_config(tmp_path / ".config" / "reclaim" / "config.toml", tmp_path).ignore == {"bar"}


def test_subcommand_accepts_config_flag_and_bad_config_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("nope = 1\n")
    code = main(["history", "--config", str(bad)], out=io.StringIO(), ctx=ScanContext(home=tmp_path), entries=[], providers=[])
    assert code == 2


def test_doctor_lists_providers(tmp_path):
    class P:
        name = "project_artifacts"

        def scan(self, ctx):
            return iter(())

    out = io.StringIO()
    code = main(["doctor"], out=out, ctx=ScanContext(home=tmp_path, which=lambda n: None), entries=[], providers=[P()])
    assert code == 0 and "project_artifacts" in out.getvalue() and "set project_roots" in out.getvalue()
