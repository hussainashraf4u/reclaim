import os

from conftest import FakeRun, ok

from reclaim.model import RemovePath, Risk
from reclaim.providers.project_artifacts import ProjectArtifactsProvider

NOW = 1_800_000_000.0
OLD = NOW - 90 * 86400
RECENT = NOW - 2 * 86400


def make_dir(path, mtime):
    path.mkdir(parents=True)
    os.utime(path, (mtime, mtime))
    return path


def scan(make_ctx, tmp_path, run=None, **kw):
    ctx = make_ctx(project_roots=(tmp_path / "proj",), run=run or FakeRun(), **kw)
    return list(ProjectArtifactsProvider().scan(ctx))


def ignored(path):
    return ("git", "-C", str(path.parent), "check-ignore", "-q", path.name)


def test_old_ignored_artifact_with_marker_is_offered(tmp_path, make_ctx):
    app = tmp_path / "proj" / "web"
    app.mkdir(parents=True)
    (app / "package.json").write_text("{}")
    nm = make_dir(app / "node_modules", OLD)
    items = scan(make_ctx, tmp_path, FakeRun({ignored(nm): ok("", 0)}))
    (item,) = items
    assert item.actions == (RemovePath(nm),) and item.risk is Risk.REGENERABLE
    assert item.group == "Project artifacts" and "90d" in item.label and "web" in item.label


def test_recent_artifact_is_skipped(tmp_path, make_ctx):
    app = tmp_path / "proj" / "web"
    app.mkdir(parents=True)
    (app / "package.json").write_text("{}")
    nm = make_dir(app / "node_modules", RECENT)
    assert scan(make_ctx, tmp_path, FakeRun({ignored(nm): ok("", 0)})) == []


def test_git_tracked_build_dir_is_never_offered(tmp_path, make_ctx):
    app = tmp_path / "proj" / "app"
    app.mkdir(parents=True)
    (app / "pubspec.yaml").write_text("name: a")
    build = make_dir(app / "build", OLD)
    assert scan(make_ctx, tmp_path, FakeRun({ignored(build): ok("", 1)})) == []


def test_missing_marker_is_skipped_even_when_ignored(tmp_path, make_ctx):
    app = tmp_path / "proj" / "misc"
    app.mkdir(parents=True)
    nm = make_dir(app / "node_modules", OLD)
    assert scan(make_ctx, tmp_path, FakeRun({ignored(nm): ok("", 0)})) == []


def test_unknown_git_state_is_never_offered(tmp_path, make_ctx):
    app = tmp_path / "proj" / "py"
    app.mkdir(parents=True)
    (app / "pyproject.toml").write_text("")
    venv = make_dir(app / ".venv", OLD)
    assert scan(make_ctx, tmp_path, FakeRun({ignored(venv): ok("", 128)})) == []
    assert scan(make_ctx, tmp_path, FakeRun({})) == []


def test_active_project_marker_keeps_old_artifact_out(tmp_path, make_ctx):
    app = tmp_path / "proj" / "web"
    app.mkdir(parents=True)
    marker = app / "package.json"
    marker.write_text("{}")
    os.utime(marker, (RECENT, RECENT))
    nm = make_dir(app / "node_modules", OLD)
    assert scan(make_ctx, tmp_path, FakeRun({ignored(nm): ok("", 0)})) == []


def test_running_interpreter_environment_is_never_offered(tmp_path, make_ctx, monkeypatch):
    import sys
    app = tmp_path / "proj" / "tool"
    app.mkdir(parents=True)
    (app / "pyproject.toml").write_text("")
    venv = make_dir(app / ".venv", OLD)
    monkeypatch.setattr(sys, "prefix", str(venv))
    assert scan(make_ctx, tmp_path, FakeRun({ignored(venv): ok("", 0)})) == []


def test_artifact_names_match_the_guard():
    from reclaim.providers.project_artifacts import ARTIFACTS
    from reclaim.safety import ARTIFACT_NAMES
    assert set(ARTIFACTS) == set(ARTIFACT_NAMES)


def test_symlinked_artifact_is_not_followed(tmp_path, make_ctx):
    app = tmp_path / "proj" / "web"
    app.mkdir(parents=True)
    (app / "package.json").write_text("{}")
    real = make_dir(tmp_path / "elsewhere" / "node_modules", OLD)
    os.symlink(real, app / "node_modules")
    assert scan(make_ctx, tmp_path, FakeRun()) == []


def test_does_not_descend_into_found_artifacts_or_hidden_dirs(tmp_path, make_ctx):
    app = tmp_path / "proj" / "web"
    app.mkdir(parents=True)
    (app / "package.json").write_text("{}")
    nm = make_dir(app / "node_modules", OLD)
    inner = make_dir(nm / "dep" / "node_modules", OLD)
    (nm / "dep" / "package.json").write_text("{}")
    hidden = tmp_path / "proj" / ".hidden" / "x"
    hidden.mkdir(parents=True)
    (hidden / "package.json").write_text("{}")
    make_dir(hidden / "node_modules", OLD)
    run = FakeRun({ignored(nm): ok("", 0), ignored(inner): ok("", 0)})
    items = scan(make_ctx, tmp_path, run)
    assert [i.actions for i in items] == [(RemovePath(nm),)]


def test_min_size_and_missing_roots(tmp_path, make_ctx):
    app = tmp_path / "proj" / "web"
    app.mkdir(parents=True)
    (app / "package.json").write_text("{}")
    nm = make_dir(app / "node_modules", OLD)
    run = FakeRun({ignored(nm): ok("", 0)})
    assert scan(make_ctx, tmp_path, run, min_size=10_000) == []
    assert list(ProjectArtifactsProvider().scan(make_ctx(project_roots=(tmp_path / "nope",)))) == []


def test_roots_are_scoped_to_artifact_names(tmp_path, make_ctx):
    from reclaim.safety import ScopedRoot
    ctx = make_ctx(project_roots=(tmp_path / "proj",))
    (root,) = ProjectArtifactsProvider().roots(ctx)
    assert isinstance(root, ScopedRoot) and root.path == tmp_path / "proj" and "node_modules" in root.names


def test_projects_five_levels_deep_are_found(tmp_path, make_ctx):
    app = tmp_path / "proj" / "a" / "b" / "c" / "d" / "web"
    app.mkdir(parents=True)
    (app / "package.json").write_text("{}")
    nm = make_dir(app / "node_modules", OLD)
    assert [i.actions for i in scan(make_ctx, tmp_path, FakeRun({ignored(nm): ok("", 0)}))] == [(RemovePath(nm),)]


def test_many_projects_are_all_found_in_stable_order(tmp_path, make_ctx):
    table = {}
    paths = []
    for i in range(12):
        app = tmp_path / "proj" / f"p{i:02d}"
        app.mkdir(parents=True)
        (app / "package.json").write_text("{}")
        nm = make_dir(app / "node_modules", OLD)
        table[ignored(nm)] = ok("", 0)
        paths.append(nm)
    items = scan(make_ctx, tmp_path, FakeRun(table))
    assert [i.actions[0].path for i in items] == sorted(paths)
