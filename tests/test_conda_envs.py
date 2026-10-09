import json
import os

from conftest import FakeRun, ok

from reclaim.model import Risk, Run
from reclaim.providers.conda_envs import CondaEnvsProvider

NOW = 1_800_000_000.0
OLD = NOW - 90 * 86400
RECENT = NOW - 2 * 86400
ENV_CMD = ("conda", "env", "list", "--json")
INFO_CMD = ("conda", "info", "--json")


def env(tmp_path, name, mtime):
    d = tmp_path / "conda" / "envs" / name
    meta = d / "conda-meta"
    meta.mkdir(parents=True)
    os.utime(meta, (mtime, mtime))
    return d


def table(tmp_path, *envs, pkgs=True, base_first=True, active=None):
    base = tmp_path / "conda"
    base.mkdir(exist_ok=True)
    listed = [str(base)] + [str(e) for e in envs] if base_first else [str(e) for e in envs] + [str(base)]
    t = {ENV_CMD: ok(json.dumps({"envs": listed}))}
    info = {"root_prefix": str(base)}
    if active:
        info["active_prefix"] = str(active)
    if pkgs:
        (base / "pkgs").mkdir(exist_ok=True)
        info["pkgs_dirs"] = [str(base / "pkgs")]
    t[INFO_CMD] = ok(json.dumps(info))
    return t


def run(make_ctx, t, **kw):
    ctx = make_ctx(run=FakeRun(t), **kw)
    return {i.id: i for i in CondaEnvsProvider().scan(ctx)}


def test_stale_non_base_envs_are_data_items_recent_and_base_skipped(tmp_path, make_ctx):
    old = env(tmp_path, "old", OLD)
    recent = env(tmp_path, "recent", RECENT)
    got = run(make_ctx, table(tmp_path, old, recent))
    envs = [i for i in got.values() if i.id.startswith("conda-env:")]
    assert len(envs) == 1
    item = envs[0]
    assert item.risk is Risk.DATA and item.label == "conda env old (no package changes in 90d)"
    assert item.actions == (Run(("conda", "env", "remove", "-y", "-p", str(old))),)
    assert " " not in item.id


def test_package_cache_item(tmp_path, make_ctx):
    got = run(make_ctx, table(tmp_path))
    pk = got["conda-pkgs"]
    assert pk.risk is Risk.REGENERABLE and pk.actions == (Run(("conda", "clean", "-a", "-y")),)
    assert pk.size_bytes == 1000


def test_min_size_filters_everything_small(tmp_path, make_ctx):
    old = env(tmp_path, "old", OLD)
    assert run(make_ctx, table(tmp_path, old), min_size=10**9) == {}


def test_no_conda_or_failures_yield_nothing(tmp_path, make_ctx):
    assert list(CondaEnvsProvider().scan(make_ctx(which=lambda n: None))) == []
    assert run(make_ctx, {}) == {}
    assert run(make_ctx, {ENV_CMD: ok("{bad")}) == {}
    assert run(make_ctx, {ENV_CMD: ok("", code=1)}) == {}


def test_without_info_no_env_items_are_offered(tmp_path, make_ctx):
    old = env(tmp_path, "old", OLD)
    t = table(tmp_path, old)
    del t[INFO_CMD]
    assert run(make_ctx, t) == {}


def test_base_is_excluded_even_when_listed_last_and_all_envs_are_scanned(tmp_path, make_ctx):
    old = env(tmp_path, "old", OLD)
    base_meta = tmp_path / "conda" / "conda-meta"
    base_meta.mkdir(parents=True)
    os.utime(base_meta, (OLD, OLD))
    got = run(make_ctx, table(tmp_path, old, base_first=False))
    labels = [i.label for i in got.values() if i.id.startswith("conda-env:")]
    assert labels == ["conda env old (no package changes in 90d)"]


def test_active_env_is_excluded(tmp_path, make_ctx):
    old = env(tmp_path, "old", OLD)
    got = run(make_ctx, table(tmp_path, old, active=old))
    assert not [i for i in got if i.startswith("conda-env:")]


def test_package_cache_has_path_and_upper_bound_wording(tmp_path, make_ctx):
    pk = run(make_ctx, table(tmp_path))["conda-pkgs"]
    assert pk.path == tmp_path / "conda" / "pkgs" and "upper bound" in pk.why.lower()
