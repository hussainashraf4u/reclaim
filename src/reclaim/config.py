from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    project_roots: tuple[Path, ...] = ()
    extra_allowlist: tuple[Path, ...] = ()
    ignore: frozenset[str] = frozenset()
    min_size_mb: int = 50
    stale_days: int = 30
    large_dir_mb: int = 1000
    big_file_mb: int = 500
    duplicate_min_mb: int = 100
    use_trash: bool = False
    ai_provider: str = "none"
    ai_model: str | None = None
    ai_base_url: str | None = None
    ai_limit: int = 15
    ai_auto_review: bool = True


def default_config_path(home: Path) -> Path:
    return home / ".config" / "reclaim" / "config.toml"


_KEYS = frozenset({"project_roots", "extra_allowlist", "ignore", "min_size_mb", "stale_days", "large_dir_mb", "use_trash", "big_file_mb", "duplicate_min_mb", "ai_provider", "ai_model", "ai_base_url", "ai_limit", "ai_auto_review"})
_SENSITIVE = frozenset({".ssh", ".gnupg", "Keychains"})


def _check_root(path: Path, key: str, home: Path, source: Path) -> None:
    if ".." in path.parts:
        raise ConfigError(f"{source}: '{key}' entry '{path}' must not contain '..'")
    real = Path(os.path.realpath(path))
    real_home = Path(os.path.realpath(home))
    if real == Path(real.anchor) or real == real_home or real in real_home.parents:
        raise ConfigError(f"{source}: '{key}' entry '{path}' is too broad (home, an ancestor, or /)")
    inside = real_home in real.parents
    if key == "project_roots":
        if inside and real.relative_to(real_home).parts[0] == "Library":
            raise ConfigError(f"{source}: '{key}' entry '{path}' must not be under ~/Library")
        return
    depth = len(real.relative_to(real_home).parts) if inside else 0
    if not inside or depth < 2 or _SENSITIVE & set(real.parts):
        raise ConfigError(f"{source}: '{key}' entry '{path}' must be a specific folder at least two levels below home")


def _paths(data: dict, key: str, home: Path, source: Path) -> tuple[Path, ...]:
    raw = data.get(key, [])
    if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
        raise ConfigError(f"{source}: '{key}' must be a list of strings")
    out = []
    for value in raw:
        if value.startswith("~/"):
            out.append(home / value[2:])
        elif value.startswith("/"):
            out.append(Path(value))
        else:
            raise ConfigError(f"{source}: '{key}' entry '{value}' must start with ~/ or /")
    for path in out:
        _check_root(path, key, home, source)
    return tuple(out)


def _int(data: dict, key: str, default: int, source: Path, minimum: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{source}: '{key}' must be an integer >= {minimum}")
    return value


def load_config(path: Path, home: Path) -> Config:
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML ({str(exc).splitlines()[0]})") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: cannot read ({exc.__class__.__name__})") from None
    unknown = sorted(set(data) - _KEYS)
    if unknown:
        raise ConfigError(f"{path}: unknown key(s): {', '.join(unknown)}")
    ignore = data.get("ignore", [])
    if not isinstance(ignore, list) or not all(isinstance(v, str) for v in ignore):
        raise ConfigError(f"{path}: 'ignore' must be a list of strings")
    use_trash = data.get("use_trash", False)
    if not isinstance(use_trash, bool):
        raise ConfigError(f"{path}: 'use_trash' must be true or false")
    ai = {}
    for key in ("ai_provider", "ai_model", "ai_base_url"):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"{path}: '{key}' must be a string")
        ai[key] = value
    if ai["ai_provider"] not in (None, "none", "ollama", "anthropic"):
        raise ConfigError(f"{path}: 'ai_provider' must be none, ollama or anthropic")
    ai_auto_review = data.get("ai_auto_review", True)
    if not isinstance(ai_auto_review, bool):
        raise ConfigError(f"{path}: 'ai_auto_review' must be true or false")
    return Config(
        ai_limit=_int(data, "ai_limit", 15, path, 1),
        ai_auto_review=ai_auto_review,
        ai_provider=ai["ai_provider"] or "none",
        ai_model=ai["ai_model"],
        ai_base_url=ai["ai_base_url"],
        project_roots=_paths(data, "project_roots", home, path),
        extra_allowlist=_paths(data, "extra_allowlist", home, path),
        ignore=frozenset(ignore),
        min_size_mb=_int(data, "min_size_mb", 50, path, 0),
        stale_days=_int(data, "stale_days", 30, path, 1),
        large_dir_mb=_int(data, "large_dir_mb", 1000, path, 1),
        big_file_mb=_int(data, "big_file_mb", 500, path, 1),
        duplicate_min_mb=_int(data, "duplicate_min_mb", 100, path, 1),
        use_trash=use_trash,
    )


_IGNORE_RE = re.compile(r"^ignore\s*=\s*\[[^\]]*\]", re.MULTILINE)


def set_ignore(path: Path, ids: set[str]) -> None:
    """Rewrite the 'ignore' key in the config file, creating the file if needed."""
    line = "ignore = [" + ", ".join(json.dumps(i) for i in sorted(ids)) + "]"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if _IGNORE_RE.search(text):
        text = _IGNORE_RE.sub(lambda _: line, text, count=1)
    else:
        text = (text.rstrip("\n") + "\n" if text.strip() else "") + line + "\n"
    path.write_text(text, encoding="utf-8")


def set_keys(path: Path, values: dict[str, str | int | bool | None]) -> None:
    """Set or remove top-level scalar keys (None removes), leaving the rest of the file untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    for key, value in values.items():
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*\n?", re.MULTILINE)
        text = pattern.sub("", text)
        if value is not None:
            text = (text.rstrip("\n") + "\n" if text.strip() else "") + f"{key} = {json.dumps(value)}\n"
    path.write_text(text, encoding="utf-8")
