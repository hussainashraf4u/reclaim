from conftest import FakeRun, ok
from reclaim.model import Risk, Run
from reclaim.providers.git_worktrees import GitWorktreesProvider, parse_worktrees

PORCELAIN = (
    "worktree {repo}\nHEAD aaaa1111\nbranch refs/heads/development\n\n"
    "worktree {repo}/.worktrees/align network\nHEAD bbbb2222\nbranch refs/heads/align/network\n\n"
    "worktree {repo}/.worktrees/dirty-ü\nHEAD cccc3333\nbranch refs/heads/feat/dirty\n\n"
    "worktree {repo}/.worktrees/detached\nHEAD dddd4444\ndetached\n\n"
)


def repo_with_git(tmp_path):
    repo = tmp_path / "proj" / "my repo"
    (repo / ".git").mkdir(parents=True)
    for name in ("align network", "dirty-ü", "detached"):
        (repo / ".worktrees" / name).mkdir(parents=True)
    return repo


def table(repo):
    wt = lambda name: str(repo / ".worktrees" / name)
    return {
        ("git", "-C", str(repo), "worktree", "list", "--porcelain", "-z"): ok(PORCELAIN.format(repo=repo).replace("\n", "\0")),
        ("git", "-C", wt("align network"), "status", "--porcelain", "--ignored"): ok("!! build/\n"),
        ("git", "-C", wt("dirty-ü"), "status", "--porcelain", "--ignored"): ok(" M file.dart\n"),
        ("git", "-C", wt("detached"), "status", "--porcelain", "--ignored"): ok(""),
        ("git", "-C", str(repo), "for-each-ref", "--contains", "dddd4444", "--format=%(refname)"): ok("refs/heads/development\n"),
        ("git", "-C", str(repo), "rev-list", "--count", "HEAD..bbbb2222"): ok("3\n"),
        ("git", "-C", str(repo), "rev-list", "--count", "HEAD..cccc3333"): ok("0\n"),
        ("git", "-C", str(repo), "rev-list", "--count", "HEAD..dddd4444"): ok("0\n"),
    }


def test_parse_worktrees():
    blocks = parse_worktrees(PORCELAIN.format(repo="/r"))
    assert [b["path"] for b in blocks][:2] == ["/r", "/r/.worktrees/align network"]
    assert blocks[1]["branch"] == "align/network" and blocks[3]["branch"] is None and blocks[3]["head"] == "dddd4444"


def test_clean_worktree_is_removed_with_git_not_rm(tmp_path, make_ctx):
    repo = repo_with_git(tmp_path)
    ctx = make_ctx(run=FakeRun(table(repo)), project_roots=(tmp_path / "proj",))
    items = {i.label.split(" (")[0]: i for i in GitWorktreesProvider().scan(ctx)}
    item = items["my repo/align network"]
    assert item.risk is Risk.REGENERABLE and item.executable
    assert item.actions == (Run(("git", "-C", str(repo), "worktree", "remove", str(repo / ".worktrees" / "align network"))),)
    assert "3 ahead of the main checkout" in item.label and "branch is kept" in item.why
    assert item.path == repo / ".worktrees" / "align network"
    assert " " not in item.id and "," not in item.id


def test_dirty_worktree_is_report_only(tmp_path, make_ctx):
    repo = repo_with_git(tmp_path)
    ctx = make_ctx(run=FakeRun(table(repo)), project_roots=(tmp_path / "proj",))
    items = {i.label.split(" (")[0]: i for i in GitWorktreesProvider().scan(ctx)}
    dirty = items["my repo/dirty-ü"]
    assert dirty.report_only and not dirty.executable and dirty.actions == ()
    assert "uncommitted" in dirty.why


def test_detached_worktree_uses_head_sha(tmp_path, make_ctx):
    repo = repo_with_git(tmp_path)
    run = FakeRun(table(repo))
    ctx = make_ctx(run=run, project_roots=(tmp_path / "proj",))
    list(GitWorktreesProvider().scan(ctx))
    assert ("git", "-C", str(repo), "rev-list", "--count", "HEAD..dddd4444") in run.calls


