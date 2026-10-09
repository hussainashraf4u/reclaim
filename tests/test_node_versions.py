import os

from reclaim.model import RemovePath, Risk
from reclaim.providers.node_versions import HIGHEST, IGNORE, NodeVersionsProvider, resolve_rule, version_kept
from reclaim.safety import check_removable, default_roots


def nvm(tmp_path, *versions, default=None):
    for v in versions:
        (tmp_path / ".nvm" / "versions" / "node" / v).mkdir(parents=True)
    if default is not None:
        alias(tmp_path, "default", default)


def alias(tmp_path, name, text):
    f = tmp_path / ".nvm" / "alias" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text + "\n")


def fnm(tmp_path, *versions, default=None):
    base = tmp_path / "Library" / "Application Support" / "fnm"
    for v in versions:
        (base / "node-versions" / v / "installation").mkdir(parents=True)
    if default is not None:
        (base / "aliases").mkdir(parents=True, exist_ok=True)
        os.symlink(base / "node-versions" / default / "installation", base / "aliases" / "default")
    return base


def pin(tmp_path, text, name=".nvmrc", project="a"):
    d = tmp_path / "proj" / project
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


def scan(tmp_path, make_ctx, roots=True, **kw):
    ctx = make_ctx(project_roots=(tmp_path / "proj",) if roots else (), **kw)
    return {i.id: i for i in NodeVersionsProvider().scan(ctx)}


def test_version_kept_rules():
    assert version_kept("20", "v20.1.0", "v22.0.0")
    assert version_kept("20.1", "v20.1.5", "v22.0.0")
    assert version_kept("20.1.0", "v20.1.0", "v22.0.0")
    assert not version_kept("20", "v200.1.0", "v22.0.0")
    assert not version_kept("20.1", "v20.10.0", "v22.0.0")
    assert version_kept(HIGHEST, "v22.0.0", "v22.0.0") and not version_kept(HIGHEST, "v18.0.0", "v22.0.0")


def test_resolve_rule_formats(tmp_path):
    assert resolve_rule("v20.1", tmp_path) == "20.1" and resolve_rule("18", tmp_path) == "18"
    assert resolve_rule("node", tmp_path) == HIGHEST and resolve_rule("stable", tmp_path) == HIGHEST
    assert resolve_rule("system", tmp_path) == IGNORE and resolve_rule("iojs-3", tmp_path) == IGNORE
    assert resolve_rule("lts/iron", tmp_path) == "20"
    for bad in (">=18", "18.x", "^18", "# use 20", "", "lts/*", "lts/unknown", "../../etc/passwd", "/etc/passwd"):
        assert resolve_rule(bad, tmp_path) is None, bad


def test_alias_chains_are_followed(tmp_path):
    alias(tmp_path, "default", "lts/*")
    alias(tmp_path, "lts/*", "lts/iron")
    alias(tmp_path, "lts/iron", "v20.11.1")
    assert resolve_rule("default", tmp_path) == "20.11.1"
    alias(tmp_path, "work", "18")
    assert resolve_rule("work", tmp_path) == "18"
    alias(tmp_path, "loop", "loop")
    assert resolve_rule("loop", tmp_path) is None


def test_nvm_unused_versions_removable_default_and_pins_kept(tmp_path, make_ctx):
    nvm(tmp_path, "v16.20.0", "v18.19.0", "v20.11.0", "v22.1.0", default="20")
    pin(tmp_path, "18\n")
    got = scan(tmp_path, make_ctx)
    assert set(got) == {"node:nvm:v16.20.0", "node:nvm:v22.1.0"}
    item = got["node:nvm:v16.20.0"]
    assert item.risk is Risk.REGENERABLE and item.executable
    assert item.actions == (RemovePath(tmp_path / ".nvm" / "versions" / "node" / "v16.20.0"),)


def test_lts_codename_and_alias_default_keep_the_right_major(tmp_path, make_ctx):
    nvm(tmp_path, "v18.20.0", "v20.1.0", "v20.11.1", "v22.3.0", default="lts/*")
    alias(tmp_path, "lts/*", "lts/iron")
    alias(tmp_path, "lts/iron", "v20.11.1")
    pin(tmp_path, "lts/iron\n", name=".node-version")
    assert set(scan(tmp_path, make_ctx)) == {"node:nvm:v18.20.0", "node:nvm:v20.1.0", "node:nvm:v22.3.0"}


def test_codename_without_alias_files_keeps_the_whole_major(tmp_path, make_ctx):
    nvm(tmp_path, "v18.20.0", "v20.1.0", "v20.11.1", "v22.3.0", default="20")
    pin(tmp_path, "lts/iron\n")
    assert set(scan(tmp_path, make_ctx)) == {"node:nvm:v18.20.0", "node:nvm:v22.3.0"}


