"""app.quant against hand-computed / textbook results. Pure: no DB, no network."""

from datetime import date
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.quant import QuantDataError
from app.quant import clustering, covariance, deploy, exposure, rebalance, returns, risk, sizing, uk_tax


def _frame(x: np.ndarray, names=None) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", periods=x.shape[0])
    return pd.DataFrame(x, index=idx, columns=names or [f"A{i}" for i in range(x.shape[1])])


# ── returns ──────────────────────────────────────────────────────────────────

def test_convert_returns_compounds_fx():
    idx = pd.bdate_range("2025-01-01", periods=2)
    out = returns.convert_returns(pd.Series([0.01, -0.02], index=idx), pd.Series([0.02, 0.01], index=idx))
    assert out.tolist() == pytest.approx([1.01 * 1.02 - 1, 0.98 * 1.01 - 1])


def test_align_returns_names_the_short_series():
    idx = pd.bdate_range("2025-01-01", periods=100)
    long = pd.Series(np.zeros(100), index=idx)
    short = pd.Series(np.zeros(30), index=idx[-30:])
    with pytest.raises(QuantDataError, match="NEW"):
        returns.align_returns({"OLD": long, "NEW": short})
    assert len(returns.align_returns({"OLD": long, "NEW": short}, min_obs=30)) == 30


# ── covariance ───────────────────────────────────────────────────────────────

def _lw_identity_loops(x: np.ndarray) -> float:
    """Ledoit-Wolf (2004) shrinkage intensity written straight from the paper."""
    x = x - x.mean(axis=0)
    t, n = x.shape
    s = x.T @ x / t
    mu = np.trace(s) / n
    d2 = ((s - mu * np.eye(n)) ** 2).sum()
    b_bar2 = sum(((np.outer(x[k], x[k]) - s) ** 2).sum() for k in range(t)) / t**2
    return min(b_bar2, d2) / d2


def _lw_cc_loops(x: np.ndarray) -> float:
    """Ledoit-Wolf (2003) constant-correlation intensity, element by element."""
    x = x - x.mean(axis=0)
    t, n = x.shape
    s = x.T @ x / t
    sd = np.sqrt(np.diag(s))
    r = s / np.outer(sd, sd)
    r_bar = (r.sum() - n) / (n * (n - 1))
    f = r_bar * np.outer(sd, sd)
    np.fill_diagonal(f, np.diag(s))
    pi = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            pi[i, j] = np.mean((x[:, i] * x[:, j] - s[i, j]) ** 2)
    rho = np.trace(pi)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            th_ii = np.mean((x[:, i] ** 2 - s[i, i]) * (x[:, i] * x[:, j] - s[i, j]))
            th_jj = np.mean((x[:, j] ** 2 - s[j, j]) * (x[:, i] * x[:, j] - s[i, j]))
            rho += r_bar / 2 * (np.sqrt(s[j, j] / s[i, i]) * th_ii + np.sqrt(s[i, i] / s[j, j]) * th_jj)
    gamma = ((f - s) ** 2).sum()
    return max(0.0, min(1.0, (pi.sum() - rho) / gamma / t))


@pytest.fixture()
def noisy_returns():
    rng = np.random.default_rng(0)
    # one market factor with different loadings → correlations differ by pair,
    # so the constant-correlation target is wrong and shrinkage is interior.
    loads = np.array([0.2, 0.6, 1.0, 1.5, 2.0, 0.8])
    return _frame(rng.normal(0, 0.01, (80, 1)) * loads + rng.normal(0, 0.015, (80, 6)))


def test_ledoit_wolf_identity_matches_paper_formula(noisy_returns):
    est = covariance.covariance(noisy_returns, "lw_identity")
    assert est.shrinkage == pytest.approx(_lw_identity_loops(noisy_returns.to_numpy()), rel=1e-10)
    sample = covariance.sample_covariance(noisy_returns)
    assert np.trace(est.matrix.to_numpy()) == pytest.approx(np.trace(sample))  # target keeps the trace


def test_ledoit_wolf_constant_correlation_matches_paper_formula(noisy_returns):
    est = covariance.covariance(noisy_returns)  # default
    assert est.method == "ledoit_wolf"
    assert est.shrinkage == pytest.approx(_lw_cc_loops(noisy_returns.to_numpy()), rel=1e-10)
    assert 0 < est.shrinkage < 1
    # variances are untouched; the matrix stays positive definite
    assert np.diag(est.matrix) == pytest.approx(np.diag(covariance.sample_covariance(noisy_returns)))
    assert np.linalg.eigvalsh(est.matrix.to_numpy()).min() > 0


