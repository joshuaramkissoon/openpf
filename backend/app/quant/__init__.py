"""Quantitative analytics toolkit -- pure computation, no I/O.

Modules
* ``indicators`` / ``series`` — technical indicators for charts.
* ``returns`` — return construction, FX conversion, alignment.
* ``covariance`` — sample and Ledoit-Wolf shrinkage covariance.
* ``risk`` — vol, drawdowns, VaR/ES, Euler risk contributions.
* ``sizing`` — vol targeting, inverse-vol, risk parity (ERC), fractional Kelly.
* ``clustering`` — correlation clusters, Hierarchical Risk Parity.
* ``exposure`` — beta / factor regression, concentration, diversification ratio.
* ``rebalance`` — band rebalancing with a turnover budget.
* ``deploy`` — lump sum vs staged entry.
* ``uk_tax`` — ISA / GIA placement, CGT allowance.

The I/O glue (book, prices, FX, rails) lives in ``app.services.quant_service``;
Archie reaches all of it through the ``quant`` MCP server.
"""

from ._helpers import MIN_OBS, QuantDataError
from .indicators import atr, bollinger_bands, ema, macd, rsi, sma
from .portfolio import concentration_hhi, correlation_matrix, portfolio_beta
from .risk import (
    annualized_volatility,
    drawdown_stats,
    ewma_volatility,
    expected_shortfall,
    max_drawdown,
    portfolio_volatility,
    risk_contributions,
    sharpe_ratio,
    sortino_ratio,
    tail_contributions,
    value_at_risk,
)
from .series import indicator_to_points, macd_to_points

__all__ = [
    "MIN_OBS",
    "QuantDataError",
    # indicators
    "rsi",
    "sma",
    "ema",
    "macd",
    "bollinger_bands",
    "atr",
    # risk
    "annualized_volatility",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    "value_at_risk",
    "expected_shortfall",
    "tail_contributions",
    "drawdown_stats",
    "ewma_volatility",
    "portfolio_volatility",
    "risk_contributions",
    # portfolio
    "concentration_hhi",
    "portfolio_beta",
    "correlation_matrix",
    # series
    "indicator_to_points",
    "macd_to_points",
]
