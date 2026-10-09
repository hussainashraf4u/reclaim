import json

from conftest import FakeRun, ok

from reclaim.model import Risk, Run
from reclaim.providers.ios_sim import IosSimProvider

CMD = ("xcrun", "simctl", "list", "devices", "-j")


def device(udid, name, state="Shutdown", available=True, size=50_000_000, path=None):
    d = {"dataPath": path or f"/x/{udid}/data", "udid": udid, "isAvailable": available, "state": state, "name": name}
    if size is not None:
        d["dataPathSize"] = size
    return d


DEVICES = json.dumps({"devices": {
    "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
        device("A", "iPhone 17 Pro", size=600_000_000),
        device("B", "iPhone 17", state="Booted", size=900_000_000),
        device("C", "iPad (A16)", size=10_000),
    ],
    "com.apple.CoreSimulator.SimRuntime.iOS-17-5": [
        device("D", "iPhone 15", available=False, size=300_000_000),
        device("E", "iPhone 14", available=False, size=200_000_000),
    ],
}})


def run(make_ctx, table, **kw):
    ctx = make_ctx(run=FakeRun(table), **kw)
    return {i.id: i for i in IosSimProvider().scan(ctx)}


def test_unavailable_devices_are_one_item_with_summed_size(make_ctx):
    got = run(make_ctx, {CMD: ok(DEVICES)})
    item = got["ios-sim-unavailable"]
    assert item.size_bytes == 500_000_000 and item.risk is Risk.REGENERABLE
    assert item.actions == (Run(("xcrun", "simctl", "delete", "unavailable")),)


def test_shutdown_available_devices_are_data_items_booted_never(make_ctx):
    got = run(make_ctx, {CMD: ok(DEVICES)}, min_size=1_000_000)
    assert set(got) == {"ios-sim-unavailable", "ios-sim:A"}
    a = got["ios-sim:A"]
    assert a.risk is Risk.DATA and a.actions == (Run(("xcrun", "simctl", "delete", "A")),)
    assert "iPhone 17 Pro" in a.label and "iOS-26-0" in a.label
    assert "ios-sim:B" not in got


def test_size_falls_back_to_sizer_when_missing(make_ctx):
    payload = json.dumps({"devices": {"r.iOS-1": [device("Z", "Old", size=None, path="/x/Z/data")]}})
    got = run(make_ctx, {CMD: ok(payload)}, sizer=lambda p: 777)
    assert got["ios-sim:Z"].size_bytes == 777


def test_missing_xcrun_failure_and_bad_json_yield_nothing(make_ctx):
    assert list(IosSimProvider().scan(make_ctx(which=lambda n: None))) == []
    assert run(make_ctx, {}) == {}
    assert run(make_ctx, {CMD: ok("", code=1)}) == {}
    assert run(make_ctx, {CMD: ok("{not json")}) == {}
    assert run(make_ctx, {CMD: ok(json.dumps({"devices": []}))}) == {}


def test_items_carry_device_directory_path(make_ctx):
    got = run(make_ctx, {CMD: ok(DEVICES)}, min_size=1_000_000)
    from pathlib import Path
    assert got["ios-sim:A"].path == Path("/x/A")
    assert got["ios-sim-unavailable"].path == Path("/x/D").parent


def test_old_xcode_availability_string_counts_as_unavailable(make_ctx):
    old = {"dataPath": "/x/O/data", "dataPathSize": 400_000_000, "udid": "O", "state": "Shutdown", "name": "Old",
           "availability": "(unavailable, runtime profile not found)"}
    payload = json.dumps({"devices": {"r.iOS-1": [old]}})
    got = run(make_ctx, {CMD: ok(payload)})
    assert set(got) == {"ios-sim-unavailable"} and got["ios-sim-unavailable"].size_bytes == 400_000_000