def test_shrinkage_fades_with_lots_of_data():
    rng = np.random.default_rng(1)
    loads = np.array([0.2, 1.0, 1.5, 2.0])
    big = _frame(rng.normal(0, 0.01, (20_000, 1)) * loads + rng.normal(0, 0.01, (20_000, 4)))
    assert covariance.covariance(big).shrinkage < 0.01
    assert covariance.covariance(big, "lw_identity").shrinkage < 0.01


def test_covariance_refuses_short_history():
    with pytest.raises(QuantDataError, match="need at least 60"):
        covariance.covariance(_frame(np.random.default_rng(2).normal(size=(30, 3))))


# ── risk ─────────────────────────────────────────────────────────────────────

def test_expected_shortfall_worst_k_days():
    r = pd.Series(np.arange(-10, 10) / 100)  # -10% .. +9%, 20 days
    assert risk.expected_shortfall(r, 0.95) == pytest.approx(-0.10)  # worst 1 day
    assert risk.expected_shortfall(r, 0.90) == pytest.approx(-0.095)  # mean of worst 2
    assert risk.expected_shortfall(r, 0.95) <= risk.value_at_risk(r, 0.95)


def test_tail_contributions_sum_to_portfolio_es():
    rng = np.random.default_rng(3)
    df = _frame(rng.normal(0, 0.02, (250, 3)), ["A", "B", "C"])
    w = {"A": 0.5, "B": 0.3, "C": 0.2}
    port = returns.portfolio_returns(df, w)
    assert sum(risk.tail_contributions(df, w).values()) == pytest.approx(risk.expected_shortfall(port))


def test_drawdown_stats_hand_example():
    curve = pd.Series([100, 120, 90, 95, 130, 117], index=pd.bdate_range("2025-01-01", periods=6), dtype=float)
    dd = risk.drawdown_stats(curve)
    assert dd.max_drawdown == pytest.approx(-0.25)
    assert dd.peak_date == curve.index[1] and dd.trough_date == curve.index[2]
    assert dd.recovery_date == curve.index[4]
    assert dd.current_drawdown == pytest.approx(117 / 130 - 1)
    assert dd.max_duration_days == 2


def test_ewma_vol_of_constant_returns():
    assert risk.ewma_volatility(pd.Series([0.01] * 60)) == pytest.approx(0.01 * np.sqrt(252))


def test_risk_contributions_two_asset_hand_example():
    cov = np.array([[0.01, 0.01], [0.01, 0.04]])  # vols 10%, 20%, rho 0.5
    rc = risk.risk_contributions(np.array([0.5, 0.5]), cov)
    assert rc.total_vol == pytest.approx(np.sqrt(0.0175))
    assert rc.component == pytest.approx([0.005 / np.sqrt(0.0175), 0.0125 / np.sqrt(0.0175)])
    assert rc.pct == pytest.approx([2 / 7, 5 / 7])
    assert rc.component.sum() == pytest.approx(rc.total_vol)


# ── sizing ───────────────────────────────────────────────────────────────────

def test_vol_scaled_weight_matches_plays_table():
    assert sizing.vol_scaled_weight(0.06, 0.5, 0.25) == (pytest.approx(0.03), 0.5)
    assert sizing.vol_scaled_weight(0.02, 0.05, 0.25) == (pytest.approx(0.025), 1.25)  # clamped
    assert sizing.vol_scaled_weight(0.04, None, 0.25) == (0.04, 1.0)


def test_vol_target_and_inverse_vol():
    assert sizing.vol_target_weight(0.45, 0.015) == pytest.approx(0.015 / 0.45)
    assert sizing.vol_target_weight(0.10, 0.015, cap=0.10) == 0.10
    assert sizing.inverse_vol_weights({"A": 0.1, "B": 0.2}) == {"A": pytest.approx(2 / 3), "B": pytest.approx(1 / 3)}


@pytest.mark.parametrize("rho", [-0.3, 0.0, 0.5, 0.9])
def test_two_asset_risk_parity_is_inverse_vol_whatever_the_correlation(rho):
    cov = pd.DataFrame([[0.01, rho * 0.02], [rho * 0.02, 0.04]], index=["A", "B"], columns=["A", "B"])
    w = sizing.risk_parity_weights(cov)
    assert w["A"] == pytest.approx(2 / 3, abs=1e-8)


def test_risk_parity_equalises_contributions_and_honours_budgets():
    rng = np.random.default_rng(4)
    a = rng.normal(size=(5, 5))
    cov = pd.DataFrame(a @ a.T / 5 + np.eye(5) * 0.1, index=list("ABCDE"), columns=list("ABCDE"))
    w = np.array(list(sizing.risk_parity_weights(cov).values()))
    rc = risk.risk_contributions(w, cov.to_numpy()).pct
    assert rc == pytest.approx([0.2] * 5, abs=1e-6)
    wb = np.array(list(sizing.risk_parity_weights(cov, {"A": 2, "B": 1, "C": 1, "D": 1, "E": 1}).values()))
    rcb = risk.risk_contributions(wb, cov.to_numpy()).pct
    assert rcb[0] / rcb[1] == pytest.approx(2.0, rel=1e-5)


