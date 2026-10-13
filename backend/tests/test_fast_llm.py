"""fast_llm: one-shot structured calls, the provider chain, timeouts, usage
pricing, and model routing. No network: Together is mocked."""

import asyncio
import json
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from app.services import fast_llm, model_routing


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_db_routing(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(model_routing, "_stored", lambda: store)
    monkeypatch.setattr(fast_llm, "_record", lambda *a, **k: None)
    return store


def _mock_together(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(fast_llm.httpx, "AsyncClient", factory)
    monkeypatch.setattr(fast_llm, "together_key", lambda: "k")


def _reply(obj, prompt_tokens=1000, completion_tokens=200):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(obj)}}],
                                     "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}})


def test_together_json_schema_call_and_usage_cost(monkeypatch):
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        assert req.headers["authorization"] == "Bearer k"
        return _reply({"ok": True})

    _mock_together(monkeypatch, handler)
    monkeypatch.setattr(fast_llm, "anthropic_key", lambda: "")
    res = run(fast_llm.complete_json("play_suggest", system="s", user="u", schema={"type": "object"}))
    assert res.data == {"ok": True} and res.provider == "together"
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert res.usage.input_tokens == 1000 and res.usage.output_tokens == 200
    # default Together price 0.30 / 1.20 per M
    assert res.cost_usd == pytest.approx(1000 / 1e6 * 0.30 + 200 / 1e6 * 1.20)


def test_schema_rejection_retries_in_json_mode(monkeypatch):
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, text="json_schema not supported for this model")
        return _reply({"x": 1})

    _mock_together(monkeypatch, handler)
    res = run(fast_llm.complete_json("x", system="s", user="u", schema={"type": "object"},
                                     chain=[("together", "m")]))
    assert calls == ["json_schema", "json_object"] and res.data == {"x": 1}


def test_falls_through_to_next_provider_and_validates(monkeypatch):
    _mock_together(monkeypatch, lambda req: _reply({"bad": True}))

    async def fake_anthropic(messages, **kw):
        return json.dumps({"suggestions": []}), fast_llm.Usage(10, 5)

    monkeypatch.setitem(fast_llm._CALLS, "anthropic", fake_anthropic)
    monkeypatch.setattr(fast_llm, "available", lambda p: p in {"together", "anthropic"})

    def validate(d):
        if "suggestions" not in d:
            raise ValueError("missing suggestions")
        return d

    res = run(fast_llm.complete_json("play_suggest", system="s", user="u", validate=validate))
    assert res.provider == "anthropic" and res.model == "claude-haiku-5-5"
    assert "together: unusable reply" in res.errors[0]


def test_timeout_moves_on(monkeypatch):
    async def slow(messages, **kw):
        await asyncio.sleep(5)

    async def quick(messages, **kw):
        return '{"a": 1}', fast_llm.Usage()

    monkeypatch.setitem(fast_llm._CALLS, "together", slow)
    monkeypatch.setitem(fast_llm._CALLS, "anthropic", quick)
    monkeypatch.setattr(fast_llm, "available", lambda p: True)
    res = run(fast_llm.complete_json("x", system="s", user="u", timeout=0.05,
                                     chain=[("together", "m"), ("anthropic", "h")]))
    assert res.provider == "anthropic" and "timed out" in res.errors[0]


def test_no_provider_raises(monkeypatch):
    monkeypatch.setattr(fast_llm, "available", lambda p: False)
    with pytest.raises(fast_llm.NoProvider):
        run(fast_llm.complete_json("play_suggest", system="s", user="u"))


def test_parse_json_tolerates_fences():
    assert fast_llm.parse_json('Sure:\n```json\n{"a": [1]}\n```') == {"a": [1]}
    with pytest.raises(ValueError):
        fast_llm.parse_json("nope")


def test_strict_schema_closes_objects():
    s = fast_llm.strict_schema({"type": "object", "properties": {"x": {"type": "array", "items": {"type": "object"}}}})
    assert s["additionalProperties"] is False
    assert s["properties"]["x"]["items"]["additionalProperties"] is False


