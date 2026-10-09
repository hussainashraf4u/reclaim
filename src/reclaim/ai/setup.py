from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import typing
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from reclaim.ai.errors import LlmResult
from reclaim.ai.provider import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL, list_models

OLLAMA_INSTALL_HINT = "install Ollama from https://ollama.com/download (macOS: brew install ollama), then re-run"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@dataclass(frozen=True)
class SetupResult:
    ok: bool
    message: str


@dataclass(frozen=True)
class Readiness:
    ok: bool
    message: str
    needs_download: bool = False


def daemon_up(base_url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def has_model(model: str, base_url: str) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=5) as response:
            names = {m.get("name", "") for m in json.loads(response.read())["models"]}
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return model in names or f"{model}:latest" in names


def pull_streaming(
    model: str,
    on_line: Callable[[str], None],
    cancelled: Callable[[], bool] = lambda: False,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> int:
    """Run `ollama pull`, reporting each progress line. Returns the exit code (nonzero if cancelled)."""
    try:
        proc = popen(["ollama", "pull", model], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError:
        return 1
    stream = typing.cast(typing.BinaryIO, proc.stdout)
    pending = ""
    stopped = False
    while True:
        chunk = stream.read1(256)  # type: ignore[attr-defined]
        if not chunk:
            break
        pending += _ANSI.sub("", chunk.decode("utf-8", errors="replace"))
        *lines, pending = re.split(r"[\r\n]", pending)
        for line in lines:
            if line.strip():
                on_line(line.strip())
        if cancelled():
            proc.terminate()
            stopped = True
            break
    code = proc.wait()
    return 1 if stopped else code


@dataclass
class AiEnv:
    """Everything setup touches outside the process, injectable so tests never download anything."""

    which: Callable[[str], str | None] = shutil.which
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    up: Callable[[str], bool] = lambda url: daemon_up(url)
    present: Callable[[str, str], bool] = lambda model, url: has_model(model, url)
    pull: Callable[..., int] = lambda model, on_line, cancelled=lambda: False: pull_streaming(model, on_line, cancelled)
    models: Callable[[str, str | None], LlmResult[list[str]]] | None = None

    def list_models(self, provider: str, base_url: str | None) -> LlmResult[list[str]]:
        if self.models is not None:
            return self.models(provider, base_url)
        return list_models(provider, base_url, self.env)


def check(provider: str, model: str | None, base_url: str | None, env: AiEnv) -> Readiness:
    """Is this provider ready to use right now? Blocking: call from a worker thread in the TUI."""
    if provider == "none":
        return Readiness(True, "AI is off")
    if provider == "anthropic":
        if not env.env.get("ANTHROPIC_API_KEY"):
            return Readiness(False, "Set ANTHROPIC_API_KEY in your environment (the key is never stored in the config file)")
        return Readiness(True, "ANTHROPIC_API_KEY found; item metadata, never file contents, is sent to the API")
    model = model or DEFAULT_OLLAMA_MODEL
    url = base_url or env.env.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_URL
    if env.which("ollama") is None:
        return Readiness(False, f"Ollama is not installed: {OLLAMA_INSTALL_HINT}")
    if not env.up(url):
        return Readiness(False, "Ollama is installed but not running: start it with `ollama serve` (or open the app)")
    if not env.present(model, url):
        return Readiness(False, f"Model {model} is not downloaded yet (several GB)", needs_download=True)
    return Readiness(True, f"Ollama is running and {model} is downloaded")


def enable_ollama(
    model: str = DEFAULT_OLLAMA_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
    *,
    confirm: Callable[[str], bool],
    env: AiEnv | None = None,
    on_line: Callable[[str], None] = print,
) -> SetupResult:
    """Make local AI ready: Ollama installed and running, model downloaded. Downloads only after confirm."""
    env = env or AiEnv()
    ready = check("ollama", model, base_url, env)
    if ready.ok:
        return SetupResult(True, ready.message)
    if not ready.needs_download:
        return SetupResult(False, ready.message)
    if not confirm(f"Download model {model} with `ollama pull`? This can be several GB."):
        return SetupResult(False, "cancelled: nothing was downloaded")
    if env.pull(model, on_line) != 0:
        return SetupResult(False, f"`ollama pull {model}` failed; check the model name and your connection")
    return SetupResult(True, f"downloaded {model}")


def enable_anthropic(env: AiEnv | Mapping[str, str]) -> SetupResult:
    ready = check("anthropic", None, None, env if isinstance(env, AiEnv) else AiEnv(env=env))
    return SetupResult(ready.ok, ready.message)


def default_cache_path(home: Path) -> Path:
    return home / ".cache" / "reclaim" / "ai_advice.json"
