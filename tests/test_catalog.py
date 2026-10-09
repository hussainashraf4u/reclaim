import pytest

from reclaim.catalog import CatalogError, entry_to_items, load_catalog, parse_entries
from reclaim.context import ScanContext
from reclaim.executor import describe
from reclaim.model import RemovePath, Risk, Run
from reclaim.safety import check_removable, default_roots

SIMPLE = """
[[item]]
id = "npm-cache"
label = "npm cache"
group = "JS"
path = "~/.npm/_cacache"
risk = "safe"
why = "downloads"
clean = { run = ["npm", "cache", "clean", "--force"] }
needs = "npm"
"""


def ctx(tmp_path, **kw):
    base = dict(home=tmp_path, which=lambda n: "/bin/" + n, sizer=lambda p: 1000, is_running=lambda n: False)
    base.update(kw)
    return ScanContext(**base)


def test_parse_entry_fields():
    (e,) = parse_entries(SIMPLE)
    assert e.id == "npm-cache" and e.risk is Risk.SAFE
    assert e.clean_run == ("npm", "cache", "clean", "--force") and e.needs == "npm"


@pytest.mark.parametrize("bad", [
    '[[item]]\nid="a"\nlabel="A"\ngroup="G"\nrisk="safe"\n',
    '[[item]]\nid="a"\nlabel="A"\ngroup="G"\nrisk="nope"\npath="~/x"\n',
    '[[item]]\nid="a"\nlabel="A"\ngroup="G"\nrisk="safe"\npath="~/x"\nglob="~/y/*"\n',
    '[[item]]\nlabel="A"\ngroup="G"\nrisk="safe"\npath="~/x"\n',
])
def test_parse_rejects_invalid(bad):
    with pytest.raises(CatalogError):
        parse_entries(bad)


def test_items_use_clean_command(tmp_path):
    (tmp_path / ".npm" / "_cacache").mkdir(parents=True)
    (e,) = parse_entries(SIMPLE)
    (item,) = entry_to_items(e, ctx(tmp_path))
    assert item.actions == (Run(("npm", "cache", "clean", "--force")),) and item.size_bytes == 1000


def test_skips_when_needs_missing_or_path_absent(tmp_path):
    (tmp_path / ".npm" / "_cacache").mkdir(parents=True)
    (e,) = parse_entries(SIMPLE)
    assert entry_to_items(e, ctx(tmp_path, which=lambda n: None)) == []
    assert entry_to_items(e, ctx(tmp_path / "elsewhere")) == []


def test_min_size_filters(tmp_path):
    (tmp_path / ".npm" / "_cacache").mkdir(parents=True)
    (e,) = parse_entries(SIMPLE)
    assert entry_to_items(e, ctx(tmp_path, min_size=2000)) == []


def test_glob_makes_item_per_match_with_spaces(tmp_path):
    base = tmp_path / "Library" / "Developer" / "Xcode" / "iOS DeviceSupport"
    (base / "iPhone 17.0 (ü)").mkdir(parents=True)
    (base / "iPad 18.1").mkdir()
    (e,) = parse_entries(
        '[[item]]\nid="ds"\nlabel="DeviceSupport"\ngroup="Xcode"\nrisk="regenerable"\n'
        'glob="~/Library/Developer/Xcode/iOS DeviceSupport/*"\n'
    )
    items = entry_to_items(e, ctx(tmp_path))
    assert [i.label for i in items] == ["DeviceSupport: iPad 18.1", "DeviceSupport: iPhone 17.0 (ü)"] or sorted(i.label for i in items) == ["DeviceSupport: iPad 18.1", "DeviceSupport: iPhone 17.0 (ü)"]
    by_label = {i.label: i for i in items}
    assert by_label["DeviceSupport: iPhone 17.0 (ü)"].actions == (RemovePath(base / "iPhone 17.0 (ü)"),)
    assert "'" in describe(by_label["DeviceSupport: iPhone 17.0 (ü)"].actions[0])


def test_glob_ids_are_typeable_and_unique(tmp_path):
    base = tmp_path / ".Trash"
    for name in ("Screenshot 2026-10-09 at 10.00.png", "a,b", "Untitled Folder"):
        (base / name).mkdir(parents=True)
    (e,) = parse_entries('[[item]]\nid="trash"\nlabel="Trash"\ngroup="S"\nrisk="data"\nglob="~/.Trash/*"\n')
    items = entry_to_items(e, ctx(tmp_path))
    ids = [i.id for i in items]
    assert len(set(ids)) == 3
    assert all(i.startswith("trash:") and not any(c in i for c in " ,\t") for i in ids)


def test_busy_names_are_carried_on_item(tmp_path):
    (tmp_path / "Library" / "Caches" / "X").mkdir(parents=True)
    (e,) = parse_entries(
        '[[item]]\nid="x"\nlabel="X"\ngroup="G"\nrisk="safe"\npath="~/Library/Caches/X"\nbusy=["Xcode"]\n'
    )
    (item,) = entry_to_items(e, ctx(tmp_path))
    assert item.busy == ("Xcode",)


def test_shipped_catalog_protects_user_state():
    ids = {e.id for e in load_catalog()}
    assert "pub-cache" not in ids and {"pub-cache-hosted", "pub-cache-git"} <= ids
    by_id = {e.id: e for e in load_catalog()}
    assert by_id["user-logs"].glob == "~/Library/Logs/*"
    assert by_id["fastlane"].risk is not Risk.SAFE
    assert by_id["maven"].risk is Risk.DATA


def test_busy_process_disables_item(tmp_path):
    (tmp_path / "Library" / "Caches" / "X").mkdir(parents=True)
    (e,) = parse_entries(
        '[[item]]\nid="x"\nlabel="X"\ngroup="G"\nrisk="safe"\npath="~/Library/Caches/X"\nbusy=["Xcode"]\n'
    )
    (item,) = entry_to_items(e, ctx(tmp_path, is_running=lambda n: n == "Xcode"))
    assert item.disabled_reason == "Xcode is running" and not item.executable


def test_shipped_catalog_is_valid_and_within_allowlist(tmp_path):
    entries = load_catalog()
    assert len(entries) >= 30
    assert len({e.id for e in entries}) == len(entries)
    roots = default_roots(tmp_path)
    for e in entries:
        if e.clean_run:
            continue
        pattern = e.path or e.glob
        assert pattern.startswith("~/"), e.id
        sample = tmp_path / pattern[2:].replace("*", "sample")
        sample.mkdir(parents=True, exist_ok=True)
        check_removable(sample, roots, tmp_path)
