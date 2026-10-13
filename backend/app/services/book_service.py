"""The book by play — who owns each holding, and what each play adds up to.

Every holding (per account) has exactly one owner:

* a **play** — the theme bet it belongs to; counts toward that play's size and P&L,
* **Core** — long-term / index / no-thesis holdings,
* **Unsorted** — not yet placed.

Owners are explicit (a ``HoldingOwner`` row the user, Archie or an accepted
suggestion wrote) or derived: a holding that sits in the basket of an active or
paused play belongs to the oldest such play; everything else is Unsorted.
The book therefore always sums to 100% with no double counting.

Each play then has three rings:

1. **Owned** — holdings it owns (size, P&L).
2. **Related** — holdings owned elsewhere that matter to the theme: any basket
   leg owned by another play/Core, plus ones added explicitly with a why.
   Shown dimmed, never counted in size; they feed *effective exposure*.
3. **Bench** — not-held candidates: unbought basket legs ("in plan") and ideas.

Nothing here places or proposes orders.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import HoldingOwner, Play, TradeIntent
from app.services import plays_service as ps

logger = logging.getLogger(__name__)

OWNER_KINDS = ("play", "core", "unsorted")
OWNING_STATUSES = {"draft", "active", "paused"}  # may own holdings explicitly
DERIVING_STATUSES = {"active", "paused"}  # claim basket holdings automatically
THEME_CONCENTRATION_WARN = 0.25  # one play above this share of the book is flagged
EFFECTIVE_CONCENTRATION_WARN = 0.35
SIZE_DRIFT_BAND = 0.3  # owned vs target size, ±30% before it's "drifted"

CORE_TITLE = "Core"
UNSORTED_TITLE = "Unsorted"


class OwnershipError(ValueError):
    """User-facing ownership / ring edit problem (message is shown as-is)."""


def _now() -> datetime:
    return datetime.utcnow()


def norm_ticker(t: str | None) -> str:
    return str(t or "").strip().upper()


def _pos_key(pos: dict[str, Any]) -> tuple[str, str]:
    return (str(pos.get("account_kind") or ""), norm_ticker(pos.get("ticker")))


def _ticker_matches(pos: dict[str, Any], ticker: str) -> bool:
    return ps._position_matches(pos, ps._leg_keys({"ticker": ticker}))


def _account_label(kind: str | None) -> str:
    return {"stocks_isa": "Stocks ISA", "invest": "Invest", "crypto": "Crypto"}.get(kind or "", kind or "account")


# ── Ownership resolution ─────────────────────────────────────────────────────

def owning_plays(db: Session) -> list[Play]:
    """Plays that can own holdings, oldest first (activation, then creation)."""
    rows = db.execute(select(Play).where(Play.status.in_(sorted(OWNING_STATUSES)))).scalars().all()
    return sorted(rows, key=lambda p: (p.activated_at or p.created_at or _now(), p.created_at or _now()))


def explicit_rows(db: Session) -> dict[tuple[str, str], HoldingOwner]:
    return {(r.account_kind, norm_ticker(r.ticker)): r for r in db.execute(select(HoldingOwner)).scalars().all()}


def _owner(kind: str, *, play: Play | None = None, source: str) -> dict[str, Any]:
    if kind == "play" and play is not None:
        return {"kind": "play", "play_id": play.id, "title": play.title, "status": play.status, "source": source}
    return {"kind": kind, "play_id": None, "title": CORE_TITLE if kind == "core" else UNSORTED_TITLE,
            "status": None, "source": source}


def resolve_owners(positions: list[dict[str, Any]], plays: list[Play],
                   rows: dict[tuple[str, str], HoldingOwner]) -> dict[tuple[str, str], dict[str, Any]]:
    """Effective owner per (account, ticker). ``source``: user | archie |
    suggestion | system (explicit) or basket | default (derived)."""
    by_id = {p.id: p for p in plays}
    deriving = [p for p in plays if p.status in DERIVING_STATUSES]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for pos in positions:
        key = _pos_key(pos)
        row = rows.get(key)
        if row is not None:
            if row.owner_kind == "play":
                play = by_id.get(row.play_id or "")
                # The play was closed or deleted — the holding is released.
                out[key] = _owner("play", play=play, source=row.source) if play else _owner("unsorted", source="system")
            elif row.owner_kind in ("core", "unsorted"):
                out[key] = _owner(row.owner_kind, source=row.source)
            else:
                out[key] = _owner("unsorted", source="system")
            continue
        acct = key[0]
        matches = [p for p in deriving if any(ps._position_matches(pos, ps._leg_keys(l)) for l in p.legs or [])]
        matches.sort(key=lambda p: 0 if p.account_kind == acct else 1)  # same-account play first, stable by age
        out[key] = _owner("play", play=matches[0], source="basket") if matches else _owner("unsorted", source="default")
    return out


def available_for_play(play: Play, positions: list[dict[str, Any]], owners: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    """Positions a play may count toward its size: those it owns, plus Unsorted ones
    (a draft adopts them on activation). Holdings owned by another play or Core don't count."""
    out = []
    for pos in positions:
        o = owners.get(_pos_key(pos)) or {}
        if o.get("kind") == "unsorted" or (o.get("kind") == "play" and o.get("play_id") == play.id):
            out.append(pos)
    return out