def test_risk_parity_equal_correlation_is_inverse_vol():
    vols = np.array([0.1, 0.2, 0.3])
    cov = pd.DataFrame(np.outer(vols, vols) * (0.4 + 0.6 * np.eye(3)), index=list("ABC"), columns=list("ABC"))
    w = sizing.risk_parity_weights(cov)
    inv = sizing.inverse_vol_weights(dict(zip("ABC", vols)))
    assert [w[k] for k in "ABC"] == pytest.approx([inv[k] for k in "ABC"], abs=1e-8)


def test_fractional_kelly_textbook():
    k = sizing.kelly_weight(0.06, 0.20, fraction=0.5, cap=0.10)
    assert k.full_kelly == pytest.approx(1.5)  # (mu - r) / sigma^2
    assert k.fractional == pytest.approx(0.75)
    assert k.weight == 0.10 and k.cap_binding
    assert sizing.kelly_weight(-0.02, 0.2).weight == 0.0  # no edge, no bet
    with pytest.raises(QuantDataError):
        sizing.kelly_weight(0.05, 0.2, fraction=1.5)


def test_loss_budget_sizing():
    assert sizing.max_weight_for_loss(0.02, 0.40) == pytest.approx(0.05)
    assert sizing.stress_loss(0.05, 0.40) == pytest.approx(0.02)


# ── clustering / HRP ─────────────────────────────────────────────────────────

def test_hrp_two_correlated_pairs_hand_example():
    # vols 10,10,20,20; rho 0.5 within each pair, 0 across.
    vols = np.array([0.1, 0.1, 0.2, 0.2])
    corr = np.array([[1, .5, 0, 0], [.5, 1, 0, 0], [0, 0, 1, .5], [0, 0, .5, 1]])
    cov = pd.DataFrame(np.outer(vols, vols) * corr, index=list("ABCD"), columns=list("ABCD"))
    w = clustering.hrp_weights(cov)
    # cluster variances 0.0075 vs 0.03 → 80% to the low-vol pair, split evenly inside.
    assert [w[k] for k in "ABCD"] == pytest.approx([0.4, 0.4, 0.1, 0.1])


def test_hrp_uncorrelated_equal_vol_is_equal_weight():
    cov = pd.DataFrame(np.eye(4) * 0.04, index=list("ABCD"), columns=list("ABCD"))
    assert list(clustering.hrp_weights(cov).values()) == pytest.approx([0.25] * 4)


def test_correlation_clusters_find_the_blocks():
    names = ["NVDA", "AMD", "TSM", "KO", "PG"]
    c = np.eye(5)
    for i, j in [(0, 1), (0, 2), (1, 2)]:
        c[i, j] = c[j, i] = 0.8
    c[3, 4] = c[4, 3] = 0.7
    groups = clustering.correlation_clusters(pd.DataFrame(c, index=names, columns=names), min_corr=0.6)
    assert groups == [["AMD", "NVDA", "TSM"], ["KO", "PG"]]


# ── exposure ─────────────────────────────────────────────────────────────────

def test_ols_beta_recovers_known_line():
    rng = np.random.default_rng(5)
    idx = pd.bdate_range("2025-01-01", periods=120)
    m = pd.Series(rng.normal(0, 0.01, 120), index=idx)
    reg = exposure.ols_exposure(1.5 * m + 0.0002, m.rename("SPY"))
    assert reg.betas["SPY"] == pytest.approx(1.5)
    assert reg.alpha_annual == pytest.approx(0.0002 * 252)
    assert reg.r_squared == pytest.approx(1.0)
    assert exposure.beta(1.5 * m, m) == pytest.approx(1.5)


def test_concentration_effective_n():
    c = exposure.concentration([0.5, 0.3, 0.2])
    assert c.hhi == pytest.approx(0.38)
    assert c.effective_n == pytest.approx(1 / 0.38)
    assert c.top1 == 0.5 and c.top3 == pytest.approx(1.0)


def test_diversification_ratio():
    cov = np.diag([0.04, 0.04])
    assert exposure.diversification_ratio(np.array([0.5, 0.5]), cov) == pytest.approx(np.sqrt(2))


# ── rebalance ────────────────────────────────────────────────────────────────

def test_band_is_five_twenty_five():
    assert rebalance.band_for(0.40) == pytest.approx(0.05)
    assert rebalance.band_for(0.08) == pytest.approx(0.02)


