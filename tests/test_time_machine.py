from conftest import FakeRun, ok
from reclaim.providers.time_machine import TimeMachineProvider

CMD = ("tmutil", "listlocalsnapshots", "/")
OUT = (
    "Snapshots for disk /:\n"
    "com.apple.TimeMachine.2026-10-07-091500.local\n"
    "com.apple.TimeMachine.2026-10-08-101500.local\n"
)


def run(make_ctx, table, **kw):
    return list(TimeMachineProvider().scan(make_ctx(run=FakeRun(table), **kw)))


def test_snapshots_give_one_report_only_item_regardless_of_min_size(make_ctx):
    (item,) = run(make_ctx, {CMD: ok(OUT)}, min_size=10**12)
    assert item.id == "time-machine-snapshots" and item.report_only and not item.executable
    assert "2 local snapshots" in item.label
    assert "tmutil deletelocalsnapshots 2026-10-07-091500" in item.why


def test_none_or_failure_yield_nothing(make_ctx):
    assert run(make_ctx, {CMD: ok("Snapshots for disk /:\n")}) == []
    assert run(make_ctx, {}) == []
    assert run(make_ctx, {CMD: ok("", code=1)}) == []
    assert run(make_ctx, {CMD: ok(OUT)}, which=lambda n: None) == []


def test_oldest_snapshot_is_chosen_by_date_not_list_order(make_ctx):
    reversed_out = (
        "Snapshots for disk /:\n"
        "com.apple.TimeMachine.2026-10-08-101500.local\n"
        "com.apple.TimeMachine.2026-10-07-091500.local\n"
    )
    (item,) = run(make_ctx, {CMD: ok(reversed_out)})
    assert "deletelocalsnapshots 2026-10-07-091500" in item.why
