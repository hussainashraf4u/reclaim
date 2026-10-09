from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from reclaim import __version__, scancache
from reclaim.ai.settings import AiSettings
from reclaim.audit import default_log_path, read_log, summarize
from reclaim.catalog import load_catalog
from reclaim.config import Config, ConfigError, default_config_path, load_config, set_ignore, set_keys
from reclaim.context import ScanContext
from reclaim.executor import describe, describe_trash
from reclaim.model import Item, RemovePath, Risk
from reclaim.providers import all_providers, provider_roots
from reclaim.runner import execute_items
from reclaim.safety import default_roots
from reclaim.scan import scan_all
from reclaim.selection import human
from reclaim.sizing import TreeSizer, dir_size

EXIT_CONFIG = 2
EXIT_FAILED = 3

# Providers that need project_roots to produce results.
_NEEDS_PROJECT_ROOTS = {"git_worktrees", "project_artifacts", "fvm", "node_versions", "rustup", "version_managers"}


def _non_negative(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _add_common(p: argparse.ArgumentParser, sub: bool) -> None:
    d = argparse.SUPPRESS if sub else None
    p.add_argument("--min-size-mb", type=_non_negative, default=d, help="hide items smaller than this (default 50)")
    p.add_argument("--config", default=d, help="config file (default ~/.config/reclaim/config.toml)")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reclaim", description="Find and safely clean reclaimable dev-machine data.")
    p.add_argument("--version", action="version", version=f"reclaim {__version__}")
    p.add_argument("--scan-only", action="store_true", help="scan and print, no TUI")
    p.add_argument("--json", action="store_true", help="with --scan-only, print JSON")
    p.add_argument("--dry-run", action="store_true", help="never execute, even after confirm")
    _add_common(p, sub=False)
    sub = p.add_subparsers(dest="command")

    clean = sub.add_parser("clean", help="clean without the TUI (shows the plan; --yes executes it)")
    _add_common(clean, sub=True)
    clean.add_argument("--risk", choices=["safe", "regenerable"], default="safe", help="clean items up to this risk (default safe)")
    clean.add_argument("--ids", nargs="+", metavar="ID", help="clean only these item ids (any risk, incl. data with --allow-data)")
    clean.add_argument("--older-than", type=_non_negative, metavar="DAYS", help="only items at least this many days old")
    clean.add_argument("--allow-data", action="store_true", help="allow data-risk items named in --ids")
    clean.add_argument("--yes", action="store_true", help="execute the plan (otherwise it is only printed)")
    clean.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS, help="never execute")

    hist = sub.add_parser("history", help="show what past runs cleaned")
    _add_common(hist, sub=True)
    hist.add_argument("--limit", type=_non_negative, default=20, help="rows to show (default 20, 0 = all)")
    hist.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    doc = sub.add_parser("doctor", help="explain what each scanner found and why one may be empty")
    _add_common(doc, sub=True)

    adv = sub.add_parser("advise", help="AI second opinion on what is safe to clean (heuristics only until `ai enable`)")
    _add_common(adv, sub=True)
    adv.add_argument("--limit", type=_non_negative, default=None, help="largest N cleanable items to review (default: ai_limit, 15)")
    adv.add_argument("--no-cache", action="store_true", help="ask the model again instead of reusing earlier answers")
    adv.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    ai = sub.add_parser("ai", help="enable, disable or inspect the optional AI advisor")
    _add_common(ai, sub=True)
    ai.add_argument("action", choices=["enable", "disable", "status"])
    ai.add_argument("--provider", choices=["ollama", "anthropic"], default="ollama", help="with enable (default ollama, runs locally)")
    ai.add_argument("--model", help="with enable: model name")
    ai.add_argument("--base-url", help="with enable: provider URL")
    ai.add_argument("--yes", action="store_true", help="with enable: download the model without asking")

    for name, text in (("ignore", "hide an item id from every scan"), ("unignore", "stop hiding an item id")):
        s = sub.add_parser(name, help=text)
        _add_common(s, sub=True)
        s.add_argument("ids", nargs="+", metavar="ID")
    return p


