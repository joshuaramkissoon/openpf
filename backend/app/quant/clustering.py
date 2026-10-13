"""Correlation clustering and Hierarchical Risk Parity.  Pure numpy, no I/O.

* Distance ``d_ij = sqrt((1 - rho_ij) / 2)`` (Mantegna 1999): 0 for perfectly
  correlated, ~0.71 for uncorrelated, 1 for perfectly anti-correlated.
* HRP: López de Prado (2016), "Building Diversified Portfolios that Outperform
  Out of Sample", JPM — tree clustering, quasi-diagonalisation, recursive
  bisection with inverse-variance splits. It never inverts the covariance
  matrix, so it is robust when assets are highly correlated (a typical
  concentrated tech book), which is exactly where mean-variance breaks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from ._helpers import QuantDataError

Linkage = Literal["single", "average", "complete"]


def correlation_distance(corr: np.ndarray) -> np.ndarray:
    c = np.clip(np.asarray(corr, dtype="float64"), -1.0, 1.0)
    d = np.sqrt((1.0 - c) / 2.0)
    np.fill_diagonal(d, 0.0)
    return d


@dataclass
class _Node:
    members: list[int]
    left: "_Node | None" = None
    right: "_Node | None" = None
    height: float = 0.0


def agglomerate(dist: np.ndarray, method: Linkage = "single") -> _Node:
    """Agglomerative clustering on a distance matrix; returns the root node.

    Ties break on the lowest index so results are deterministic. O(n^3), fine
    for a retail book (n < 100).
    """
    d = np.asarray(dist, dtype="float64")
    n = d.shape[0]
    if n == 0:
        raise QuantDataError("cannot cluster zero assets")
    clusters: list[_Node] = [_Node([i]) for i in range(n)]

    def link(a: _Node, b: _Node) -> float:
        block = d[np.ix_(a.members, b.members)]
        if method == "single":
            return float(block.min())
        if method == "complete":
            return float(block.max())
        return float(block.mean())

    while len(clusters) > 1:
        best = (np.inf, 0, 1)
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                h = link(clusters[i], clusters[j])
                if h < best[0] - 1e-15:
                    best = (h, i, j)
        h, i, j = best
        a, b = clusters[i], clusters[j]
        merged = _Node(a.members + b.members, a, b, h)
        clusters = [c for k, c in enumerate(clusters) if k not in (i, j)] + [merged]
    return clusters[0]


def leaf_order(node: _Node) -> list[int]:
    """Quasi-diagonal order: leaves left-to-right, so similar assets sit together."""
    if node.left is None or node.right is None:
        return list(node.members)
    return leaf_order(node.left) + leaf_order(node.right)


def _cut(node: _Node, height: float) -> list[list[int]]:
    if node.left is None or node.right is None or node.height <= height:
        return [sorted(node.members)]
    return _cut(node.left, height) + _cut(node.right, height)


def correlation_clusters(corr: pd.DataFrame, min_corr: float = 0.6, method: Linkage = "average") -> list[list[str]]:
    """Groups of assets whose (average-linkage) correlation is at least ``min_corr``.

    Largest groups first; singletons included. "NVDA, AMD, TSM, ASML move as one
    bet" is the kind of answer this gives.
    """
    names = list(corr.index)
    root = agglomerate(correlation_distance(corr.to_numpy()), method)
    height = float(np.sqrt((1.0 - min_corr) / 2.0))
    groups = [sorted(names[i] for i in g) for g in _cut(root, height)]
    return sorted(groups, key=lambda g: (-len(g), g))


def _cluster_variance(cov: np.ndarray, idx: list[int]) -> float:
    sub = cov[np.ix_(idx, idx)]
    ivp = 1.0 / np.diag(sub)
    ivp = ivp / ivp.sum()
    return float(ivp @ sub @ ivp)


def hrp_weights(cov: pd.DataFrame, method: Linkage = "single") -> dict[str, float]:
    """Hierarchical Risk Parity weights (long-only, sum to 1).

    Uses the classic single-linkage tree and halves-of-the-ordered-list
    bisection from the original paper.
    """
    names = list(cov.index)
    s = cov.to_numpy(dtype="float64")
    if np.any(np.diag(s) <= 0):
        raise QuantDataError("HRP needs positive variances for every asset")
    sd = np.sqrt(np.diag(s))
    corr = s / np.outer(sd, sd)
    order = leaf_order(agglomerate(correlation_distance(corr), method))
    w = np.ones(len(names))
    stack = [order]
    while stack:
        items = stack.pop()
        if len(items) <= 1:
            continue
        half = len(items) // 2
        left, right = items[:half], items[half:]
        v_l, v_r = _cluster_variance(s, left), _cluster_variance(s, right)
        alpha = 1.0 - v_l / (v_l + v_r)
        w[left] *= alpha
        w[right] *= 1.0 - alpha
        stack.extend([left, right])
    w = w / w.sum()
    return {k: float(v) for k, v in zip(names, w)}
