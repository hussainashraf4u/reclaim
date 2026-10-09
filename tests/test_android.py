import pytest

from reclaim.model import RemovePath, Risk
from reclaim.providers.android import QEMU_PROCESSES, AndroidProvider, required_ndks
from reclaim.safety import UnsafePath, check_removable, default_roots

KOTLIN = 'class FlutterExtension {\n    val ndkVersion: String = "28.2.13676358"\n}\n'


def flutter(tmp_path, version="3.44.8", ndk="28.2.13676358"):
    path = tmp_path / "fvm" / "versions" / version / "packages" / "flutter_tools" / "gradle" / "src" / "main" / "kotlin"
    path.mkdir(parents=True)
    (path / "FlutterExtension.kt").write_text(KOTLIN.replace("28.2.13676358", ndk))


def ndks(tmp_path, *versions):
    for v in versions:
        (tmp_path / "Library" / "Android" / "sdk" / "ndk" / v).mkdir(parents=True)


def avd(tmp_path, name, files=("userdata-qemu.img.qcow2", "cache.img.qcow2"), snapshots=True):
    d = tmp_path / ".android" / "avd" / f"{name}.avd"
    d.mkdir(parents=True)
    (d / "config.ini").write_text("x")
    for f in files:
        (d / f).write_text("x")
    if snapshots:
        (d / "snapshots").mkdir()
    return d


def test_required_ndks_reads_every_installed_flutter(tmp_path):
    flutter(tmp_path, "3.44.8", "28.2.13676358")
    flutter(tmp_path, "3.38.10", "27.0.12077973")
    assert required_ndks(tmp_path)[0] == {"28.2.13676358", "27.0.12077973"}
    assert required_ndks(tmp_path / "nowhere")[0] == set()


def test_unrequired_ndks_are_removable_required_kept(tmp_path, make_ctx):
    flutter(tmp_path)
    ndks(tmp_path, "27.0.12077973", "28.2.13676358", "29.0.13846066")
    items = {i.id: i for i in AndroidProvider().scan(make_ctx())}
    assert {i for i in items if i.startswith("ndk:")} == {"ndk:27.0.12077973", "ndk:29.0.13846066"}
    item = items["ndk:29.0.13846066"]
    assert item.risk is Risk.REGENERABLE and item.executable
    assert item.actions == (RemovePath(tmp_path / "Library/Android/sdk/ndk/29.0.13846066"),)


def test_without_flutter_ndks_are_report_only(tmp_path, make_ctx):
    ndks(tmp_path, "27.0.12077973")
    (item,) = [i for i in AndroidProvider().scan(make_ctx()) if i.id.startswith("ndk:")]
    assert item.report_only and not item.executable


def test_avd_overlay_item_lists_only_existing_files_and_is_data(tmp_path, make_ctx):
    d = avd(tmp_path, "Pixel_9")
    (item,) = [i for i in AndroidProvider().scan(make_ctx()) if i.id.startswith("avd:")]
    assert item.id == "avd:Pixel_9" and item.risk is Risk.DATA
    assert set(item.actions) == {
        RemovePath(d / "userdata-qemu.img.qcow2"),
        RemovePath(d / "cache.img.qcow2"),
        RemovePath(d / "snapshots"),
    }
    assert item.busy == QEMU_PROCESSES and "config" in item.why.lower()


def test_running_emulator_disables_avd(tmp_path, make_ctx):
    avd(tmp_path, "Pixel_9")
    ctx = make_ctx(is_running=lambda n: n == "qemu-system-aarch64")
    (item,) = [i for i in AndroidProvider().scan(ctx) if i.id.startswith("avd:")]
    assert item.disabled_reason == "qemu-system-aarch64 is running" and not item.executable


def test_avd_without_overlays_or_small_is_skipped(tmp_path, make_ctx):
    avd(tmp_path, "Empty", files=(), snapshots=False)
    avd(tmp_path, "Small")
    items = list(AndroidProvider().scan(make_ctx(min_size=10_000_000)))
    assert [i for i in items if i.id.startswith("avd:")] == []


def test_avd_name_with_space_gets_hashed_id(tmp_path, make_ctx):
    avd(tmp_path, "My Phone")
    (item,) = [i for i in AndroidProvider().scan(make_ctx()) if i.id.startswith("avd:")]
    assert " " not in item.id and "My Phone" in item.label


def test_roots_allow_exactly_the_paths_actions_use(tmp_path, make_ctx):
    flutter(tmp_path)
    ndks(tmp_path, "29.0.13846066")
    avd(tmp_path, "Pixel_9")
    provider = AndroidProvider()
    ctx = make_ctx()
    roots = default_roots(tmp_path) + provider.roots(ctx)
    checked = 0
    for item in provider.scan(ctx):
        for action in item.actions:
            check_removable(action.path, roots, tmp_path)
            checked += 1
    assert checked >= 4
    for denied in (tmp_path / ".android" / "avd", tmp_path / "Library/Android/sdk/ndk", tmp_path / "Library/Android/sdk/platforms"):
        denied.mkdir(parents=True, exist_ok=True)
        with pytest.raises(UnsafePath):
            check_removable(denied, roots, tmp_path)


def gradle(tmp_path, text, name="build.gradle.kts", project="app1"):
    d = tmp_path / "proj" / project / "android" / "app"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


def test_literal_ndk_pins_in_project_gradle_files_are_kept(tmp_path, make_ctx):
    flutter(tmp_path)
    ndks(tmp_path, "26.1.10909125", "27.0.12077973", "29.0.13846066")
    gradle(tmp_path, 'android {\n    ndkVersion = "27.0.12077973"\n}\n')
    gradle(tmp_path, "android {\n    ndkVersion '26.1.10909125'\n}\n", name="build.gradle", project="app2")
    ctx = make_ctx(project_roots=(tmp_path / "proj",))
    got = {i.id for i in AndroidProvider().scan(ctx) if i.id.startswith("ndk:")}
    assert got == {"ndk:29.0.13846066"}


def test_flutter_ndk_reference_in_gradle_is_not_a_literal(tmp_path, make_ctx):
    flutter(tmp_path)
    ndks(tmp_path, "29.0.13846066")
    gradle(tmp_path, "android { ndkVersion = flutter.ndkVersion }\n")
    ctx = make_ctx(project_roots=(tmp_path / "proj",))
    assert [i.id for i in AndroidProvider().scan(ctx) if i.id.startswith("ndk:")] == ["ndk:29.0.13846066"]


def test_unreadable_gradle_file_makes_ndks_report_only(tmp_path, make_ctx):
    flutter(tmp_path)
    ndks(tmp_path, "29.0.13846066")
    d = tmp_path / "proj" / "app" / "android" / "app"
    d.mkdir(parents=True)
    (d / "build.gradle").write_bytes(b"\xff\xfe\x00")
    ctx = make_ctx(project_roots=(tmp_path / "proj",))
    (item,) = [i for i in AndroidProvider().scan(ctx) if i.id.startswith("ndk:")]
    assert item.report_only