def _launch_tui(**kwargs) -> None:
    from reclaim.tui import run_app

    run_app(**kwargs)


def _setup(args, ctx: ScanContext) -> tuple[Config, Path]:
    config_path = Path(args.config) if args.config else default_config_path(ctx.home)
    cfg = load_config(config_path, ctx.home)
    ctx.project_roots = cfg.project_roots
    ctx.stale_days = cfg.stale_days
    ctx.large_dir_bytes = cfg.large_dir_mb * 1024 * 1024
    ctx.big_file_bytes = cfg.big_file_mb * 1024 * 1024
    ctx.duplicate_min_bytes = cfg.duplicate_min_mb * 1024 * 1024
    min_mb = args.min_size_mb if args.min_size_mb is not None else cfg.min_size_mb
    ctx.min_size = min_mb * 1024 * 1024
    if ctx.sizer is dir_size:
        ctx.sizer = TreeSizer()
    return cfg, config_path


def _history(args, ctx: ScanContext, out) -> int:
    rows = read_log(default_log_path(ctx.home))
    total = summarize(rows)
    shown = rows if not args.limit else rows[-args.limit:]
    if getattr(args, "json", False):
        payload = {
            "version": 1,
            "runs": total.runs,
            "failures": total.failures,
            "freed_bytes": total.freed_bytes,
            "rows": [
                {"ts": r.ts, "item": r.item, "ok": r.ok, "freed_bytes": r.freed_bytes, "log": list(r.log)} for r in shown
            ],
        }
        out.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return 0
    if not rows:
        out.write("No cleanups recorded yet.\n")
        return 0
    for r in shown:
        freed = human(r.freed_bytes) if r.freed_bytes is not None else "-"
        out.write(f"{r.ts[:19]}  {'ok    ' if r.ok else 'FAILED'}  {freed:>10}  {r.item}\n")
    out.write(
        f"{total.runs} runs, {total.failures} failed, {human(total.freed_bytes)} freed in total (measured items only)\n"
    )
    return 0


def _select_for_clean(items: list[Item], args) -> tuple[list[Item], list[str]]:
    """Pick items for non-interactive cleaning. Returns (items, problems)."""
    problems: list[str] = []
    if args.ids:
        by_id = {i.id: i for i in items}
        chosen: list[Item] = []
        for item_id in args.ids:
            item = by_id.get(item_id)
            if item is None:
                problems.append(f"no such item: {item_id}")
            elif item.risk is Risk.DATA and not args.allow_data:
                problems.append(f"{item_id} is data-risk: pass --allow-data to clean it")
            elif not item.executable:
                problems.append(f"{item_id} cannot be cleaned ({item.disabled_reason or 'report only'})")
            else:
                chosen.append(item)
        return chosen, problems
    allowed = {Risk.SAFE} if args.risk == "safe" else {Risk.SAFE, Risk.REGENERABLE}
    chosen = [i for i in items if i.executable and i.risk in allowed]
    if args.older_than is not None:
        chosen = [i for i in chosen if i.age_days is not None and i.age_days >= args.older_than]
    return chosen, problems


