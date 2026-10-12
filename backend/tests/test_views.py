"""Views engine: validation guardrails, builder path restriction, storage
versioning/revert, data-source whitelist and the HTTP surface."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.services import views_builder as builder
from app.services import views_data
from app.services import views_store as store
from app.services.views_validate import parse_meta, validate_html
from app.views_sdk import SDK_DIR, starters

HEADER = '<!-- openpf-view {"title": "T", "description": "d", "icon": "coins"} -->\n'
OK = HEADER + "<div id='x'></div><script>openpf.ready(async () => { const p = await openpf.data('portfolio') })</script>"


@pytest.fixture()
def views_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "views_data_dir", str(tmp_path / "views"))
    return tmp_path / "views"


# ── validation ──────────────────────────────────────────────────────────
def test_valid_view_passes_and_meta_parses():
    assert validate_html(OK) == []
    assert parse_meta(OK) == {"title": "T", "description": "d", "icon": "coins"}


@pytest.mark.parametrize(
    "snippet",
    [
        "<script>fetch('/api/orders')</script>",
        "<script>new XMLHttpRequest()</script>",
        "<script>const ws = new WebSocket('x')</script>",
        "<script src='/evil.js'></script>",
        "<img src='https://tracker.example/p.gif'>",
        "<img src=\"//tracker.example/p.gif\">",
        "<script>const u = 'http://example.com'</script>",
        "<iframe></iframe>",
        "<link rel='stylesheet' href='x.css'>",
        "<form action='/x'></form>",
        "<style>@import 'x.css';</style>",
        "<style>.a{background:url(/img.png)}</style>",
        "<script>eval('1')</script>",
        "<script>new Function('return 1')()</script>",
        "<script>localStorage.setItem('a', 1)</script>",
        "<script>window.parent.postMessage({}, '*')</script>",
        "<script>window.open('x')</script>",
        "<script>import('x')</script>",
        "<a href='javascript:alert(1)'>x</a>",
    ],
)
def test_validation_rejects_network_and_escapes(snippet):
    assert validate_html(OK + snippet), snippet


def test_validation_allows_svg_namespace_and_data_uris():
    html = OK + "<script>document.createElementNS('http://www.w3.org/2000/svg', 'svg')</script><img src='data:image/png;base64,AAAA'>"
    assert validate_html(html) == []


def test_validation_requires_header_and_size_limit():
    assert any("header" in p for p in validate_html("<script>openpf.ready(()=>{})</script>"))
    assert any("limit" in p for p in validate_html(OK + "<!--" + "x" * 210_000 + "-->"))


def test_starters_pass_validation():
    shipped = starters()
    assert {s["slug"] for s in shipped} >= {"dividend-income", "drift-vs-targets", "concentration"}
    for s in shipped:
        assert validate_html(s["html"]) == [], s["slug"]


# ── builder path restriction ────────────────────────────────────────────
def test_builder_tools_restricted_to_draft_and_sdk(tmp_path):
    draft = tmp_path / "abc" / "draft.html"
    draft.parent.mkdir()
    check = builder.check_tool_path
    assert check("Write", {"file_path": str(draft)}, draft) is None
    assert check("Edit", {"file_path": str(draft)}, draft) is None
    assert check("Read", {"file_path": str(draft)}, draft) is None
    assert check("Read", {"file_path": str(SDK_DIR / "VIEW_SDK.md")}, draft) is None
    assert check("Write", {"file_path": str(SDK_DIR / "VIEW_SDK.md")}, draft)  # can read the SDK, never write it
    assert check("Write", {"file_path": str(tmp_path / "abc" / "v1.html")}, draft)
    assert check("Write", {"file_path": str(draft.parent / ".." / "other" / "draft.html")}, draft)
    assert check("Read", {"file_path": "/etc/passwd"}, draft)
    assert check("Read", {"file_path": str(Path.home() / ".env")}, draft)
    assert check("Bash", {"command": "ls"}, draft)
    assert check("WebFetch", {"url": "https://x"}, draft)
    assert check("mcp__views__sample_data", {}, draft) is None
    assert check("mcp__trading212__place_market_order", {}, draft)


# ── storage ─────────────────────────────────────────────────────────────
def test_versioning_and_revert(views_dir):
    meta = store.create_view(title="Dividend income", prompt="dividends by month")
    vid = meta["id"]
    assert vid == "dividend-income"
    assert meta["current_version"] == 0
    store.save_version(vid, OK.replace("T", "One"), note="v1")
    m2 = store.save_version(vid, OK, note="v2", title="Renamed")
    assert m2["current_version"] == 2 and m2["title"] == "Renamed"
    assert [v["version"] for v in m2["versions"]] == [1, 2]
    reverted = store.revert(vid, 1)
    assert reverted["current_version"] == 1
    assert "One" in store.read_version(vid)
    # Editing after a revert appends a new version (history is never rewritten).
    m3 = store.save_version(vid, OK, note="v3")
    assert m3["current_version"] == 3
    with pytest.raises(store.ViewNotFound):
        store.revert(vid, 99)


def test_ids_are_unique_and_safe(views_dir):
    a = store.create_view(title="Same")
    b = store.create_view(title="Same")
    assert a["id"] != b["id"]
    for bad in ("../etc", "A/B", "", "x" * 80):
        with pytest.raises(store.ViewNotFound):
            store.view_dir(bad)


def test_state_roundtrip_and_limit(views_dir):
    vid = store.create_view(title="Drift")["id"]
    assert store.get_state(vid) == {}
    store.set_state(vid, {"targets": {"NVDA": 0.1}})
    assert store.get_state(vid)["targets"]["NVDA"] == 0.1
    with pytest.raises(ValueError):
        store.set_state(vid, {"blob": "x" * (store.MAX_STATE_BYTES + 1)})


def test_delete_and_pin(views_dir):
    vid = store.create_view(title="Tmp")["id"]
    assert store.update_meta(vid, pinned=False)["pinned"] is False
    store.delete_view(vid)
    assert store.list_views() == []


# ── data whitelist ──────────────────────────────────────────────────────
def test_unknown_source_rejected():
    with pytest.raises(views_data.DataSourceError):
        views_data.resolve(None, "orders_place", {})
    with pytest.raises(views_data.DataSourceError):
        views_data.resolve(None, "__import__", {})
    assert set(views_data.SOURCES) == {"portfolio", "history", "orders", "dividends", "prices", "quotes", "fundamentals", "watchlist", "proposals", "plays"}


@pytest.mark.parametrize(
    "source,params",
    [
        ("portfolio", {"account": "everything"}),
        ("history", {"range": "forever"}),
        ("prices", {"ticker": "AAPL; DROP TABLE", "range": "1y"}),
        ("prices", {"ticker": "AAPL", "range": "10y"}),
        ("quotes", {"tickers": []}),
        ("quotes", {"tickers": [f"T{i}" for i in range(31)]}),
        ("watchlist", {"status": "deleted"}),
    ],
)
def test_bad_params_rejected(source, params):
    with pytest.raises(views_data.DataSourceError):
        views_data.resolve(None, source, params)


def test_truncate_sample_keeps_shape():
    out = views_data.truncate_sample({"items": list(range(10)), "nested": {"a": [{"x": 1}] * 5}}, max_items=2)
    assert out["items"][:2] == [0, 1] and "8 more" in out["items"][2]
    assert len(out["nested"]["a"]) == 3


# ── HTTP ────────────────────────────────────────────────────────────────
def test_api_install_starter_and_render(views_dir):
    from app.main import app

    client = TestClient(app)
    listed = client.get("/api/views/starters").json()
    assert any(s["slug"] == "drift-vs-targets" for s in listed)
    meta = client.post("/api/views/starters/drift-vs-targets/install").json()
    assert meta["current_version"] == 1 and meta["icon"] == "target"
    doc = client.get(f"/api/views/{meta['id']}/html").json()
    assert doc["problems"] == [] and "openpf.state" in doc["html"]
    assert client.put(f"/api/views/{meta['id']}/state", json={"band": 0.03}).json() == {"band": 0.03}
    assert client.post("/api/views/data", json={"source": "nope"}).status_code == 400
    assert client.get("/api/views/missing-view").status_code == 404
    assert client.post("/api/views/generate", json={"prompt": "x", "view_id": "missing-view"}).status_code == 404
    assert client.delete(f"/api/views/{meta['id']}").json() == {"ok": True}


def test_api_html_revalidates_tampered_file(views_dir):
    from app.main import app

    client = TestClient(app)
    vid = store.create_view(title="Tampered")["id"]
    store.save_version(vid, OK + "<script>fetch('https://evil.example')</script>")
    doc = client.get(f"/api/views/{vid}/html").json()
    assert doc["html"] == "" and doc["problems"]
