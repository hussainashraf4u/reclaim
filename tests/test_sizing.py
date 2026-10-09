import os

from reclaim.sizing import dir_size


def test_counts_file_bytes(tmp_path):
    f = tmp_path / "a"
    f.write_bytes(b"x" * 1_048_576)
    assert 1_048_576 <= dir_size(f) < 1_048_576 + 65_536


def test_missing_path_is_zero(tmp_path):
    assert dir_size(tmp_path / "nope") == 0


def test_recurses(tmp_path):
    (tmp_path / "d" / "e").mkdir(parents=True)
    (tmp_path / "d" / "e" / "f").write_bytes(b"x" * 100_000)
    assert dir_size(tmp_path / "d") >= 100_000


def test_unreadable_subdir_is_skipped(tmp_path):
    (tmp_path / "ok").write_bytes(b"x" * 50_000)
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "f").write_bytes(b"x" * 50_000)
    os.chmod(locked, 0)
    try:
        size = dir_size(tmp_path)
    finally:
        os.chmod(locked, 0o700)
    assert size >= 50_000


def test_symlink_loop_terminates_and_is_not_followed(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    os.symlink(d, d / "loop")
    big = tmp_path / "big"
    big.mkdir()
    (big / "f").write_bytes(b"x" * 500_000)
    os.symlink(big, d / "to_big")
    assert dir_size(d) < 100_000


def test_tree_sizer_matches_dir_size(tmp_path):
    from reclaim.sizing import TreeSizer, dir_size
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "a" / "f1").write_bytes(b"x" * 70_000)
    (tmp_path / "a" / "b" / "f2").write_bytes(b"x" * 30_000)
    (tmp_path / "a" / "b" / "c" / "f3").write_bytes(b"x" * 10_000)
    (tmp_path / "top").write_bytes(b"x" * 5_000)
    sizer = TreeSizer()
    assert sizer(tmp_path) == dir_size(tmp_path)
    assert sizer(tmp_path / "top") == dir_size(tmp_path / "top")
    assert sizer(tmp_path / "missing") == 0


def test_tree_sizer_answers_descendants_without_walking_again(tmp_path, monkeypatch):
    import os as _os
    from reclaim import sizing
    from reclaim.sizing import TreeSizer, dir_size
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "f").write_bytes(b"x" * 40_000)
    sizer = TreeSizer()
    sizer(tmp_path)
    calls = []
    real = _os.scandir
    monkeypatch.setattr(sizing.os, "scandir", lambda p: (calls.append(p), real(p))[1])
    assert sizer(tmp_path / "a" / "b") == dir_size(tmp_path / "a" / "b")
    calls.clear()
    sizer(tmp_path / "a" / "b")
    assert calls == []


def test_tree_sizer_is_thread_safe(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from reclaim.sizing import TreeSizer, dir_size
    for i in range(5):
        (tmp_path / f"d{i}" / "x").mkdir(parents=True)
        (tmp_path / f"d{i}" / "x" / "f").write_bytes(b"x" * 20_000)
    sizer = TreeSizer()
    with ThreadPoolExecutor(8) as pool:
        got = list(pool.map(sizer, [tmp_path / f"d{i}" for i in range(5)] * 4))
    assert got == [dir_size(tmp_path / f"d{i}") for i in range(5)] * 4
