import os
from pathlib import Path

import pytest

from reclaim.safety import ARTIFACT_NAMES, ScopedRoot, UnsafePath, check_removable, default_roots


def test_allows_path_inside_root(tmp_path):
    target = tmp_path / ".npm" / "_cacache"
    target.mkdir(parents=True)
    roots = default_roots(tmp_path)
    assert check_removable(target, roots, tmp_path) == target.resolve()


def test_allows_exact_root_itself(tmp_path):
    root = tmp_path / ".fastlane"
    root.mkdir()
    assert check_removable(root, default_roots(tmp_path), tmp_path) == root.resolve()


@pytest.mark.parametrize("rel", [".", "Documents", "Library", "Desktop/x"])
def test_rejects_home_and_non_allowlisted(tmp_path, rel):
    target = tmp_path / rel
    target.mkdir(parents=True, exist_ok=True)
    with pytest.raises(UnsafePath):
        check_removable(target, default_roots(tmp_path), tmp_path)


def test_rejects_filesystem_root_and_ancestor_of_home(tmp_path):
    roots = default_roots(tmp_path)
    for p in (Path("/"), tmp_path.parent):
        with pytest.raises(UnsafePath):
            check_removable(p, roots, tmp_path)


def test_rejects_dotdot_and_relative(tmp_path):
    roots = default_roots(tmp_path)
    with pytest.raises(UnsafePath):
        check_removable(tmp_path / ".npm" / ".." / "Documents", roots, tmp_path)
    with pytest.raises(UnsafePath):
        check_removable(Path(".npm"), roots, tmp_path)


def test_rejects_symlink_escaping_allowlist(tmp_path):
    outside = tmp_path / "Documents"
    outside.mkdir()
    cache = tmp_path / ".npm"
    cache.mkdir()
    link = cache / "evil"
    os.symlink(outside, link)
    with pytest.raises(UnsafePath):
        check_removable(link, default_roots(tmp_path), tmp_path)


def test_handles_spaces_and_unicode(tmp_path):
    target = tmp_path / "Library" / "Developer" / "Xcode" / "iOS DeviceSupport" / "ü 17.0"
    target.mkdir(parents=True)
    assert check_removable(target, default_roots(tmp_path), tmp_path) == target.resolve()


@pytest.mark.parametrize("rel", [
    "Library/Caches", "Library/Logs", ".cache", ".Trash", ".m2/settings.xml", ".cargo/bin",
    ".pub-cache/bin", ".local/share", ".ollama", "Library/Android/sdk", "fvm/versions",
])
def test_rejects_tree_roots_themselves_and_unneeded_roots(tmp_path, rel):
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=True)
    with pytest.raises(UnsafePath):
        check_removable(target, default_roots(tmp_path), tmp_path)


@pytest.mark.parametrize("rel", [".cargo/registry", ".m2/repository", ".pub-cache/hosted", ".fastlane"])
def test_allows_specific_exact_roots(tmp_path, rel):
    target = tmp_path / rel
    target.mkdir(parents=True)
    assert check_removable(target, default_roots(tmp_path), tmp_path) == target.resolve()


def scoped(tmp_path):
    return default_roots(tmp_path) + [ScopedRoot(tmp_path / "proj", frozenset(ARTIFACT_NAMES))]


def test_scoped_root_allows_only_artifact_names_inside(tmp_path):
    target = tmp_path / "proj" / "web" / "node_modules"
    target.mkdir(parents=True)
    assert check_removable(target, scoped(tmp_path), tmp_path) == target.resolve()


@pytest.mark.parametrize("rel", ["", "web", "web/src", "web/src/node_modules_backup", "web/.env"])
def test_scoped_root_rejects_everything_else(tmp_path, rel):
    target = tmp_path / "proj" / rel
    target.mkdir(parents=True, exist_ok=True)
    with pytest.raises(UnsafePath):
        check_removable(target, scoped(tmp_path), tmp_path)


def test_rejects_symlink_located_outside_roots_even_if_target_inside(tmp_path):
    inside = tmp_path / ".cache" / "keep"
    inside.mkdir(parents=True)
    link = tmp_path / "Documents" / "link"
    link.parent.mkdir()
    os.symlink(inside, link)
    with pytest.raises(UnsafePath):
        check_removable(link, default_roots(tmp_path), tmp_path)


def test_symlinked_root_still_works_when_path_is_lexically_inside(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, tmp_path / ".cache")
    target = tmp_path / ".cache" / "x"
    (elsewhere / "x").mkdir()
    assert check_removable(target, default_roots(tmp_path), tmp_path) == (elsewhere / "x").resolve()
