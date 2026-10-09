import os

from conftest import FakeRun, ok

from reclaim.model import Risk, Run
from reclaim.providers import _plugin_root_ok, plugin_providers, provider_roots
from reclaim.providers.files import BigFilesProvider, DuplicatesProvider
from reclaim.providers.homebrew import HomebrewProvider
from reclaim.providers.rustup import RustupProvider
from reclaim.providers.version_managers import VersionManagersProvider
from reclaim.providers.vm_disks import VmDisksProvider
from reclaim.safety import check_removable, default_roots


def scan(provider, ctx):
    return {i.id: i for i in provider.scan(ctx)}


# --- version managers -------------------------------------------------------------------------


def test_pyenv_keeps_global_and_pinned_versions(tmp_path, make_ctx):
    for v in ("3.9.1", "3.11.4", "3.12.2", "3.8.0"):
        (tmp_path / ".pyenv" / "versions" / v).mkdir(parents=True)
    (tmp_path / ".pyenv" / "version").write_text("3.12.2\n")
    proj = tmp_path / "proj" / "a"
    proj.mkdir(parents=True)
    (proj / ".python-version").write_text("3.11\n")
    items = scan(VersionManagersProvider(), make_ctx(project_roots=(tmp_path / "proj",)))
    assert set(items) == {"pyenv:3.9.1", "pyenv:3.8.0"}
    assert all(i.executable and i.risk is Risk.REGENERABLE for i in items.values())


def test_without_project_roots_results_are_report_only(tmp_path, make_ctx):
    (tmp_path / ".rbenv" / "versions" / "3.0.0").mkdir(parents=True)
    (item,) = VersionManagersProvider().scan(make_ctx())
    assert item.report_only and not item.actions


def test_asdf_tool_layout_and_tool_versions_pin(tmp_path, make_ctx):
    base = tmp_path / ".asdf" / "installs"
    for tool, ver in (("nodejs", "18.1.0"), ("nodejs", "20.0.0"), ("python", "3.10.0")):
        (base / tool / ver).mkdir(parents=True)
    proj = tmp_path / "proj" / "a"
    proj.mkdir(parents=True)
    (proj / ".tool-versions").write_text("nodejs 20.0.0\n")
    items = scan(VersionManagersProvider(), make_ctx(project_roots=(tmp_path / "proj",)))
    assert "asdf:nodejs-18.1.0" in items and "asdf:python-3.10.0" in items
    assert "asdf:nodejs-20.0.0" not in items


def test_sdkman_skips_current_symlink_dir_name(tmp_path, make_ctx):
    base = tmp_path / ".sdkman" / "candidates" / "java"
    (base / "17.0.1-tem").mkdir(parents=True)
    (base / "current").mkdir()
    (tmp_path / "proj").mkdir()
    items = scan(VersionManagersProvider(), make_ctx(project_roots=(tmp_path / "proj",)))
    assert list(items) == ["sdkman:java-17.0.1-tem"]


def test_version_manager_removals_pass_guard(tmp_path, make_ctx):
    (tmp_path / ".pyenv" / "versions" / "3.1.0").mkdir(parents=True)
    (tmp_path / "proj").mkdir()
    ctx = make_ctx(project_roots=(tmp_path / "proj",))
    p = VersionManagersProvider()
    roots = default_roots(tmp_path) + provider_roots([p], ctx)
    (item,) = p.scan(ctx)
    check_removable(item.actions[0].path, roots, tmp_path)


# --- rustup -----------------------------------------------------------------------------------


