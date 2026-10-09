from __future__ import annotations

from pathlib import Path

from reclaim.context import ScanContext


def all_providers() -> list:
    from reclaim.providers.android import AndroidProvider
    from reclaim.providers.conda_envs import CondaEnvsProvider
    from reclaim.providers.docker import DockerProvider
    from reclaim.providers.fvm import FvmProvider
    from reclaim.providers.git_worktrees import GitWorktreesProvider
    from reclaim.providers.ios_sim import IosSimProvider
    from reclaim.providers.large_dirs import LargeDirsProvider
    from reclaim.providers.node_versions import NodeVersionsProvider
    from reclaim.providers.ollama import OllamaProvider
    from reclaim.providers.project_artifacts import ProjectArtifactsProvider
    from reclaim.providers.time_machine import TimeMachineProvider

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
    ]


def provider_roots(providers, ctx: ScanContext) -> list[Path]:
    roots: list[Path] = []
    for provider in providers:
        fn = getattr(provider, "roots", None)
        if fn:
            roots.extend(fn(ctx))
    return roots