def _clean(args, ctx: ScanContext, cfg: Config, entries, providers, out) -> int:
    result = scan_all(ctx, entries, providers, ignore=cfg.ignore)
    chosen, problems = _select_for_clean(result.items, args)
    for problem in problems:
        sys.stderr.write(f"reclaim: {problem}\n")
    if problems:
        return EXIT_CONFIG
    roots = default_roots(ctx.home) + list(cfg.extra_allowlist) + provider_roots(providers, ctx)
    trash_dir = ctx.home / ".Trash" if cfg.use_trash else None
    if not chosen:
        out.write("Nothing to clean.\n")
        return 0
    expected = 0
    for item in chosen:
        out.write(f"[{item.risk.value}] {item.label} ({human(item.size_bytes)})\n")
        for action in item.actions:
            if trash_dir is not None and item.risk is Risk.DATA and isinstance(action, RemovePath):
                out.write(f"    {describe_trash(action, trash_dir)}\n")
            else:
                out.write(f"    {describe(action)}\n")
        expected += item.size_bytes
    out.write(f"Expected to free: {human(expected)} (estimate)\n")
    dry = getattr(args, "dry_run", False)
    if not args.yes or dry:
        out.write("dry run: nothing executed.\n" if dry else "Re-run with --yes to execute this plan.\n")
        return 0

    def report(item: Item, res) -> None:
        out.write(f"{'ok    ' if res.ok else 'FAILED'}  {item.label}" + ("" if res.ok else f": {res.log[-1] if res.log else ''}") + "\n")

    summary = execute_items(chosen, roots, ctx.home, default_log_path(ctx.home), trash_dir=trash_dir, on_result=report)
    out.write(f"Cleaned {len(summary.cleaned)} of {len(chosen)} items, freed {human(summary.freed_bytes)} (measured).\n")
    if trash_dir is not None:
        out.write("Data items were moved to the Trash; empty it to release the space.\n")
    return EXIT_FAILED if summary.failed else 0


def _ai(args, ctx: ScanContext, cfg: Config, config_path: Path, out) -> int:
    from reclaim.ai import setup

    if args.action == "status":
        out.write(f"ai_provider: {cfg.ai_provider}\n")
        if cfg.ai_provider != "none":
            out.write(f"ai_model: {cfg.ai_model or '(provider default)'}\n")
        return 0
    if args.action == "disable":
        set_keys(config_path, {"ai_provider": None, "ai_model": None, "ai_base_url": None})
        out.write("AI advisor disabled. Downloaded models are left in place (remove with `ollama rm <model>`).\n")
        return 0
    if args.provider == "ollama":
        model = args.model or setup.DEFAULT_OLLAMA_MODEL
        res = setup.enable_ollama(
            model,
            args.base_url or setup.DEFAULT_OLLAMA_URL,
            confirm=lambda q: args.yes or input(f"{q} [y/N] ").strip().lower() in ("y", "yes"),
            env=setup.AiEnv(which=ctx.which, env=ctx.env),
        )
    else:
        model = args.model
        res = setup.enable_anthropic(ctx.env)
    out.write(res.message + "\n")
    if not res.ok:
        return EXIT_FAILED
    set_keys(config_path, {"ai_provider": args.provider, "ai_model": model, "ai_base_url": args.base_url})
    out.write(f"AI advisor enabled ({args.provider}). Try: reclaim advise\n")
    return 0


def _advise(args, ctx: ScanContext, cfg: Config, entries, providers, out) -> int:
    from reclaim.ai import setup
    from reclaim.ai.advisor import advise
    from reclaim.ai.provider import build_provider

    try:
        provider = build_provider(cfg.ai_provider, cfg.ai_model, cfg.ai_base_url, ctx.env)
    except ValueError as exc:
        sys.stderr.write(f"reclaim: {exc}\n")
        return EXIT_CONFIG
    result = scan_all(ctx, entries, providers, ignore=cfg.ignore)
    cache = None if args.no_cache else setup.default_cache_path(ctx.home)
    res = advise(result.items, ctx, provider, cache, cfg.ai_limit if args.limit is None else args.limit,
                 share_names=cfg.ai_share_names)
    reviewed = [i for i in result.items if i.advice is not None]
    if getattr(args, "json", False):
        out.write(json.dumps({"version": 1, "provider": cfg.ai_provider, "error": str(res.error) if res.error else None,
                              "items": [i.to_json() for i in reviewed]}, ensure_ascii=False, indent=2) + "\n")
        return 0
    if provider is None:
        out.write("AI is off: showing offline checks only. Run `reclaim ai enable` for model review.\n")
    for item in reviewed:
        a = item.advice
        assert a is not None
        out.write(f"{human(item.size_bytes):>10}  [{a.level.value:7}] {item.label}\n            {a.reason}\n")
        if a.rebuild:
            out.write(f"            rebuild: {a.rebuild}\n")
    if not reviewed:
        out.write("Nothing to flag.\n")
    if res.error:
        out.write(f"AI review failed ({res.error}); showing offline checks only.\n")
    return 0


