from pathlib import Path

import pytest

from reclaim.config import Config, ConfigError, default_config_path, load_config


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml", tmp_path) == Config()


def test_parses_all_keys(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text(
        'project_roots = ["~/Documents/projects", "/abs/p"]\n'
        'extra_allowlist = ["~/.cache/extra"]\n'
        'ignore = ["trash", "maven"]\n'
        "min_size_mb = 10\nstale_days = 7\n"
    )
    cfg = load_config(f, tmp_path)
    assert cfg.project_roots == (tmp_path / "Documents/projects", Path("/abs/p"))
    assert cfg.extra_allowlist == (tmp_path / ".cache/extra",)
    assert cfg.ignore == frozenset({"trash", "maven"})
    assert cfg.min_size_mb == 10 and cfg.stale_days == 7


@pytest.mark.parametrize("text", [
    "project_roots = [",
    'project_roots = ["relative/dir"]',
    'ignore = "trash"',
    'min_size_mb = "ten"',
    "stale_days = -1",
])
def test_invalid_config_raises_one_line_error(tmp_path, text):
    f = tmp_path / "c.toml"
    f.write_text(text)
    with pytest.raises(ConfigError) as exc:
        load_config(f, tmp_path)
    assert "\n" not in str(exc.value)


def test_default_path(tmp_path):
    assert default_config_path(tmp_path) == tmp_path / ".config" / "reclaim" / "config.toml"


@pytest.mark.parametrize("key,value", [
    ("project_roots", "~/"), ("project_roots", "/"), ("project_roots", "~/.."), ("project_roots", "~/Library/Caches"),
    ("extra_allowlist", "~/"), ("extra_allowlist", "/"), ("extra_allowlist", "~/Documents"),
    ("extra_allowlist", "/usr/local"), ("extra_allowlist", "~/.ssh/x"), ("extra_allowlist", "~/a/../b"),
])
def test_dangerous_roots_are_rejected(tmp_path, key, value):
    f = tmp_path / "c.toml"
    f.write_text(f'{key} = ["{value}"]\n')
    with pytest.raises(ConfigError) as exc:
        load_config(f, tmp_path)
    assert "\n" not in str(exc.value)


def test_reasonable_roots_are_accepted(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('project_roots = ["~/Documents/projects"]\nextra_allowlist = ["~/.cache/extra"]\n')
    cfg = load_config(f, tmp_path)
    assert cfg.project_roots == (tmp_path / "Documents/projects",)


def test_unreadable_config_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path, tmp_path)
    bad = tmp_path / "bin.toml"
    bad.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ConfigError):
        load_config(bad, tmp_path)


def test_unknown_keys_are_rejected(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('project_root = ["~/p"]\n')
    with pytest.raises(ConfigError) as exc:
        load_config(f, tmp_path)
    assert "project_root" in str(exc.value)


def test_ancestor_of_home_is_rejected_as_project_root(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text(f'project_roots = ["{tmp_path.parent}"]\n')
    with pytest.raises(ConfigError):
        load_config(f, tmp_path)
