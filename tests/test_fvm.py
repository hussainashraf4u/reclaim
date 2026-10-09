import os

from reclaim.model import RemovePath, Risk
from reclaim.providers._walk import find_files
from reclaim.providers.fvm import FvmProvider, pinned_versions
from reclaim.safety import check_removable, default_roots


def versions(tmp_path, *names):
    for n in names:
        (tmp_path / "fvm" / "versions" / n).mkdir(parents=True)


def pin(tmp_path, project, text, name=".fvmrc"):
    d = tmp_path / "proj" / project
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


def items(tmp_path, make_ctx, **kw):
    ctx = make_ctx(project_roots=kw.pop("project_roots", (tmp_path / "proj",)), **kw)
    return {i.id: i for i in FvmProvider().scan(ctx)}


def test_find_files_prunes_heavy_dirs_and_limits_depth(tmp_path):
    (tmp_path / "p" / "a" / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "p" / "a" / "node_modules" / "x" / ".fvmrc").write_text("{}")
    (tmp_path / "p" / "a" / ".fvmrc").write_text("{}")
    (tmp_path / "p" / "b" / "c" / "d" / "e" / "f").mkdir(parents=True)
    (tmp_path / "p" / "b" / "c" / "d" / "e" / "f" / ".fvmrc").write_text("{}")
    found = list(find_files((tmp_path / "p",), {".fvmrc"}, max_depth=4))
    assert found == [tmp_path / "p" / "a" / ".fvmrc"]


def test_pinned_versions_reads_both_formats(tmp_path):
    pin(tmp_path, "a", '{"flutter": "3.44.8"}')
    pin(tmp_path, "b", '{"flutterSdkVersion": "3.38.10"}', name="fvm_config.json")
    (tmp_path / "proj" / "b" / ".fvm").mkdir()
    os.rename(tmp_path / "proj" / "b" / "fvm_config.json", tmp_path / "proj" / "b" / ".fvm" / "fvm_config.json")
    versions_, ok = pinned_versions((tmp_path / "proj",))
    assert versions_ == {"3.44.8", "3.38.10"} and ok


def test_unreadable_pin_marks_result_uncertain(tmp_path):
    pin(tmp_path, "a", "{not json")
    assert pinned_versions((tmp_path / "proj",))[1] is False


def test_unpinned_versions_are_removable_pinned_and_default_kept(tmp_path, make_ctx):
    versions(tmp_path, "3.38.10", "3.44.8", "stable", "3.27.0")
    pin(tmp_path, "a", '{"flutter": "3.44.8"}')
    os.symlink(tmp_path / "fvm" / "versions" / "stable", tmp_path / "fvm" / "default")
    got = items(tmp_path, make_ctx)
    assert set(got) == {"fvm:3.38.10", "fvm:3.27.0"}
    item = got["fvm:3.38.10"]
    assert item.risk is Risk.REGENERABLE and item.executable
    assert item.actions == (RemovePath(tmp_path / "fvm" / "versions" / "3.38.10"),)


def test_without_project_roots_everything_is_report_only(tmp_path, make_ctx):
    versions(tmp_path, "3.38.10")
    got = items(tmp_path, make_ctx, project_roots=())
    assert got["fvm:3.38.10"].report_only and not got["fvm:3.38.10"].executable


def test_unreadable_pin_makes_items_report_only(tmp_path, make_ctx):
    versions(tmp_path, "3.38.10")
    pin(tmp_path, "a", "{not json")
    assert items(tmp_path, make_ctx)["fvm:3.38.10"].report_only


def test_name_with_space_gets_hashed_id_and_min_size_filters(tmp_path, make_ctx):
    versions(tmp_path, "custom build")
    pin(tmp_path, "a", '{"flutter": "x"}')
    (item,) = items(tmp_path, make_ctx).values()
    assert " " not in item.id
    assert items(tmp_path, make_ctx, min_size=10**9) == {}


