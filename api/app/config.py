import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Values come from the environment or api/.env (never hard-coded)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    anthropic_api_key: str = ""
    companies_house_api_key: str = ""
    database_url: str = ""

    # AI fallback for photos and layouts the parser can't read.
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "medium"
    # Cost safety net: total Claude calls allowed per UTC day across all checks.
    ai_daily_call_cap: int = 200

    # Reader limits (any single document).
    max_upload_mb: int = 20
    max_pages: int = 30

    # Upload limits for POST /checks.
    max_files: int = 12
    max_file_mb: int = 10

    # Privacy and abuse limits.
    report_ttl_hours: int = 24
    checks_per_ip_per_day: int = 3
    # Secret mixed into IP hashes so stored rate-limit rows can't be reversed into IPs.
    rate_limit_salt: str = "change-me"
    # Only trust X-Forwarded-For when running behind a proxy you control (e.g. Railway).
    trust_proxy_headers: bool = False
    # Uploads are written only under this folder, and deleted when processing ends.
    upload_temp_dir: str = str(Path(tempfile.gettempdir()) / "tallied-uploads")
    cleanup_interval_minutes: int = 10

    # Comma-separated list of origins allowed to call the API (the web app).
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