def _doctor(ctx: ScanContext, cfg: Config, config_path: Path, entries, providers, out) -> int:
    out.write(f"config: {config_path} ({'found' if config_path.exists() else 'not found, using defaults'})\n")
    out.write(f"project_roots: {', '.join(str(p) for p in cfg.project_roots) or '(none)'}\n")
    out.write(f"audit log: {default_log_path(ctx.home)}\n")
    for tool in ("brew", "docker", "ollama", "git", "tmutil", "flutter", "node"):
        out.write(f"  {tool:8} {'found' if ctx.which(tool) else 'missing'}\n")
    catalog = scan_all(ctx, entries, (), ignore=cfg.ignore)
    out.write(f"catalog: {len(entries)} rules, {len(catalog.items)} matched on this machine\n")
    for provider in providers:
        start = time.monotonic()
        try:
            items = list(provider.scan(ctx))
            note = ""
        except Exception as exc:
            items, note = [], f"error: {exc}"
        took = time.monotonic() - start
        if not items and not note:
            if provider.name in _NEEDS_PROJECT_ROOTS and not ctx.project_roots:
                note = "empty: set project_roots in the config"
            else:
                note = "nothing found above the size threshold"
        out.write(f"  {provider.name:18} {len(items):3} items  {took:5.1f}s  {note}\n")
    return 0


def main(argv=None, out=None, ctx: ScanContext | None = None, entries=None, launch=None, providers=None) -> int:
    args = _parser().parse_args(argv)
    out = out or sys.stdout
    ctx = ctx or ScanContext(home=Path.home())
    try:
        cfg, config_path = _setup(args, ctx)
    except ConfigError as exc:
        sys.stderr.write(f"reclaim: {exc}\n")
        return EXIT_CONFIG
    if args.command == "history":
        return _history(args, ctx, out)
    if args.command in ("ignore", "unignore"):
        ids = set(cfg.ignore) | set(args.ids) if args.command == "ignore" else set(cfg.ignore) - set(args.ids)
        set_ignore(config_path, ids)
        out.write(f"ignore list now has {len(ids)} id(s) in {config_path}\n")
        return 0
    if args.command == "ai":
        return _ai(args, ctx, cfg, config_path, out)
    entries = load_catalog() if entries is None else entries
    providers = all_providers() if providers is None else providers
    if args.command == "advise":
        return _advise(args, ctx, cfg, entries, providers, out)
    if args.command == "doctor":
        return _doctor(ctx, cfg, config_path, entries, providers, out)
    if args.command == "clean":
        return _clean(args, ctx, cfg, entries, providers, out)
    cache_path = scancache.default_cache_path(ctx.home)
    if args.scan_only:
        previous = scancache.load(cache_path)
        result = scan_all(ctx, entries, providers, ignore=cfg.ignore)
        scancache.annotate(previous, result.items)
        scancache.save(cache_path, result.items, ctx.now())
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
                delta = f"  ({'+' if item.delta_bytes > 0 else '-'}{human(abs(item.delta_bytes))} since last scan)" if item.delta_bytes else ""
                out.write(f"{item.size_bytes / 1e9:8.2f} GB  [{item.risk.value:11}] {item.group}: {item.label}{delta}\n")
            for err in result.errors:
                out.write(f"error: {err}\n")
        return 0
    roots = default_roots(ctx.home) + list(cfg.extra_allowlist) + provider_roots(providers, ctx)
    (launch or _launch_tui)(
        ctx=ctx,
        entries=entries,
        dry_run=args.dry_run,
        providers=providers,
        roots=roots,
        ignore=cfg.ignore,
        use_trash=cfg.use_trash,
        config_path=config_path,
        ai_settings=AiSettings.from_config(cfg),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
