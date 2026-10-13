"""Service for reading and listing artifacts from the filesystem."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from app.services.claude_sdk_config import artifacts_dir, memory_dir, project_root


# ── Report summaries ──────────────────────────────────────────────────────
# Every LLM-written report ends with a fenced ```summary JSON block:
#   {"headline": "...", "points": ["..."], "action": "..." | null, "signal": "none"|"low"|"high"}
# It is parsed and stripped when the artifact is stored (kept in frontmatter as
# one-line JSON) so Home can show a high-signal digest without reading the body.

#
# Optional richer fields (older summaries without them stay valid):
#   stats:    ≤4 stat tiles {label, value, kind: money|pct|signedPct|date|count|text,
#             tone?, sub?, tickers?, spark?, bar?: {value, max}, timeline?: [{date, label}]}
#   viz:      one small viz spec (components/viz), validated like any other
#   holdings: per-name cards {ticker, name?, date?, timing?, verdict?: hold|trim|opportunity,
#             value?, weight?, gain_pct?, forward_pe?, usual_move?, watch?: [..]}
#   gaps:     short names of data that wasn't available ("timing", "surprise history")

_SUMMARY_FENCE = re.compile(r"```summary[ \t]*\n([\s\S]*?)\n?```[ \t]*\n?", re.IGNORECASE)
_SIGNALS = ("none", "low", "high")
STAT_KINDS = ("money", "pct", "signedPct", "date", "count", "text")
_TONES = ("positive", "negative", "neutral")
_VERDICTS = {
    "hold": "hold", "hold through": "hold", "hold_through": "hold",
    "trim": "trim", "consider trimming": "trim", "consider trimming before": "trim", "consider_trimming": "trim",
    "opportunity": "opportunity",
}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_STATS = 4
MAX_HOLDINGS = 8


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    if isinstance(v, str):
        t = v.strip().replace(",", "").lstrip("£$€").rstrip("%").strip()
        try:
            return float(t)
        except ValueError:
            return None
    return None


def _clip(v: Any, n: int) -> str:
    return str(v if v is not None else "").strip()[:n]


def normalise_stat(raw: Any) -> dict[str, Any] | None:
    """One stat tile, or None if it has no label or value."""
    if not isinstance(raw, dict):
        return None
    label = _clip(raw.get("label"), 40)
    kind = str(raw.get("kind") or "text").strip()
    if kind not in STAT_KINDS:
        kind = "text"
    value: Any = raw.get("value")
    if kind in ("money", "pct", "signedPct", "count"):
        n = _num(value)
        if n is None:
            kind, value = "text", _clip(value, 40)
        else:
            value = round(n, 6)
    elif kind == "date":
        value = _clip(value, 10)
        if not _ISO_DATE.match(value):
            return None
    else:
        value = _clip(value, 40)
    if not label or value in (None, ""):
        return None
    out: dict[str, Any] = {"label": label, "value": value, "kind": kind}
    tone = str(raw.get("tone") or "").strip().lower()
    if tone in _TONES:
        out["tone"] = tone
    sub = _clip(raw.get("sub"), 60)
    if sub:
        out["sub"] = sub
    tickers = raw.get("tickers")
    if isinstance(tickers, list):
        t = [_clip(x, 12).upper() for x in tickers if _clip(x, 12)][:6]
        if t:
            out["tickers"] = t
    spark = raw.get("spark")
    if isinstance(spark, list):
        pts = [n for n in (_num(x) for x in spark[:90]) if n is not None]
        if len(pts) >= 2:
            out["spark"] = [round(x, 4) for x in pts]
    bar = raw.get("bar")
    if isinstance(bar, dict):
        bv, bm = _num(bar.get("value")), _num(bar.get("max"))
        if bv is not None and bm and bm > 0:
            out["bar"] = {"value": round(bv, 6), "max": round(bm, 6)}
    timeline = raw.get("timeline")
    if isinstance(timeline, list):
        marks = [{"date": _clip(m.get("date"), 10), "label": _clip(m.get("label"), 16)}
                 for m in timeline[:8] if isinstance(m, dict) and _ISO_DATE.match(_clip(m.get("date"), 10))]
        if marks:
            out["timeline"] = marks
    return out


def normalise_holding(raw: Any) -> dict[str, Any] | None:
    """One per-name card, or None without a ticker."""
    if not isinstance(raw, dict):
        return None
    ticker = _clip(raw.get("ticker"), 12).upper()
    if not ticker:
        return None
    out: dict[str, Any] = {"ticker": ticker}
    for k, n in (("name", 80), ("timing", 24), ("note", 160)):
        v = _clip(raw.get(k), n)
        if v:
            out[k] = v
    d = _clip(raw.get("date"), 10)
    if _ISO_DATE.match(d):
        out["date"] = d
    verdict = _VERDICTS.get(str(raw.get("verdict") or "").strip().lower())
    if verdict:
        out["verdict"] = verdict
    for k in ("value", "weight", "gain_pct", "forward_pe", "usual_move"):
        n = _num(raw.get(k))
        if n is not None:
            out[k] = round(n, 6)
    watch = raw.get("watch")
    if isinstance(watch, str):
        watch = [watch]
    if isinstance(watch, list):
        w = [_clip(x, 90) for x in watch if _clip(x, 90)][:3]
        if w:
            out["watch"] = w
    return out


def _normalise_viz(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        from app.services.viz_spec import validate_spec

        spec, problems = validate_spec(raw)
    except Exception:  # noqa: BLE001 — a bad picture is dropped, never fatal
        return None
    return spec if spec and not problems else None


def enrich_summary(summary: dict[str, Any], raw: Any) -> dict[str, Any]:
    """Add the optional stats / viz / holdings / gaps from ``raw`` to a normalised summary."""
    if not isinstance(raw, dict):
        return summary
    stats_raw = raw.get("stats") if isinstance(raw.get("stats"), list) else []
    stats = [s for s in (normalise_stat(x) for x in stats_raw) if s]
    if stats:
        summary["stats"] = stats[:MAX_STATS]
    viz = _normalise_viz(raw.get("viz"))
    if viz:
        summary["viz"] = viz
    holdings_raw = raw.get("holdings") if isinstance(raw.get("holdings"), list) else []
    holdings = [h for h in (normalise_holding(x) for x in holdings_raw) if h]
    if holdings:
        summary["holdings"] = holdings[:MAX_HOLDINGS]
    gaps_raw = raw.get("gaps") if isinstance(raw.get("gaps"), list) else []
    gaps = [_clip(g, 40) for g in gaps_raw if _clip(g, 40)][:6]
    if gaps:
        summary["gaps"] = gaps
    return summary


# Machine-readable blocks a task emits for code (```json {...}```): kept as
# data in frontmatter, never rendered in the report.
_JSON_FENCE = re.compile(r"```json[ \t]*\n([\s\S]*?)\n?```[ \t]*\n?", re.IGNORECASE)


def extract_machine_blocks(text: str) -> tuple[dict[str, Any] | None, str]:
    """Strip every ```json fence holding a JSON object/array. Returns (data, text without them)."""
    if not text or "```json" not in text.lower():
        return None, text
    data: dict[str, Any] = {}
    pieces: list[str] = []
    last = 0
    for m in _JSON_FENCE.finditer(text):
        try:
            parsed = json.loads(m.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            data.update(parsed)
        elif isinstance(parsed, list):
            data.setdefault("items", []).extend(parsed)
        else:
            continue
        pieces.append(text[last:m.start()])
        last = m.end()
    if not pieces:
        return None, text
    pieces.append(text[last:])
    cleaned = re.sub(r"\n{3,}", "\n\n", "".join(pieces)).rstrip() + "\n"
    return data, cleaned


def data_frontmatter_line(data: dict[str, Any] | None) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n" if data else ""


def normalise_summary(raw: Any) -> dict[str, Any] | None:
    """Coerce a parsed summary into the canonical shape; None if unusable."""
    if not isinstance(raw, dict):
        return None
    headline = str(raw.get("headline") or "").strip()
    signal = str(raw.get("signal") or "low").strip().lower()
    if signal not in _SIGNALS:
        signal = "low"
    points_raw = raw.get("points") or []
    if isinstance(points_raw, str):
        points_raw = [points_raw]
    points = [str(p).strip() for p in points_raw if str(p).strip()][:3] if isinstance(points_raw, list) else []
    action = raw.get("action")
    action = str(action).strip() if action not in (None, "", "none", "None") else None
    if not headline and signal != "none":
        return None
    return enrich_summary({"headline": headline, "points": points, "action": action, "signal": signal}, raw)


def extract_summary(text: str) -> tuple[dict[str, Any] | None, str]:
    """Pull the last ```summary block out of a report. Returns (summary, text without it)."""
    if not text:
        return None, text
    matches = list(_SUMMARY_FENCE.finditer(text))
    if not matches:
        return None, text
    m = matches[-1]
    try:
        summary = normalise_summary(json.loads(m.group(1)))
    except (json.JSONDecodeError, TypeError):
        summary = None
    stripped = (text[: m.start()] + text[m.end():]).rstrip() + "\n"
    return summary, stripped


def summary_frontmatter_line(summary: dict[str, Any] | None) -> str:
    return f"summary: {json.dumps(summary, ensure_ascii=False)}\n" if summary else ""


def _plain(md: str) -> str:
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)
    out = re.sub(r"(\*\*|__|`|~~)", "", out)
    out = re.sub(r"(?<![\w*])[*_](?=\S)([^*_\n]+?)(?<=\S)[*_](?![\w*])", r"\1", out)
    return re.sub(r"\s+", " ", out).strip(" -:")


