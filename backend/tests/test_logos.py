"""Company logos: the server-side fetch is allow-listed, size-capped, raster
only, off means no network, and hits / misses are cached. No real network:
upstream is an httpx.MockTransport."""

from pathlib import Path
import struct
import sys
import time
import zlib

sys.path.append(str(Path(__file__).resolve().parents[1]))

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import entities  # noqa: F401 — register all tables
from app.services import logo_service as ls


def _png(w=4, h=4) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = _png()
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(ls, "_identity_cache", {})
    monkeypatch.setattr(ls, "resolve_identity", lambda _db, key: {"isin": None, "symbol": key.upper(), "label": key})
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()
    yield session
    session.close()


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


IDENT = {"isin": None, "symbol": "AAPL", "label": "AAPL"}


# ── Fetch safety ──────────────────────────────────────────────────────────


def test_fetches_png_from_allowed_host():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)

    assert ls.fetch_logo(IDENT, _client(handler)) == (PNG, "png")
    assert seen and seen[0].startswith("https://api.elbstream.com/logos/symbol/AAPL?format=png")


def test_refuses_hosts_off_the_allow_list():
    with pytest.raises(ls.LogoFetchError):
        ls._check_url("https://evil.example.com/logo.png")
    with pytest.raises(ls.LogoFetchError):
        ls._check_url("http://api.elbstream.com/logos/symbol/AAPL")  # https only
    with pytest.raises(ls.LogoFetchError):
        ls._check_url("https://api.elbstream.com:8443/x")


def test_redirect_to_disallowed_host_is_not_followed():
    seen = []

    def handler(req):
        seen.append(req.url.host)
        if req.url.host == "api.elbstream.com":
            return httpx.Response(302, headers={"location": "https://tracker.example.net/a.png"})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)

    assert ls.fetch_logo(IDENT, _client(handler)) is None
    assert "tracker.example.net" not in seen


def test_size_cap_streams_and_refuses():
    big = PNG + b"\x00" * (ls.MAX_BYTES + 10)

    def handler(req):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=big)

    assert ls.fetch_logo(IDENT, _client(handler)) is None


def test_declared_oversize_is_refused():
    def handler(req):
        return httpx.Response(200, headers={"content-type": "image/png", "content-length": str(ls.MAX_BYTES + 1)},
                              content=PNG)

    with pytest.raises(ls.LogoFetchError):
        ls._image(_client(handler), "https://api.elbstream.com/logos/symbol/AAPL")


@pytest.mark.parametrize("ctype", ["text/html", "application/octet-stream", "image/svg+xml", ""])
def test_non_raster_content_type_is_refused(ctype):
    def handler(req):
        return httpx.Response(200, headers={"content-type": ctype}, content=PNG)

    assert ls.fetch_logo(IDENT, _client(handler)) is None


def test_svg_body_is_never_accepted_even_labelled_png():
    def handler(req):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=SVG)

    assert ls.fetch_logo(IDENT, _client(handler)) is None
    assert ls.sniff_image(SVG) is None


def test_sniff_formats():
    assert ls.sniff_image(PNG) == "png"
    assert ls.sniff_image(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == "jpg"
    assert ls.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 8) == "webp"
    assert ls.sniff_image(_png(5000, 10)) is None  # absurd dimensions
    assert ls.sniff_image(b"<html>") is None


