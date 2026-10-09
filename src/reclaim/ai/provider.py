from __future__ import annotations

import json
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping

from reclaim.ai.errors import LlmError, LlmErrorCode, LlmResult, classify_exception

REQUEST_TIMEOUT_SECONDS = 120
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models?limit=100"

# (url, headers, body, timeout) -> parsed JSON response. Injected so tests never touch the network.
Post = Callable[[str, Mapping[str, str], dict, float], dict]
# Same arguments, but yields the response one line at a time as it arrives.
StreamPost = Callable[[str, Mapping[str, str], dict, float], Iterator[str]]
OnChunk = Callable[[str], None]


def _request(url: str, headers: Mapping[str, str], body: dict) -> urllib.request.Request:
    return urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", **headers}, method="POST"
    )


def urllib_post(url: str, headers: Mapping[str, str], body: dict, timeout: float) -> dict:
    with urllib.request.urlopen(_request(url, headers, body), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def urllib_stream(url: str, headers: Mapping[str, str], body: dict, timeout: float) -> Iterator[str]:
    with urllib.request.urlopen(_request(url, headers, body), timeout=timeout) as response:
        for raw in response:
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")


# (url, headers, timeout) -> parsed JSON response.
Get = Callable[[str, Mapping[str, str], float], dict]
MODELS_TIMEOUT_SECONDS = 8


def urllib_get(url: str, headers: Mapping[str, str], timeout: float) -> dict:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def list_models(provider: str, base_url: str | None, env: Mapping[str, str], get: Get = urllib_get) -> LlmResult[list[str]]:
    """Model names the provider offers right now. Blocking; never raises."""
    try:
        if provider == "ollama":
            root = (base_url or env.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_URL).rstrip("/")
            reply = get(root + "/api/tags", {}, MODELS_TIMEOUT_SECONDS)
            return LlmResult(sorted(str(m["name"]) for m in reply["models"]))
        if provider == "anthropic":
            key = env.get("ANTHROPIC_API_KEY", "")
            if not key:
                return LlmResult(None, LlmError(LlmErrorCode.AUTH_OR_CONFIG_ERROR, "ANTHROPIC_API_KEY is not set"))
            reply = get(ANTHROPIC_MODELS_URL, {"x-api-key": key, "anthropic-version": "2023-06-01"}, MODELS_TIMEOUT_SECONDS)
            return LlmResult([str(m["id"]) for m in reply["data"]])
    except Exception as exc:
        return LlmResult(None, LlmError(classify_exception(exc), str(exc)))
    return LlmResult([])


def parse_json_object(text: str) -> dict | None:
    """Parse the first {...} object in a model reply, tolerating code fences and chatter around it."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class LlmProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def chat_json(
        self, system_prompt: str, user_prompt: str, *, schema: dict | None = None, on_chunk: OnChunk | None = None
    ) -> LlmResult[dict]:
        """The model's JSON-object reply, or a classified LlmError. Never raises.

        With on_chunk the reply is streamed and each piece of text is passed to it as it arrives."""


class _HttpProvider(LlmProvider):
    def __init__(self, model: str, post: Post, stream: StreamPost) -> None:
        self.model = model
        self._post = post
        self._stream = stream

    def _endpoint(self) -> tuple[str, dict[str, str]]:
        raise NotImplementedError

    def _body(self, system_prompt: str, user_prompt: str, schema: dict | None, stream: bool) -> dict:
        raise NotImplementedError

    def _text(self, reply: dict) -> str:
        raise NotImplementedError

    def _delta(self, line: str) -> tuple[str, bool]:
        """(text piece, finished) for one streamed line. Raises on an error line."""
        raise NotImplementedError

    def _complete(self, system_prompt: str, user_prompt: str, schema: dict | None, on_chunk: OnChunk | None) -> str:
        url, headers = self._endpoint()
        if on_chunk is None:
            return self._text(self._post(url, headers, self._body(system_prompt, user_prompt, schema, False), REQUEST_TIMEOUT_SECONDS))
        parts: list[str] = []
        for line in self._stream(url, headers, self._body(system_prompt, user_prompt, schema, True), REQUEST_TIMEOUT_SECONDS):
            if not line.strip():
                continue
            piece, finished = self._delta(line)
            if piece:
                parts.append(piece)
                on_chunk(piece)
            if finished:
                break
        return "".join(parts)

    def chat_json(
        self, system_prompt: str, user_prompt: str, *, schema: dict | None = None, on_chunk: OnChunk | None = None
    ) -> LlmResult[dict]:
        try:
            content = self._complete(system_prompt, user_prompt, schema, on_chunk)
        except Exception as exc:
            return LlmResult(None, LlmError(classify_exception(exc), str(exc)))
        parsed = parse_json_object(content)
        if parsed is None:
            return LlmResult(None, LlmError(LlmErrorCode.MALFORMED_RESPONSE, f"unparseable reply: {content[:200]!r}"))
        return LlmResult(parsed)


class OllamaProvider(_HttpProvider):
    name = "ollama"

    def __init__(
        self,
        model: str = DEFAULT_OLLAMA_MODEL,
        base_url: str = DEFAULT_OLLAMA_URL,
        post: Post = urllib_post,
        stream: StreamPost = urllib_stream,
    ) -> None:
        super().__init__(model, post, stream)
        self._url = base_url.rstrip("/") + "/api/chat"

    def _endpoint(self) -> tuple[str, dict[str, str]]:
        return self._url, {}

    def _body(self, system_prompt: str, user_prompt: str, schema: dict | None, stream: bool) -> dict:
        return {
            "model": self.model,
            "stream": stream,
            "format": schema if schema is not None else "json",
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        }

    def _text(self, reply: dict) -> str:
        return reply["message"]["content"]

    def _delta(self, line: str) -> tuple[str, bool]:
        obj = json.loads(line)
        if obj.get("error"):
            raise OSError(str(obj["error"]))
        return obj.get("message", {}).get("content", ""), bool(obj.get("done"))


class AnthropicProvider(_HttpProvider):
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        post: Post = urllib_post,
        stream: StreamPost = urllib_stream,
    ) -> None:
        super().__init__(model, post, stream)
        self._api_key = api_key

    def _endpoint(self) -> tuple[str, dict[str, str]]:
        return ANTHROPIC_URL, {"x-api-key": self._api_key, "anthropic-version": "2023-06-01"}

    def _body(self, system_prompt: str, user_prompt: str, schema: dict | None, stream: bool) -> dict:
        return {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": 0,
            "stream": stream,
            "system": system_prompt + "\nReply with one JSON object and nothing else.",
            "messages": [{"role": "user", "content": user_prompt}],
        }

    def _text(self, reply: dict) -> str:
        return "".join(block.get("text", "") for block in reply["content"] if block.get("type") == "text")

    def _delta(self, line: str) -> tuple[str, bool]:
        if not line.startswith("data:"):
            return "", False
        obj = json.loads(line[5:])
        kind = obj.get("type")
        if kind == "error":
            raise OSError(str(obj.get("error", {}).get("message", "stream error")))
        if kind == "content_block_delta":
            return obj.get("delta", {}).get("text", ""), False
        return "", kind == "message_stop"


class FallbackProvider(LlmProvider):
    """Try the primary; route to the secondary when it fails."""

    def __init__(self, primary: LlmProvider, secondary: LlmProvider) -> None:
        self._primary, self._secondary = primary, secondary
        self.name, self.model = primary.name, primary.model

    def chat_json(
        self, system_prompt: str, user_prompt: str, *, schema: dict | None = None, on_chunk: OnChunk | None = None
    ) -> LlmResult[dict]:
        result = self._primary.chat_json(system_prompt, user_prompt, schema=schema, on_chunk=on_chunk)
        if result.ok:
            return result
        return self._secondary.chat_json(system_prompt, user_prompt, schema=schema, on_chunk=on_chunk)


def build_provider(name: str, model: str | None, base_url: str | None, env: Mapping[str, str]) -> LlmProvider | None:
    """None means heuristics only. The API key is read from the environment, never from the config file."""
    name = (name or "none").lower()
    if name == "none":
        return None
    if name == "ollama":
        return OllamaProvider(model or DEFAULT_OLLAMA_MODEL, base_url or env.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_URL)
    if name == "anthropic":
        key = env.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("anthropic provider needs ANTHROPIC_API_KEY in the environment")
        return AnthropicProvider(key, model or DEFAULT_ANTHROPIC_MODEL)
    raise ValueError(f"unknown AI provider {name!r} (use none, ollama or anthropic)")
