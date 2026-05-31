"""Tests for regime gating of leveraged signals (_apply_regime)."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services import leveraged_service as ls
from app.services.regime_service import RegimeState


def _regime(name, score):
    long_bias = max(0.0, min(1.0, 0.5 + score / 2))
    return RegimeState(
        regime=name,
        label=name.replace("_", "-").title(),
        score=score,
        long_bias=long_bias,
        inverse_bias=1 - long_bias,
        vix=15.0,
        vix_state="calm",
        breadth=1.0 if score > 0 else 0.0,
        rationale="test",
        components={},
        as_of="2026-05-30T00:00:00Z",
        stale=False,
    )


def test_long_boosted_in_risk_on():
    base = 0.70
    conf, drop, note = ls._apply_regime(base, "long", _regime("risk_on", 0.5))
    assert not drop
    assert conf > base
    assert "supports long" in note


def test_inverse_dropped_in_strong_risk_on():
    # A strong risk-on tape must NOT propose buying an inverse ETP.
    conf, drop, note = ls._apply_regime(0.80, "short", _regime("risk_on", 0.9))
    assert drop is True
    assert "fights a strong" in note


def test_inverse_trimmed_in_weak_risk_on():
    # Weak regime: keep the counter-signal but trim conviction.
    base = 0.80
    conf, drop, note = ls._apply_regime(base, "short", _regime("risk_on", 0.4))
    assert not drop
    assert conf < base
    assert "counter" in note.lower()


def test_inverse_boosted_in_risk_off():
    base = 0.70
    conf, drop, note = ls._apply_regime(base, "short", _regime("risk_off", -0.5))
    assert not drop
    assert conf > base


def test_long_dropped_in_strong_risk_off():
    conf, drop, note = ls._apply_regime(0.80, "long", _regime("risk_off", -0.9))
    assert drop is True


def test_neutral_is_noop():
    base = 0.66
    for direction in ("long", "short"):
        conf, drop, note = ls._apply_regime(base, direction, _regime("neutral", 0.0))
        assert not drop
        assert conf == base
        assert note is None


def test_stale_regime_is_noop():
    base = 0.66
    r = _regime("risk_on", 0.9)
    r.stale = True
    conf, drop, note = ls._apply_regime(base, "short", r)
    assert not drop
    assert conf == base


def test_none_regime_is_noop():
    base = 0.66
    conf, drop, note = ls._apply_regime(base, "short", None)
    assert not drop and conf == base and note is None
