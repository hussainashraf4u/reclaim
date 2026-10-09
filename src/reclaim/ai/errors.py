from __future__ import annotations

import http.client
import json
import socket
import urllib.error
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


class LlmErrorCode(str, Enum):
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    RATE_LIMITED = "rate_limited"
    AUTH_OR_CONFIG_ERROR = "auth_or_config_error"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LlmError:
    code: LlmErrorCode
    detail: str

    def __str__(self) -> str:
        return f"{self.code.value}: {self.detail}"


@dataclass(frozen=True)
class LlmResult(Generic[T]):
    """A failure is reported here, never raised, so callers can always fall back to heuristics."""

    value: T | None
    error: LlmError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.value is not None


def classify_exception(exc: Exception) -> LlmErrorCode:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return LlmErrorCode.RATE_LIMITED
        if exc.code in (401, 403):
            return LlmErrorCode.AUTH_OR_CONFIG_ERROR
        if exc.code >= 500:
            return LlmErrorCode.PROVIDER_UNAVAILABLE
        return LlmErrorCode.TRANSPORT_ERROR
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return LlmErrorCode.TIMEOUT
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, (TimeoutError, socket.timeout)):
        return LlmErrorCode.TIMEOUT
    if isinstance(exc, (OSError, http.client.HTTPException)):
        return LlmErrorCode.TRANSPORT_ERROR
    if isinstance(exc, (json.JSONDecodeError, KeyError, IndexError, TypeError, UnicodeDecodeError)):
        return LlmErrorCode.MALFORMED_RESPONSE
    return LlmErrorCode.UNKNOWN
