import json

from conftest import FakeRun, ok
from reclaim.model import Risk, Run
from reclaim.providers.docker import DockerProvider, parse_size

SUMMARY = "\n".join(json.dumps(r) for r in [
    {"Active": "7", "Reclaimable": "226.8MB (1%)", "Size": "14.32GB", "TotalCount": "9", "Type": "Images"},
    {"Active": "5", "Reclaimable": "44.78MB (91%)", "Size": "48.83MB", "TotalCount": "9", "Type": "Containers"},
    {"Active": "12", "Reclaimable": "630.3MB (3%)", "Size": "19.67GB", "TotalCount": "13", "Type": "Local Volumes"},
    {"Active": "0", "Reclaimable": "19.19GB", "Size": "30.19GB", "TotalCount": "491", "Type": "Build Cache"},
])
VERBOSE = json.dumps({"Volumes": [
    {"Links": "0", "Name": "dashboard_data", "Size": "630.3MB"},
    {"Links": "1", "Name": "apiv2_clickhouse-data", "Size": "1.8GB"},
    {"Links": "0", "Name": "tiny", "Size": "0B"},
]})

SUMMARY_CMD = ("docker", "system", "df", "--format", "{{json .}}")
VERBOSE_CMD = ("docker", "system", "df", "-v", "--format", "json")


def provider_items(make_ctx, table, **kw):
    ctx = make_ctx(run=FakeRun(table), **kw)
    return {i.id: i for i in DockerProvider().scan(ctx)}


def test_parse_size():
    assert parse_size("19.19GB") == 19_190_000_000
    assert parse_size("226.8MB (1%)") == 226_800_000
    assert parse_size("0B") == 0 and parse_size("") == 0 and parse_size("garbage") == 0


def test_items_from_summary_and_volumes(make_ctx):
    items = provider_items(make_ctx, {SUMMARY_CMD: ok(SUMMARY), VERBOSE_CMD: ok(VERBOSE)})
    assert items["docker-build-cache"].size_bytes == 19_190_000_000
    assert items["docker-build-cache"].actions == (Run(("docker", "builder", "prune", "-a", "-f")),)
    assert items["docker-images-unused"].actions == (Run(("docker", "image", "prune", "-a", "-f")),)
    assert items["docker-containers-stopped"].actions == (Run(("docker", "container", "prune", "-f")),)
    assert items["docker-containers-stopped"].risk is Risk.REGENERABLE
    assert items["docker-images-unused"].risk is Risk.DATA
    vol = items["docker-volume:dashboard_data"]
    assert vol.risk is Risk.DATA and vol.actions == (Run(("docker", "volume", "rm", "dashboard_data")),)
    assert "docker-volume:apiv2_clickhouse-data" not in items
    assert "docker-volume:tiny" not in items


def test_min_size_filters_small_items(make_ctx):
    items = provider_items(make_ctx, {SUMMARY_CMD: ok(SUMMARY), VERBOSE_CMD: ok(VERBOSE)}, min_size=1_000_000_000)
    assert set(items) == {"docker-build-cache"}


def test_nothing_reclaimable_yields_no_items(make_ctx):
    empty = "\n".join(json.dumps({"Reclaimable": "0B", "Size": "1GB", "Type": t}) for t in ("Images", "Containers", "Local Volumes", "Build Cache"))
    items = provider_items(make_ctx, {SUMMARY_CMD: ok(empty), VERBOSE_CMD: ok(json.dumps({"Volumes": []}))})
    assert items == {}


def test_daemon_down_yields_one_disabled_report_only_item(make_ctx):
    items = provider_items(make_ctx, {SUMMARY_CMD: ok("", code=1)})
    (item,) = items.values()
    assert item.id == "docker-down" and item.report_only and not item.executable
    assert item.disabled_reason == "Docker is not running"


def test_runner_failure_is_treated_as_down(make_ctx):
    items = provider_items(make_ctx, {})
    assert list(items) == ["docker-down"]


def test_no_docker_binary_yields_nothing(make_ctx):
    ctx = make_ctx(which=lambda n: None)
    assert list(DockerProvider().scan(ctx)) == []


def test_malformed_json_does_not_raise(make_ctx):
    items = provider_items(make_ctx, {SUMMARY_CMD: ok("not json"), VERBOSE_CMD: ok("{")})
    assert list(items) == ["docker-down"]


CONTEXT_CMD = ("docker", "context", "show")


def test_non_default_context_is_named_in_the_item_text(make_ctx):
    items = provider_items(make_ctx, {SUMMARY_CMD: ok(SUMMARY), VERBOSE_CMD: ok(VERBOSE), CONTEXT_CMD: ok("remote-ssh\n")})
    assert "remote-ssh" in items["docker-build-cache"].why
    assert "remote-ssh" in items["docker-volume:dashboard_data"].why


def test_desktop_context_adds_no_noise(make_ctx):
    items = provider_items(make_ctx, {SUMMARY_CMD: ok(SUMMARY), VERBOSE_CMD: ok(VERBOSE), CONTEXT_CMD: ok("desktop-linux\n")})
    assert "context" not in items["docker-build-cache"].why


def test_verbose_failure_is_reported_not_silent(make_ctx):
    items = provider_items(make_ctx, {SUMMARY_CMD: ok(SUMMARY)})
    unknown = items["docker-volumes-unknown"]
    assert unknown.report_only and not unknown.executable and "volume" in unknown.label.lower()
    assert "docker-build-cache" in items
