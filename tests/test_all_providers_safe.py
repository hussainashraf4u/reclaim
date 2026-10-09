import os

import pytest
from conftest import FakeRun, ok

from reclaim.model import RemovePath
from reclaim.providers import all_providers, provider_roots
from reclaim.safety import UnsafePath, check_removable, default_roots


def test_every_remove_action_from_every_provider_passes_the_guard(tmp_path, make_ctx):
    proj = tmp_path / "proj" / "web"
    proj.mkdir(parents=True)
    (proj / "package.json").write_text("{}")
    (proj / "node_modules").mkdir()
    os.utime(proj / "node_modules", (1, 1))
    avd = tmp_path / ".android" / "avd" / "P.avd"
    avd.mkdir(parents=True)
    (avd / "userdata-qemu.img.qcow2").write_text("x")
    ndk = tmp_path / "Library" / "Android" / "sdk" / "ndk" / "29.0.1"
    ndk.mkdir(parents=True)
    kotlin = tmp_path / "fvm/versions/3/packages/flutter_tools/gradle/src/main/kotlin"
    kotlin.mkdir(parents=True)
    (kotlin / "FlutterExtension.kt").write_text('val ndkVersion: String = "28.2.1"\n')
    (tmp_path / "fvm" / "versions" / "3.38.10").mkdir(parents=True)
    (proj / ".fvmrc").write_text('{"flutter": "3.44.8"}')
    (tmp_path / ".nvm" / "versions" / "node" / "v16.20.0").mkdir(parents=True)
    (tmp_path / ".nvm" / "alias").mkdir(parents=True)
    (tmp_path / ".nvm" / "alias" / "default").write_text("20")
    run = FakeRun({("git", "-C", str(proj), "check-ignore", "-q", "node_modules"): ok("", 0)})
    ctx = make_ctx(project_roots=(tmp_path / "proj",), run=run)
    providers = all_providers()
    roots = default_roots(tmp_path) + provider_roots(providers, ctx)
    removed = 0
    for provider in providers:
        for item in provider.scan(ctx):
            for action in item.actions:
                if isinstance(action, RemovePath):
                    removed += 1
                    check_removable(action.path, roots, tmp_path)
    assert removed >= 5


def test_provider_roots_never_allow_deleting_the_root_itself(tmp_path, make_ctx):
    ctx = make_ctx(project_roots=(tmp_path / "proj",))
    providers = all_providers()
    roots = default_roots(tmp_path) + provider_roots(providers, ctx)
    for root in provider_roots(providers, ctx):
        path = getattr(root, "path", root)
        path.mkdir(parents=True, exist_ok=True)
        with pytest.raises(UnsafePath):
            check_removable(path, roots, tmp_path)
