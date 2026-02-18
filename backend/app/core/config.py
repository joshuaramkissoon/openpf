from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="dev", alias="APP_ENV")
    app_name: str = Field(default="MyPF Agent", alias="APP_NAME")
    api_prefix: str = Field(default="/api", alias="API_PREFIX")
    database_url: str = Field(default="sqlite:///./mypf.db", alias="DATABASE_URL")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )
    cors_allow_origin_regex: str = Field(
        default=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        alias="CORS_ALLOW_ORIGIN_REGEX",
    )

    t212_base_env: Literal["live", "demo"] = Field(default="demo", alias="T212_BASE_ENV")
    t212_api_key: str = Field(default="", alias="T212_API_KEY")
    t212_api_secret: str = Field(default="", alias="T212_API_SECRET")
    t212_invest_api_key: str = Field(default="", alias="T212_INVEST_API_KEY")
    t212_invest_api_secret: str = Field(default="", alias="T212_INVEST_API_SECRET")
    t212_stocks_isa_api_key: str = Field(default="", alias="T212_STOCKS_ISA_API_KEY")
    t212_stocks_isa_api_secret: str = Field(default="", alias="T212_STOCKS_ISA_API_SECRET")
    # Alternate naming variants used by some SDK/skill docs.
    t212_api_key_invest: str = Field(default="", alias="T212_API_KEY_INVEST")
    t212_api_secret_invest: str = Field(default="", alias="T212_API_SECRET_INVEST")
    t212_api_key_stocks_isa: str = Field(default="", alias="T212_API_KEY_STOCKS_ISA")
    t212_api_secret_stocks_isa: str = Field(default="", alias="T212_API_SECRET_STOCKS_ISA")
    portfolio_display_currency: str = Field(default="GBP", alias="PORTFOLIO_DISPLAY_CURRENCY")

    # Archie-specific T212 keys (unrestricted / read-only).
    # Injected into the Claude runtime subprocess via env field.
    # Falls back to the regular T212 keys if not set.
    archie_t212_api_key_invest: str = Field(default="", alias="ARCHIE_T212_API_KEY_INVEST")
    archie_t212_api_secret_invest: str = Field(default="", alias="ARCHIE_T212_API_SECRET_INVEST")
    archie_t212_api_key_stocks_isa: str = Field(default="", alias="ARCHIE_T212_API_KEY_STOCKS_ISA")
    archie_t212_api_secret_stocks_isa: str = Field(default="", alias="ARCHIE_T212_API_SECRET_STOCKS_ISA")

    broker_mode: Literal["paper", "live"] = Field(default="paper", alias="BROKER_MODE")
    autopilot_enabled: bool = Field(default=False, alias="AUTOPILOT_ENABLED")

    max_single_order_notional: float = Field(default=500.0, alias="MAX_SINGLE_ORDER_NOTIONAL")
    max_daily_notional: float = Field(default=1500.0, alias="MAX_DAILY_NOTIONAL")
    max_position_weight: float = Field(default=0.25, alias="MAX_POSITION_WEIGHT")
    duplicate_order_window_seconds: int = Field(default=90, alias="DUPLICATE_ORDER_WINDOW_SECONDS")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-sonnet-4-20250514", alias="CLAUDE_MODEL")
    claude_memory_model: str = Field(default="claude-haiku-4-5", alias="CLAUDE_MEMORY_MODEL")
    claude_setting_sources: str = Field(default="project", alias="CLAUDE_SETTING_SOURCES")
    claude_project_cwd: str = Field(default=".claude/runtime", alias="CLAUDE_PROJECT_CWD")
    claude_chat_allow_writes: bool = Field(default=True, alias="CLAUDE_CHAT_ALLOW_WRITES")
    claude_memory_strategy: Literal["distill", "self_managed", "off"] = Field(
        default="self_managed",
        alias="CLAUDE_MEMORY_STRATEGY",
    )
    claude_memory_enabled: bool = Field(default=True, alias="CLAUDE_MEMORY_ENABLED")
    claude_memory_max_facts: int = Field(default=80, alias="CLAUDE_MEMORY_MAX_FACTS")

    agent_provider: Literal["rules", "claude"] = Field(default="claude", alias="AGENT_PROVIDER")
    agent_workspace: str = Field(default="./.claude/agent_workspace", alias="AGENT_WORKSPACE")
    agent_max_turns: int = Field(default=6, alias="AGENT_MAX_TURNS")
    agent_allow_bash: bool = Field(default=False, alias="AGENT_ALLOW_BASH")
    inproc_scheduler_enabled: bool = Field(default=False, alias="INPROC_SCHEDULER_ENABLED")

    newsapi_api_key: str = Field(default="", alias="NEWSAPI_API_KEY")
    x_api_bearer_token: str = Field(default="", alias="X_API_BEARER_TOKEN")

    @property
    def cors_origins_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
