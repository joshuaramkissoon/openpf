from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Conviction = Literal["low", "medium", "high"]
AccountKind = Literal["invest", "stocks_isa"]
PlayStatus = Literal["draft", "active", "paused", "closed"]
LegRole = Literal["core", "satellite", "hedge"]
RuleType = Literal["price_below", "drawdown_pct", "weight_above", "date_after"]


# ── API inputs ───────────────────────────────────────────────────────────────

class PlayDraftRequest(BaseModel):
    idea: str = Field(min_length=3, max_length=4000)
    budget_gbp: float | None = Field(default=None, gt=0)
    conviction: Conviction | None = None
    horizon_months: int | None = Field(default=None, ge=1, le=120)
    account_kind: AccountKind | None = None
    source_session_id: str | None = None


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


class PlayDraftOut(BaseModel):
    title: str = Field(max_length=80)
    thesis: str = Field(description="Markdown, 2-4 short paragraphs")
    legs: list[DraftLeg] = Field(min_length=2, max_length=8)
    kill_criteria: list[DraftKillCriterion] = Field(min_length=2, max_length=6)
    what_would_change_my_mind: str
    suggested_conviction: Conviction = "medium"
    benchmark: str = "SPY"
    key_risks: list[str] = Field(default_factory=list, max_length=5)

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

    @field_validator("kill_criteria")
    @classmethod
    def _machine_checkable(cls, items: list[DraftKillCriterion]) -> list[DraftKillCriterion]:
        if sum(1 for c in items if c.rule is not None) < 2:
            raise ValueError("at least 2 kill criteria must carry a machine-checkable rule")
        return items


class PlayReviewOut(BaseModel):
    verdict: Literal["on_track", "watch", "broken"]
    notes: str = Field(max_length=1200)
