from reclaim.executor import run_item
from reclaim.model import Item, RemovePath, Risk
from reclaim.safety import default_roots


def data_item(path, risk=Risk.DATA):
    return Item(id="d", label="D", group="G", size_bytes=1, risk=risk, actions=(RemovePath(path),))


def test_data_item_goes_to_trash_with_unique_name(tmp_path):
    trash = tmp_path / ".Trash"
    trash.mkdir()
    (trash / "vol").mkdir()
    src = tmp_path / ".cache" / "vol"
    src.mkdir(parents=True)
    (src / "f").write_text("x")
    res = run_item(data_item(src), default_roots(tmp_path), tmp_path, trash_dir=trash)
    assert res.ok and not src.exists() and (trash / "vol 2" / "f").exists()
    assert not res.measured and res.log[0].startswith("mv ")


def test_non_data_items_are_still_deleted(tmp_path):
    src = tmp_path / ".cache" / "c"
    src.mkdir(parents=True)
    res = run_item(data_item(src, Risk.SAFE), default_roots(tmp_path), tmp_path, trash_dir=tmp_path / ".Trash")
    assert res.ok and not src.exists() and not (tmp_path / ".Trash").exists()


def test_trash_dry_run_moves_nothing_and_guard_still_applies(tmp_path):
    src = tmp_path / ".cache" / "c"
    src.mkdir(parents=True)
    run_item(data_item(src), default_roots(tmp_path), tmp_path, dry_run=True, trash_dir=tmp_path / ".Trash")
    assert src.exists()
    bad = tmp_path / "Documents"
    bad.mkdir()
    res = run_item(data_item(bad), default_roots(tmp_path), tmp_path, trash_dir=tmp_path / ".Trash")
    assert not res.ok and bad.exists()