def test_main_worktree_is_never_listed(tmp_path, make_ctx):
    repo = repo_with_git(tmp_path)
    ctx = make_ctx(run=FakeRun(table(repo)), project_roots=(tmp_path / "proj",))
    labels = [i.label for i in GitWorktreesProvider().scan(ctx)]
    assert not any(l.startswith("my repo/my repo") or "/development" in l for l in labels)
    assert len(labels) == 3


def test_no_project_roots_or_git_yields_nothing(tmp_path, make_ctx):
    assert list(GitWorktreesProvider().scan(make_ctx())) == []
    assert list(GitWorktreesProvider().scan(make_ctx(which=lambda n: None, project_roots=(tmp_path,)))) == []


def test_git_failure_skips_repo(tmp_path, make_ctx):
    repo_with_git(tmp_path)
    ctx = make_ctx(run=FakeRun({}), project_roots=(tmp_path / "proj",))
    assert list(GitWorktreesProvider().scan(ctx)) == []


def by_name(items):
    return {i.label.split(" (")[0]: i for i in items}


def test_detached_head_with_commits_on_no_branch_is_report_only(tmp_path, make_ctx):
    repo = repo_with_git(tmp_path)
    t = table(repo)
    t[("git", "-C", str(repo), "for-each-ref", "--contains", "dddd4444", "--format=%(refname)")] = ok("")
    items = by_name(GitWorktreesProvider().scan(make_ctx(run=FakeRun(t), project_roots=(tmp_path / "proj",))))
    det = items["my repo/detached"]
    assert det.report_only and not det.executable and "no branch" in det.why


def test_detached_head_check_failure_is_report_only(tmp_path, make_ctx):
    repo = repo_with_git(tmp_path)
    t = table(repo)
    del t[("git", "-C", str(repo), "for-each-ref", "--contains", "dddd4444", "--format=%(refname)")]
    items = by_name(GitWorktreesProvider().scan(make_ctx(run=FakeRun(t), project_roots=(tmp_path / "proj",))))
    assert items["my repo/detached"].report_only


def test_detached_head_reachable_from_a_branch_is_removable(tmp_path, make_ctx):
    repo = repo_with_git(tmp_path)
    items = by_name(GitWorktreesProvider().scan(make_ctx(run=FakeRun(table(repo)), project_roots=(tmp_path / "proj",))))
    assert items["my repo/detached"].executable


def test_ignored_non_artifact_files_make_worktree_report_only(tmp_path, make_ctx):
    repo = repo_with_git(tmp_path)
    t = table(repo)
    t[("git", "-C", str(repo / ".worktrees" / "align network"), "status", "--porcelain", "--ignored")] = ok("!! .env.dev\n!! build/\n!! keystore.jks\n")
    items = by_name(GitWorktreesProvider().scan(make_ctx(run=FakeRun(t), project_roots=(tmp_path / "proj",))))
    item = items["my repo/align network"]
    assert item.report_only and ".env.dev" in item.why and "keystore.jks" in item.why and "build" not in item.why.split(":")[-1]


def test_ignored_artifact_dirs_alone_do_not_block_removal(tmp_path, make_ctx):
    repo = repo_with_git(tmp_path)
    items = by_name(GitWorktreesProvider().scan(make_ctx(run=FakeRun(table(repo)), project_roots=(tmp_path / "proj",))))
    assert items["my repo/align network"].executable


def test_parse_worktrees_handles_nul_separated_output_with_newlines_in_paths():
    text = "worktree /r\0HEAD aaaa\0branch refs/heads/main\0\0worktree /r/wt\nweird\0HEAD bbbb\0detached\0\0"
    blocks = parse_worktrees(text)
    assert [b["path"] for b in blocks] == ["/r", "/r/wt\nweird"] and blocks[1]["branch"] is None
