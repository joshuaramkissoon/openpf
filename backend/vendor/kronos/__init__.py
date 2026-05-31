"""Vendored Kronos financial K-line foundation model.

Upstream: https://github.com/shiyu-coder/Kronos (MIT License)
Pinned commit: 67b630e67f6a18c9e9be918d9b4337c960db1e9a

Only the `model/` package (kronos.py, module.py) is vendored. The single
upstream import `from model.module import *` was changed to a relative
import so this package is self-contained. See VENDOR.md for details.
"""

from .kronos import Kronos, KronosPredictor, KronosTokenizer

__all__ = ["Kronos", "KronosPredictor", "KronosTokenizer"]