def owned_positions(db: Session, play: Play, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    owners = resolve_owners(positions, owning_plays(db), explicit_rows(db))
    return [p for p in positions if (owners.get(_pos_key(p)) or {}).get("play_id") == play.id]


# ── Needs-you cues (cheap: no market-data calls) ─────────────────────────────

_WEEK_RE = re.compile(r"week\s+(\d+)", re.I)


def _next_tranche(play: Play, today: date) -> dict[str, Any] | None:
    sizing = play.sizing or {}
    tranches = sizing.get("tranches") or []
    staged_idx = {s.get("index") for s in sizing.get("staged") or []}
    for i, t in enumerate(tranches):
        if i in staged_idx:
            continue
        due: date | None = None
        if i == 0:
            due = (play.activated_at or play.created_at or _now()).date()
        else:
            m = _WEEK_RE.search(str(t.get("trigger") or ""))
            if m and play.activated_at:
                due = play.activated_at.date() + timedelta(weeks=int(m.group(1)))
        return {"index": i, "label": t.get("label") or f"Tranche {i + 1}", "due": due.isoformat() if due else None,
                "is_due": bool(due and due <= today)}
    return None


def play_needs(play: Play, *, owned_value: float, total: float, positions: list[dict[str, Any]],
               pending: int, today: date | None = None) -> list[dict[str, Any]]:
    """What a play needs from the user, most urgent first.
    kinds: kill_tripped | kill_near | tranche_due | drift_over | drift_under | empty | awaiting | draft"""
    today = today or date.today()
    needs: list[dict[str, Any]] = []
    if play.status == "draft":
        return [{"kind": "draft", "tone": "info", "text": "Draft to review"}]
    if play.status != "active":
        return []

    owned_pct = owned_value / total if total else 0.0
    prices: dict[str, float] = {}
    for leg in play.legs or []:
        for pos in positions:
            if ps._position_matches(pos, ps._leg_keys(leg)) and pos.get("price"):
                prices[norm_ticker(leg.get("ticker"))] = float(pos["price"])
    ctx = {"prices": prices, "drawdown_pct": None, "weights": {"__play__": owned_pct} if owned_value > 0 else {}, "today": today}
    tripped, near = [], []
    for c in play.kill_criteria or []:
        st = ps.evaluate_rule(c.get("rule"), ctx)
        if st["state"] == "tripped":
            tripped.append(c.get("text") or st["detail"])
        elif st["state"] == "near":
            near.append(c.get("text") or st["detail"])
    # Short, crafted phrases only — the play page carries the detail.
    verdict = (play.review or {}).get("verdict")
    if tripped or verdict == "broken":
        n = max(len(tripped), 1)
        needs.append({"kind": "kill_tripped", "tone": "critical",
                      "text": "A kill rule tripped" if n == 1 else f"{n} kill rules tripped"})
    elif near or verdict == "watch":
        needs.append({"kind": "kill_near", "tone": "warn", "text": "A kill rule is close to tripping"})

    sizing = play.sizing or {}
    tranches = sizing.get("tranches") or []
    staged = sizing.get("staged") or []
    nxt = _next_tranche(play, today)
    if nxt and nxt["is_due"] and nxt["index"] > 0 and not pending:
        needs.append({"kind": "tranche_due", "tone": "warn", "text": f"Tranche {nxt['index'] + 1} of {len(tranches)} is due"})
    elif nxt and nxt["index"] == 0 and not pending:
        needs.append({"kind": "tranche_due", "tone": "warn", "text": "Starter tranche ready to stage"})

    if pending:
        needs.append({"kind": "awaiting", "tone": "info",
                      "text": f"{pending} order{'s' if pending != 1 else ''} to approve"})

    built = len(staged) >= len(tranches)
    target_pct = float(sizing.get("target_pct_of_book") or 0.0)
    if built and staged and owned_value <= 0 and not pending:
        needs.append({"kind": "empty", "tone": "warn", "text": "Nothing held any more"})
    elif built and target_pct > 0 and owned_value > 0:
        if owned_pct > target_pct * (1 + SIZE_DRIFT_BAND) and owned_pct - target_pct > 0.005:
            needs.append({"kind": "drift_over", "tone": "warn",
                          "text": f"Over target size ({owned_pct:.0%} vs {target_pct:.0%})"})
        elif owned_pct < target_pct * (1 - SIZE_DRIFT_BAND) and staged and not pending:
            needs.append({"kind": "drift_under", "tone": "warn",
                          "text": f"Under target size ({owned_pct:.1%} vs {target_pct:.0%})"})
    order = {"critical": 0, "warn": 1, "info": 2}
    return sorted(needs, key=lambda n: order.get(n["tone"], 3))


def _pending_by_play(db: Session) -> dict[str, int]:
    out: dict[str, int] = {}
    for intent in db.execute(select(TradeIntent).where(TradeIntent.status.in_(["proposed", "approved"]))).scalars().all():
        pid = (intent.meta or {}).get("play_id")
        if pid:
            out[pid] = out.get(pid, 0) + 1
    return out


# ── The book ─────────────────────────────────────────────────────────────────

def _holding_row(pos: dict[str, Any], owner: dict[str, Any], total: float) -> dict[str, Any]:
    value = float(pos.get("value") or 0.0)
    ppl = float(pos.get("ppl") or 0.0)
    return {
        "key": f"{pos.get('account_kind')}:{norm_ticker(pos.get('ticker'))}",
        "account_kind": pos.get("account_kind"),
        "ticker": norm_ticker(pos.get("ticker")),
        "instrument_code": pos.get("instrument_code"),
        "name": pos.get("name") or "",
        "value_gbp": round(value, 2),
        "ppl_gbp": round(ppl, 2),
        "weight": (value / total) if total else 0.0,
        "owner": owner,
        "related_to": [],
    }


def _sum(rows: list[dict[str, Any]], total: float) -> dict[str, Any]:
    value = sum(r["value_gbp"] for r in rows)
    ppl = sum(r["ppl_gbp"] for r in rows)
    cost = value - ppl
    return {"value_gbp": round(value, 2), "ppl_gbp": round(ppl, 2), "ppl_pct": (ppl / cost) if cost > 0 else None,
            "weight": (value / total) if total else 0.0, "holdings_count": len(rows)}


def compute_book(db: Session, *, book: dict[str, Any] | None = None, today: date | None = None) -> dict[str, Any]:
    """Every holding with its owner, grouped into plays + Core + Unsorted (+ cash),
    with each play's three rings, needs-you cues and per-theme concentration."""
    book = book or ps.book_context(db)
    positions = book.get("positions") or []
    total = float(book.get("total_gbp") or 0.0)
    cash = float(sum((book.get("free_cash_gbp") or {}).values()))
    plays = owning_plays(db)
    owners = resolve_owners(positions, plays, explicit_rows(db))
    pending = _pending_by_play(db)

    holdings = [_holding_row(p, owners[_pos_key(p)], total) for p in positions]
    by_key = {h["key"]: h for h in holdings}
    pos_by_key = {f"{p.get('account_kind')}:{norm_ticker(p.get('ticker'))}": p for p in positions}

    groups: list[dict[str, Any]] = []
    for idx, play in enumerate(plays):
        owned = [h for h in holdings if h["owner"].get("play_id") == play.id]
        if play.status == "draft" and not owned:
            continue
        legs = play.legs or []
        owned_rows = []
        for h in owned:
            pos = pos_by_key[h["key"]]
            leg = next((l for l in legs if ps._position_matches(pos, ps._leg_keys(l))), None)
            owned_rows.append({**h, "is_leg": leg is not None,
                               "target_weight": float(leg.get("target_weight") or 0.0) if leg else None,
                               "why": (leg or {}).get("rationale") or ""})
        stats = _sum(owned_rows, total)
        for r in owned_rows:
            r["share_of_play"] = (r["value_gbp"] / stats["value_gbp"]) if stats["value_gbp"] else 0.0

        # Ring 2: related — basket legs owned elsewhere, plus explicit picks.
        hidden = {norm_ticker(r.get("ticker")) for r in play.related or [] if r.get("hidden")}
        explicit = {norm_ticker(r.get("ticker")): r for r in play.related or [] if not r.get("hidden")}
        related: list[dict[str, Any]] = []
        seen: set[str] = set()
        for h in holdings:
            if h["owner"].get("play_id") == play.id:
                continue
            pos = pos_by_key[h["key"]]
            leg = next((l for l in legs if ps._position_matches(pos, ps._leg_keys(l))), None)
            pick = next((r for t, r in explicit.items() if _ticker_matches(pos, t)), None)
            if pick is None and (leg is None or h["ticker"] in hidden):
                continue
            why = (pick or {}).get("why") or (leg or {}).get("rationale") or "In this play's basket"
            related.append({"key": h["key"], "ticker": h["ticker"], "account_kind": h["account_kind"], "name": h["name"],
                            "value_gbp": h["value_gbp"], "weight": h["weight"], "owner": h["owner"], "why": why,
                            "source": "user" if pick else "basket"})
            seen.add(h["ticker"])
            by_key[h["key"]]["related_to"].append({"play_id": play.id, "title": play.title, "why": why})
        related_value = sum(r["value_gbp"] for r in related)

        # Ring 3: bench — legs not held at all, then ideas not held.
        bench = []
        for leg in legs:
            if any(ps._position_matches(p, ps._leg_keys(leg)) for p in positions):
                continue
            bench.append({"ticker": norm_ticker(leg.get("ticker")), "name": leg.get("name") or "",
                          "why": leg.get("rationale") or "", "in_plan": True,
                          "target_weight": float(leg.get("target_weight") or 0.0),
                          "instrument_verified": leg.get("instrument_verified", True)})
        for b in play.bench or []:
            t = norm_ticker(b.get("ticker"))
            if any(_ticker_matches(p, t) for p in positions) or any(x["ticker"] == t for x in bench):
                continue
            bench.append({"ticker": t, "name": b.get("name") or "", "why": b.get("why") or "", "in_plan": False,
                          "target_weight": None, "instrument_verified": b.get("instrument_verified", True)})

        sizing = play.sizing or {}
        groups.append({
            "kind": "play", "id": play.id, "play_id": play.id, "title": play.title, "status": play.status,
            "conviction": play.conviction, "account_kind": play.account_kind, "color_index": idx,
            **stats,
            "target_pct": float(sizing.get("target_pct_of_book") or 0.0),
            "effective_value_gbp": round(stats["value_gbp"] + related_value, 2),
            "effective_weight": ((stats["value_gbp"] + related_value) / total) if total else 0.0,
            "tranches_total": len(sizing.get("tranches") or []),
            "tranches_staged": len(sizing.get("staged") or []),
            "next_tranche": _next_tranche(play, today or date.today()) if play.status == "active" else None,
            "verdict": (play.review or {}).get("verdict"),
            "needs": play_needs(play, owned_value=stats["value_gbp"], total=total, positions=[pos_by_key[r["key"]] for r in owned_rows],
                                pending=pending.get(play.id, 0), today=today),
            "owned": sorted(owned_rows, key=lambda r: -r["value_gbp"]),
            "related": sorted(related, key=lambda r: -r["value_gbp"]),
            "bench": bench,
        })

    for kind, title in (("core", CORE_TITLE), ("unsorted", UNSORTED_TITLE)):
        rows = sorted([h for h in holdings if h["owner"]["kind"] == kind], key=lambda r: -r["value_gbp"])
        groups.append({"kind": kind, "id": kind, "play_id": None, "title": title, "status": None, **_sum(rows, total),
                       "owned": rows, "related": [], "bench": [], "needs": [], "effective_value_gbp": None,
                       "effective_weight": None})

    theme_risk = []
    for g in groups:
        if g["kind"] != "play" or not g["value_gbp"]:
            continue
        tone = "warn" if g["weight"] >= THEME_CONCENTRATION_WARN or (g["effective_weight"] or 0) >= EFFECTIVE_CONCENTRATION_WARN else "neutral"
        text = f"{g['weight']:.0%} of your book is one bet: {g['title']}"
        if (g["effective_weight"] or 0) - g["weight"] >= 0.01:
            text += f" ({g['effective_weight']:.0%} counting related holdings)"
        theme_risk.append({"play_id": g["play_id"], "title": g["title"], "weight": g["weight"],
                           "effective_weight": g["effective_weight"], "tone": tone, "text": text})
    theme_risk.sort(key=lambda r: -r["weight"])

    unsorted = next(g for g in groups if g["kind"] == "unsorted")
    return {
        "as_of": _now().isoformat(),
        "total_gbp": round(total, 2),
        "cash_gbp": round(cash, 2),
        "cash_weight": (cash / total) if total else 0.0,
        "groups": groups,
        "holdings": sorted(holdings, key=lambda h: -h["value_gbp"]),
        "theme_risk": theme_risk,
        "unsorted_count": unsorted["holdings_count"],
        "unsorted_weight": unsorted["weight"],
    }


def play_rings(db: Session, play: Play, *, book: dict[str, Any] | None = None) -> dict[str, Any]:
    """The book's group for one play (works for drafts and closed plays too)."""
    book = book or ps.book_context(db)
    full = compute_book(db, book=book)
    g = next((g for g in full["groups"] if g.get("play_id") == play.id), None)
    if g is None:  # draft with nothing owned, or closed — rings still useful
        positions = book["positions"]
        total = full["total_gbp"]
        legs = play.legs or []
        related = []
        for h in full["holdings"]:
            pos = next((p for p in positions if _pos_key(p) == (h["account_kind"], h["ticker"])), None)
            if pos and any(ps._position_matches(pos, ps._leg_keys(l)) for l in legs):
                leg = next(l for l in legs if ps._position_matches(pos, ps._leg_keys(l)))
                related.append({**{k: h[k] for k in ("key", "ticker", "account_kind", "name", "value_gbp", "weight", "owner")},
                                "why": leg.get("rationale") or "In this play's basket", "source": "basket"})
        bench = [{"ticker": norm_ticker(l.get("ticker")), "name": l.get("name") or "", "why": l.get("rationale") or "",
                  "in_plan": True, "target_weight": float(l.get("target_weight") or 0.0),
                  "instrument_verified": l.get("instrument_verified", True)}
                 for l in legs if not any(ps._position_matches(p, ps._leg_keys(l)) for p in positions)]
        rv = sum(r["value_gbp"] for r in related)
        g = {"kind": "play", "id": play.id, "play_id": play.id, "title": play.title, "status": play.status,
             "value_gbp": 0.0, "ppl_gbp": 0.0, "ppl_pct": None, "weight": 0.0, "holdings_count": 0,
             "target_pct": float((play.sizing or {}).get("target_pct_of_book") or 0.0),
             "effective_value_gbp": round(rv, 2), "effective_weight": (rv / total) if total else 0.0,
             "owned": [], "related": related, "bench": bench, "needs": [], "verdict": (play.review or {}).get("verdict")}
    return {**g, "total_gbp": full["total_gbp"]}


# ── Ownership edits ──────────────────────────────────────────────────────────

def _resolve_target(db: Session, owner: str) -> tuple[str, Play | None]:
    o = (owner or "").strip()
    if o.lower() in ("core", "unsorted"):
        return o.lower(), None
    play = db.get(Play, o)
    if play is None:
        raise OwnershipError("That play doesn't exist any more.")
    if play.status not in OWNING_STATUSES:
        raise OwnershipError(f"“{play.title}” is closed — reopen it before moving holdings into it.")
    return "play", play


def assign_owner(db: Session, ticker: str, owner: str, *, account_kind: str | None = None, source: str = "user",
                 book: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Make ``owner`` ('core' | 'unsorted' | a play id) the owner of a holding — in
    one account, or every account that holds it. Returns the rows written."""
    t = norm_ticker(ticker)
    if not t:
        raise OwnershipError("Pick a holding to move.")
    kind, play = _resolve_target(db, owner)
    book = book or ps.book_context(db)
    held = [p for p in book["positions"] if _ticker_matches(p, t) and (account_kind is None or p.get("account_kind") == account_kind)]
    if not held:
        where = f" in your {_account_label(account_kind)}" if account_kind else ""
        raise OwnershipError(f"You don't hold {t}{where} — nothing to move.")
    rows = explicit_rows(db)
    written = []
    for pos in held:
        key = _pos_key(pos)
        row = rows.get(key) or HoldingOwner(account_kind=key[0], ticker=key[1])
        row.owner_kind = kind
        row.play_id = play.id if play else None
        row.source = (source or "user")[:16]
        row.updated_at = _now()
        db.add(row)
        written.append({"account_kind": key[0], "ticker": key[1], "owner_kind": kind, "play_id": row.play_id})
    db.commit()
    return written


def release_play(db: Session, play: Play, *, book: dict[str, Any] | None = None) -> int:
    """Send everything a play owns to Unsorted (used when it's deleted)."""
    moved: set[tuple[str, str]] = set()
    try:
        book = book or ps.book_context(db)
        rows = explicit_rows(db)
        for pos in owned_positions(db, play, book["positions"]):
            key = _pos_key(pos)
            row = rows.get(key) or HoldingOwner(account_kind=key[0], ticker=key[1])
            row.owner_kind, row.play_id, row.source, row.updated_at = "unsorted", None, "system", _now()
            db.add(row)
            moved.add(key)
    except Exception as exc:  # noqa: BLE001 — no portfolio: fall back to explicit rows only
        logger.info("release_play without a portfolio snapshot: %s", exc)
    db.flush()
    for row in db.execute(select(HoldingOwner).where(HoldingOwner.play_id == play.id)).scalars().all():
        row.owner_kind, row.play_id, row.source, row.updated_at = "unsorted", None, "system", _now()
        db.add(row)
        moved.add((row.account_kind, norm_ticker(row.ticker)))
    db.commit()
    return len(moved)


# ── Rings: related + bench edits ─────────────────────────────────────────────

def _clean_why(why: str | None) -> str:
    return re.sub(r"\s+", " ", str(why or "")).strip()[:280]


def add_related(db: Session, play: Play, ticker: str, why: str = "", *, book: dict[str, Any] | None = None) -> Play:
    t = norm_ticker(ticker)
    if not t:
        raise OwnershipError("Which holding is related?")
    book = book or ps.book_context(db)
    held = [p for p in book["positions"] if _ticker_matches(p, t)]
    if not held:
        raise OwnershipError(f"You don't hold {t}. Add it to the bench as an idea instead.")
    owners = resolve_owners(book["positions"], owning_plays(db), explicit_rows(db))
    if all((owners.get(_pos_key(p)) or {}).get("play_id") == play.id for p in held):
        raise OwnershipError(f"{t} is already owned by this play.")
    items = [r for r in play.related or [] if norm_ticker(r.get("ticker")) != t]
    items.append({"ticker": t, "why": _clean_why(why), "added_at": _now().isoformat()})
    play.related = items
    db.add(play)
    db.commit()
    db.refresh(play)
    return play


def remove_related(db: Session, play: Play, ticker: str) -> Play:
    """Drop an explicit related pick; for a basket leg owned elsewhere, hide it."""
    t = norm_ticker(ticker)
    items = [r for r in play.related or [] if norm_ticker(r.get("ticker")) != t]
    if any(norm_ticker(l.get("ticker")) == t or norm_ticker(l.get("ticker")).split(".")[0] == t for l in play.legs or []):
        items.append({"ticker": t, "hidden": True, "added_at": _now().isoformat()})
    play.related = items
    db.add(play)
    db.commit()
    db.refresh(play)
    return play


def add_bench(db: Session, play: Play, ticker: str, why: str = "", name: str = "", *,
              book: dict[str, Any] | None = None, verify: bool = True) -> Play:
    t = norm_ticker(ticker)
    if not t or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-_]{0,15}", t):
        raise OwnershipError("That doesn't look like a ticker (e.g. CCJ or NUCG.L).")
    try:
        book = book or ps.book_context(db)
        if any(_ticker_matches(p, t) for p in book["positions"]):
            raise OwnershipError(f"You already hold {t} — move it into this play, or mark it related.")
    except OwnershipError:
        raise
    except Exception:  # noqa: BLE001 — no portfolio: skip the held check
        pass
    if any(norm_ticker(l.get("ticker")) == t for l in play.legs or []):
        raise OwnershipError(f"{t} is already in this play's plan.")
    verified = True
    if verify:
        code, verified = ps.resolve_instrument(db, t)
        if not verified and ps.instruments_known(db):
            raise OwnershipError(f"{t} isn't available on Trading 212.")
    items = [b for b in play.bench or [] if norm_ticker(b.get("ticker")) != t]
    items.append({"ticker": t, "name": str(name or "")[:120], "why": _clean_why(why), "instrument_verified": verified,
                  "added_at": _now().isoformat()})
    play.bench = items
    db.add(play)
    db.commit()
    db.refresh(play)
    return play


def remove_bench(db: Session, play: Play, ticker: str) -> Play:
    t = norm_ticker(ticker)
    play.bench = [b for b in play.bench or [] if norm_ticker(b.get("ticker")) != t]
    db.add(play)
    db.commit()
    db.refresh(play)
    return play


def promote_bench(db: Session, play: Play, ticker: str, weight: float = 0.1) -> Play:
    """Move a bench idea into the plan as a basket leg (then re-size)."""
    t = norm_ticker(ticker)
    idea = next((b for b in play.bench or [] if norm_ticker(b.get("ticker")) == t), None)
    if idea is None:
        raise OwnershipError(f"{t} isn't on this play's bench.")
    weight = ps._clamp(float(weight or 0.1), 0.01, 0.5)
    legs = [dict(l) for l in play.legs or []]
    scale = 1 - weight
    for l in legs:
        l["target_weight"] = float(l.get("target_weight") or 0.0) * scale
    legs.append({"ticker": t, "name": idea.get("name") or "", "role": "satellite", "target_weight": weight,
                 "rationale": idea.get("why") or ""})
    play.bench = [b for b in play.bench or [] if norm_ticker(b.get("ticker")) != t]
    return ps.update_play(db, play, {"legs": legs, "resize": True})


# ── Suggestions: group Unsorted holdings into plays / Core ───────────────────

SUGGESTIONS_KEY = "play_suggestions"

# A small, deliberately boring theme map for the no-model fallback.
THEMES: list[dict[str, Any]] = [
    {"key": "core", "kind": "core", "title": "Core",
     "why": "Broad index funds and long-term compounders — no thesis to track.",
     "tickers": {"SPY", "QQQ", "VOO", "VTI", "IVV", "VUSA", "VWRL", "VWRP", "CSPX", "SWDA", "EQQQ", "VUAG", "IWDA", "VEVE",
                 "BRK.B", "BRK-B", "V", "MA", "COST", "KO", "PG", "PEP", "JNJ", "WMT", "MCD", "HD", "AAPL", "MSFT", "GOOGL",
                 "GOOG", "AMZN", "JPM"}},
    {"key": "ai_infra", "kind": "play", "title": "AI infrastructure",
     "why": "Chips and kit the AI build-out runs on.",
     "thesis": "AI capex keeps compounding, and the picks-and-shovels — accelerators, foundry, lithography, memory and networking — capture a durable share of it whichever model lab wins.",
     "tickers": {"NVDA", "AVGO", "TSM", "ASML", "AMD", "MU", "ARM", "ANET", "SMCI", "MRVL", "LRCX", "AMAT", "KLAC", "INTC",
                 "QCOM", "DELL", "VRT", "CRWV", "NBIS", "IREN", "META"}},
    {"key": "power", "kind": "play", "title": "Nuclear & power",
     "why": "Firm power for a grid that AI and electrification are straining.",
     "thesis": "Electricity demand is rising for the first time in decades. Uranium, reactors and grid-scale power generators benefit from a long, policy-backed build cycle.",
     "tickers": {"CCJ", "OKLO", "SMR", "LEU", "UUUU", "NXE", "UEC", "DNN", "BWXT", "CEG", "VST", "TLN", "NRG", "GEV", "NEE",
                 "ETN", "PWR", "NUCG"}},
    {"key": "software", "kind": "play", "title": "Software & cyber",
     "why": "High-growth software platforms and security.",
     "thesis": "Enterprises keep shifting spend to cloud software and security; the category leaders compound revenue with high margins.",
     "tickers": {"PLTR", "NET", "CRWD", "SNOW", "DDOG", "ZS", "PANW", "FTNT", "NOW", "CRM", "ORCL", "ADBE", "SHOP", "APP"}},
    {"key": "health", "kind": "play", "title": "GLP-1 & healthcare",
     "why": "Obesity drugs and the healthcare names around them.",
     "thesis": "GLP-1 drugs are expanding into new indications and markets; the leading makers have years of growth ahead.",
     "tickers": {"LLY", "NVO", "ISRG", "VRTX", "REGN", "HIMS", "AZN", "ABBV", "MRK"}},
    {"key": "space", "kind": "play", "title": "Space economy",
     "why": "Launch, satellites and space infrastructure.",
     "thesis": "Launch costs keep falling, opening commercial markets for satellites, connectivity and defence.",
     "tickers": {"RKLB", "ASTS", "LUNR", "JOBY", "ACHR"}},
    {"key": "crypto", "kind": "play", "title": "Crypto rails",
     "why": "Exchanges, brokers and treasuries that move with crypto adoption.",
     "thesis": "Crypto adoption and regulation clarity grow volumes for the exchanges and brokers that serve it.",
     "tickers": {"COIN", "MSTR", "HOOD", "SOFI", "SQ", "PYPL"}},
    {"key": "ev", "kind": "play", "title": "EVs & autonomy",
     "why": "Electric vehicles, autonomy and robotics.",
     "thesis": "Autonomy and electrification reshape transport; the leaders own software, data and manufacturing scale.",
     "tickers": {"TSLA", "UBER", "RIVN", "LCID", "NIO"}},
    {"key": "quantum", "kind": "play", "title": "Quantum computing",
     "why": "Early, volatile quantum hardware names.",
     "thesis": "Quantum hardware is moving from lab to early commercial use; a small basket captures the optionality.",
     "tickers": {"IONQ", "RGTI", "QBTS", "QUBT"}},
]


def _base(t: str) -> str:
    return norm_ticker(t).split(".")[0].replace("-", ".") if not norm_ticker(t).startswith("BRK") else norm_ticker(t).replace("-", ".")


def unsorted_holdings(db: Session, *, book: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    full = compute_book(db, book=book)
    rows = next(g for g in full["groups"] if g["kind"] == "unsorted")["owned"]
    # One entry per ticker across accounts.
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = agg.setdefault(r["ticker"], {"ticker": r["ticker"], "name": r["name"], "value_gbp": 0.0, "weight": 0.0, "accounts": []})
        a["value_gbp"] = round(a["value_gbp"] + r["value_gbp"], 2)
        a["weight"] += r["weight"]
        a["accounts"].append(r["account_kind"])
    return sorted(agg.values(), key=lambda r: -r["value_gbp"]), full


def deterministic_suggestions(unsorted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by a fixed theme map. A theme needs 2+ names (or one name ≥3% of book)
    to become a play; Core takes any number."""
    used: set[str] = set()
    out: list[dict[str, Any]] = []
    # Plays first so e.g. META goes to AI before Core; Core last mops up compounders.
    for theme in [t for t in THEMES if t["kind"] == "play"] + [t for t in THEMES if t["kind"] == "core"]:
        members = [h for h in unsorted if h["ticker"] not in used and (_base(h["ticker"]) in theme["tickers"] or h["ticker"] in theme["tickers"])]
        if not members:
            continue
        weight = sum(h["weight"] for h in members)
        if theme["kind"] == "play" and len(members) < 2 and weight < 0.03:
            continue
        used.update(h["ticker"] for h in members)
        out.append({"kind": theme["kind"], "title": theme["title"], "tickers": [h["ticker"] for h in members],
                    "why": theme["why"], "thesis": theme.get("thesis") or ""})
    return out


_SUGGEST_SYSTEM = """You help a UK investor organise holdings they ALREADY own into "plays" (theme bets with a
thesis) or "Core" (long-term index funds and quality compounders with no specific thesis to track).
Group the UNSORTED holdings you're given. Rules:
- Only use tickers from the unsorted list, each at most once. Leave a holding out if it fits nothing.
- A play needs a clear, specific theme (e.g. "AI infrastructure", "Nuclear & power"), usually 2+ names.
- Prefer joining an EXISTING play when a holding clearly fits it: use kind "existing" and its play_id.
- Core: broad ETFs and steady compounders. Never put speculative names in Core.
- title ≤ 40 chars; why: one plain sentence a user would nod at; thesis: 1-2 sentences (plays only).
Return only the structured JSON."""

_SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["play", "core", "existing"]},
                    "play_id": {"type": ["string", "null"]},
                    "title": {"type": "string"},
                    "tickers": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "string"},
                    "thesis": {"type": "string"},
                },
                "required": ["kind", "title", "tickers", "why"],
            },
        }
    },
    "required": ["suggestions"],
}


def _suggest_payload(unsorted: list[dict[str, Any]], plays: list[Play]) -> dict[str, Any]:
    return {
        "unsorted": [{"ticker": h["ticker"], "name": h["name"], "weight_of_book": round(h["weight"], 4)} for h in unsorted],
        "existing_plays": [{"play_id": p.id, "title": p.title, "legs": [l.get("ticker") for l in p.legs or []]}
                           for p in plays if p.status in DERIVING_STATUSES],
    }


def _model_suggestions(unsorted: list[dict[str, Any]], plays: list[Play], total: float) -> list[dict[str, Any]]:
    """One fast structured call (Together → Claude Haiku via the API). No agent loop,
    no CLI spawn: a few seconds, not half a minute."""
    import json

    from app.services import fast_llm

    payload = _suggest_payload(unsorted, plays)
    res = fast_llm.complete_json_sync(
        "play_suggest", system=_SUGGEST_SYSTEM, user=json.dumps(payload), schema=_SUGGEST_SCHEMA,
        max_tokens=2500, usage_id="book",
    )
    return list((res.data or {}).get("suggestions") or [])


def refine_available() -> bool:
    """Is any fast model configured to refine the rule-based groups?"""
    from app.services import fast_llm, model_routing

    return any(fast_llm.available(p) for p, _ in model_routing.fast_chain("play_suggest"))


def clean_suggestions(items: list[dict[str, Any]], unsorted: list[dict[str, Any]], db: Session) -> list[dict[str, Any]]:
    """Validate model/agent output: known unsorted tickers only, each once; drop empties."""
    pool = {h["ticker"]: h for h in unsorted}
    alias = {_base(t): t for t in pool}
    used: set[str] = set()
    out = []
    for it in items:
        kind = str(it.get("kind") or "play").lower()
        play = None
        if kind == "existing":
            play = db.get(Play, str(it.get("play_id") or ""))
            if play is None or play.status not in OWNING_STATUSES:
                kind = "play"
        if kind not in ("play", "core", "existing"):
            continue
        tickers = []
        for raw in it.get("tickers") or []:
            t = norm_ticker(raw)
            t = t if t in pool else alias.get(_base(t), "")
            if t and t not in used:
                tickers.append(t)
                used.add(t)
        if not tickers:
            continue
        title = (play.title if play else str(it.get("title") or "").strip()) or ("Core" if kind == "core" else "Untitled play")
        out.append({"kind": kind, "play_id": play.id if play else None, "title": title[:60], "tickers": tickers,
                    "why": _clean_why(it.get("why")), "thesis": str(it.get("thesis") or "").strip()[:1200]})
    return out


def _decorate(items: list[dict[str, Any]], unsorted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pool = {h["ticker"]: h for h in unsorted}
    out = []
    for it in items:
        live = [t for t in it.get("tickers") or [] if t in pool]
        if not live or it.get("status", "open") != "open":
            continue
        out.append({**it, "tickers": live, "value_gbp": round(sum(pool[t]["value_gbp"] for t in live), 2),
                    "weight": sum(pool[t]["weight"] for t in live),
                    "holdings": [{k: pool[t][k] for k in ("ticker", "name", "value_gbp", "weight")} for t in live]})
    return out


def _store(db: Session) -> Any:
    from app.services.config_store import ConfigStore

    return ConfigStore(db)


MODEL_WAIT_S = 12.0  # the sheet waits this long for the model before showing the rules


def get_suggestions(db: Session, *, book: dict[str, Any] | None = None) -> dict[str, Any]:
    stored = _store(db).get(SUGGESTIONS_KEY, {"items": []})
    unsorted, full = unsorted_holdings(db, book=book)
    return {"generated_at": stored.get("generated_at"), "source": stored.get("source"),
            "refine_error": stored.get("refine_error"),
            "items": _decorate(stored.get("items") or [], unsorted),
            "unsorted": unsorted, "unsorted_weight": full["unsorted_weight"]}


def _save(db: Session, items: list[dict[str, Any]], source: str, *, note: str | None = None) -> None:
    for it in items:
        it.setdefault("id", uuid.uuid4().hex[:10])
        it.setdefault("status", "open")
    _store(db).set(SUGGESTIONS_KEY, {"generated_at": _now().isoformat(), "source": source, "items": items,
                                     "refine_error": note})


def _model_within(unsorted: list[dict[str, Any]], plays: list[Play], wait_s: float) -> list[dict[str, Any]]:
    """The model's groups, or TimeoutError after ``wait_s``. A late answer is dropped."""
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="play-suggest")
    try:
        return pool.submit(_model_suggestions, unsorted, plays, 0.0).result(timeout=wait_s)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def generate_suggestions(db: Session, *, use_model: bool = True, book: dict[str, Any] | None = None,
                         wait_s: float = MODEL_WAIT_S) -> dict[str, Any]:
    """Group Unsorted holdings into suggested plays / Core. Never applies anything.

    The fast model groups them (a few seconds; the UI shows a loading state). The
    theme rules are the fallback only: no model configured, or it failed, found
    nothing, or took longer than ``wait_s`` (then ``refine_error`` says so, quietly)."""
    unsorted, full = unsorted_holdings(db, book=book)
    if not unsorted:
        _save(db, [], "none")
        return get_suggestions(db, book=book)
    rules = clean_suggestions(deterministic_suggestions(unsorted), unsorted, db)
    if not (use_model and refine_available()):
        _save(db, rules, "rules")
        return get_suggestions(db, book=book)
    try:
        items = clean_suggestions(_model_within(unsorted, owning_plays(db), wait_s), unsorted, db)
        note = None if items else "Archie didn't find clearer groups, so these follow theme rules."
    except TimeoutError:
        items, note = [], "Archie took too long, so these follow theme rules."
    except Exception as exc:  # noqa: BLE001 — no model / provider error → the rules
        logger.info("play suggestions fell back to the rules: %s", exc)
        items, note = [], "Archie couldn't group these just now, so these follow theme rules."
    if items:
        _save(db, items, "archie")
    else:
        _save(db, rules, "rules", note=note)
    return get_suggestions(db, book=book)


def save_agent_suggestions(db: Session, items: list[dict[str, Any]], *, book: dict[str, Any] | None = None) -> dict[str, Any]:
    unsorted, _ = unsorted_holdings(db, book=book)
    cleaned = clean_suggestions(items, unsorted, db)
    if not cleaned:
        raise OwnershipError("None of those tickers are Unsorted holdings — nothing to suggest.")
    _save(db, cleaned, "archie")
    return get_suggestions(db, book=book)


def _find_suggestion(db: Session, sid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    stored = _store(db).get(SUGGESTIONS_KEY, {"items": []})
    item = next((i for i in stored.get("items") or [] if i.get("id") == sid), None)
    if item is None or item.get("status", "open") != "open":
        raise OwnershipError("That suggestion is no longer available — refresh suggestions.")
    return stored, item


def dismiss_suggestion(db: Session, sid: str) -> None:
    stored, item = _find_suggestion(db, sid)
    item["status"] = "dismissed"
    _store(db).set(SUGGESTIONS_KEY, stored)


DEFAULT_ADOPTED_KILLS = [
    {"text": "The basket falls 30% from its peak", "rule": {"type": "drawdown_pct", "value": 30}},
    {"text": "This bet grows past a quarter of the book", "rule": {"type": "weight_above", "ticker": None, "value": 0.25}},
]


_ADOPTED_CAP_RE = re.compile(r"^This bet grows past \d+% of the book$")


def adopted_cap(weight: float) -> float:
    """Concentration cap for a play built from holdings: comfortably above today's weight."""
    return round(max(0.25, min(0.9, weight * 1.25 + 0.02)), 2)


def _is_adopted_cap(c: dict[str, Any]) -> bool:
    rule = c.get("rule") or {}
    return (rule.get("type") == "weight_above" and not rule.get("ticker")
            and (c.get("auto") == "adopted_cap" or bool(_ADOPTED_CAP_RE.match(str(c.get("text") or "")))))


def fit_adopted_cap(db: Session, play: Play, book: dict[str, Any]) -> float:
    """Re-base an adopted play on what it *actually* owns now, and lift its
    automatic concentration cap above that weight (never lowers it).

    Called after holdings are moved into the play. The weight is measured the same
    way the kill rule is evaluated (owned positions over total equity), so the cap
    can't sit below the play's real starting weight — which happened when it was
    set from the Unsorted pool alone while the move also pulled in the same
    ticker's holdings from other accounts, or more holdings joined later."""
    total = float(book.get("total_gbp") or 0.0)
    owned = sum(float(p.get("value") or 0.0) for p in owned_positions(db, play, book["positions"]))
    weight = owned / total if total else 0.0
    cap = adopted_cap(weight)
    kills = [dict(c) for c in play.kill_criteria or []]
    changed = False
    for i, c in enumerate(kills):
        if _is_adopted_cap(c) and float((c.get("rule") or {}).get("value") or 0.0) < cap:
            kills[i] = {**c, "text": f"This bet grows past {cap:.0%} of the book", "auto": "adopted_cap",
                        "rule": {"type": "weight_above", "ticker": None, "value": cap}}
            changed = True
    if changed:
        play.kill_criteria = kills
    sizing = dict(play.sizing or {})
    if weight > float(sizing.get("target_pct_of_book") or 0.0):
        sizing["target_pct_of_book"] = round(weight, 4)
        play.sizing = sizing
    db.add(play)
    db.commit()
    return weight


def accept_suggestion(db: Session, sid: str, *, title: str | None = None, tickers: list[str] | None = None,
                      kind: str | None = None, book: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply one suggestion (with the user's edits). Creates an active play built from
    the holdings (no orders), joins an existing play, or moves names to Core."""
    stored, item = _find_suggestion(db, sid)
    book = book or ps.book_context(db)
    unsorted, _ = unsorted_holdings(db, book=book)
    pool = {h["ticker"]: h for h in unsorted}
    chosen = [norm_ticker(t) for t in (tickers if tickers is not None else item["tickers"])]
    chosen = [t for t in chosen if t in pool]
    if not chosen:
        raise OwnershipError("Pick at least one holding that's still Unsorted.")
    kind = (kind or item["kind"]).lower()
    play_id: str | None = None

    def move(owner: str) -> None:
        # Only the Unsorted holdings the suggestion showed: the same ticker owned
        # by Core or another play in a different account stays where it is.
        for t in chosen:
            for acct in dict.fromkeys(pool[t]["accounts"]):
                assign_owner(db, t, owner, account_kind=acct, source="suggestion", book=book)

    if kind == "core":
        move("core")
    elif kind == "existing" and item.get("play_id"):
        play = db.get(Play, item["play_id"])
        if play is None:
            raise OwnershipError("That play doesn't exist any more.")
        move(play.id)
        if (play.sizing or {}).get("adopted"):
            fit_adopted_cap(db, play, book)
        play_id = play.id
    else:
        held_value = {t: pool[t]["value_gbp"] for t in chosen}
        total_v = sum(held_value.values()) or 1.0
        accounts: dict[str, float] = {}
        for p in book["positions"]:
            t = norm_ticker(p.get("ticker"))
            if t in held_value and p.get("account_kind") in pool[t]["accounts"]:
                accounts[p["account_kind"]] = accounts.get(p["account_kind"], 0.0) + float(p.get("value") or 0.0)
        name = (title or item.get("title") or "Untitled play").strip()[:80]
        total_book = float(book.get("total_gbp") or 0.0)
        weight_now = (sum(held_value.values()) / total_book) if total_book else 0.0
        # An adopted play describes what you already hold: its target is today's
        # weight, and the concentration cap sits comfortably above it.
        cap = adopted_cap(weight_now)
        kills = [DEFAULT_ADOPTED_KILLS[0],
                 {"text": f"This bet grows past {cap:.0%} of the book", "auto": "adopted_cap",
                  "rule": {"type": "weight_above", "ticker": None, "value": cap}}]
        play = ps.create_play(db, {
            "title": name,
            "thesis": item.get("thesis") or item.get("why") or "",
            "conviction": "medium",
            "account_kind": max(accounts, key=accounts.get) if accounts else "stocks_isa",
            "legs": [{"ticker": t, "name": pool[t]["name"], "role": "core", "target_weight": held_value[t] / total_v,
                      "rationale": ""} for t in chosen],
            "kill_criteria": kills,
            "what_would_change_my_mind": "",
            "origin": "adopted",
        }, book=book)
        play.budget_gbp = 0.0
        play.budget_pct_of_book = 0.0
        step = (f"Built from holdings you already own ({weight_now:.0%} of your book), so no new money is planned. "
                "Raise the budget to add to it in tranches.")
        play.sizing = {**(play.sizing or {}), "budget_gbp": 0.0, "suggested_gbp": 0.0, "tranches": [], "staged": [],
                       "allocations": [], "target_pct_of_book": round(weight_now, 4), "adopted": True,
                       "steps": [step], "rationale": step, "warnings": []}
        ps.set_status(play, "active")
        db.add(play)
        db.commit()
        move(play.id)
        # Measure what it really owns now (as the kill rule will) and keep the
        # cap above that, so adopting holdings never trips the play at once.
        fit_adopted_cap(db, play, book)
        play_id = play.id
    item["status"] = "accepted"
    item["play_id"] = play_id
    _store(db).set(SUGGESTIONS_KEY, stored)
    return {"ok": True, "kind": kind, "play_id": play_id, "tickers": chosen}


# ── Proposal owner inference ─────────────────────────────────────────────────

def ownership_index(db: Session) -> dict[str, Any]:
    """Cheap ticker → owner lookup for labelling proposals (no portfolio fetch)."""
    plays = owning_plays(db)
    by_id = {p.id: p for p in plays}
    explicit: dict[str, dict[str, Any]] = {}
    for r in explicit_rows(db).values():
        if r.owner_kind == "play" and r.play_id in by_id:
            explicit[norm_ticker(r.ticker)] = _owner("play", play=by_id[r.play_id], source=r.source)
        elif r.owner_kind == "core":
            explicit[norm_ticker(r.ticker)] = _owner("core", source=r.source)
    return {"plays": by_id, "explicit": explicit, "deriving": [p for p in plays if p.status in DERIVING_STATUSES]}


def intent_owner(idx: dict[str, Any], intent: TradeIntent, all_titles: dict[str, str] | None = None) -> dict[str, Any] | None:
    """``{kind: play|core, play_id, title, inferred}`` or None ("not part of any play")."""
    meta = intent.meta or {}
    pid = meta.get("play_id")
    if pid:
        p = idx["plays"].get(pid)
        title = p.title if p else (all_titles or {}).get(pid)
        if title:  # a deleted play falls through to inference below
            return {"kind": "play", "play_id": pid, "title": title, "inferred": False}
    if meta.get("owner") == "core":
        return {"kind": "core", "play_id": None, "title": CORE_TITLE, "inferred": False}
    t = norm_ticker(intent.symbol)
    o = idx["explicit"].get(t)
    if o:
        return {"kind": o["kind"], "play_id": o.get("play_id"), "title": o["title"], "inferred": True}
    for p in idx["deriving"]:
        if any(t in ps._leg_keys(l) for l in p.legs or []):
            return {"kind": "play", "play_id": p.id, "title": p.title, "inferred": True}
    return None
