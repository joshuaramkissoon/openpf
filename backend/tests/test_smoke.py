from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.main import app


client = TestClient(app)


def test_health():
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'


def test_end_to_end_flow():
    refresh = client.post('/api/portfolio/refresh')
    assert refresh.status_code == 200

    snapshot = client.get('/api/portfolio/snapshot')
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload['account']['total'] > 0
    assert len(payload['positions']) > 0

    run = client.post('/api/agent/run', json={'include_watchlist': True, 'execute_auto': False})
    assert run.status_code == 200

    intents = client.get('/api/agent/intents')
    assert intents.status_code == 200
    assert isinstance(intents.json(), list)