def test_roots_cover_exactly_the_removed_paths(tmp_path, make_ctx):
    versions(tmp_path, "3.38.10")
    pin(tmp_path, "a", '{"flutter": "3.44.8"}')
    ctx = make_ctx(project_roots=(tmp_path / "proj",))
    roots = default_roots(tmp_path) + FvmProvider().roots(ctx)
    for item in FvmProvider().scan(ctx):
        for a in item.actions:
            check_removable(a.path, roots, tmp_path)


def test_no_fvm_dir_yields_nothing(tmp_path, make_ctx):
    assert items(tmp_path, make_ctx) == {}


def test_version_behind_flutter_on_path_is_kept(tmp_path, make_ctx):
    versions(tmp_path, "3.38.10", "3.44.8")
    (tmp_path / "fvm" / "versions" / "3.44.8" / "bin").mkdir()
    real = tmp_path / "fvm" / "versions" / "3.44.8" / "bin" / "flutter"
    real.write_text("")
    link = tmp_path / ".local" / "bin" / "flutter"
    link.parent.mkdir(parents=True)
    os.symlink(real, link)
    pin(tmp_path, "a", '{"flutter": "9.9.9"}')
    got = items(tmp_path, make_ctx, which=lambda n: str(link) if n == "flutter" else None)
    assert set(got) == {"fvm:3.38.10"}


def test_flavor_pins_are_kept(tmp_path, make_ctx):
    versions(tmp_path, "3.10.0", "3.38.10")
    pin(tmp_path, "a", '{"flutter": "3.44.8", "flavors": {"prod": "3.10.0"}}')
    assert set(items(tmp_path, make_ctx)) == {"fvm:3.38.10"}


def test_fork_pin_keeps_the_fork_directory(tmp_path, make_ctx):
    (tmp_path / "fvm" / "versions" / "myfork" / "3.19.0").mkdir(parents=True)
    versions(tmp_path, "3.38.10")
    pin(tmp_path, "a", '{"flutter": "myfork/3.19.0"}')
    assert set(items(tmp_path, make_ctx)) == {"fvm:3.38.10"}


def test_pin_search_cut_off_at_a_project_makes_result_uncertain(tmp_path):
    deep = tmp_path / "proj"
    for part in "abcde":
        deep = deep / part
    deep.mkdir(parents=True)
    cutoff = deep / "f"
    cutoff.mkdir()
    (cutoff / "pubspec.yaml").write_text("name: x")
    (cutoff / ".fvmrc").write_text('{"flutter": "3.1.0"}')
    assert pinned_versions((tmp_path / "proj",))[1] is False


def test_deep_source_folders_without_project_markers_do_not_make_it_uncertain(tmp_path):
    deep = tmp_path / "proj"
    for part in "abcdefg":
        deep = deep / part
    deep.mkdir(parents=True)
    assert pinned_versions((tmp_path / "proj",))[1] is True


def test_fvm_cache_path_env_inside_home_is_used(tmp_path, make_ctx):
    custom = tmp_path / "tools" / "fvm-cache"
    (custom / "versions" / "3.38.10").mkdir(parents=True)
    pin(tmp_path, "a", '{"flutter": "9.9.9"}')
    ctx = make_ctx(project_roots=(tmp_path / "proj",), env={"FVM_CACHE_PATH": str(custom)})
    provider = FvmProvider()
    assert [i.id for i in provider.scan(ctx)] == ["fvm:3.38.10"]
    assert provider.roots(ctx) == [custom / "versions"]


def test_fvm_cache_path_env_outside_home_is_ignored(tmp_path, make_ctx):
    versions(tmp_path, "3.38.10")
    pin(tmp_path, "a", '{"flutter": "9.9.9"}')
    ctx = make_ctx(project_roots=(tmp_path / "proj",), env={"FVM_CACHE_PATH": "/"})
    provider = FvmProvider()
    assert provider.roots(ctx) == [tmp_path / "fvm" / "versions"]
    assert [i.id for i in provider.scan(ctx)] == ["fvm:3.38.10"]
