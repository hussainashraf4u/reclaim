import io

from reclaim.cli import main
from reclaim.context import ScanContext
from reclaim.model import Item, RemovePath, Risk, Run


class FakeProvider:
    name = "fake"

    def __init__(self, items):
        self.items = items

    def scan(self, ctx):
        return iter(self.items)


def setup(tmp_path):
    safe_dir = tmp_path / ".npm" / "_cacache"
    safe_dir.mkdir(parents=True)
    (safe_dir / "f").write_text("x")
    regen = tmp_path / ".gradle" / "caches"
    regen.mkdir(parents=True)
    data = tmp_path / ".cache" / "vol"
    data.mkdir(parents=True)
    items = [
        Item("safe1", "Safe one", "G", 10, Risk.SAFE, actions=(RemovePath(safe_dir),)),
        Item("regen1", "Regen one", "G", 20, Risk.REGENERABLE, actions=(RemovePath(regen),), age_days=90),
        Item("data1", "Data one", "G", 30, Risk.DATA, actions=(RemovePath(data),)),
        Item("report", "Report", "G", 40, Risk.SAFE, report_only=True),
    ]
    return safe_dir, regen, data, FakeProvider(items)


def run(tmp_path, provider, *args):
    out = io.StringIO()
    code = main(["clean", *args, "--min-size-mb", "0"], out=out, ctx=ScanContext(home=tmp_path), entries=[], providers=[provider])
    return code, out.getvalue()


def test_without_yes_prints_plan_only(tmp_path):
    safe_dir, _, _, provider = setup(tmp_path)
    code, text = run(tmp_path, provider)
    assert code == 0 and "Safe one" in text and "Regen one" not in text
    assert "Re-run with --yes" in text and safe_dir.exists()


def test_yes_cleans_safe_and_logs(tmp_path):
    safe_dir, regen, data, provider = setup(tmp_path)
    code, text = run(tmp_path, provider, "--yes")
    assert code == 0 and not safe_dir.exists() and regen.exists() and data.exists()
    assert (tmp_path / ".local" / "state" / "reclaim" / "log.jsonl").exists()


def test_risk_regenerable_and_older_than(tmp_path):
    _, regen, _, provider = setup(tmp_path)
    run(tmp_path, provider, "--risk", "regenerable", "--older-than", "60", "--yes")
    assert not regen.exists()


def test_older_than_excludes_items_without_age(tmp_path):
    safe_dir, _, _, provider = setup(tmp_path)
    code, text = run(tmp_path, provider, "--older-than", "1", "--yes")
    assert "Nothing to clean" in text and safe_dir.exists()


def test_data_needs_explicit_ids_and_allow_data(tmp_path):
    _, _, data, provider = setup(tmp_path)
    code, _ = run(tmp_path, provider, "--ids", "data1", "--yes")
    assert code == 2 and data.exists()
    code, _ = run(tmp_path, provider, "--ids", "data1", "--allow-data", "--yes")
    assert code == 0 and not data.exists()


def test_unknown_and_report_only_ids_are_errors(tmp_path):
    _, _, _, provider = setup(tmp_path)
    assert run(tmp_path, provider, "--ids", "nope")[0] == 2
    assert run(tmp_path, provider, "--ids", "report")[0] == 2


def test_dry_run_never_executes(tmp_path):
    safe_dir, _, _, provider = setup(tmp_path)
    code, text = run(tmp_path, provider, "--yes", "--dry-run")
    assert code == 0 and safe_dir.exists() and "nothing executed" in text


def test_failure_gives_exit_3(tmp_path):
    provider = FakeProvider([Item("bad", "Bad", "G", 5, Risk.SAFE, actions=(Run(("false",)),))])
    code, text = run(tmp_path, provider, "--yes")
    assert code == 3 and "FAILED" in text


def test_use_trash_moves_data_items(tmp_path):
    _, _, data, provider = setup(tmp_path)
    cfg = tmp_path / ".config" / "reclaim"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text("use_trash = true\n")
    code, text = run(tmp_path, provider, "--ids", "data1", "--allow-data", "--yes")
    assert code == 0 and not data.exists() and (tmp_path / ".Trash" / "vol").exists()
    assert "Trash" in text
