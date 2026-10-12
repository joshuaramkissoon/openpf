"""Static validation for view documents.

The real guardrail is the renderer: a ``sandbox="allow-scripts"`` iframe with an
opaque origin and a ``default-src 'none'`` CSP, so a view cannot reach the
network, the app's storage/DOM, or open windows whatever it contains. This check
is defence in depth and fast feedback for the builder: it rejects documents that
*try* to do those things, so a broken or hostile view never gets saved.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

MAX_VIEW_BYTES = 200 * 1024

# Namespace URIs are identifiers, not fetches — needed for createElementNS.
_ALLOWED_URLS = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink", "http://www.w3.org/1999/xhtml")

_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:fetch|importScripts)\s*\(", re.I), "network calls (fetch/importScripts) are not allowed — use openpf.data()"),
    (re.compile(r"\b(?:XMLHttpRequest|WebSocket|EventSource|RTCPeerConnection|SharedWorker|Worker)\b"), "network/worker APIs are not allowed — use openpf.data()"),
    (re.compile(r"\bsendBeacon\b"), "navigator.sendBeacon is not allowed"),
    (re.compile(r"\bimport\s*\(|^\s*import\s+[\w{*]", re.M), "ES module imports are not allowed — everything must be inline"),
    (re.compile(r"<\s*script\b[^>]*\bsrc\s*=", re.I), "external scripts are not allowed"),
    (re.compile(r"<\s*script\b[^>]*\btype\s*=\s*[\"']?module", re.I), "module scripts are not allowed — use a classic inline <script>"),
    (re.compile(r"<\s*(?:iframe|frame|object|embed|base|form|link|portal)\b", re.I), "this element is not allowed in a view"),
    (re.compile(r"<\s*meta\b[^>]*http-equiv", re.I), "meta http-equiv is not allowed"),
    (re.compile(r"\beval\s*\(|\bnew\s+Function\b|(?<![\w.])Function\s*\(", re.I), "eval / Function() are not allowed"),
    (re.compile(r"\bset(?:Timeout|Interval)\s*\(\s*[\"'`]"), "string timers are not allowed"),
    (re.compile(r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie|caches)\b"), "browser storage is unavailable — use openpf.state"),
    (re.compile(r"\b(?:window\.)?(?:parent|top|opener)\s*\.\s*(?:postMessage|location|document|window)"), "talking to the parent window directly is not allowed — use the openpf API"),
    (re.compile(r"\bwindow\.open\s*\(|\blocation\s*\.\s*(?:href|assign|replace)\b|\blocation\s*="), "navigation is not allowed — use openpf.navigate()"),
    (re.compile(r"@import\b", re.I), "CSS @import is not allowed"),
    (re.compile(r"url\(\s*[\"']?(?!data:|#|[\"']?\))", re.I), "CSS url() may only reference data: URIs"),
    (re.compile(r"\b(?:src|href|action|xlink:href|srcset|poster)\s*=\s*[\"']?\s*(?!data:)(?://|[a-z][a-z0-9+.-]*:)", re.I), "external src/href references are not allowed"),
]

_URL_RE = re.compile(r"\b(?:https?|wss?|ftp):(?://)?[^\s\"'<>)]*", re.I)
_META_RE = re.compile(r"<!--\s*openpf-view\s+(\{.*?\})\s*-->", re.S)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def validate_html(html: str) -> list[str]:
    """Return a list of human-readable problems (empty list = valid)."""
    problems: list[str] = []
    if not isinstance(html, str) or not html.strip():
        return ["the view is empty"]
    size = len(html.encode("utf-8"))
    if size > MAX_VIEW_BYTES:
        problems.append(f"the view is {size // 1024}KB; the limit is {MAX_VIEW_BYTES // 1024}KB")
    if not _META_RE.search(html):
        problems.append('missing header comment: <!-- openpf-view {"title": "...", "description": "...", "icon": "lucide-name"} -->')
    if "<script" not in html.lower():
        problems.append("a view needs an inline <script> that loads data with openpf.data()")

    for m in _URL_RE.finditer(html):
        url = m.group(0).rstrip(".,;")
        if not url.startswith(_ALLOWED_URLS):
            problems.append(f"line {_line_of(html, m.start())}: external URL '{url[:60]}' is not allowed (no network in views)")
    for pattern, message in _RULES:
        m = pattern.search(html)
        if m:
            problems.append(f"line {_line_of(html, m.start())}: {message}")

    # De-duplicate while keeping order.
    seen: set[str] = set()
    return [p for p in problems if not (p in seen or seen.add(p))]


def parse_meta(html: str) -> dict[str, Any]:
    """Read the ``<!-- openpf-view {...} -->`` header (title/description/icon)."""
    m = _META_RE.search(html or "")
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(data[k]) for k in ("title", "description", "icon") if data.get(k)}


def syntax_check(html: str, *, timeout: float = 8.0) -> list[str]:
    """Best-effort JS syntax check of inline scripts via ``node --check``.

    Skipped (returns []) when Node isn't installed — the in-app error panel and
    "Fix it" loop still catch runtime errors.
    """
    node = shutil.which("node")
    if not node:
        return []
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="openpf-view-") as tmp:
        for idx, m in enumerate(_SCRIPT_RE.finditer(html or "")):
            code = m.group(1)
            if not code.strip():
                continue
            path = Path(tmp) / f"script{idx}.js"
            path.write_text(code, encoding="utf-8")
            try:
                proc = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=timeout)
            except (OSError, subprocess.TimeoutExpired):
                return []
            if proc.returncode != 0:
                lines = [ln for ln in proc.stderr.splitlines() if ln.strip() and not ln.startswith("Node.js")]
                detail = " | ".join(lines[:4])[:400]
                problems.append(f"script #{idx + 1} has a syntax error: {detail}")
    return problems