def test_rebalance_to_target_edge_and_budget():
    cur, tgt = {"A": 0.60, "B": 0.40}, {"A": 0.50, "B": 0.50}
    p = rebalance.rebalance_plan(cur, tgt, 10_000, turnover_budget=None)
    assert {t.ticker: t.notional_gbp for t in p.trades} == {"A": pytest.approx(-1000), "B": pytest.approx(1000)}
    e = rebalance.rebalance_plan(cur, tgt, 10_000, trade_to="edge", turnover_budget=None)
    assert {t.ticker: t.notional_gbp for t in e.trades} == {"A": pytest.approx(-500), "B": pytest.approx(500)}
    b = rebalance.rebalance_plan(cur, tgt, 10_000, turnover_budget=0.05)
    assert b.budget_binding and b.turnover_gbp == pytest.approx(500)
    assert [t.ticker for t in b.trades] == ["A"]  # the sell is funded first; the buy waits


def test_rebalance_in_band_does_nothing_and_buys_need_cash():
    assert not rebalance.rebalance_plan({"A": 0.52, "B": 0.48}, {"A": 0.5, "B": 0.5}, 10_000).trades
    p = rebalance.rebalance_plan({"A": 0.3}, {"A": 0.5}, 10_000, cash_available_gbp=1000, turnover_budget=None)
    assert p.cash_binding and p.trades[0].notional_gbp == pytest.approx(1000)


# ── deploy ───────────────────────────────────────────────────────────────────

def test_lump_sum_wins_on_expectation_when_equities_beat_cash():
    c = deploy.compare_lump_vs_staged(12_000, 12, expected_return=0.07, vol=0.18, cash_rate=0.04)
    assert c.expected_lump_gbp == pytest.approx(12_000 * np.exp(0.07))
    assert c.expected_lump_gbp > c.expected_staged_gbp
    assert c.prob_lump_wins > 0.5
    # With the historical equity premium (~6-7% over cash) lump sum wins about
    # two thirds of the time — Vanguard's empirical finding.
    hist = deploy.compare_lump_vs_staged(12_000, 12, expected_return=0.10, vol=0.16, cash_rate=0.03)
    assert 0.6 < hist.prob_lump_wins < 0.8
    assert hist.prob_lump_wins > c.prob_lump_wins
    assert c.staged_worst_drop_gbp == pytest.approx(1000 * 0.20)
    assert c.staged_p05_gbp > c.lump_p05_gbp  # staging cuts the bad tail
    again = deploy.compare_lump_vs_staged(12_000, 12, expected_return=0.07, vol=0.18, cash_rate=0.04)
    assert again.prob_lump_wins == c.prob_lump_wins  # seeded


def test_staged_schedule_sums_exactly():
    s = deploy.staged_schedule(1000, 3)
    assert sum(x["amount_gbp"] for x in s) == pytest.approx(1000)
    assert [x["week"] for x in s] == [0, 4, 8]


# ── UK tax ───────────────────────────────────────────────────────────────────

def test_tax_year_boundaries():
    assert uk_tax.tax_year(date(2026, 4, 5)) == (date(2025, 4, 6), date(2026, 4, 5))
    assert uk_tax.tax_year_label(date(2026, 4, 6)) == "2026/27"
    assert uk_tax.days_to_year_end(date(2027, 3, 31)) == 5


def test_cgt_and_tax_free_sale():
    assert uk_tax.cgt_due(5_000, band="higher") == pytest.approx(2_000 * 0.24)
    assert uk_tax.cgt_due(5_000, other_gains_gbp=4_000, band="basic") == pytest.approx(5_000 * 0.18)
    assert uk_tax.cgt_due(-1_000) == 0.0
    # £10k holding with a £5k gain: half of every £ sold is gain → £6k uses the £3k exemption.
    assert uk_tax.tax_free_sale_gbp(10_000, 5_000) == pytest.approx(6_000)
    assert uk_tax.tax_free_sale_gbp(10_000, -500) == 10_000
    assert uk_tax.isa_headroom(14_500) == 5_500


def test_placement_gives_the_isa_to_the_biggest_tax_drag():
    res = uk_tax.place_buys(
        [
            {"ticker": "KO", "amount_gbp": 3_000, "dividend_yield": 0.03, "expected_growth": 0.04},
            {"ticker": "NVDA", "amount_gbp": 3_000, "dividend_yield": 0.0, "expected_growth": 0.25},
            {"ticker": "3LTS", "amount_gbp": 1_000, "isa_only": True},
            {"ticker": "RGTI", "amount_gbp": 500, "expected_growth": 0.5, "speculative": True},
        ],
        isa_cash_gbp=1_000, allowance_left_gbp=4_000,
    )
    by = {i.ticker: i for i in res.items}
    assert by["3LTS"].account == "stocks_isa"
    assert by["NVDA"].account == "stocks_isa"
    assert by["KO"].account == "split" and by["KO"].isa_gbp == pytest.approx(1_000)
    assert by["RGTI"].account == "invest"
    assert res.needs_isa_deposit_gbp == pytest.approx(4_000)