def test_wikidata_fallback_uses_rendered_png_not_svg():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.host == "api.elbstream.com":
            return httpx.Response(404)
        if req.url.host == "query.wikidata.org":
            assert "US0378331005" in str(req.url)
            return httpx.Response(200, json={"results": {"bindings": [
                {"logo": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/Apple%20logo.svg"}}]}})
        if req.url.host == "commons.wikimedia.org":
            return httpx.Response(302, headers={"location": "https://upload.wikimedia.org/thumb/apple.svg.png"})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)

    got = ls.fetch_logo({"isin": "US0378331005", "symbol": "AAPL", "label": "AAPL"}, _client(handler))
    assert got == (PNG, "png")
    commons = [u for u in seen if "commons.wikimedia.org" in u][0]
    assert "width=" in commons


def test_bad_keys_are_rejected():
    assert ls.valid_key("AAPL") and ls.valid_key("BAl_EQ") and ls.valid_key("BA.L") and ls.valid_key("SAAB-B.ST")
    for bad in ("", "../etc/passwd", "a/b", "x" * 40, ".hidden", "A B"):
        assert not ls.valid_key(bad)


# ── Setting + caches ──────────────────────────────────────────────────────


def test_disabled_setting_means_no_network(db):
    ls.set_logo_settings(db, False)
    calls = []
    assert ls.get_logo(db, "AAPL", fetcher=lambda ident: calls.append(ident) or (PNG, "png")) is None
    assert calls == []


def test_disabled_setting_blocks_prewarm(db, monkeypatch):
    import app.core.database as database

    ls.set_logo_settings(db, False)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    calls = []
    assert ls.prewarm(["AAPL", "MSFT"], interval_s=0, fetcher=lambda i: calls.append(i) or (PNG, "png")) == 0
    assert calls == []


def test_cache_hit_fetches_once(db):
    calls = []

    def fetcher(ident):
        calls.append(ident)
        return PNG, "png"

    assert ls.get_logo(db, "AAPL", fetcher=fetcher) == (PNG, "image/png")
    assert ls.get_logo(db, "AAPL", fetcher=fetcher) == (PNG, "image/png")
    assert len(calls) == 1


def test_negative_cache_for_seven_days(db, tmp_path):
    calls = []

    def fetcher(ident):
        calls.append(ident)
        return None

    assert ls.get_logo(db, "ZZZQ", fetcher=fetcher) is None
    assert ls.get_logo(db, "ZZZQ", fetcher=fetcher) is None
    assert len(calls) == 1
    # Older than 7 days → asked again.
    marker = next(tmp_path.glob("*.miss"))
    old = time.time() - ls.MISS_TTL_S - 60
    import os

    os.utime(marker, (old, old))
    ls.get_logo(db, "ZZZQ", fetcher=fetcher)
    assert len(calls) == 2


def test_network_error_is_not_a_miss(db):
    def boom(ident):
        raise httpx.ConnectError("offline")

    assert ls.get_logo(db, "AAPL", fetcher=boom) is None
    assert ls.get_logo(db, "AAPL", fetcher=lambda i: (PNG, "png")) == (PNG, "image/png")


def test_identity_prefers_held_listing(monkeypatch):
    from app.services import portfolio_service

    monkeypatch.setattr(ls, "_identity_cache", {})
    monkeypatch.setattr(ls, "_held_codes", lambda db: {"BA": "BAL_EQ"})
    monkeypatch.setattr(portfolio_service, "_instrument_meta_map", lambda db: {
        "BAL_EQ": {"ticker": "BAl_EQ", "short_name": "BA", "currency": "GBX", "isin": "GB0002634946"}})
    ident = ls.resolve_identity(None, "BA")
    assert ident["isin"] == "GB0002634946"
    assert ident["symbol"] == "BA.L"


# ── Route ─────────────────────────────────────────────────────────────────


def _app(db):
    from fastapi import FastAPI

    from app.api import logos
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(logos.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    return app


def test_route_serves_raster_with_safe_headers(db, monkeypatch):
    monkeypatch.setattr(ls, "fetch_logo", lambda ident, client=None: (PNG, "png"))
    client = TestClient(_app(db))
    r = client.get("/api/logos/AAPL")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert client.get("/api/logos/..%2Fsecret").status_code in (400, 404)


def test_route_settings_roundtrip_and_off_is_404(db, monkeypatch):
    calls = []
    monkeypatch.setattr(ls, "fetch_logo", lambda ident, client=None: calls.append(ident) or (PNG, "png"))
    client = TestClient(_app(db))
    assert client.get("/api/logos/settings").json() == {"enabled": True}
    assert client.put("/api/logos/settings", json={"enabled": False}).json() == {"enabled": False}
    assert client.get("/api/logos/MSFT").status_code == 404
    assert calls == []


def test_no_agent_tool_for_logos():
    from app.services import mcp_registry

    src = Path(mcp_registry.__file__).read_text()
    assert "logo" not in src.lower()