# ── routing ─────────────────────────────────────────────────────────────
def test_routing_defaults_use_the_claude_5_family():
    assert model_routing.JOB_INDEX["chat"].model == "claude-sonnet-5-5"
    assert model_routing.JOB_INDEX["deep"].model == "claude-opus-5-5"
    assert model_routing.JOB_INDEX["plays_review"].model == "claude-haiku-5-5"
    assert model_routing.route("play_suggest")["provider"] == "together"
    # play suggestions never fall back to the slow CLI path
    assert all(p != "claude_sdk" for p, _ in model_routing.fast_chain("play_suggest"))
    assert model_routing.fast_chain("report_digest")[-1][0] == "claude_sdk"


def test_routing_overrides_and_reset(no_db_routing, monkeypatch):
    no_db_routing["jobs"] = {"chat_title": {"provider": "anthropic", "model": "claude-haiku-5-5"},
                             "chat": {"model": "claude-opus-5-5", "provider": "together"}}
    no_db_routing["prices"] = {"together": [0.5, 2.0]}
    assert model_routing.route("chat_title") == {"provider": "anthropic", "model": "claude-haiku-5-5", "timeout_s": 6.0}
    assert model_routing.fast_chain("chat_title")[1][0] == "together"
    # agent jobs are always the SDK
    assert model_routing.route("chat")["provider"] == "claude_sdk"
    assert model_routing.sdk_model("chat") == "claude-opus-5-5"
    assert model_routing.prices()["together"] == (0.5, 2.0)


def test_routing_update_validates(monkeypatch):
    saved = {}

    class Store:
        def __init__(self, db):
            pass

        def get(self, k, d):
            return saved.get(k, d)

        def set(self, k, v):
            saved[k] = v

    import app.services.config_store as cs

    monkeypatch.setattr(cs, "ConfigStore", Store)
    monkeypatch.setattr(model_routing, "_stored", lambda: saved.get(model_routing.CONFIG_KEY, {}))
    model_routing.update(None, {"jobs": {"chat_title": {"provider": "anthropic", "model": ""}}, "together_price": [0.2, 0.8]})
    assert saved["model_routing"]["jobs"] == {"chat_title": {"provider": "anthropic"}}
    assert saved["model_routing"]["prices"]["together"] == [0.2, 0.8]
    with pytest.raises(ValueError):
        model_routing.update(None, {"jobs": {"chat_title": {"provider": "openai"}}})
    model_routing.update(None, {"jobs": {"chat_title": {"provider": "", "model": ""}}})
    assert saved["model_routing"]["jobs"] == {}


def test_settings_endpoints_never_return_keys(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.api import model_settings
    from app.core.database import Base, get_db
    from app.main import app

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    maker = sessionmaker(bind=eng)

    def db():
        with maker() as s:
            yield s

    monkeypatch.setattr(model_settings, "_env_value", lambda env, attr: "")
    app.dependency_overrides[get_db] = db
    try:
        c = TestClient(app)
        r = c.put("/api/config/data-providers", json={"together_api_key": "tg-secret-123"})
        assert r.status_code == 200 and "tg-secret-123" not in r.text
        assert r.json()["together_api_key"] == {"label": "Together AI", "configured": True, "source": "settings"}
        assert "tg-secret-123" not in c.get("/api/config/data-providers").text
        # blank keeps the stored key, clear removes it
        assert c.put("/api/config/data-providers", json={"together_api_key": ""}).json()["together_api_key"]["configured"]
        assert not c.put("/api/config/data-providers", json={"clear": ["together_api_key"]}).json()["together_api_key"]["configured"]
        assert c.get("/api/config/models").status_code == 200
        assert c.put("/api/config/models", json={"jobs": {"chat_title": {"provider": "nope"}}}).status_code == 422
    finally:
        app.dependency_overrides.clear()
