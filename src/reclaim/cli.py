from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from reclaim import __version__
from reclaim.catalog import load_catalog
from reclaim.config import ConfigError, default_config_path, load_config
from reclaim.context import ScanContext
from reclaim.providers import all_providers, provider_roots
from reclaim.safety import default_roots
from reclaim.scan import scan_all
from reclaim.sizing import TreeSizer, dir_size


def _non_negative(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reclaim", description="Find and safely clean reclaimable dev-machine data.")
    p.add_argument("--version", action="version", version=f"reclaim {__version__}")
    p.add_argument("--scan-only", action="store_true", help="scan and print, no TUI")
    p.add_argument("--json", action="store_true", help="with --scan-only, print JSON")
    p.add_argument("--dry-run", action="store_true", help="never execute, even after confirm")
    p.add_argument("--min-size-mb", type=_non_negative, default=None, help="hide items smaller than this (default 50)")
    p.add_argument("--config", default=None, help="config file (default ~/.config/reclaim/config.toml)")
    return p


def _launch_tui(**kwargs) -> None:
    from reclaim.tui import run_app

    run_app(**kwargs)


def main(argv=None, out=None, ctx: ScanContext | None = None, entries=None, launch=None, providers=None) -> int:
    args = _parser().parse_args(argv)
    out = out or sys.stdout
    ctx = ctx or ScanContext(home=Path.home())
    config_path = Path(args.config) if args.config else default_config_path(ctx.home)
    try:
        cfg = load_config(config_path, ctx.home)
    except ConfigError as exc:
        sys.stderr.write(f"reclaim: {exc}\n")
        return 2
    ctx.project_roots = cfg.project_roots
    ctx.stale_days = cfg.stale_days
    ctx.large_dir_bytes = cfg.large_dir_mb * 1024 * 1024
    min_mb = args.min_size_mb if args.min_size_mb is not None else cfg.min_size_mb
    ctx.min_size = min_mb * 1024 * 1024
    if ctx.sizer is dir_size:
        ctx.sizer = TreeSizer()
    entries = load_catalog() if entries is None else entries
    providers = all_providers() if providers is None else providers
    if args.scan_only:
        result = scan_all(ctx, entries, providers, ignore=cfg.ignore)
        if args.json:
            payload = {
                "version": 1,
                "free_bytes": shutil.disk_usage(ctx.home).free,
                "errors": result.errors,
                "items": [i.to_json() for i in result.items],
            }
            out.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        else:
            for item in result.items:
                out.write(f"{item.size_bytes / 1e9:8.2f} GB  [{item.risk.value:11}] {item.group}: {item.label}\n")
            for err in result.errors:
                out.write(f"error: {err}\n")
        return 0
    roots = default_roots(ctx.home) + list(cfg.extra_allowlist) + provider_roots(providers, ctx)
    (launch or _launch_tui)(
        ctx=ctx, entries=entries, dry_run=args.dry_run, providers=providers, roots=roots, ignore=cfg.ignore
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
