"""The /charts/forecast endpoint must degrade gracefully.

The forecast now runs in an isolated worker process. The endpoint must turn
each failure mode into a clean HTTP status instead of letting it take the
process down:

* worker crash (segfault/OOM) -> 503 (transient, retryable)
* worker timeout              -> 504
* deps/model unavailable      -> 503
* bad input / no data         -> 400
"""
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.main import app
from app.services import forecast_pool
from app.services.kronos_service import ForecastError, ForecastUnavailableError

client = TestClient(app)


def _raises(exc):
    async def _fn(**kwargs):
        raise exc

    return _fn


def test_worker_crash_maps_to_503(monkeypatch):
    monkeypatch.setattr(forecast_pool, "run_forecast", _raises(forecast_pool.ForecastWorkerError("crashed")))
    resp = client.get("/api/charts/forecast", params={"ticker": "MU"})
    assert resp.status_code == 503


def test_worker_timeout_maps_to_504(monkeypatch):
    monkeypatch.setattr(forecast_pool, "run_forecast", _raises(forecast_pool.ForecastTimeout("slow")))
    resp = client.get("/api/charts/forecast", params={"ticker": "MU"})
    assert resp.status_code == 504


def test_model_unavailable_maps_to_503(monkeypatch):
    monkeypatch.setattr(forecast_pool, "run_forecast", _raises(ForecastUnavailableError("no torch")))
    resp = client.get("/api/charts/forecast", params={"ticker": "MU"})
    assert resp.status_code == 503


def test_bad_input_maps_to_400(monkeypatch):
    monkeypatch.setattr(forecast_pool, "run_forecast", _raises(ForecastError("bad ticker")))
    resp = client.get("/api/charts/forecast", params={"ticker": "MU"})
    assert resp.status_code == 400
