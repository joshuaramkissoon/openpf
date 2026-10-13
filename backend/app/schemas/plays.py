from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Conviction = Literal["low", "medium", "high"]
AccountKind = Literal["invest", "stocks_isa", "crypto"]
PlayStatus = Literal["draft", "active", "paused", "closed"]
LegRole = Literal["core", "satellite", "hedge"]
RuleType = Literal["price_below", "drawdown_pct", "weight_above", "date_after"]


# ── API inputs ───────────────────────────────────────────────────────────────

class PlayDraftRequest(BaseModel):
    idea: str = Field(default="", max_length=8000)  # friendly checks in plays_service.idea_problem
    budget_gbp: float | None = Field(default=None, gt=0)
    conviction: Conviction | None = None
    horizon_months: int | None = Field(default=None, ge=1, le=120)
    account_kind: AccountKind | None = None
    source_session_id: str | None = None
    # Names the user wants in (must-haves) or out: market symbols or company names.
    include: list[str] = Field(default_factory=list, max_length=12)
    exclude: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("include", "exclude")
    @classmethod
    def _clean_names(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for x in v:
            x = " ".join(str(x).split())[:60]
            if x and x.lower() not in {o.lower() for o in out}:
                out.append(x)
        return out


class PlayLegIn(BaseModel):
    ticker: str
    instrument_code: str | None = None
    name: str = ""
    role: LegRole = "core"
    target_weight: float = Field(ge=0, le=1)
    rationale: str = ""
    currency: str | None = None


class KillRule(BaseModel):
    type: RuleType
    ticker: str | None = None
    value: float | str


class KillCriterion(BaseModel):
    text: str
    rule: KillRule | None = None


class PlayUpdate(BaseModel):
    title: str | None = None
    thesis: str | None = None
    status: PlayStatus | None = None
    conviction: Conviction | None = None
    horizon_months: int | None = Field(default=None, ge=1, le=120)
    account_kind: AccountKind | None = None
    budget_gbp: float | None = Field(default=None, ge=0)
    legs: list[PlayLegIn] | None = None
    kill_criteria: list[KillCriterion] | None = None
    what_would_change_my_mind: str | None = None
    # Re-run the deterministic sizing (tranches/max loss) after edits.
    resize: bool = False


class PlayCreate(BaseModel):
    title: str
    thesis: str = ""
    conviction: Conviction = "medium"
    horizon_months: int = Field(default=12, ge=1, le=120)
    account_kind: AccountKind = "stocks_isa"
    budget_gbp: float | None = Field(default=None, ge=0)
    legs: list[PlayLegIn] = Field(default_factory=list)
    kill_criteria: list[KillCriterion] = Field(default_factory=list)
    what_would_change_my_mind: str = ""


class StageRequest(BaseModel):
    tranche_index: int | None = Field(default=None, ge=0)


# ── Book / ownership / rings ─────────────────────────────────────────────────

class AssignOwnerRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    # 'core' | 'unsorted' | a play id
    owner: str = Field(min_length=1, max_length=64)
    # Omit to move the holding in every account that holds it.
    account_kind: AccountKind | None = None


class RingItemRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    why: str = Field(default="", max_length=500)
    name: str = Field(default="", max_length=120)


class PromoteBenchRequest(BaseModel):
    weight: float = Field(default=0.1, gt=0, le=0.5)


class SuggestRequest(BaseModel):
    use_model: bool = True


class AcceptSuggestionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    tickers: list[str] | None = None
    kind: Literal["play", "core", "existing"] | None = None


# ── LLM draft output (validated; one retry on failure) ───────────────────────

class DraftLeg(BaseModel):
    ticker: str = Field(description="Yahoo Finance symbol, e.g. CCJ or NUCG.L")
    name: str
    role: LegRole
    weight: float = Field(gt=0, le=1, description="Weight within the play; all legs sum to 1")
    rationale: str
    currency: str | None = Field(default=None, description="Listing currency: USD, GBP, GBX or EUR")


class DraftKillCriterion(BaseModel):
    text: str
    rule: KillRule | None = None


class LeftOut(BaseModel):
    ticker: str = Field(description="A name the user asked to include that is not in legs")
    reason: str = Field(description="One sentence: why it's not in the basket (e.g. not on Trading 212)")


class PlayDraftOut(BaseModel):
    # Set (with legs/kill_criteria empty) when the idea isn't an investable theme.
    unclear: str | None = Field(default=None, description="Only when the idea can't be drafted: one friendly sentence why")
    title: str = Field(default="", max_length=80)
    thesis: str = Field(default="", description="Markdown, 2-4 short paragraphs")
    legs: list[DraftLeg] = Field(default_factory=list, max_length=8)
    kill_criteria: list[DraftKillCriterion] = Field(default_factory=list, max_length=6)
    what_would_change_my_mind: str = ""
    suggested_conviction: Conviction = "medium"
    benchmark: str = "SPY"
    key_risks: list[str] = Field(default_factory=list, max_length=5)
    left_out: list[LeftOut] = Field(default_factory=list, max_length=12,
                                    description="Only for must-have names you could not keep, with why")

    @model_validator(mode="after")
    def _complete_unless_unclear(self) -> "PlayDraftOut":
        if self.unclear and self.unclear.strip():
            return self
        if not self.title.strip():
            raise ValueError("title is required")
        if len(self.legs) < 2:
            raise ValueError("at least 2 legs are required")
        if len(self.kill_criteria) < 2:
            raise ValueError("at least 2 kill criteria are required")
        if sum(1 for c in self.kill_criteria if c.rule is not None) < 2:
            raise ValueError("at least 2 kill criteria must carry a machine-checkable rule")
        return self

    @field_validator("legs")
    @classmethod
    def _unique_tickers(cls, legs: list[DraftLeg]) -> list[DraftLeg]:
        seen: set[str] = set()
        for leg in legs:
            key = leg.ticker.strip().upper()
            if key in seen:
                raise ValueError(f"duplicate ticker {key}")
            seen.add(key)
        return legs


class PlayReviewOut(BaseModel):
    verdict: Literal["on_track", "watch", "broken"]
    notes: str = Field(max_length=1200)


# ── Rebalance ────────────────────────────────────────────────────────────────

class RebalancePreviewRequest(BaseModel):
    # Unsaved target weights from the basket editor ({ticker: weight}); omit to use the saved ones.
    weights: dict[str, float] | None = None
    use_cash: bool = False


class RebalanceProposeRequest(BaseModel):
    use_cash: bool = False


class RebalanceUserAction(BaseModel):
    """Proof a person pressed "Place them myself" in the review step. Agents have
    no route here at all (no MCP tool; their hooks block the endpoint)."""

    kind: Literal["manual_trade"]
    reviewed: Literal[True]


class RebalancePlaceRequest(BaseModel):
    user_action: RebalanceUserAction
    live_confirmed: bool = False
    use_cash: bool = False
