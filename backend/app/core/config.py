from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables.

    Docker Compose supplies these values from `.env`. Keeping configuration in a
    single typed object makes modules portable and keeps secrets out of code.
    """

    model_config = SettingsConfigDict(
        env_prefix="IACS_",
        env_file=".env",
        extra="ignore",
        enable_decoding=False,
    )

    app_name: str = "Intelligent Access Control System"
    environment: str = "development"
    log_level: str = "INFO"
    site_timezone: str = "Europe/London"

    database_url: str = "postgresql+asyncpg://iacs:iacs_dev_password@postgres:5432/iacs"
    redis_url: str = "redis://redis:6379/0"

    data_dir: Path = Path("/app/data")
    log_dir: Path = Path("/app/logs")

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])
    public_base_url: AnyHttpUrl | None = None
    root_path: str = ""
    auto_create_schema: bool = True
    seed_demo_data: bool = True

    auth_secret_key: str = "change-me-before-production"
    auth_cookie_name: str = "iacs_session"
    auth_access_token_minutes: int = 720
    auth_remember_days: int = 30
    auth_cookie_secure: bool = False

    lpr_debounce_quiet_seconds: float = 2.5
    lpr_debounce_max_seconds: float = 6.0
    lpr_similarity_threshold: float = 0.78

    home_assistant_url: AnyHttpUrl | None = None
    home_assistant_token: str | None = None
    home_assistant_gate_entity_id: str | None = None
    home_assistant_gate_open_service: str = "cover.open_cover"
    home_assistant_tts_service: str = "tts.cloud_say"
    home_assistant_default_media_player: str | None = None
    home_assistant_presence_entities: dict[str, str] = Field(default_factory=dict)
    apprise_urls: str | None = None

    openai_api_key: str | None = None
    openai_model: str = "gpt-5"
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "llama3.1"
    llm_provider: str = "local"
    llm_timeout_seconds: float = 45.0

    lpr_adapter: str = "ubiquiti"
    gate_controller: str = "home_assistant"
    notification_sender: str = "apprise"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("trusted_hosts", mode="before")
    @classmethod
    def parse_trusted_hosts(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [host.strip() for host in value.split(",") if host.strip()]
        return value

    @field_validator("home_assistant_presence_entities", mode="before")
    @classmethod
    def parse_presence_entities(cls, value: str | dict[str, str] | None) -> dict[str, str]:
        if not value:
            return {}
        if isinstance(value, dict):
            return value

        pairs: dict[str, str] = {}
        for item in value.split(","):
            if not item.strip() or "=" not in item:
                continue
            name, entity_id = item.split("=", 1)
            pairs[name.strip()] = entity_id.strip()
        return pairs

    @field_validator(
        "public_base_url",
        "home_assistant_url",
        "home_assistant_token",
        "home_assistant_gate_entity_id",
        "home_assistant_default_media_player",
        "apprise_urls",
        "openai_api_key",
        "gemini_api_key",
        "anthropic_api_key",
        mode="before",
    )
    @classmethod
    def empty_string_as_none(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