def test_rustup_keeps_default_override_and_pinned(tmp_path, make_ctx):
    base = tmp_path / ".rustup" / "toolchains"
    for n in ("stable-aarch64-apple-darwin", "nightly-2024-01-01-aarch64-apple-darwin", "1.70.0-aarch64-apple-darwin", "beta-aarch64-apple-darwin"):
        (base / n).mkdir(parents=True)
    (tmp_path / ".rustup" / "settings.toml").write_text('default_toolchain = "stable-aarch64-apple-darwin"\n')
    proj = tmp_path / "proj" / "a"
    proj.mkdir(parents=True)
    (proj / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "1.70.0"\n')
    items = scan(RustupProvider(), make_ctx(project_roots=(tmp_path / "proj",)))
    assert set(items) == {"rustup:nightly-2024-01-01-aarch64-apple-darwin", "rustup:beta-aarch64-apple-darwin"}
    roots = default_roots(tmp_path) + RustupProvider().roots(make_ctx())
    for i in items.values():
        check_removable(i.actions[0].path, roots, tmp_path)


def test_rustup_plain_text_pin_and_unreadable_pin(tmp_path, make_ctx):
    base = tmp_path / ".rustup" / "toolchains"
    (base / "nightly-x").mkdir(parents=True)
    proj = tmp_path / "proj" / "a"
    proj.mkdir(parents=True)
    (proj / "rust-toolchain").write_text("nightly\n")
    assert scan(RustupProvider(), make_ctx(project_roots=(tmp_path / "proj",))) == {}
    (proj / "rust-toolchain").write_text("")
    (item,) = RustupProvider().scan(make_ctx(project_roots=(tmp_path / "proj",)))
    assert item.report_only


# --- homebrew ---------------------------------------------------------------------------------


def test_homebrew_autoremove_item(tmp_path, make_ctx):
    cellar = tmp_path / "Cellar"
    (cellar / "libfoo").mkdir(parents=True)
    run = FakeRun({
        ("brew", "autoremove", "--dry-run"): ok("==> Would autoremove 1 unneeded formula:\nlibfoo\n"),
        ("brew", "--cellar"): ok(str(cellar) + "\n"),
    })
    (item,) = HomebrewProvider().scan(make_ctx(run=run))
    assert item.actions == (Run(("brew", "autoremove")),) and "libfoo" in item.why


def test_homebrew_nothing_to_remove_or_no_brew(tmp_path, make_ctx):
    run = FakeRun({("brew", "autoremove", "--dry-run"): ok("")})
    assert list(HomebrewProvider().scan(make_ctx(run=run))) == []
    assert list(HomebrewProvider().scan(make_ctx(which=lambda n: None))) == []
    assert list(HomebrewProvider().scan(make_ctx(run=FakeRun()))) == []


# --- vm disks ---------------------------------------------------------------------------------


def test_vm_disks_are_report_only(tmp_path, make_ctx):
    (tmp_path / ".colima").mkdir()
    (tmp_path / ".local" / "share" / "containers" / "podman" / "machine").mkdir(parents=True)
    items = scan(VmDisksProvider(), make_ctx())
    assert set(items) == {"vm:colima", "vm:podman"}
    assert all(i.report_only and i.risk is Risk.DATA and not i.executable for i in items.values())


# --- big files / duplicates -------------------------------------------------------------------


def test_big_files_and_duplicates(tmp_path, make_ctx):
    docs = tmp_path / "Documents"
    docs.mkdir()
    (tmp_path / "Downloads").mkdir()
    (docs / "a.bin").write_bytes(b"x" * 3000)
    (tmp_path / "Downloads" / "a-copy.bin").write_bytes(b"x" * 3000)
    (docs / "c.bin").write_bytes(b"y" * 3000)
    (docs / "small.txt").write_text("hi")
    (docs / "node_modules").mkdir()
    (docs / "node_modules" / "big.bin").write_bytes(b"z" * 9000)
    ctx = make_ctx(big_file_bytes=2000, duplicate_min_bytes=2000)
    big = list(BigFilesProvider().scan(ctx))
    assert len(big) == 3 and all(i.report_only for i in big)
    dups = list(DuplicatesProvider().scan(ctx))
    assert len(dups) == 1 and dups[0].size_bytes == 3000 and "Downloads/a-copy.bin" in dups[0].why
    assert not dups[0].executable


def test_same_size_different_content_is_not_duplicate(tmp_path, make_ctx):
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "a").write_bytes(b"a" * 4000)
    (docs / "b").write_bytes(b"b" * 4000)
    assert list(DuplicatesProvider().scan(make_ctx(duplicate_min_bytes=1000))) == []


def test_big_files_skip_symlinks(tmp_path, make_ctx):
    docs = tmp_path / "Documents"
    docs.mkdir()
    (tmp_path / "real.bin").write_bytes(b"x" * 5000)
    os.symlink(tmp_path / "real.bin", docs / "link.bin")
    assert list(BigFilesProvider().scan(make_ctx(big_file_bytes=1000))) == []


# --- plugins ----------------------------------------------------------------------------------


class _EP:
    def __init__(self, name, target):
        self.name, self.target = name, target

    def load(self):
        return self.target


class _Good:
    name = "good"

    def scan(self, ctx):
        return iter(())


def test_plugins_load_and_bad_ones_are_skipped(monkeypatch, capsys):
    import reclaim.providers as mod

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(mod, "entry_points", lambda group: [_EP("good", _Good), _EP("broken", boom), _EP("odd", lambda: object())])
    found = plugin_providers()
    assert [p.name for p in found] == ["good"] and found[0].external
    err = capsys.readouterr().err
    assert "broken" in err and "odd" in err


def test_plugin_roots_are_restricted(tmp_path):
    ok_root = tmp_path / "projects" / "cache"
    assert _plugin_root_ok(ok_root, tmp_path)
    for bad in (tmp_path, tmp_path / "Documents", tmp_path / ".ssh" / "keys", tmp_path / "Library" / "Caches", tmp_path / ".." / "x", tmp_path.parent / "other" / "x"):
        assert not _plugin_root_ok(bad, tmp_path), bad
    assert _plugin_root_ok(tmp_path / "Library" / "Caches" / "mine", tmp_path)


def test_external_provider_roots_filtered(tmp_path, make_ctx):
    class Greedy:
        name = "greedy"
        external = True

        def roots(self, ctx):
            return [ctx.home / "Documents", ctx.home / "work" / "cache"]

    roots = provider_roots([Greedy()], make_ctx())
    assert roots == [tmp_path / "work" / "cache"]