# Section labels from the report frame: never a run's headline.
GENERIC_HEADING = re.compile(
    r"^(key (numbers|figures|metrics)|at a glance|by the numbers|numbers|what stands out|risks?|notes?|"
    r"summary|tl;?\s?dr|overview|details|what to do|next steps)\b",
    re.IGNORECASE,
)


def derive_summary(body: str) -> dict[str, Any] | None:
    """Cheap fallback for reports written before summaries existed: the TL;DR's
    first sentence, else the first bolded takeaway, else the first heading.
    Never a raw excerpt."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^#{1,4}\s*(tl;?\s?dr|summary|bottom line|executive summary)\b", line.strip(), re.I):
            for nxt in lines[i + 1:]:
                t = nxt.strip()
                if not t:
                    continue
                if t.startswith("#"):
                    break
                t = re.sub(r"^([-*]|\d+\.)\s+", "", t)
                sentence = re.split(r"(?<=[.!?])\s+", _plain(t))[0]
                if len(sentence) > 12:
                    return {"headline": sentence[:220], "points": [], "action": None, "signal": None, "derived": True}
                break
    for m in re.finditer(r"\*\*([^*\n]{16,200})\*\*", body):
        txt = _plain(m.group(1))
        if len(txt) >= 16:
            return {"headline": txt, "points": [], "action": None, "signal": None, "derived": True}
    for line in lines:
        hm = re.match(r"^#{1,4}\s+(.+)$", line.strip())
        if hm and not GENERIC_HEADING.match(_plain(hm.group(1))):
            return {"headline": _plain(hm.group(1)), "points": [], "action": None, "signal": None, "derived": True}
    return None


def _summary_of(metadata: dict[str, Any], body: str) -> tuple[dict[str, Any] | None, str]:
    """(summary, body) for a stored artifact: frontmatter first, then an
    unstripped block in the body, then a derived fallback."""
    raw = metadata.get("summary")
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            summary = normalise_summary(json.loads(raw))
        except json.JSONDecodeError:
            summary = None
        if summary:
            _, body = extract_summary(body)
            return summary, body
    summary, body = extract_summary(body)
    if summary:
        return summary, body
    return derive_summary(body), body


def summary_at(path: str | Path | None) -> dict[str, Any] | None:
    """The summary of a stored report by its absolute path (frontmatter first); None if missing."""
    if not path:
        return None
    try:
        p = Path(path)
        if not p.is_file():
            return None
        metadata, body = _parse_frontmatter(p.read_text(encoding="utf-8"))
    except OSError:
        return None
    summary, _ = _summary_of(metadata, body)
    return summary


def _artifacts_root() -> Path:
    """Return the root directory for artifacts, creating it if needed."""
    root = artifacts_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _legacy_cron_root() -> Path:
    """Return the legacy cron_logs directory (may not exist)."""
    return memory_dir() / "cron_logs"


def _parse_yaml_value(raw: str) -> Any:
    """Parse a simple YAML scalar value without a full YAML library.

    Handles strings, numbers, booleans, and bracket-delimited lists.
    """
    raw = raw.strip()
    if not raw:
        return ""

    # Boolean
    if raw.lower() in ("true", "yes"):
        return True
    if raw.lower() in ("false", "no"):
        return False

    # List in [a, b, c] form
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        items: list[str] = []
        for item in inner.split(","):
            item = item.strip().strip("'\"")
            if item:
                items.append(item)
        return items

    # Number
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        pass

    # Plain string (strip surrounding quotes if present)
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]

    return raw


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse optional YAML frontmatter from a markdown file.

    Uses simple line-by-line key: value parsing (no PyYAML dependency).
    Returns (metadata_dict, body_text). If no frontmatter is found,
    metadata_dict will be empty and body_text will be the full text.
    """
    if not text.startswith("---"):
        return {}, text

    # Find the closing --- marker (must be on its own line)
    end_idx = text.find("\n---", 3)
    if end_idx == -1:
        return {}, text

    yaml_block = text[3:end_idx].strip()
    body = text[end_idx + 4:]  # skip past "\n---"
    if body.startswith("\n"):
        body = body[1:]

    metadata: dict[str, Any] = {}
    for line in yaml_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)", line)
        if match:
            key = match.group(1)
            value = _parse_yaml_value(match.group(2))
            metadata[key] = value

    return metadata, body


