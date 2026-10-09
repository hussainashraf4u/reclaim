import os
import subprocess
from pathlib import Path

import pytest

from reclaim.executor import NotExecutable, describe, run_item
from reclaim.model import Item, RemovePath, Risk, Run
from reclaim.safety import default_roots


def item(actions, **kw):
    return Item(id="x", label="X", group="G", size_bytes=1, risk=Risk.SAFE, actions=tuple(actions), **kw)


def test_remove_deletes_dir(tmp_path):
    d = tmp_path / ".npm" / "_cacache"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "f").write_text("x")
    res = run_item(item([RemovePath(d)]), default_roots(tmp_path), tmp_path)
    assert res.ok and not d.exists()


def test_dry_run_changes_nothing_and_lists_commands(tmp_path):
    d = tmp_path / ".npm" / "_cacache"
    d.mkdir(parents=True)
    calls = []
    res = run_item(
        item([RemovePath(d), Run(("npm", "cache", "clean"))]),
        default_roots(tmp_path), tmp_path, dry_run=True, runner=lambda *a, **k: calls.append(a),
    )
    assert res.ok and d.exists() and calls == []
    assert res.log == (f"rm -rf {d}", "npm cache clean")


def test_unsafe_path_fails_result_not_exception(tmp_path):
    victim = tmp_path / "Documents"
    victim.mkdir()
    res = run_item(item([RemovePath(victim)]), default_roots(tmp_path), tmp_path)
    assert not res.ok and victim.exists()
    assert "outside allowlist" in res.log[-1]


def test_missing_path_is_ok(tmp_path):
    gone = tmp_path / ".npm" / "_cacache"
    res = run_item(item([RemovePath(gone)]), default_roots(tmp_path), tmp_path)
    assert res.ok


def test_removing_symlink_does_not_follow(tmp_path):
    keep = tmp_path / ".cache" / "keep"
    keep.mkdir(parents=True)
    link = tmp_path / ".cache" / "link"
    os.symlink(keep, link)
    res = run_item(item([RemovePath(link)]), default_roots(tmp_path), tmp_path)
    assert res.ok and keep.exists()


def test_run_failure_reports_stderr(tmp_path):
    def runner(argv, **kw):
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="boom")
    res = run_item(item([Run(("tool", "x"))]), [], tmp_path, runner=runner)
    assert not res.ok and res.log[-1] == "boom"


def test_missing_binary_is_failed_result(tmp_path):
    def runner(argv, **kw):
        raise FileNotFoundError(argv[0])
    res = run_item(item([Run(("brew", "cleanup"))]), [], tmp_path, runner=runner)
    assert not res.ok and "brew" in res.log[-1]


def test_not_executable_raises(tmp_path):
    with pytest.raises(NotExecutable):
        run_item(item([Run(("a",))], report_only=True), [], tmp_path)


def test_describe_quotes_spaces():
    assert describe(RemovePath(Path("/a b/ü"))) == "rm -rf '/a b/ü'"
    assert describe(Run(("echo", "a b"))) == "echo 'a b'"


def test_timeout_is_failed_result(tmp_path):
    def runner(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1800)
    res = run_item(item([Run(("brew", "cleanup"))]), [], tmp_path, runner=runner)
    assert not res.ok and res.log[0] == "brew cleanup" and "timed out" in res.log[-1]


def test_decode_error_is_failed_result(tmp_path):
    def runner(argv, **kw):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
    res = run_item(item([Run(("x",))]), [], tmp_path, runner=runner)
    assert not res.ok


def test_busy_is_rechecked_at_run_time(tmp_path):
    d = tmp_path / "Library" / "Caches" / "X"
    d.mkdir(parents=True)
    busy_item = item([RemovePath(d)], busy=("Xcode",))
    res = run_item(busy_item, default_roots(tmp_path), tmp_path, is_running=lambda n: n == "Xcode")
    assert not res.ok and d.exists() and "Xcode is running" in res.log[-1]
    ok = run_item(busy_item, default_roots(tmp_path), tmp_path, is_running=lambda n: False)
    assert ok.ok and not d.exists()


def test_measured_only_when_every_action_removed_an_existing_path(tmp_path):
    d = tmp_path / ".npm" / "_cacache"
    d.mkdir(parents=True)
    roots = default_roots(tmp_path)
    assert run_item(item([RemovePath(d)]), roots, tmp_path).measured is True
    assert run_item(item([RemovePath(d)]), roots, tmp_path).measured is False
    assert run_item(item([Run(("true",))]), roots, tmp_path, runner=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")).measured is False


def test_dry_run_is_never_measured(tmp_path):
    d = tmp_path / ".npm" / "_cacache"
    d.mkdir(parents=True)
    assert run_item(item([RemovePath(d)]), default_roots(tmp_path), tmp_path, dry_run=True).measured is False


def test_rmtree_keeps_going_after_undeletable_entries(tmp_path):
    d = tmp_path / ".cache" / "big"
    bad = [d / f"bad{i}" for i in range(3)]
    good = [d / f"good{i}" for i in range(10)]
    for p in bad + good:
        p.mkdir(parents=True)
        (p / "f").write_text("x")
    for p in bad:
        os.chmod(p, 0o555)
    try:
        res = run_item(item([RemovePath(d)]), default_roots(tmp_path), tmp_path)
        assert not res.ok
        assert not any(p.exists() for p in good)
        assert all((p / "f").exists() for p in bad)
        assert len(res.log) >= 2
    finally:
        for p in bad:
            os.chmod(p, 0o755)
