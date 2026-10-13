"""Viz spec schema (see ``app/views_sdk/VIZ_SPEC.md``).

A spec is declarative JSON — layout plus blocks bound to whitelisted, read-only
data queries — rendered natively by the frontend's viz library. Validation here
is the gate for everything that stores or serves a spec (views, presets, the
spec builder, "pin as view" from chat): unknown block types, sources,
transforms, formats or oversize specs are rejected with a message that names the
problem, so the builder can repair it in one pass.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.services.views_data import SOURCES

logger = logging.getLogger(__name__)

MAX_SPEC_BYTES = 32 * 1024
MAX_BLOCKS = 12
MAX_QUERIES = 8
MAX_STEPS = 12

Format = Literal["money", "moneyCompact", "signedMoney", "pct", "signedPct", "num", "int", "date", "text"]
Width = Literal["full", "half", "third", "two-thirds"]
Agg = Literal["sum", "avg", "min", "max", "count"]

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
_FROM_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}(\.[A-Za-z_][A-Za-z0-9_]{0,39}){0,3}$")
_ICON_RE = re.compile(r"^[a-z0-9-]{1,40}$")
ENRICH_FIELDS = ("sector", "industry", "country", "asset_type", "play", "theme", "play_id", "dividend_yield", "ex_dividend_date")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


FieldName = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")]


class Query(_Strict):
    source: str
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in SOURCES:
            raise ValueError(f"unknown source '{v}'. Available: {', '.join(SOURCES)}")
        return v

    @field_validator("params")
    @classmethod
    def _small(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v, default=str)) > 2000:
            raise ValueError("query params are too large")
        return v


# ── transforms ──────────────────────────────────────────────────────────
class FilterArgs(_Strict):
    field: FieldName
    op: Literal["=", "!=", ">", ">=", "<", "<=", "in", "notin", "contains"] = "="
    value: Any = None


class SortArgs(_Strict):
    by: FieldName
    dir: Literal["asc", "desc"] = "desc"


class TopArgs(_Strict):
    n: int = Field(ge=1, le=100)
    other: bool = False


class ComputeArgs(_Strict):
    as_: FieldName = Field(alias="as")
    op: Literal["share", "ratio", "diff", "product", "abs", "neg", "scale"]
    field: FieldName | None = None
    a: FieldName | None = None
    b: FieldName | None = None
    by: float | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _args(self) -> "ComputeArgs":
        if self.op in ("ratio", "diff", "product") and not (self.a and self.b):
            raise ValueError(f"compute '{self.op}' needs 'a' and 'b'")
        if self.op in ("share", "abs", "neg", "scale") and not self.field:
            raise ValueError(f"compute '{self.op}' needs 'field'")
        if self.op == "scale" and self.by is None:
            raise ValueError("compute 'scale' needs a number 'by'")
        return self


class MonthArgs(_Strict):
    field: FieldName
    sum: FieldName
    last: int = Field(default=12, ge=1, le=60)


class Step(_Strict):
    filter: FilterArgs | None = None
    enrich: list[Literal["sector", "industry", "country", "asset_type", "play", "theme", "play_id", "dividend_yield", "ex_dividend_date"]] | None = None
    groupBy: FieldName | None = None
    sort: SortArgs | None = None
    top: TopArgs | None = None
    compute: ComputeArgs | None = None
    month: MonthArgs | None = None

    @model_validator(mode="after")
    def _one(self) -> "Step":
        keys = [k for k in ("filter", "enrich", "groupBy", "sort", "top", "compute", "month") if getattr(self, k) is not None]
        if len(keys) != 1:
            raise ValueError("each transform step must have exactly one of filter, enrich, groupBy, sort, top, compute, month")
        return self


class Measure(_Strict):
    field: FieldName | None = None
    agg: Agg | None = None
    per: "Measure | None" = None
    from_: str | None = Field(default=None, alias="from")
    transform: list[Step] | None = Field(default=None, max_length=MAX_STEPS)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _shape(self) -> "Measure":
        if not self.field and self.agg != "count":
            raise ValueError("a measure needs 'field' (or agg 'count')")
        if self.from_ is not None and not _FROM_RE.match(self.from_):
            raise ValueError(f"'from' must look like 'query.path', got '{self.from_}'")
        return self


# ── blocks ──────────────────────────────────────────────────────────────
class _Block(_Strict):
    title: str | None = Field(default=None, max_length=60)
    subtitle: str | None = Field(default=None, max_length=160)
    width: Width = "full"
    from_: str | None = Field(default=None, alias="from")
    transform: list[Step] = Field(default_factory=list, max_length=MAX_STEPS)
    empty: str | None = Field(default=None, max_length=160)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("from_")
    @classmethod
    def _from(cls, v: str | None) -> str | None:
        if v is not None and not _FROM_RE.match(v):
            raise ValueError(f"'from' must look like 'query.path' (e.g. 'pf.positions'), got '{v}'")
        return v


class _Rows(_Block):
    """Blocks that need rows."""

    @model_validator(mode="after")
    def _needs_from(self):  # type: ignore[no-untyped-def]
        if not self.from_:
            raise ValueError(f"block '{getattr(self, 'type', '?')}' needs 'from' (e.g. 'pf.positions')")
        return self


class Trend(_Strict):
    """A sparkline under a number: one field over rows (e.g. hist.points → total)."""

    from_: str = Field(alias="from")
    field: FieldName
    x: FieldName = "date"

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("from_")
    @classmethod
    def _from(cls, v: str) -> str:
        if not _FROM_RE.match(v):
            raise ValueError(f"trend 'from' must look like 'query.path', got '{v}'")
        return v


class HeroBlock(_Block):
    type: Literal["hero"]
    label: str = Field(max_length=60)
    value: Measure
    format: Format = "money"
    delta: Measure | None = None
    deltaFormat: Format = "signedPct"
    caption: str | None = Field(default=None, max_length=200)
    trend: Trend | None = None


class MeterBlock(_Block):
    """A single amount against a limit (e.g. ISA allowance used)."""

    type: Literal["meter"]
    label: str = Field(max_length=60)
    value: Measure
    max: Measure | float
    format: Format = "money"
    mark: Measure | None = None  # optional 0–1 pace marker (e.g. share of the year gone)
    markLabel: str | None = Field(default=None, max_length=40)
    caption: str | None = Field(default=None, max_length=200)


class KpiItem(_Strict):
    label: str = Field(max_length=40)
    value: Measure
    format: Format = "money"
    delta: Measure | None = None
    deltaFormat: Format = "signedPct"
    hint: str | None = Field(default=None, max_length=80)


class KpisBlock(_Block):
    type: Literal["kpis"]
    items: list[KpiItem] = Field(min_length=1, max_length=5)


class Column(_Strict):
    field: FieldName
    label: str = Field(max_length=40)
    format: Format = "text"


# Extra per-row fields a chart carries into its tooltip and table view, so a
# separate table never has to repeat the chart's rows to show them.
Details = Annotated[list[Column], Field(max_length=6)]


class TreemapBlock(_Rows):
    type: Literal["treemap"]
    label: FieldName
    size: FieldName
    color: FieldName | None = None
    group: FieldName | None = None
    format: Format = "money"
    colorFormat: Format = "signedPct"
    details: Details | None = None


class AllocationBlock(_Rows):
    type: Literal["allocation"]
    label: FieldName
    value: FieldName
    style: Literal["bar", "donut"] = "bar"
    format: Format = "money"
    details: Details | None = None


class BarsBlock(_Rows):
    type: Literal["bars"]
    label: FieldName
    value: FieldName
    format: Format = "money"
    orientation: Literal["horizontal", "vertical"] = "horizontal"
    limit: int = Field(default=12, ge=1, le=40)
    secondary: FieldName | None = None
    secondaryFormat: Format = "signedPct"
    details: Details | None = None


class Series(_Strict):
    field: FieldName
    label: str = Field(max_length=40)


class TimeseriesBlock(_Rows):
    type: Literal["timeseries"]
    x: FieldName = "date"
    series: list[Series] = Field(min_length=1, max_length=4)
    style: Literal["area", "line"] = "area"
    format: Format = "moneyCompact"


class MultiplesBlock(_Rows):
    type: Literal["multiples"]
    label: FieldName
    ticker: FieldName = "yf"
    range: Literal["5d", "1mo", "3mo", "6mo", "1y", "ytd"] = "3mo"
    value: FieldName | None = None
    valueFormat: Format = "signedPct"


class HeatgridBlock(_Rows):
    type: Literal["heatgrid"]
    mode: Literal["pivot", "returns", "correlation"] = "pivot"
    rows: FieldName | None = None
    cols: FieldName | None = None
    value: FieldName | None = None
    label: FieldName | None = None
    ticker: FieldName = "yf"
    days: int = Field(default=10, ge=3, le=30)
    range: Literal["3mo", "6mo", "1y"] = "6mo"
    format: Format = "money"

    @model_validator(mode="after")
    def _mode(self) -> "HeatgridBlock":
        if self.mode == "pivot" and not (self.rows and self.cols and self.value):
            raise ValueError("heatgrid mode 'pivot' needs rows, cols and value")
        if self.mode != "pivot" and not self.label:
            raise ValueError(f"heatgrid mode '{self.mode}' needs 'label'")
        return self


class TableBlock(_Rows):
    type: Literal["table"]
    columns: list[Column] = Field(min_length=1, max_length=8)
    limit: int = Field(default=25, ge=1, le=200)


class NoteBlock(_Block):
    type: Literal["note"]
    text: str = Field(min_length=1, max_length=600)


Block = Annotated[
    Union[HeroBlock, MeterBlock, KpisBlock, TreemapBlock, AllocationBlock, BarsBlock, TimeseriesBlock, MultiplesBlock, HeatgridBlock, TableBlock, NoteBlock],
    Field(discriminator="type"),
]


class Controls(_Strict):
    account: bool = False


class VizSpec(_Strict):
    version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=160)
    icon: str | None = None
    controls: Controls | None = None
    data: dict[str, Query] = Field(default_factory=dict)
    blocks: list[Block] = Field(min_length=1, max_length=MAX_BLOCKS)

    @field_validator("icon")
    @classmethod
    def _icon(cls, v: str | None) -> str | None:
        return v if v and _ICON_RE.match(v) else None

    @field_validator("data")
    @classmethod
    def _queries(cls, v: dict[str, Query]) -> dict[str, Query]:
        if len(v) > MAX_QUERIES:
            raise ValueError(f"at most {MAX_QUERIES} queries")
        for name in v:
            if not _NAME_RE.match(name):
                raise ValueError(f"query name '{name}' must be lowercase letters/digits/underscore")
        return v

    @model_validator(mode="after")
    def _refs(self) -> "VizSpec":
        names = set(self.data) | {"pf"}

        def check(ref: str | None, where: str) -> None:
            if ref and ref.split(".")[0] not in names:
                raise ValueError(f"{where}: query '{ref.split('.')[0]}' is not defined in 'data'")

        for i, b in enumerate(self.blocks):
            where = f"block {i + 1} ({b.type})"
            check(b.from_, where)
            measures: list[Measure] = []
            if isinstance(b, HeroBlock):
                measures = [m for m in (b.value, b.delta) if m]
                if b.trend:
                    check(b.trend.from_, where)
            elif isinstance(b, MeterBlock):
                measures = [m for m in (b.value, b.mark, b.max if isinstance(b.max, Measure) else None) if m]
            elif isinstance(b, KpisBlock):
                measures = [m for it in b.items for m in (it.value, it.delta) if m]
            for m in measures:
                check(m.from_, where)
                if not (m.from_ or b.from_):
                    raise ValueError(f"{where}: a measure needs 'from' (on the measure or the block)")
        return self


def _explain(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors()[:8]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if not str(p).startswith("function-"))
        msg = str(err.get("msg", "invalid")).removeprefix("Value error, ")
        out.append(f"{loc}: {msg}" if loc else msg)
    return out


def validate_spec(raw: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Return ``(normalised_spec, [])`` or ``(None, problems)``."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            return None, [f"not valid JSON: {exc}"]
    if not isinstance(raw, dict):
        return None, ["a spec must be a JSON object"]
    size = len(json.dumps(raw, default=str).encode())
    if size > MAX_SPEC_BYTES:
        return None, [f"spec is {size // 1024}KB; the limit is {MAX_SPEC_BYTES // 1024}KB"]
    try:
        spec = VizSpec.model_validate(raw)
    except ValidationError as exc:
        return None, _explain(exc)
    out = spec.model_dump(by_alias=True, exclude_none=True)
    tidied, notes = tidy_spec(out)
    for note in notes:
        logger.info("viz spec '%s': %s", out.get("title"), note)
    return tidied, []


# ── tidy: warnings + auto-fixes for redundant or dishonest layouts ──────
_CHARTS = ("bars", "allocation", "treemap")
# Grouping dimensions a title may name ("… by sector").
_DIMENSIONS = {
    "sector": "sector",
    "industry": "industry",
    "country": "country",
    "theme": "theme",
    "play": "play",
    "account": "account",
    "asset_type": "asset type",
    "exposure_currency": "currency",
    "instrument_currency": "currency",
}
_DIM_WORDS = r"(sectors?|industry|industries|country|countries|themes?|plays?|accounts?|asset types?|currency|currencies)"
_PLURAL = {"industry": "industries", "country": "countries", "currency": "currencies"}


def _group_key(block: dict[str, Any]) -> str | None:
    """The field a block's rows are grouped by (last groupBy step), if any."""
    key = None
    for step in block.get("transform") or []:
        if isinstance(step, dict) and "groupBy" in step:
            key = step["groupBy"]
    return key


