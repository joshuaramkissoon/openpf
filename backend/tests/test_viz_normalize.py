from app.services.viz_spec import normalize_spec, validate_spec


def test_fast_model_near_misses_are_filled_in():
    spec = {"version": 1, "title": "Countries", "blocks": [
        {"type": "kpis", "items": [{"label": "Countries", "value": {"agg": "count"}},
                                   {"label": "Invested", "value": {"agg": "sum"}, "format": "money"}]},
        {"type": "allocation", "label": "country", "value": "value", "style": "bar"},
        {"type": "bars", "label": "sector", "value": "value"},
    ]}
    out, problems = validate_spec(normalize_spec(spec))
    assert not problems, problems
    alloc = out["blocks"][1]
    assert alloc["from"] == "pf.positions"
    assert {"groupBy": "country"} in [{k: v for k, v in s.items() if v is not None} for s in alloc["transform"]]