def test_pin_comment_lines_are_skipped(tmp_path, make_ctx):
    nvm(tmp_path, "v16.20.0", "v20.11.0", default="20")
    pin(tmp_path, "# project node\n20\n")
    assert set(scan(tmp_path, make_ctx)) == {"node:nvm:v16.20.0"}


def test_unparseable_pin_or_default_makes_everything_report_only(tmp_path, make_ctx):
    nvm(tmp_path, "v16.20.0", "v20.11.0", default="20")
    pin(tmp_path, ">=18\n")
    assert all(i.report_only for i in scan(tmp_path, make_ctx).values())
    alias(tmp_path, "default", "something-odd")
    pin(tmp_path, "20\n")
    assert all(i.report_only for i in scan(tmp_path, make_ctx).values())


def test_fnm_versions_remove_the_version_dir_not_installation(tmp_path, make_ctx):
    base = fnm(tmp_path, "v18.19.0", "v20.11.0", default="v20.11.0")
    pin(tmp_path, "20")
    got = scan(tmp_path, make_ctx)
    assert set(got) == {"node:fnm:v18.19.0"}
    assert got["node:fnm:v18.19.0"].actions == (RemovePath(base / "node-versions" / "v18.19.0"),)


def test_unknown_default_or_no_project_roots_is_report_only(tmp_path, make_ctx):
    nvm(tmp_path, "v16.20.0")
    assert scan(tmp_path, make_ctx)["node:nvm:v16.20.0"].report_only
    nvm(tmp_path, default="20")
    assert scan(tmp_path, make_ctx, roots=False)["node:nvm:v16.20.0"].report_only


def test_unreadable_pin_is_report_only(tmp_path, make_ctx):
    nvm(tmp_path, "v16.20.0", default="20")
    d = tmp_path / "proj" / "a"
    d.mkdir(parents=True)
    (d / ".nvmrc").write_bytes(b"\xff\xfe")
    assert scan(tmp_path, make_ctx)["node:nvm:v16.20.0"].report_only


def test_min_size_and_missing_managers(tmp_path, make_ctx):
    assert scan(tmp_path, make_ctx) == {}
    nvm(tmp_path, "v16.20.0", default="20")
    assert scan(tmp_path, make_ctx, min_size=10**9) == {}


def test_roots_cover_removed_paths(tmp_path, make_ctx):
    nvm(tmp_path, "v16.20.0", default="20")
    fnm(tmp_path, "v18.19.0", default="v18.19.0")
    fnm_dir = tmp_path / "Library" / "Application Support" / "fnm" / "node-versions"
    (fnm_dir / "v14.0.0" / "installation").mkdir(parents=True)
    pin(tmp_path, "20")
    ctx = make_ctx(project_roots=(tmp_path / "proj",))
    provider = NodeVersionsProvider()
    roots = default_roots(tmp_path) + provider.roots(ctx)
    n = 0
    for item in provider.scan(ctx):
        for a in item.actions:
            check_removable(a.path, roots, tmp_path)
            n += 1
    assert n >= 2


def test_tool_versions_and_volta_pins_are_honoured(tmp_path, make_ctx):
    nvm(tmp_path, "v16.20.0", "v18.19.0", "v20.1.0", "v22.3.0", default="22")
    d = tmp_path / "proj" / "asdf"
    d.mkdir(parents=True)
    (d / ".tool-versions").write_text("python 3.12.1\nnodejs 18.19.0\n")
    v = tmp_path / "proj" / "volta"
    v.mkdir(parents=True)
    (v / "package.json").write_text('{"volta": {"node": "20.1.0"}, "engines": {"node": ">=14"}}')
    assert set(scan(tmp_path, make_ctx)) == {"node:nvm:v16.20.0"}


def test_unrelated_or_broken_package_json_does_not_make_things_unknown(tmp_path, make_ctx):
    nvm(tmp_path, "v16.20.0", default="22")
    d = tmp_path / "proj" / "x"
    d.mkdir(parents=True)
    (d / "package.json").write_text("{broken")
    got = scan(tmp_path, make_ctx)
    assert got["node:nvm:v16.20.0"].executable


def test_active_node_from_nvm_bin_is_kept(tmp_path, make_ctx):
    nvm(tmp_path, "v16.20.0", "v18.19.0", default="22")
    env = {"NVM_BIN": str(tmp_path / ".nvm" / "versions" / "node" / "v16.20.0" / "bin")}
    assert set(scan(tmp_path, make_ctx, env=env)) == {"node:nvm:v18.19.0"}
