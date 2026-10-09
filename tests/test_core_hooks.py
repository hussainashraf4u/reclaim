from pathlib import Path

import pytest

from reclaim.catalog import entry_to_items, parse_entries
from reclaim.config import ConfigError, load_config
from reclaim.model import Item, Risk
from reclaim.scan import drop_covered, scan_all
from reclaim.units import parse_size


def test_parse_size_handles_spaced_units():
    assert parse_size("7.7 GB") == 7_700_000_000
    assert parse_size("274 MB") == 274_000_000
    assert parse_size("19.19GB") == 19_190_000_000
    assert parse_size("") == 0 and parse_size("junk") == 0


def mk(id, path, group="G", size=1, actions=()):
    return Item(id=id, label=id, group=group, size_bytes=size, risk=Risk.SAFE, path=Path(path) if path else None, actions=actions)


def test_catalog_items_carry_path(tmp_path, make_ctx):
    (tmp_path / ".cache" / "uv").mkdir(parents=True)
    (e,) = parse_entries('[[item]]\nid="uv"\nlabel="uv"\ngroup="P"\nrisk="safe"\npath="~/.cache/uv"\n')
    (item,) = entry_to_items(e, make_ctx())
    assert item.path == tmp_path / ".cache" / "uv" and item.to_json()["path"] == str(item.path)


def test_drop_covered_removes_overlapping_large_folders_only():
    items = [
        mk("uv", "/h/.cache/uv"),
        mk("big-exact", "/h/.cache/uv", "Large folders"),
        mk("big-parent", "/h/.cache", "Large folders"),
        mk("big-child", "/h/.cache/uv/wheels", "Large folders"),
        mk("big-other", "/h/Movies2", "Large folders"),
        mk("nopath", None, "Large folders"),
    ]
    assert [i.id for i in drop_covered(items)] == ["uv", "big-other", "nopath"]


def test_scan_all_applies_drop_covered(make_ctx, tmp_path):
    class P:
        name = "p"

        def scan(self, ctx):
            yield mk("a", str(tmp_path / "x"))
            yield mk("b", str(tmp_path / "x"), "Large folders")

    assert [i.id for i in scan_all(make_ctx(), [], [P()]).items] == ["a"]


def test_config_large_dir_mb(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("large_dir_mb = 500\n")
    assert load_config(f, tmp_path).large_dir_mb == 500
    assert load_config(tmp_path / "none.toml", tmp_path).large_dir_mb == 1000
    f.write_text("large_dir_mb = 0\n")
    with pytest.raises(ConfigError):
        load_config(f, tmp_path)


def test_large_folder_is_kept_when_covered_items_are_a_small_part_of_it():
    big = mk("big", "/h/.android", "Large folders", size=100)
    small = mk("avd", "/h/.android/avd/x", size=10)
    assert [i.id for i in drop_covered([small, big])] == ["avd", "big"]


def test_large_folder_is_dropped_when_covered_items_are_most_of_it():
    big = mk("big", "/h/.android", "Large folders", size=100)
    covered = mk("avd", "/h/.android/avd/x", size=60)
    assert [i.id for i in drop_covered([covered, big])] == ["avd"]


def test_items_without_path_fall_back_to_their_remove_targets():
    from reclaim.model import RemovePath
    big = mk("big", "/h/.android", "Large folders", size=100)
    covered = mk("avd", None, size=80, actions=(RemovePath(Path("/h/.android/avd/x")),))
    assert [i.id for i in drop_covered([covered, big])] == ["avd"]


def test_dedupe_ties_keep_the_shallowest_path():
    from reclaim.scan import dedupe
    child = mk("child", "/h/a/b", size=100)
    parent = mk("parent", "/h/a", size=100)
    assert [i.id for i in dedupe([child, parent])] == ["parent"]


def test_dedupe_uses_item_path_so_artifacts_inside_a_worktree_collapse():
    from reclaim.model import RemovePath
    from reclaim.scan import dedupe
    worktree = mk("wt", "/h/p/.worktrees/x", size=100, actions=())
    artifact = mk("art", None, size=40, actions=(RemovePath(Path("/h/p/.worktrees/x/node_modules")),))
    other = mk("o", "/h/other", size=5)
    assert [i.id for i in dedupe([worktree, artifact, other])] == ["wt", "o"]


def test_ignored_items_still_cover_large_folders_but_are_not_returned(make_ctx, tmp_path):
    class P:
        name = "p"

        def scan(self, ctx):
            yield mk("cat", str(tmp_path / "x"), size=100)
            yield mk("big", str(tmp_path / "x"), "Large folders", size=100)
            yield mk("keep", str(tmp_path / "y"), size=1)

    res = scan_all(make_ctx(), [], [P()], ignore=frozenset({"cat"}))
    assert [i.id for i in res.items] == ["keep"]


def test_catalog_paths_must_be_home_relative_or_absolute():
    from reclaim.catalog import CatalogError
    for bad in ('path="relative/x"', 'glob="x/*"'):
        with pytest.raises(CatalogError):
            parse_entries(f'[[item]]\nid="a"\nlabel="A"\ngroup="G"\nrisk="safe"\n{bad}\n')
    parse_entries('[[item]]\nid="a"\nlabel="A"\ngroup="G"\nrisk="safe"\npath="/abs/x"\n')


def test_stale_days_zero_and_negative_min_size_are_rejected(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("stale_days = 0\n")
    with pytest.raises(ConfigError):
        load_config(f, tmp_path)


def test_negative_min_size_flag_is_rejected(tmp_path):
    import io
    from reclaim.cli import main
    from reclaim.context import ScanContext
    with pytest.raises(SystemExit):
        main(["--scan-only", "--min-size-mb", "-1"], out=io.StringIO(), ctx=ScanContext(home=tmp_path), entries=[], providers=[])


def test_cli_installs_the_caching_sizer_for_the_default_sizer(tmp_path):
    import io
    from reclaim.cli import main
    from reclaim.context import ScanContext
    from reclaim.sizing import TreeSizer
    ctx = ScanContext(home=tmp_path)
    main(["--scan-only", "--json"], out=io.StringIO(), ctx=ctx, entries=[], providers=[])
    assert isinstance(ctx.sizer, TreeSizer)