def _duplicates(table: dict[str, Any], chart: dict[str, Any]) -> bool:
    """True when ``table`` lists the same rows ``chart`` already draws."""
    if table.get("from") != chart.get("from"):
        return False
    tkey, ckey = _group_key(table), _group_key(chart)
    if tkey or ckey:
        # A table grouped by X repeats a chart grouped by X, or a treemap whose
        # tiles nest under X headers.
        return bool(tkey) and (tkey == ckey or (chart.get("type") == "treemap" and chart.get("group") == tkey))
    label = chart.get("label")
    return bool(label) and any(c.get("field") == label for c in table.get("columns") or [])


def spec_warnings(spec: dict[str, Any]) -> list[str]:
    """Layout problems the tidy pass fixes (also useful as builder feedback)."""
    return tidy_spec(spec)[1]


def tidy_spec(spec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Fix a valid spec's layout smells. Returns ``(spec, notes)``.

    * A ``table`` that repeats a chart's rows (same source and grouping) is
      dropped; its extra columns (value, weight, holdings…) move into the
      chart's ``details`` so they show in the tooltip and the table view.
    * A chart titled "… by theme" that actually groups by sector (or any other
      mismatch) is retitled to name the dimension it really uses.
    """
    blocks = [dict(b) for b in spec.get("blocks") or []]
    notes: list[str] = []
    keep: list[dict[str, Any]] = []
    charts = [b for b in blocks if b.get("type") in _CHARTS]
    for b in blocks:
        if b.get("type") == "table":
            twin = next((c for c in charts if _duplicates(b, c)), None)
            if twin is not None:
                shown = {twin.get(k) for k in ("label", "value", "size", "color", "secondary", "group")}
                details = list(twin.get("details") or [])
                have = {d["field"] for d in details}
                for col in b.get("columns") or []:
                    if col["field"] not in shown and col["field"] not in have and len(details) < 6:
                        details.append(dict(col))
                        have.add(col["field"])
                if details:
                    twin["details"] = details
                notes.append(
                    f"dropped table '{b.get('title') or 'untitled'}': it repeats the rows of chart "
                    f"'{twin.get('title') or twin.get('type')}' (its extra columns moved into the chart's tooltip and table view)"
                )
                continue
        keep.append(b)
    for b in keep:
        title = b.get("title")
        key = _group_key(b) or (b.get("group") if b.get("type") == "treemap" else None)
        if not key and b.get("type") in _CHARTS and b.get("label") in _DIMENSIONS:
            key = b.get("label")
        if not title or key not in _DIMENSIONS:
            continue
        actual = _DIMENSIONS[key]
        m = re.search(rf"\b(by|per|across)\s+{_DIM_WORDS}\b", title, re.I)
        if not m:
            continue
        said = m.group(2).lower()
        if said.startswith(actual[:5]):
            continue
        plural = said.endswith("s") and not said.endswith("ss")
        word = _PLURAL.get(actual, actual + "s") if plural else actual
        fixed = title[: m.start(2)] + word + title[m.end(2) :]
        notes.append(f"retitled '{title}' to '{fixed}': the chart groups by {actual}")
        b["title"] = fixed
    # The page title too, when every grouped block agrees on one dimension.
    out = {**spec, "blocks": keep}
    used = {
        _DIMENSIONS[k]
        for b in keep
        for k in (_group_key(b), b.get("group") if b.get("type") == "treemap" else None)
        if k in _DIMENSIONS
    }
    page = spec.get("title") or ""
    m = re.search(rf"\b(by|per|across)\s+{_DIM_WORDS}\b", page, re.I)
    if m and len(used) == 1:
        actual = next(iter(used))
        said = m.group(2).lower()
        if not said.startswith(actual[:5]):
            plural = said.endswith("s") and not said.endswith("ss")
            word = _PLURAL.get(actual, actual + "s") if plural else actual
            out["title"] = page[: m.start(2)] + word + page[m.end(2) :]
            notes.append(f"retitled the page '{page}' to '{out['title']}'")
    if len(keep) == len(blocks) and not notes:
        return spec, []
    return out, notes
