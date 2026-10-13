"""Reports end with a ```summary block that Home shows instead of a raw excerpt.

The block is parsed and stripped when a scheduled report is stored (kept as
one-line JSON in frontmatter); older reports fall back to a cheap derived
headline — never raw markdown.
"""

from pathlib import Path
import json
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services import artifact_service  # noqa: E402
from app.services.artifact_service import derive_summary, extract_summary  # noqa: E402

REPORT = """## Key numbers
- Week: +2.4%

## What stands out
- NVDA is 12.8% of the book.

```summary
{"headline": "NVDA + PLTR are now 55% of your money", "points": ["a", "b", "c", "d"], "action": null, "signal": "HIGH"}
```
"""


def test_extract_and_strip():
    summary, body = extract_summary(REPORT)
    assert summary == {
        "headline": "NVDA + PLTR are now 55% of your money",
        "points": ["a", "b", "c"],  # capped at 3
        "action": None,
        "signal": "high",
    }
    assert "```summary" not in body and "What stands out" in body


def test_signal_none_allowed_without_headline():
    summary, _ = extract_summary('x\n```summary\n{"signal": "none"}\n```\n')
    assert summary and summary["signal"] == "none"


def test_bad_json_is_stripped_but_ignored():
    summary, body = extract_summary("Body\n```summary\n{not json}\n```\n")
    assert summary is None and "summary" not in body


def test_derived_fallback_never_raw():
    old = "_ISA account. Prices live from marketdata…_\n\n## TL;DR\nA **good** week: the book rose +2.4%. More text.\n"
    d = derive_summary(old)
    assert d["headline"] == "A good week: the book rose +2.4%."
    assert d["derived"] is True and d["signal"] is None
    # No TL;DR: first bolded takeaway, markers stripped.
    d2 = derive_summary("_source line_\n\nSome prose. **Concentration is now the main risk** here.")
    assert d2["headline"] == "Concentration is now the main risk"


def test_cron_log_path_stores_summary_as_frontmatter(tmp_path, monkeypatch):
    """Any caller of _cron_log_path (scheduled tasks, recipes) gets the block
    parsed into frontmatter and stripped from the body."""
    from app.services import task_scheduler_service as ts

    monkeypatch.setattr(ts, "project_root", lambda: tmp_path)
    path = Path(ts._cron_log_path("weekly_review", REPORT, task_kind="claude", description="Weekly"))
    text = path.read_text(encoding="utf-8")
    assert "```summary" not in text
    assert 'summary: {"headline": "NVDA + PLTR' in text
    meta, _ = artifact_service._parse_frontmatter(text)
    assert json.loads(meta["summary"])["signal"] == "high"


def test_stored_artifact_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact_service, "_artifacts_root", lambda: tmp_path)
    summary, body = extract_summary(REPORT)
    (tmp_path / "scheduled" / "weekly_review").mkdir(parents=True)
    (tmp_path / "scheduled" / "weekly_review" / "r.md").write_text(
        "---\ntype: scheduled\ntask_name: weekly_review\ncreated_at: 2026-10-12T10:00:00\ntitle: Weekly\n"
        + artifact_service.summary_frontmatter_line(summary)
        + "---\n\n"
        + body,
        encoding="utf-8",
    )
    [item] = artifact_service.list_artifacts()
    assert item["summary"]["headline"].startswith("NVDA + PLTR")
    detail = artifact_service.get_artifact("scheduled/weekly_review/r.md")
    assert detail["metadata"]["summary"]["signal"] == "high"
    assert "```summary" not in detail["content"]
    json.dumps(item)  # serialisable
