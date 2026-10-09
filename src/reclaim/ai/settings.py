from __future__ import annotations

from dataclasses import dataclass

from reclaim.config import Config

PROVIDERS = ("none", "ollama", "anthropic")


@dataclass(frozen=True)
class AiSettings:
    provider: str = "none"
    model: str | None = None
    base_url: str | None = None
    limit: int = 15
    auto_review: bool = True
    share_names: bool = False

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    @classmethod
    def from_config(cls, cfg: Config) -> AiSettings:
        return cls(cfg.ai_provider, cfg.ai_model, cfg.ai_base_url, cfg.ai_limit, cfg.ai_auto_review, cfg.ai_share_names)

    def to_keys(self) -> dict[str, str | int | bool | None]:
        """Config keys for this setting. Defaults are removed so the file only holds deliberate choices."""
        if not self.enabled:
            return {"ai_provider": None, "ai_model": None, "ai_base_url": None, "ai_limit": None, "ai_auto_review": None, "ai_share_names": None}
        return {
            "ai_provider": self.provider,
            "ai_model": self.model or None,
            "ai_base_url": self.base_url or None,
            "ai_limit": None if self.limit == 15 else self.limit,
            "ai_auto_review": None if self.auto_review else False,
            "ai_share_names": True if self.share_names and self.provider == "anthropic" else None,
        }
