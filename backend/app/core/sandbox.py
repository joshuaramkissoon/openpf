"""Sandbox-mode isolation (``OPENPF_SANDBOX=1``).

A sandbox instance must be impossible to confuse with — or leak into — a live
one. Defence in depth, all keyed off :func:`is_sandbox`:

* every per-instance data folder (DB, Archie runtime: artifacts + memory, views,
  mock-broker state) defaults under ``<repo>/.demo`` and may never be the live
  instance's folder (see ``core.config``);
* broker credentials are fixed to the mock broker's fake keys — env and
  DB-stored keys are ignored (``ConfigStore``);
* the T212 client and the trading212 MCP server refuse any host but the local
  sandbox broker, and ``execute_intent`` refuses a non-sandbox broker;
* crypto: the exchange key is fixed to the in-process mock exchange's fake key
  (env/DB keys ignored), ``build_crypto_client`` only ever returns the mock, and
  the real CCXT client refuses to be constructed — or to send any request to
  an exchange host — in a sandbox process (``crypto_client``);
* broker/aggregator connections (IBKR, Alpaca, SnapTrade): ``build_provider_client``
  only returns offline fixtures that accept only fake sandbox credentials, and
  the real HTTP clients refuse to be built or to reach any host
  (``provider_clients``);
* startup asserts all of the above and fails closed (``main.lifespan``).
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

_TRUTHY = {"1", "true", "yes", "on"}
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SANDBOX_PREFIX = "/sandbox-t212/api/v0"


class SandboxViolation(RuntimeError):
    """A sandbox instance was about to touch live data or a live broker."""


def is_sandbox() -> bool:
    return (
        os.environ.get("OPENPF_SANDBOX", "").strip().lower() in _TRUTHY
        or os.environ.get("T212_BASE_ENV", "").strip().lower() == "sandbox"
    )


def verify_isolation() -> list[str]:
    """Startup assertion for a sandbox instance. Raises :class:`SandboxViolation`
    (the app must not start) if anything would reach live data or a live
    broker; returns human-readable lines describing the isolated setup."""
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.services.claude_sdk_config import runtime_dir
    from app.services.config_store import ConfigStore
    from app.services.sandbox_broker import SANDBOX_CREDENTIALS
    from app.services.t212_client import build_t212_client

    settings = get_settings()
    url = sandbox_base_url()
    assert_sandbox_url(url)
    runtime = runtime_dir()
    if is_within(runtime, live_runtime_dir()) or is_within(live_runtime_dir(), runtime):
        raise SandboxViolation(f"sandbox runtime folder {runtime} overlaps the live one")
    fake_keys = {v for creds in SANDBOX_CREDENTIALS.values() for v in creds.values()}
    with SessionLocal() as db:
        store = ConfigStore(db)
        if store.get_broker().get("t212_base_env") != "sandbox":
            raise SandboxViolation("sandbox broker config is not pointed at the sandbox")
        for kind in ("invest", "stocks_isa"):
            creds = store.get_account_credentials(kind)  # type: ignore[arg-type]
            for field in ("t212_api_key", "t212_api_secret", "exec_api_key", "exec_api_secret"):
                if creds.get(field) and creds[field] not in fake_keys:
                    raise SandboxViolation(f"sandbox would use a real {kind} credential")
            for purpose in ("read", "execute"):
                client = build_t212_client(store, account_kind=kind, purpose=purpose)  # type: ignore[arg-type]
                assert_sandbox_url(client.base_url)
        from app.services import crypto_service
        from app.services.crypto_sandbox import SANDBOX_CRYPTO_CREDENTIALS

        ccfg = crypto_service.get_config(store)
        if ccfg.get("api_key") != SANDBOX_CRYPTO_CREDENTIALS["api_key"] or not ccfg.get("sandbox"):
            raise SandboxViolation("sandbox would use a real crypto exchange credential")
        crypto_client = crypto_service.client_for(store)
        if crypto_client is not None and not crypto_client.sandbox:
            raise SandboxViolation("sandbox would reach a real crypto exchange")
        # Every crypto connection (incl. Settings → Connections ones) is a mock.
        for cfg in crypto_service.crypto_configs(store):
            c = crypto_service._client_for_cfg(cfg)
            if c is not None and not c.sandbox:
                raise SandboxViolation(f"sandbox would reach a real crypto exchange ({cfg['account_kind']})")
        # Brokers/aggregators: only the offline fixtures can be built.
        from app.services.provider_clients import ProviderAuthError, SANDBOX_TOKENS, build_provider_client

        for provider_id, creds in SANDBOX_TOKENS.items():
            if not build_provider_client(provider_id, creds).sandbox:
                raise SandboxViolation(f"sandbox would reach a real {provider_id} API")
            try:
                build_provider_client(provider_id, {k: "real-looking" for k in creds})
                raise SandboxViolation(f"sandbox accepted a non-sandbox {provider_id} credential")
            except ProviderAuthError:
                pass
    return [
        f"broker   : {url} (mock Trading 212 — no real money)",
        f"database : {settings.database_url}",
        f"runtime  : {runtime} (Archie artifacts + memory)",
        f"views    : {settings.views_data_dir}",
        "crypto   : mock Kraken exchange (in-process — exchange hosts refused)",
        "brokers  : IBKR / Alpaca / SnapTrade fixtures (offline — provider hosts refused)",
    ]


def repo_root() -> Path:
    env = os.environ.get("PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[3]


def demo_dir() -> Path:
    return repo_root() / ".demo"


def live_runtime_dir() -> Path:
    """The live instance's Archie runtime folder (artifacts, memory)."""
    return repo_root() / ".claude" / "runtime"


def sandbox_base_url() -> str:
    return os.environ.get("T212_SANDBOX_URL", "").strip() or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}{SANDBOX_PREFIX}"


def is_sandbox_url(url: str) -> bool:
    """True only for the local mock broker (loopback host, sandbox path)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (parsed.hostname or "").lower() in _LOOPBACK and parsed.path.startswith(SANDBOX_PREFIX)


def assert_sandbox_url(url: str) -> None:
    if not is_sandbox_url(url):
        raise SandboxViolation(f"sandbox mode: refusing broker request to non-sandbox host {urlparse(url).hostname!r}")


def same_path(a: Path | str, b: Path | str) -> bool:
    try:
        return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    except OSError:
        return False


def is_within(child: Path | str, parent: Path | str) -> bool:
    try:
        Path(child).expanduser().resolve().relative_to(Path(parent).expanduser().resolve())
        return True
    except (ValueError, OSError):
        return False
