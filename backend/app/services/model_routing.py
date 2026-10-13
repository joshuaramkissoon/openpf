"""Which model runs which job.

Two kinds of job:

* **agent** jobs run Claude through the Agent SDK (tools, multi-turn). Only the
  model is configurable.
* **fast** jobs are one-shot structured calls through ``fast_llm``. Provider and
  model are configurable; the other configured providers are the fallback
  chain.

Resolution order: Settings → Models (config key ``model_routing``) → the env
setting the job used before (e.g. ``CLAUDE_CHAT_MODEL``) → the default here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-5-5"

CONFIG_KEY = "model_routing"


@dataclass(frozen=True)
class Job:
    key: str
    kind: str  # agent | fast
    label: str
    explain: str
    provider: str = "claude_sdk"
    model: str = SONNET
    env_attr: str | None = None  # Settings attribute whose env value overrides the default
    sdk_fallback: bool = False  # fast jobs: may fall back to the slow Agent SDK
    timeout_s: float = 20.0
    group: str = "Archie"


JOBS: list[Job] = [
    # ── agent jobs (Claude Agent SDK) ──
    Job("chat", "agent", "Archie chat", "Your conversations with Archie. Uses tools and reasons over several steps, so it runs on Claude.",
        model=SONNET, env_attr="claude_chat_model"),
    Job("plays_draft", "agent", "Drafting plays", "Turning an idea into a play: researches tickers, sizes legs and writes the thesis.",
        model=SONNET, env_attr="claude_plays_model"),
    Job("plays_review", "agent", "Play check-ups", "A short review of a play against its thesis. Small job, so a small model.",
        model=HAIKU, env_attr="claude_plays_review_model"),
    Job("scheduled_agent", "agent", "Scheduled investigations",
        "Scheduled tasks and recipes that need to dig around (agent mode). Capped in turns and budget.",
        model=SONNET, env_attr="claude_agent_model"),
    Job("agent_run", "agent", "Analysis runs", "On-demand Archie analysis runs (regime, sizing, proposals).",
        model=SONNET, env_attr="claude_agent_model"),
    Job("deep", "agent", "Deep analysis",
        "Only the explicitly deep paths: the quant subagent. The most capable, most expensive model.",
        model=OPUS, env_attr="claude_deep_model"),
    Job("views_code", "agent", "Custom-code views", "Views that need real code (calculators, inputs). Rare; the fast spec builder handles most views.",
        model=SONNET, env_attr="views_model"),
    Job("recipes_author", "agent", "Writing recipes", "Archie drafting a new recipe from your description.",
        model=SONNET, env_attr="recipes_model"),
    # ── fast one-shot jobs (fast_llm) ──
    Job("chat_title", "fast", "Chat titles", "Names each conversation. One tiny call; speed matters more than depth.",
        provider="together", model="", timeout_s=6.0, group="Fast"),
    Job("play_suggest", "fast", "Sort with Archie", "Groups unsorted holdings into suggested plays. Theme rules show first; this refines them.",
        provider="together", model="", timeout_s=12.0, group="Fast"),
    Job("view_spec", "fast", "View builder", "Turns a sentence into a chart page spec. Must answer in a few seconds.",
        provider="together", model="", env_attr="views_spec_model", timeout_s=40.0, group="Fast"),
    Job("report_digest", "fast", "Report write-ups",
        "Morning brief, weekly review and similar: code gathers the numbers, then one call writes them up.",
        provider="together", model="", timeout_s=60.0, sdk_fallback=True, group="Fast"),
    Job("memory_extract", "fast", "Memory notes", "Picks durable facts about you out of a chat (only in 'distill' memory mode).",
        provider="together", model="", timeout_s=20.0, sdk_fallback=True, group="Fast"),
    Job("report_summary", "fast", "Report summaries", "Pulls the one-line summary and key numbers out of a finished report.",
        provider="together", model="", timeout_s=20.0, group="Fast"),
]

JOB_INDEX = {j.key: j for j in JOBS}
FAST_PROVIDERS = ("together", "anthropic", "gemini", "claude_sdk")


def _stored() -> dict[str, Any]:
    try:
        from app.core.database import SessionLocal
        from app.services.config_store import ConfigStore

        with SessionLocal() as db:
            v = ConfigStore(db).get(CONFIG_KEY, {})
            return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _env_default(job: Job) -> str | None:
    if not job.env_attr:
        return None
    try:
        from app.core.config import get_settings

        v = getattr(get_settings(), job.env_attr, None)
        return str(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def route(key: str, stored: dict[str, Any] | None = None) -> dict[str, Any]:
    """{provider, model, timeout_s} for a job, after overrides."""
    job = JOB_INDEX[key]
    cfg = ((stored if stored is not None else _stored()).get("jobs") or {}).get(key) or {}
    provider = str(cfg.get("provider") or job.provider)
    if job.kind == "agent":
        provider = "claude_sdk"
    model = str(cfg.get("model") or "").strip()
    if not model:
        env = _env_default(job)
        # The env model only applies to the provider it was written for.
        if env and (job.kind == "agent" or provider == job.provider):
            model = env
    if not model:
        from app.services.fast_llm import DEFAULT_MODELS

        model = job.model or DEFAULT_MODELS.get(provider, "")
    return {"provider": provider, "model": model, "timeout_s": float(cfg.get("timeout_s") or job.timeout_s)}


def sdk_model(key: str) -> str:
    """The Claude model an agent job runs on."""
    return route(key)["model"]


def timeout_for(key: str) -> float:
    return route(key)["timeout_s"] if key in JOB_INDEX else 20.0


def fast_chain(key: str, *, allow_sdk: bool | None = None) -> list[tuple[str, str]]:
    """Provider chain for a fast job: its configured provider first, then the
    other fast providers (default models), the slow Agent SDK only if allowed."""
    from app.services.fast_llm import DEFAULT_MODELS

    if key not in JOB_INDEX:
        return [("together", DEFAULT_MODELS["together"]), ("anthropic", DEFAULT_MODELS["anthropic"])]
    job = JOB_INDEX[key]
    r = route(key)
    sdk_ok = job.sdk_fallback if allow_sdk is None else allow_sdk
    chain = [(r["provider"], r["model"])]
    for p in ("together", "anthropic"):
        if p != r["provider"]:
            chain.append((p, DEFAULT_MODELS[p]))
    if sdk_ok and r["provider"] != "claude_sdk":
        chain.append(("claude_sdk", DEFAULT_MODELS["claude_sdk"]))
    return chain


def prices(stored: dict[str, Any] | None = None) -> dict[str, tuple[float, float]]:
    from app.services.fast_llm import DEFAULT_PRICES

    out = dict(DEFAULT_PRICES)
    for k, v in ((stored if stored is not None else _stored()).get("prices") or {}).items():
        try:
            out[k] = (float(v[0]), float(v[1]))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    return out


def describe() -> dict[str, Any]:
    """Everything the Settings → Models page needs."""
    from app.services import fast_llm

    stored = _stored()
    jobs = []
    for j in JOBS:
        r = route(j.key, stored)
        cfg = (stored.get("jobs") or {}).get(j.key) or {}
        jobs.append({"key": j.key, "kind": j.kind, "group": j.group, "label": j.label, "explain": j.explain,
                     "provider": r["provider"], "model": r["model"], "default_provider": j.provider,
                     "default_model": j.model or fast_llm.DEFAULT_MODELS.get(j.provider, ""),
                     "customised": bool(cfg.get("provider") or cfg.get("model")),
                     "fallbacks": [f"{p}:{m}" for p, m in fast_chain(j.key)[1:]] if j.kind == "fast" else []})
    p = prices(stored)
    return {
        "jobs": jobs,
        "providers": {name: fast_llm.available(name) for name in FAST_PROVIDERS},
        "default_models": fast_llm.DEFAULT_MODELS,
        "claude_models": [SONNET, OPUS, HAIKU],
        "prices": {"together": list(p["together"])},
    }


def update(db: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Save job overrides / Together price. Empty strings reset to default."""
    from app.services.config_store import ConfigStore

    store = ConfigStore(db)
    cur = store.get(CONFIG_KEY, {}) or {}
    jobs = dict(cur.get("jobs") or {})
    for key, cfg in (payload.get("jobs") or {}).items():
        if key not in JOB_INDEX or not isinstance(cfg, dict):
            continue
        job = JOB_INDEX[key]
        entry = dict(jobs.get(key) or {})
        if "provider" in cfg:
            prov = str(cfg.get("provider") or "").strip()
            if job.kind == "fast" and prov and prov not in FAST_PROVIDERS:
                raise ValueError(f"Unknown provider '{prov}'")
            entry["provider"] = prov if job.kind == "fast" else ""
        if "model" in cfg:
            entry["model"] = str(cfg.get("model") or "").strip()[:120]
        entry = {k: v for k, v in entry.items() if v}
        if entry:
            jobs[key] = entry
        else:
            jobs.pop(key, None)
    out = {**cur, "jobs": jobs}
    if "together_price" in payload:
        tp = payload.get("together_price")
        prices_cfg = dict(cur.get("prices") or {})
        if tp:
            prices_cfg["together"] = [max(0.0, float(tp[0])), max(0.0, float(tp[1]))]
        else:
            prices_cfg.pop("together", None)
        out["prices"] = prices_cfg
    store.set(CONFIG_KEY, out)
    return describe()
