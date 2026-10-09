from __future__ import annotations

import os
import sys
from importlib.metadata import entry_points
from pathlib import Path

from reclaim.context import ScanContext
from reclaim.safety import ScopedRoot

PLUGIN_GROUP = "reclaim.providers"
_SENSITIVE = frozenset({".ssh", ".gnupg", "Keychains"})


def all_providers() -> list:
    from reclaim.providers.android import AndroidProvider
    from reclaim.providers.conda_envs import CondaEnvsProvider
    from reclaim.providers.docker import DockerProvider
    from reclaim.providers.files import BigFilesProvider, DuplicatesProvider
    from reclaim.providers.fvm import FvmProvider
    from reclaim.providers.git_worktrees import GitWorktreesProvider
    from reclaim.providers.homebrew import HomebrewProvider
    from reclaim.providers.ios_sim import IosSimProvider
    from reclaim.providers.large_dirs import LargeDirsProvider
    from reclaim.providers.node_versions import NodeVersionsProvider
    from reclaim.providers.ollama import OllamaProvider
    from reclaim.providers.project_artifacts import ProjectArtifactsProvider
    from reclaim.providers.rustup import RustupProvider
    from reclaim.providers.time_machine import TimeMachineProvider
    from reclaim.providers.version_managers import VersionManagersProvider
    from reclaim.providers.vm_disks import VmDisksProvider

    return [
        DockerProvider(),
        GitWorktreesProvider(),
        ProjectArtifactsProvider(),
        AndroidProvider(),
        FvmProvider(),
        NodeVersionsProvider(),
        IosSimProvider(),
        OllamaProvider(),
        CondaEnvsProvider(),
        TimeMachineProvider(),
        LargeDirsProvider(),
        RustupProvider(),
        VersionManagersProvider(),
        HomebrewProvider(),
        VmDisksProvider(),
        BigFilesProvider(),
        DuplicatesProvider(),
    ] + plugin_providers()


def plugin_providers() -> list:
    """Providers contributed by installed packages via the 'reclaim.providers' entry point group.

    Each entry point must load to a zero-argument callable (usually a class) returning an object
    with a `name` and `scan(ctx)`. A plugin that fails to load is skipped with a warning.
    """
    found: list = []
    for ep in entry_points(group=PLUGIN_GROUP):
        try:
            provider = ep.load()()
            if not hasattr(provider, "scan") or not hasattr(provider, "name"):
                raise TypeError("provider needs 'name' and 'scan(ctx)'")
        except Exception as exc:
            sys.stderr.write(f"reclaim: skipping plugin '{ep.name}': {exc}\n")
            continue
        provider.external = True
        found.append(provider)
    return found


def _plugin_root_ok(root, home: Path) -> bool:
    """Plugin roots get the same limits as 'extra_allowlist': a specific folder well below home."""
    if isinstance(root, ScopedRoot):
        root = root.path
    path = Path(root)
    if ".." in path.parts:
        return False
    real, real_home = Path(os.path.realpath(path)), Path(os.path.realpath(home))
    if real_home not in real.parents or _SENSITIVE & set(real.parts):
        return False
    rel = real.relative_to(real_home).parts
    return len(rel) >= (3 if rel[0] == "Library" else 2)



def provider_roots(providers, ctx: ScanContext) -> list[Path]:
    roots: list[Path] = []
    for provider in providers:
        fn = getattr(provider, "roots", None)
        if fn:
            found = fn(ctx)
            if getattr(provider, "external", False):
                found = [r for r in found if _plugin_root_ok(r, ctx.home)]
            roots.extend(found)
    return roots
