"""Home layout: default, validation (kinds, sizes, refs, inline specs), dedupe,
the add-to-Home endpoint and the HTTP surface."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.services import home_layout as hl

SPEC = {
    "version": 1,
    "title": "Today",
    "blocks": [{"type": "hero", "label": "Today", "from": "pf.positions", "value": {"agg": "sum", "field": "day_change"}, "format": "signedMoney"}],
}


def test_default_matches_todays_home():
    refs = [w["ref"] for w in hl.DEFAULT_LAYOUT["widgets"]]
    assert refs == ["book", "risk", "today", "permissions"]
    assert hl.normalise(hl.DEFAULT_LAYOUT) == hl.DEFAULT_LAYOUT


def test_normalise_assigns_ids_and_validates():
    out = hl.normalise({"widgets": [{"kind": "preset", "ref": "movers", "size": "S"}, {"kind": "view", "ref": "dividends", "size": "L"}]})
    assert [w["kind"] for w in out["widgets"]] == ["preset", "view"]
    assert all(w["id"].startswith("w_") for w in out["widgets"])
    assert out["widgets"][1]["block"] == 0


@pytest.mark.parametrize(
    "bad, msg",
    [
        ([], "object"),
        ({"widgets": "x"}, "widgets list"),
        ({"widgets": [{"kind": "iframe", "ref": "x"}]}, "unknown kind"),
        ({"widgets": [{"kind": "preset", "ref": "x", "size": "XL"}]}, "unknown size"),
        ({"widgets": [{"kind": "preset", "ref": "../etc"}]}, "valid ref"),
        ({"widgets": [{"kind": "builtin", "ref": "decisions"}]}, "unknown card"),
        ({"widgets": [{"kind": "spec", "spec": {"version": 1, "title": "x", "blocks": [{"type": "script"}]}}]}, "isn't valid"),
        ({"widgets": [{"kind": "view", "ref": "v", "block": -1}]}, "block index"),
        ({"widgets": [{"kind": "preset", "ref": f"p{i}"} for i in range(hl.MAX_WIDGETS + 1)]}, "at most"),
    ],
)
def test_rejects_bad_layouts(bad, msg):
    with pytest.raises(hl.LayoutError, match=msg):
        hl.normalise(bad)


def test_builtin_size_is_clamped_and_duplicates_dropped():
    out = hl.normalise(
        {
            "widgets": [
                {"id": "a", "kind": "builtin", "ref": "book", "size": "S"},
                {"id": "b", "kind": "builtin", "ref": "book", "size": "L"},
                {"id": "c", "kind": "spec", "spec": SPEC},
                {"id": "c", "kind": "spec", "spec": SPEC},
            ]
        }
    )
    assert [(w["id"], w["size"]) for w in out["widgets"][:1]] == [("a", "M")]
    assert len(out["widgets"]) == 3  # second book dropped; chat specs may repeat
    assert out["widgets"][1]["id"] != out["widgets"][2]["id"]


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        c.post("/api/home/layout/reset")
        yield c


def test_http_roundtrip(client):
    assert client.get("/api/home/layout").json() == hl.DEFAULT_LAYOUT

    put = client.put("/api/home/layout", json={"widgets": [{"id": "x", "kind": "preset", "ref": "movers", "size": "S"}]})
    assert put.status_code == 200
    assert client.get("/api/home/layout").json()["widgets"] == [{"id": "x", "kind": "preset", "ref": "movers", "size": "S"}]

    bad = client.put("/api/home/layout", json={"widgets": [{"kind": "nope"}]})
    assert bad.status_code == 422 and "unknown kind" in bad.json()["detail"]

    added = client.post("/api/home/widgets", json={"kind": "spec", "spec": SPEC, "title": "From chat", "size": "S"}).json()
    # A chart from chat is saved as a view and lands as a view widget, so it deep-links.
    w = added["widget"]
    assert w["kind"] == "view" and w["block"] == 0 and w["size"] == "S" and len(added["layout"]["widgets"]) == 2
    view = client.get(f"/api/views/{w['ref']}").json()
    assert view["title"] == "From chat"
    again = client.post("/api/home/widgets", json={"kind": "preset", "ref": "movers"}).json()
    assert len(again["layout"]["widgets"]) == 2 and again["widget"]["id"] == "x"

    assert client.post("/api/home/layout/reset").json() == hl.DEFAULT_LAYOUT


def test_inline_spec_widgets_migrate_to_views_on_read(client):
    from app.core.database import SessionLocal
    from app.services.config_store import ConfigStore

    lead = {**SPEC, "blocks": [{"type": "note", "text": "Context first"}, *SPEC["blocks"]]}
    with SessionLocal() as db:  # a layout saved before chat specs became views
        ConfigStore(db).set(hl.KEY, {"version": 1, "widgets": [{"id": "old", "kind": "spec", "spec": lead, "title": "Pinned", "size": "M"}]})
    first = client.get("/api/home/layout").json()["widgets"]
    assert [(w["id"], w["kind"], w.get("block"), w["size"]) for w in first] == [("old", "view", 1, "M")]
    assert client.get(f"/api/views/{first[0]['ref']}").json()["title"] == "Pinned"
    # Persisted once: a second read neither re-migrates nor makes another view.
    views = len(client.get("/api/views").json())
    assert client.get("/api/home/layout").json()["widgets"] == first
    assert len(client.get("/api/views").json()) == views


def test_view_widgets_accept_code_views_and_validate_block():
    out = hl.normalise({"widgets": [{"kind": "view", "ref": "abc123", "size": "L"}]})
    assert out["widgets"][0]["block"] == 0  # code views have no blocks: index 0
    with pytest.raises(hl.LayoutError, match="block index"):
        hl.normalise({"widgets": [{"kind": "view", "ref": "abc123", "block": -1}]})
    with pytest.raises(hl.LayoutError, match="block index"):
        hl.normalise({"widgets": [{"kind": "view", "ref": "abc123", "block": True}]})
    with pytest.raises(hl.LayoutError, match="valid ref"):
        hl.normalise({"widgets": [{"kind": "view", "ref": "../etc", "block": 0}]})


def test_preset_widget_page_is_a_findable_view(client):
    ok = client.post("/api/views/specs", json={"spec": SPEC, "starter": "widget:value"})
    assert ok.status_code == 200 and ok.json()["starter"] == "widget:value"
    assert any(v["starter"] == "widget:value" for v in client.get("/api/views").json())
    for bad in ["today", "widget:../x", "widget:" + "a" * 41]:
        assert client.post("/api/views/specs", json={"spec": SPEC, "starter": bad}).status_code == 422