def _infer_type(relative_path: str) -> str:
    """Infer the artifact type from its relative path."""
    parts = Path(relative_path).parts
    if parts and parts[0] in ("scheduled", "chat", "adhoc"):
        return parts[0]
    return "adhoc"


def _infer_task_name(relative_path: str, artifact_type: str) -> str | None:
    """For scheduled artifacts, infer the task name from the path."""
    if artifact_type != "scheduled":
        return None
    parts = Path(relative_path).parts
    # expected: scheduled/{task_name}/{timestamp}.md
    if len(parts) >= 2:
        return parts[1]
    return None


def list_artifacts() -> list[dict[str, Any]]:
    """Walk the artifacts directory and return metadata for each .md file."""
    root = _artifacts_root()
    items: list[dict[str, Any]] = []

    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if not filename.endswith(".md"):
                continue
            full_path = Path(dirpath) / filename
            try:
                relative = str(full_path.relative_to(root))
            except ValueError:
                continue

            try:
                text = full_path.read_text(encoding="utf-8")
            except OSError:
                continue

            size_bytes = full_path.stat().st_size
            metadata, body = _parse_frontmatter(text)
            summary, _ = _summary_of(metadata, body)

            artifact_type = metadata.get("type") or _infer_type(relative)
            title = metadata.get("title") or filename.removesuffix(".md")
            created_at = metadata.get("created_at") or ""
            task_name = metadata.get("task_name") or _infer_task_name(relative, artifact_type)
            tags = metadata.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]

            # If created_at is missing, fall back to file mtime
            if not created_at:
                from datetime import datetime, timezone

                mtime = full_path.stat().st_mtime
                created_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

            items.append(
                {
                    "path": relative,
                    "title": str(title),
                    "type": artifact_type,
                    "created_at": str(created_at),
                    "task_name": task_name,
                    "tags": tags if isinstance(tags, list) else [],
                    "size_bytes": size_bytes,
                    "summary": summary,
                }
            )

    # Sort newest first
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items


