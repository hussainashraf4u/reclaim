from __future__ import annotations

import os
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


def default_config_path(home: Path) -> Path:
    return home / ".config" / "reclaim" / "config.toml"


_KEYS = frozenset({"project_roots", "extra_allowlist", "ignore", "min_size_mb", "stale_days", "large_dir_mb"})
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
    return Config(
        project_roots=_paths(data, "project_roots", home, path),
        extra_allowlist=_paths(data, "extra_allowlist", home, path),
        ignore=frozenset(ignore),
        min_size_mb=_int(data, "min_size_mb", 50, path, 0),
        stale_days=_int(data, "stale_days", 30, path, 1),
        large_dir_mb=_int(data, "large_dir_mb", 1000, path, 1),
    )
