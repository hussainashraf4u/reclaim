# reclaim

**Find and safely clean reclaimable dev-machine data on macOS.**

`reclaim` is an interactive terminal app (TUI) that scans your Mac for the things developers accumulate and forget about: package-manager caches, build output, Xcode and Android leftovers, Docker junk, old Node and Flutter versions, simulators, local LLM models. It ranks them by size, explains each one, and cleans only the items you pick.

```
  12.40 GB  [safe       ] Xcode: DerivedData
   8.10 GB  [regenerable] Projects: node_modules (stale, git-ignored)
   5.72 GB  [safe       ] Docker: build cache
   ...
```

> **Status:** early (`0.1.0`), macOS only. Contributions are welcome, see [Contributing](#contributing).

## Table of contents

- [Why reclaim](#why-reclaim)
- [Install](#install)
- [Usage](#usage)
- [Safety model](#safety-model)
- [What it finds](#what-it-finds)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Contributing](#contributing)
- [Building a standalone executable](#building-a-standalone-executable)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Why reclaim

Generic disk cleaners know about system caches but not about `DerivedData`, unpinned FVM Flutter SDKs, Android NDKs, stale `node_modules` in a project you haven't opened in a month, or Ollama models you pulled once. `reclaim` knows about those, and it is built around one rule: **it should be hard to delete something you care about.**

- Every action is shown as the exact command or path before anything runs.
- Deletion is restricted to an allowlist of cache locations. It never touches `$HOME` or `/`.
- Anything that could be real data needs you to type its id to confirm.
- Anything it can't clean safely is shown as report-only, so you still see where the space went.

## Install

**Requirements:** macOS, Python 3.11+. The only runtime dependency is [Textual](https://textual.textualize.io/).

### From source (recommended while the project is young)

```bash
git clone https://github.com/<your-username>/reclaim.git
cd reclaim
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/reclaim
```

With [uv](https://docs.astral.sh/uv/):

```bash
uv run reclaim
```

### Standalone binary

See [Building a standalone executable](#building-a-standalone-executable) to produce a single-file binary you can hand to someone without Python installed.

## Usage

```bash
reclaim                       # launch the TUI
reclaim --dry-run             # TUI, but nothing is ever executed, even after confirming
reclaim --scan-only           # print a ranked table, no TUI
reclaim --scan-only --json    # same, as JSON (scriptable)
reclaim --min-size-mb 200     # hide items smaller than 200 MB (default 50)
reclaim --config PATH         # use another config file
reclaim --version
```

The first time, run it with `--dry-run` or `--scan-only` to see what it finds.

### TUI keys

| Key      | Action                                   |
| -------- | ---------------------------------------- |
| `space`  | Select / deselect the highlighted item   |
| `a`      | Select everything marked `safe`          |
| `c`      | Show the plan (exact commands) and confirm |
| `/`      | Filter the list by text                  |
| `1`–`5`  | Switch tab: All / Safe / Regenerable / Data / Report-only |
| `esc`    | Clear the filter / close a dialog        |
| `q`      | Quit                                     |

### Risk levels

| Risk          | Meaning                                                                 | Confirmation                |
| ------------- | ----------------------------------------------------------------------- | --------------------------- |
| `safe`        | Pure cache. Gets rebuilt on demand with no loss.                        | Plan screen                 |
| `regenerable` | Can be recreated, but costs time or a download (`node_modules`, SDKs).  | Plan screen                 |
| `data`        | May hold something you want (volumes, models, environments).            | Type the item id to confirm |

### JSON output

`--scan-only --json` prints one object:

```jsonc
{
  "version": 1,
  "free_bytes": 123456789012,
  "errors": [],
  "items": [
    {
      "id": "homebrew-cache",
      "label": "Homebrew downloads",
      "group": "System",
      "size_bytes": 1234567890,
      "risk": "safe",
      "why": "Old bottles and downloads.",
      "actions": [{ "type": "run", "argv": ["brew", "cleanup", "-s"] }],
      "disabled_reason": null,
      "report_only": false,
      "executable": true,
      "path": "/Users/you/Library/Caches/Homebrew"
    }
  ]
}
```

### Exit codes

| Code | Meaning                                                   |
| ---- | --------------------------------------------------------- |
| `0`  | Success                                                   |
| `2`  | Invalid config file (one error line is printed to stderr) |

## Safety model

Safety is the core of this project, so it is documented in full. Please read this before sending a PR that touches deletion.

1. **Plan first.** Every clean is rendered as an exact command (`rm -rf '<path>'` or an argv such as `brew cleanup -s`) on the plan screen before it runs.
2. **Allowlist of roots.** `RemovePath` actions are checked by `check_removable` in [`src/reclaim/safety.py`](src/reclaim/safety.py). A path must be:
   - absolute and free of `..`
   - not `/`, `$HOME`, or an ancestor of `$HOME`
   - inside an allowlisted root, checked against both the resolved path **and** the link location, so a symlink can't smuggle a deletion outside the allowlist
3. **Scoped roots.** Project roots from your config only allow specific artifact names (`node_modules`, `build`, `.venv`, `target`, ...), never arbitrary files in your project.
4. **Stale and ignored only.** Project artifacts are listed only when untouched for `stale_days` and git-ignored, so nothing tracked by git is offered.
5. **Busy-process check.** Items can declare processes that must not be running (for example a tool whose cache is in use). If one is running, the item is skipped with a message.
6. **Type-to-confirm for `data`.** You must type the id of every `data` item in the plan.
7. **Dry run.** `--dry-run` never executes, even after you confirm.
8. **Audit log.** Every run is appended to `~/.local/state/reclaim/log.jsonl` with timestamp, item, success, output and bytes freed.
9. **Config can't widen the blast radius.** `project_roots` and `extra_allowlist` are rejected if they are `/`, your home, an ancestor, contain `..`, sit under `~/Library` (project roots), or point at `.ssh`, `.gnupg` or `Keychains`. `extra_allowlist` entries must be at least two levels below home.

There is a test that runs every provider against a fake home and asserts that **every** remove action it emits passes the guard ([`tests/test_all_providers_safe.py`](tests/test_all_providers_safe.py)). New providers must keep it green.

## What it finds

### Catalog (static, declarative)

Simple "this folder is a cache" rules live in TOML files under [`src/reclaim/catalog_data/`](src/reclaim/catalog_data): `js.toml`, `languages.toml`, `mobile.toml`, `python.toml`, `system.toml`. They cover things like Homebrew downloads, user logs, the Trash, and package manager, language and mobile tooling caches.

### Providers (logic-driven)

Anything that needs inspection rather than a fixed path is a provider:

| Provider            | What it reports / cleans                                                                                         |
| ------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Docker              | Build cache, unused images, stopped containers, unattached volumes                                               |
| Git worktrees       | Clean checkouts only; the branch is kept                                                                         |
| Project artifacts   | `node_modules`, `build`, `.venv`, `target` and similar in your project roots, only when stale and git-ignored    |
| Android             | AVD userdata overlays; NDKs Flutter does not need                                                                |
| FVM                 | Flutter versions no project pins                                                                                 |
| Node versions       | nvm/fnm Node versions that are neither the default nor pinned by a project                                       |
| iOS Simulators      | Unavailable and shut-down devices                                                                                |
| Ollama              | Downloaded models (needs the server running; scanning never launches it)                                         |
| Conda               | Environments and package cache                                                                                   |
| Time Machine        | Local snapshots (**report only**)                                                                                |
| Large directories   | Bounded scan for big folders no rule covers (**report only**)                                                    |

Providers that need project knowledge (worktrees, artifacts, FVM, Node versions) require `project_roots` in your config, because they need to know what your projects pin.

## Configuration

Optional. Default location: `~/.config/reclaim/config.toml`. All keys are optional:

```toml
project_roots   = ["~/Documents/projects"]   # enables worktree / artifact / FVM / Node-version providers
extra_allowlist = []                          # extra removable roots (be conservative)
ignore          = ["trash"]                   # item ids to hide
min_size_mb     = 50                          # hide items smaller than this
stale_days      = 30                          # age before a project artifact counts as stale
large_dir_mb    = 1000                        # size above which unmanaged folders are listed
```

Paths must start with `~/` or `/`. Unknown keys are an error, and a bad config prints a single error line and exits with code `2`.

## Project layout

```
src/reclaim/
├── cli.py            argument parsing, wiring, --scan-only output
├── tui.py            Textual app: tabs, table, plan / run / detail screens
├── tui.tcss          Textual stylesheet (bundled into the executable)
├── scan.py           runs catalog + providers, collects errors
├── catalog.py        loads and validates catalog_data/*.toml
├── catalog_data/     declarative cache rules (TOML)
├── providers/        logic-driven scanners (docker, android, fvm, ...)
├── model.py          Item, Risk, RemovePath, Run
├── safety.py         the deletion allowlist and guard
├── executor.py       runs an item's actions, busy checks, dry run
├── audit.py          append-only JSONL log
├── config.py         config loading and validation
├── context.py        ScanContext: injectable home, which, run, sizer, clock
├── sizing.py         fast directory sizing
├── selection.py      selection state and plan text
└── units.py          size parsing and formatting
tests/                pytest suite (268 tests)
scripts/              build_executable.sh
```

`ScanContext` injects everything environment-specific (home directory, `which`, command runner, process check, clock), so scanners are tested against a fake home without touching your real disk.

## Contributing

Contributions are very welcome, especially new cache rules and providers for tools you use.

### Dev setup

```bash
git clone https://github.com/<your-username>/reclaim.git
cd reclaim
python3 -m venv .venv
.venv/bin/pip install -e . pytest pytest-asyncio
.venv/bin/pytest
```

Or with uv: `uv sync && uv run pytest`.

### Add a cache (the easy way)

Add an entry to a file in `src/reclaim/catalog_data/`:

```toml
[[item]]
id    = "my-cache"
label = "My cache"
group = "Tools"
path  = "~/.cache/my-tool"        # or: glob = "~/Library/Foo/*"
risk  = "safe"                     # safe | regenerable | data
why   = "Why it grows and what happens if removed."

# optional
clean = { run = ["my-tool", "cache", "clean"] }   # use a tool's own cleaner instead of rm
needs = "my-tool"                                 # only show if this binary is on PATH
busy  = ["MyApp"]                                 # refuse to clean while this process is running
```

Rules:

- Use exactly one of `path` or `glob`, and start it with `~/` or `/`.
- If the path is outside `safety.default_roots`, add its root there. The catalog test fails otherwise, on purpose.
- Be honest in `why` and pick the **more** cautious risk when unsure.

### Add a provider (when a fixed path isn't enough)

1. Create `src/reclaim/providers/<name>.py` with a class that has a `name` and a `scan(ctx) -> Iterator[Item]` method. Optionally add `roots(ctx) -> list` to contribute removable roots.
2. Register it in `all_providers()` in `src/reclaim/providers/__init__.py`.
3. Use only `ctx` (`ctx.home`, `ctx.which`, `ctx.run`, `ctx.is_running`, ...), never the real environment, so it's testable.
4. Scanning must be **read-only** and must not launch applications.
5. Add `tests/test_<name>.py` and make sure `tests/test_all_providers_safe.py` still passes.

### Guidelines

- **Safety changes need extra scrutiny.** Anything that widens the allowlist or weakens a check needs a clear justification and a test.
- Keep scans fast: skip anything below `ctx.min_size`, bound any directory walks.
- Follow the surrounding style. The codebase uses type hints and `from __future__ import annotations`.
- Add or update tests for every behaviour change; run `pytest` before opening a PR.
- Open an issue first for large changes.

## Building a standalone executable

```bash
scripts/build_executable.sh
```

This creates `.venv` if needed, installs a pinned PyInstaller, builds a single-file binary, smoke-tests it against an empty `HOME`, and packages it:

```
dist/reclaim
dist/reclaim-<version>-macos-<arch>.zip
dist/reclaim-<version>-macos-<arch>.zip.sha256
```

Verify a download with `cd dist && shasum -a 256 -c reclaim-*.zip.sha256`.

**Sharing notes**

- The binary matches the machine it was built on (arm64 vs x86_64) and targets macOS 11+.
- It is only ad-hoc signed, so macOS Gatekeeper will block a downloaded copy. Recipients can run:
  ```bash
  xattr -d com.apple.quarantine ./reclaim
  ```
  For frictionless distribution, sign with a Developer ID and notarize.
- The smoke test does not start the TUI (it needs a terminal); run `dist/reclaim` once by hand before publishing a release.

## Troubleshooting

- **"reclaim: ... unknown key(s)" / exit code 2:** your config file has a typo or an invalid path. The message names the file and key.
- **Project artifacts, worktrees, FVM or Node versions never show up:** set `project_roots` in your config.
- **Ollama models are missing:** the server must be running; `reclaim` deliberately won't start it.
- **An item can't be selected:** the Note column shows why (`disabled reason` or `report only`). Report-only items (Time Machine snapshots, large folders) are never cleaned by `reclaim`.
- **"<app> is running":** an item declares apps that must be closed first (for example Xcode). Quit the app and run again.
- **Space didn't change after cleaning:** APFS snapshots and Time Machine local snapshots can hold freed space. Time Machine snapshots are report-only.

## License

No license has been chosen yet. Add a `LICENSE` file (for example MIT or Apache-2.0) before accepting outside contributions, then update this section.