def get_artifact(relative_path: str) -> dict[str, Any] | None:
    """Read a single artifact by its path relative to the artifacts root.

    Also checks the legacy ``memory/cron_logs/`` directory so that old
    log entries whose ``output_path`` pointed there still resolve.  The
    caller may pass a path like ``scheduled/weekly_review/20260218.md``
    (new layout) **or** ``weekly_review/20260218.md`` (legacy layout --
    the frontend strips the ``cron_logs/`` prefix).

    Returns None if the file does not exist in either location.
    """
    if "\x00" in relative_path:
        return None
    root = _artifacts_root()
    full_path = (root / relative_path).resolve()

    # Prevent path traversal
    try:
        full_path.relative_to(root)
    except ValueError:
        full_path = None  # type: ignore[assignment]

    if full_path is not None and full_path.is_file():
        text = full_path.read_text(encoding="utf-8")
        metadata, body = _parse_frontmatter(text)
        summary, body = _summary_of(metadata, body)
        metadata["summary"] = summary
        # Machine-readable blocks are data, never part of the rendered report
        # (older artifacts still carry them in the body).
        data, body = extract_machine_blocks(body)
        stored = metadata.get("data")
        if isinstance(stored, str) and stored.strip().startswith("{"):
            try:
                data = {**json.loads(stored), **(data or {})}
            except json.JSONDecodeError:
                pass
        if data:
            metadata["data"] = data
        else:
            metadata.pop("data", None)
        return {
            "path": relative_path,
            "content": body,
            "metadata": metadata,
        }

    # Fallback: try the legacy memory/cron_logs/ directory.
    # relative_path may be e.g. "weekly_review/20260218-010209.md"
    legacy_root = _legacy_cron_root()
    if legacy_root.is_dir():
        legacy_path = (legacy_root / relative_path).resolve()
        try:
            legacy_path.relative_to(legacy_root)
        except ValueError:
            return None
        if legacy_path.is_file():
            text = legacy_path.read_text(encoding="utf-8")
            metadata, body = _parse_frontmatter(text)
            return {
                "path": relative_path,
                "content": body,
                "metadata": metadata,
            }

    return None
