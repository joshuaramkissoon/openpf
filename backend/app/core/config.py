import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_logger = logging.getLogger(__name__)


def resolve_legacy_sqlite_url(url: str) -> str:
    """``sqlite:///…/openpf.db`` → the legacy ``mypf.db`` in the same directory when
    openpf.db doesn't exist but mypf.db does (pre-rename installs). Never renames
    anything on disk."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url
    path = Path(url[len(prefix):])
    if path.name != "openpf.db" or path.exists():
        return url
    legacy = path.with_name("mypf.db")
    if not legacy.exists():
        return url
    _logger.warning("Using legacy database %s (openpf.db not found); rename it to openpf.db when convenient.", legacy)
    return url[: -len("openpf.db")] + "mypf.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="dev", alias="APP_ENV")
    app_name: str = Field(default="OpenPF", alias="APP_NAME")
    api_prefix: str = Field(default="/api", alias="API_PREFIX")
    # Falls back to a legacy ./mypf.db next to it when openpf.db doesn't exist
    # yet (see _legacy_sqlite_fallback) — existing installs keep their data.
    database_url: str = Field(default="sqlite:///./openpf.db", alias="DATABASE_URL")

    # Optional shared-secret auth. When set, every /api route and websocket
    # (except /api/health) requires `Authorization: Bearer <token>` (WS: ?token=).
    openpf_api_token: str = Field(default="", alias="OPENPF_API_TOKEN")
    # How Archie addresses the owner in prompts/UI. Empty → "the user".
    openpf_user_name: str = Field(default="", alias="OPENPF_USER_NAME")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )
    cors_allow_origin_regex: str = Field(
        default=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        alias="CORS_ALLOW_ORIGIN_REGEX",
    )

    # Safe default for new installs: the T212 practice (demo) environment. An
    # existing broker_config row in the DB (Settings → Broker) takes precedence.
    t212_base_env: Literal["live", "demo", "sandbox"] = Field(default="demo", alias="T212_BASE_ENV")
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
    # Dedicated execution (write) keys, IP-restricted to this machine. Kept
    # separate from the read keys above so an IP rotation only breaks writes,
    # never reads. Used only for placing/cancelling orders. Env vars are a
    # fallback — the DB (ConfigStore) is the source of truth (set via Settings).
    t212_exec_invest_api_key: str = Field(default="", alias="T212_EXEC_API_KEY_INVEST")
    t212_exec_invest_api_secret: str = Field(default="", alias="T212_EXEC_API_SECRET_INVEST")
    t212_exec_stocks_isa_api_key: str = Field(default="", alias="T212_EXEC_API_KEY_STOCKS_ISA")
    t212_exec_stocks_isa_api_secret: str = Field(default="", alias="T212_EXEC_API_SECRET_STOCKS_ISA")
    portfolio_display_currency: str = Field(default="GBP", alias="PORTFOLIO_DISPLAY_CURRENCY")

    # Archie-specific T212 keys (unrestricted / read-only).
    # Injected into the Claude runtime subprocess via env field.
    # Falls back to the regular T212 keys if not set.
    archie_t212_api_key_invest: str = Field(default="", alias="ARCHIE_T212_API_KEY_INVEST")
    archie_t212_api_secret_invest: str = Field(default="", alias="ARCHIE_T212_API_SECRET_INVEST")
    archie_t212_api_key_stocks_isa: str = Field(default="", alias="ARCHIE_T212_API_KEY_STOCKS_ISA")
    archie_t212_api_secret_stocks_isa: str = Field(default="", alias="ARCHIE_T212_API_SECRET_STOCKS_ISA")

    # Default to paper: nothing reaches the market until the owner switches to
    # live in Settings → Broker (a saved broker_config row overrides this) and
    # configures an IP-allowed execution key.
    broker_mode: Literal["paper", "live"] = Field(default="paper", alias="BROKER_MODE")
    autopilot_enabled: bool = Field(default=False, alias="AUTOPILOT_ENABLED")

    max_single_order_notional: float = Field(default=500.0, alias="MAX_SINGLE_ORDER_NOTIONAL")
    max_daily_notional: float = Field(default=1500.0, alias="MAX_DAILY_NOTIONAL")
    max_position_weight: float = Field(default=0.25, alias="MAX_POSITION_WEIGHT")
    duplicate_order_window_seconds: int = Field(default=90, alias="DUPLICATE_ORDER_WINDOW_SECONDS")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    # Cheap, fast model for small structured-output tasks (e.g. chat titles).
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_title_model: str = Field(default="gemini-3.1-flash-lite", alias="GEMINI_TITLE_MODEL")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_code_oauth_token: str = Field(default="", alias="CLAUDE_CODE_OAUTH_TOKEN")
    claude_model: str = Field(default="claude-sonnet-4-6", alias="CLAUDE_MODEL")
    # Reasoning-heavy runtimes default to Opus 4.8; routine/structured work
    # (and any task with no explicit model) falls back to claude_model.
    claude_chat_model: str = Field(default="claude-opus-4-8", alias="CLAUDE_CHAT_MODEL")
    claude_agent_model: str = Field(default="claude-opus-4-8", alias="CLAUDE_AGENT_MODEL")
    claude_memory_model: str = Field(default="claude-haiku-4-5", alias="CLAUDE_MEMORY_MODEL")
    claude_plays_model: str = Field(default="claude-sonnet-4-6", alias="CLAUDE_PLAYS_MODEL")
    claude_plays_review_model: str = Field(default="claude-haiku-4-5", alias="CLAUDE_PLAYS_REVIEW_MODEL")
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
    agent_max_turns: int = Field(default=25, alias="AGENT_MAX_TURNS")
    # Interactive chat has NO turn cap by default — Archie runs until it
    # naturally finishes (the Agent SDK omits --max-turns when this is None).
    # Set AGENT_CHAT_MAX_TURNS to a positive int only to re-impose a hard cap.
    agent_chat_max_turns: int | None = Field(default=None, alias="AGENT_CHAT_MAX_TURNS")
    # Cost-based backstop for a chat session (USD). The SDK client is created once
    # per chat_session_id and reused across every message, so this budget is
    # CUMULATIVE over the whole conversation, not per single response. A long,
    # legitimate multi-turn chat (research, scans, proposing alpha-loop intents)
    # can easily exceed a couple of dollars over its lifetime; too low a cap makes
    # later responses error with error_max_budget_usd and silently drops their
    # output (e.g. proposed leveraged intents never render). Keep it high enough to
    # only stop a pathological loop, not normal long use. Set to None / 0 to disable.
    agent_chat_max_budget_usd: float | None = Field(default=20.0, alias="AGENT_CHAT_MAX_BUDGET_USD")
    agent_allow_bash: bool = Field(default=False, alias="AGENT_ALLOW_BASH")
    inproc_scheduler_enabled: bool = Field(default=False, alias="INPROC_SCHEDULER_ENABLED")

    newsapi_api_key: str = Field(default="", alias="NEWSAPI_API_KEY")
    x_api_bearer_token: str = Field(default="", alias="X_API_BEARER_TOKEN")

    # Optional upgrade path for fundamentals data. NOT used today — the
    # fundamentals MCP server currently runs on keyless yfinance. Present so a
    # future switch to the Financial Datasets API needs no schema change.
    financial_datasets_api_key: str = Field(default="", alias="FINANCIAL_DATASETS_API_KEY")

    # Views — agent-built, sandboxed pages. The builder favours speed (Sonnet).
    views_model: str = Field(default="claude-sonnet-4-6", alias="VIEWS_MODEL")
    # Spec builder (prompt → declarative viz spec, no code). Fast by design:
    # Together AI (JSON-schema constrained) first, Claude as the fallback, the
    # closest preset when neither is available. TOGETHER_API_KEY comes from the
    # env or Settings → data providers (config key data_providers.together_api_key).
    views_spec_provider: str = Field(default="together", alias="VIEWS_SPEC_PROVIDER")  # together | anthropic
    views_spec_model: str = Field(default="deepseek-ai/DeepSeek-V4.1-Flash", alias="VIEWS_SPEC_MODEL")
    views_spec_claude_model: str = Field(default="claude-haiku-4-5", alias="VIEWS_SPEC_CLAUDE_MODEL")
    views_spec_timeout_s: float = Field(default=40.0, alias="VIEWS_SPEC_TIMEOUT_S")
    # Where view HTML/versions/state live. Relative paths resolve from backend/.
    views_data_dir: str = Field(default="data/views", alias="VIEWS_DATA_DIR")
    # Archie's per-instance runtime folder: his working dir, artifacts/ and
    # memory/. Empty → CLAUDE_PROJECT_CWD (default .claude/runtime). Sandbox
    # mode defaults it to .demo/runtime and refuses the live folder.
    openpf_runtime_dir: str = Field(default="", alias="OPENPF_RUNTIME_DIR")

    # Recipes — Archie's own harness (skills + triggers). User/Archie-made recipes
    # are personal data and live here (gitignored); built-ins ship in the repo.
    recipes_data_dir: str = Field(default="data/recipes", alias="RECIPES_DATA_DIR")
    recipes_model: str = Field(default="claude-sonnet-4-6", alias="RECIPES_MODEL")

    @model_validator(mode="after")
    def _legacy_sqlite_fallback(self) -> "Settings":
        self.database_url = resolve_legacy_sqlite_url(self.database_url)
        return self

    @property
    def user_name(self) -> str:
        """The owner's name for prompts, or "the user" when unset."""
        return self.openpf_user_name.strip() or "the user"

    @property
    def user_possessive(self) -> str:
        name = self.openpf_user_name.strip()
        return f"{name}'s" if name else "the user's"

    @property
    def cors_origins_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _sqlite_path(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    return Path(url[len(prefix):]).expanduser().resolve()


def _apply_sandbox_isolation(settings: "Settings") -> None:
    """Sandbox runs get their own data folders and can never reuse the live
    instance's. Only the *process* env can relocate them — a live ``.env``
    (which may name the live DB) is not trusted for paths in sandbox mode."""
    from app.core.sandbox import SandboxViolation, demo_dir, is_within, live_runtime_dir, repo_root, same_path

    # Never pick up real broker keys from .env — blank every T212 credential so
    # only the mock broker's keys exist in this process.
    for name in type(settings).model_fields:
        if name.startswith(("t212_", "archie_t212_")) and ("_key" in name or "_secret" in name):
            setattr(settings, name, "")
    settings.t212_base_env = "sandbox"

    demo = demo_dir()
    if not os.environ.get("DATABASE_URL"):
        settings.database_url = f"sqlite:///{demo / 'openpf-demo.db'}"
    if not os.environ.get("VIEWS_DATA_DIR"):
        settings.views_data_dir = str(demo / "views")
    if not os.environ.get("RECIPES_DATA_DIR"):
        settings.recipes_data_dir = str(demo / "recipes")
    if not os.environ.get("OPENPF_RUNTIME_DIR"):
        settings.openpf_runtime_dir = str(demo / "runtime")
    if not os.environ.get("AGENT_WORKSPACE"):
        settings.agent_workspace = str(Path(settings.openpf_runtime_dir) / "agent_workspace")
    os.environ.setdefault("OPENPF_SANDBOX_STATE", str(demo / "sandbox_state.json"))

    backend = repo_root() / "backend"
    db_path = _sqlite_path(settings.database_url)
    if db_path is None or any(same_path(db_path, backend / n) for n in ("openpf.db", "mypf.db")) or is_within(
        db_path, live_runtime_dir()
    ):
        raise SandboxViolation(f"sandbox mode refuses database {settings.database_url!r} — use a separate sqlite file")
    if is_within(settings.views_data_dir if Path(settings.views_data_dir).is_absolute() else backend / settings.views_data_dir,
                 backend / "data" / "views"):
        raise SandboxViolation("sandbox mode refuses the live views folder — set VIEWS_DATA_DIR elsewhere")
    if is_within(settings.recipes_data_dir if Path(settings.recipes_data_dir).is_absolute() else backend / settings.recipes_data_dir,
                 backend / "data" / "recipes"):
        raise SandboxViolation("sandbox mode refuses the live recipes folder — set RECIPES_DATA_DIR elsewhere")
    runtime = Path(settings.openpf_runtime_dir).expanduser()
    if not runtime.is_absolute():
        runtime = repo_root() / runtime
    if is_within(runtime, live_runtime_dir()) or is_within(live_runtime_dir(), runtime):
        raise SandboxViolation("sandbox mode refuses the live Archie runtime folder — set OPENPF_RUNTIME_DIR elsewhere")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    from app.core.sandbox import is_sandbox

    if is_sandbox():
        _apply_sandbox_isolation(settings)
    return settings
