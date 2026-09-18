"""Central configuration, loaded from environment variables and `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"


class Settings(BaseSettings):
    """Runtime settings.

    Every field can be overridden with a `CAREXPERT_`-prefixed environment
    variable, except `anthropic_api_key` which uses the SDK's standard name.
    """

    model_config = SettingsConfigDict(
        env_prefix="CAREXPERT_",
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Storage -----------------------------------------------------------
    database_url: str = f"sqlite:///{DATA_DIR / 'carexpert.db'}"
    cache_dir: Path = CACHE_DIR
    cache_ttl_hours: int = 12

    # --- Claude ------------------------------------------------------------
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    model: str = "claude-opus-5"
    max_photos: int = 8
    photo_max_edge: int = 1024
    # Thinking tokens count against this ceiling. Too low and the report is
    # truncated mid-sentence, which costs a full retry: 8k was not enough
    # with adaptive thinking and a dozen photos.
    analysis_max_tokens: int = 16000

    # --- Collection policy -------------------------------------------------
    # These defaults are deliberately conservative: one polite request every
    # few seconds, robots.txt honoured. Raise them only against sources you
    # are contractually allowed to crawl faster.
    respect_robots: bool = True
    request_delay: float = 2.5
    request_timeout: float = 20.0
    max_retries: int = 3
    user_agent: str = "CarExpertBot/0.1 (+contact: set CAREXPERT_USER_AGENT)"
    max_pages_per_search: int = 3

    # --- Scoring / alerting ------------------------------------------------
    alert_threshold: int = 75
    min_comps_for_confidence: int = 8
    #: A valuation drifts as new comparables arrive, so it is redone once a
    #: day even when the advert itself has not moved.
    valuation_ttl_hours: int = 24
    #: Listings valued per batch. Only affects memory, not the result.
    valuation_batch_size: int = 500

    # --- Notifications -----------------------------------------------------
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    alert_email_to: str | None = None

    def ensure_dirs(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.ensure_dirs()
    return settings
