import os

from reclaim.providers.large_dirs import LargeDirsProvider, USER_CONTENT

BIG = 2 * 1024**3


def tree(tmp_path):
    for rel in (
        ".android", ".cache/pip", ".local/share/foo", "Library/Application Support/Slack",
        "Library/Caches/com.x", "Documents/projects", "Desktop/stuff", "Movies/film", "small",
    ):
        (tmp_path / rel).mkdir(parents=True)
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "installer.dmg").write_text("x")
    return tmp_path


def sizer_for(sizes, calls=None):
    def sizer(path):
        if calls is not None:
            calls.append(path)
        return sizes.get(path.name, 1000)
    return sizer


def scan(make_ctx, sizes, **kw):
    calls = []
    ctx = make_ctx(sizer=sizer_for(sizes, calls), large_dir_bytes=kw.pop("limit", 10**9), **kw)
    return {i.label: i for i in LargeDirsProvider().scan(ctx)}, calls


def test_large_folders_are_report_only_with_path_and_hashed_id(tmp_path, make_ctx):
    tree(tmp_path)
    got, _ = scan(make_ctx, {".android": BIG, "Slack": 3 * 1024**3, "installer.dmg": BIG, "pip": 5})
    assert set(got) == {"~/.android", "~/Library/Application Support/Slack", "~/Downloads/installer.dmg"}
    item = got["~/.android"]
    assert item.report_only and not item.executable and item.actions == ()
    assert item.group == "Large folders" and item.path == tmp_path / ".android"
    assert item.id.startswith("large:") and " " not in item.id


def test_user_content_is_never_sized(tmp_path, make_ctx):
    tree(tmp_path)
    _, calls = scan(make_ctx, {})
    names = {p.name for p in calls}
    assert not (names & set(USER_CONTENT))
    assert "projects" not in names and "film" not in names and "stuff" not in names


def test_threshold_applies(tmp_path, make_ctx):
    tree(tmp_path)
    got, _ = scan(make_ctx, {".android": BIG}, limit=3 * 1024**3)
    assert got == {}


def test_symlinks_are_skipped(tmp_path, make_ctx):
    tree(tmp_path)
    os.symlink(tmp_path / "small", tmp_path / ".linked")
    _, calls = scan(make_ctx, {})
    assert tmp_path / ".linked" not in calls


def test_missing_roots_are_fine(tmp_path, make_ctx):
    got, calls = scan(make_ctx, {})
    assert got == {} and calls == []


def test_container_folders_are_not_sized_to_avoid_privacy_prompts(tmp_path, make_ctx):
    tree(tmp_path)
    (tmp_path / "Library" / "Containers" / "com.big").mkdir(parents=True)
    (tmp_path / "Library" / "Group Containers" / "group.big").mkdir(parents=True)
    _, calls = scan(make_ctx, {})
    assert not any("Containers" in str(p) for p in calls)


def test_wording_is_neutral_about_user_data(tmp_path, make_ctx):
    tree(tmp_path)
    got, _ = scan(make_ctx, {".android": BIG})
    assert "own data" in got["~/.android"].why


def test_iphone_backups_get_a_never_delete_warning(tmp_path, make_ctx):
    tree(tmp_path)
    (tmp_path / "Library" / "Application Support" / "MobileSync").mkdir(parents=True)
    got, _ = scan(make_ctx, {"MobileSync": BIG})
    assert "backups" in got["~/Library/Application Support/MobileSync"].why.lower()
