from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    proxy_api_key: str = Field(default="change-me", alias="PROXY_API_KEY")
    whoop_client_id: str = Field(default="", alias="WHOOP_CLIENT_ID")
    whoop_client_secret: str = Field(default="", alias="WHOOP_CLIENT_SECRET")
    whoop_redirect_uri: str = Field(
        default="http://127.0.0.1:8001/auth/callback",
        alias="WHOOP_REDIRECT_URI",
    )

    whoop_api_base_url: str = Field(
        default="https://api.prod.whoop.com/developer",
        alias="WHOOP_API_BASE_URL",
    )
    whoop_oauth_authorize_url: str = Field(
        default="https://api.prod.whoop.com/oauth/oauth2/auth",
        alias="WHOOP_OAUTH_AUTHORIZE_URL",
    )
    whoop_oauth_token_url: str = Field(
        default="https://api.prod.whoop.com/oauth/oauth2/token",
        alias="WHOOP_OAUTH_TOKEN_URL",
    )

    timezone: str = Field(default="Europe/Moscow", alias="TZ")
    cache_dir: Path = Field(default=Path("/cache"), alias="CACHE_DIR")
    secrets_dir: Path = Field(default=Path("/secrets"), alias="SECRETS_DIR")
    token_file_name: str = Field(default="whoop_tokens.json", alias="TOKEN_FILE_NAME")

    whoop_timeout_seconds: float = Field(default=10.0, alias="WHOOP_TIMEOUT_SECONDS")
    health_timeout_seconds: float = Field(default=3.0, alias="HEALTH_TIMEOUT_SECONDS")
    whoop_min_interval_seconds: int = Field(default=300, alias="WHOOP_MIN_INTERVAL_SECONDS")
    cache_retention_days: int = Field(default=30, alias="CACHE_RETENTION_DAYS")
    whoop_http_log_enabled: bool = Field(default=True, alias="WHOOP_HTTP_LOG_ENABLED")
    whoop_http_log_level: str = Field(default="INFO", alias="WHOOP_HTTP_LOG_LEVEL")
    whoop_http_log_body_max_chars: int = Field(default=4000, alias="WHOOP_HTTP_LOG_BODY_MAX_CHARS")
    whoop_http_log_redact_sensitive: bool = Field(
        default=True,
        alias="WHOOP_HTTP_LOG_REDACT_SENSITIVE",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def token_path(self) -> Path:
        return self.secrets_dir / self.token_file_name


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
