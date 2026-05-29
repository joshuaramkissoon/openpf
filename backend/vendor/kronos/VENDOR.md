# Vendored: Kronos

This directory contains a vendored copy of the model code from the
**Kronos** financial K-line foundation model.

- **Upstream:** https://github.com/shiyu-coder/Kronos
- **License:** MIT (see `LICENSE` in this directory)
- **Pinned commit:** `67b630e67f6a18c9e9be918d9b4337c960db1e9a`
- **Vendored:** 2026-05-29

## What's included

Only the upstream `model/` package is vendored — it's all that's needed
for inference:

| File | Source |
|------|--------|
| `kronos.py` | `model/kronos.py` (upstream) |
| `module.py` | `model/module.py` (upstream, unchanged) |
| `__init__.py` | rewritten thin re-export shim |

## Local modifications

`kronos.py` has exactly one change from upstream: the import

```python
import sys
sys.path.append("../")
from model.module import *
```

was replaced with a relative import

```python
from .module import *
```

so the package is self-contained under `backend/vendor/kronos` and does
not depend on `sys.path` manipulation. No other lines were modified.

## Model weights

Weights are **not** vendored. They are pulled from Hugging Face on first
use via `from_pretrained`:

- Tokenizer: `NeoQuasar/Kronos-Tokenizer-base`
- Model: `NeoQuasar/Kronos-base` (default), `Kronos-small`, or `Kronos-mini`

## Updating

To refresh against upstream:

```bash
git clone --depth 1 https://github.com/shiyu-coder/Kronos.git /tmp/kronos
cp /tmp/kronos/model/kronos.py  backend/vendor/kronos/kronos.py
cp /tmp/kronos/model/module.py  backend/vendor/kronos/module.py
# re-apply the single relative-import edit in kronos.py, bump the commit above
```
