"""Map execution/broker exceptions to a stable, UI-friendly error taxonomy.

Every execution entry point (intent execute, order cancel, exec-key test) funnels
its exceptions through ``classify_t212_error`` so the frontend gets a typed
``{code, message, meta}`` envelope it can route to a specific toast — instead of a
raw stack-y string. Kept dependency-free of the service layer (classifies by
exception type from ``t212_client`` + message text) so it can be imported anywhere
without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.t212_client import T212AuthError, T212Error, T212RateLimitError

# Error codes the frontend switches on. Keep in sync with the TS union in
# frontend/src/api/orders.ts.
CODE_INSUFFICIENT_FUNDS = "insufficient_funds"
CODE_IP_RESTRICTED = "ip_restricted"
CODE_AUTH_FAILED = "auth_failed"
CODE_RATE_LIMITED = "rate_limited"
CODE_RISK_BLOCKED = "risk_blocked"
CODE_VALIDATION = "validation"
CODE_BROKER_ERROR = "broker_error"

# HTTP status per code: client-actionable issues are 400; upstream/broker issues 502.
_STATUS_BY_CODE = {
    CODE_INSUFFICIENT_FUNDS: 400,
    CODE_RISK_BLOCKED: 400,
    CODE_VALIDATION: 400,
    CODE_IP_RESTRICTED: 502,
    CODE_AUTH_FAILED: 502,
    CODE_RATE_LIMITED: 429,
    CODE_BROKER_ERROR: 502,
}

_INSUFFICIENT_HINTS = (
    "insufficient",
    "not enough",
    "no free funds",
    "free cash",
    "not_enough_cash",
)

_RISK_HINTS = (
    "risk-guard",
    "duplicate-order",
    "daily rail",
    "rail block",
    "concentration",
    "max total exposure",
    "max open position",
    "max single",
    "max daily",
    "exceeds",
    "loss limit",
    "profit target",
)


@dataclass
class ClassifiedError:
    code: str
    message: str
    meta: dict = field(default_factory=dict)

    @property
    def status_code(self) -> int:
        return _STATUS_BY_CODE.get(self.code, 400)

    def as_detail(self) -> dict:
        return {"code": self.code, "message": self.message, "meta": self.meta}


def classify_t212_error(exc: Exception, *, account_kind: str | None = None) -> ClassifiedError:
    """Classify an exception raised during an execution/broker operation."""
    meta: dict = {}
    if account_kind:
        meta["account_kind"] = account_kind

    text = (str(exc) or exc.__class__.__name__).strip()
    low = text.lower()
    status = getattr(exc, "status_code", None)

    if isinstance(exc, T212RateLimitError):
        return ClassifiedError(
            CODE_RATE_LIMITED,
            "Trading 212 rate limit hit — retry in a few seconds.",
            meta,
        )

    # Insufficient funds wins over the generic risk-guard bucket: our cash guard
    # raises "risk-guard: insufficient available cash", and T212 itself returns a
    # body mentioning insufficient funds. Either way the user message is the same.
    if any(h in low for h in _INSUFFICIENT_HINTS):
        suffix = f" in {account_kind}" if account_kind else ""
        return ClassifiedError(
            CODE_INSUFFICIENT_FUNDS,
            f"Not enough available cash{suffix} for this order.",
            meta,
        )

    if isinstance(exc, T212AuthError) or status in (401, 403):
        if status == 403 or ("ip" in low and "restrict" in low):
            return ClassifiedError(
                CODE_IP_RESTRICTED,
                "Execution key blocked — your machine's IP may have changed. "
                "Update the key's IP allowlist in Trading 212 (or paste a fresh key) and re-test.",
                {**meta, "status": status},
            )
        return ClassifiedError(
            CODE_AUTH_FAILED,
            "Trading 212 rejected the key — check the key/secret, account type, and demo/live setting.",
            {**meta, "status": status},
        )

    if any(h in low for h in _RISK_HINTS):
        return ClassifiedError(CODE_RISK_BLOCKED, text, meta)

    if any(
        k in low
        for k in ("invalid", "must be", "not found", "missing", "not configured", "no execution key", "is disabled")
    ):
        return ClassifiedError(CODE_VALIDATION, text, meta)

    if isinstance(exc, T212Error):
        return ClassifiedError(CODE_BROKER_ERROR, text, meta)

    # Unknown — surface the text but treat as a broker-side problem.
    return ClassifiedError(CODE_BROKER_ERROR, text, meta)
