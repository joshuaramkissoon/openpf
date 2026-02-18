"""Service for reading and listing artifacts from the filesystem."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from app.services.claude_sdk_config import project_root


def _artifacts_root() -> Path:
    """Return the root directory for artifacts, creating it if needed."""
    root = project_root() / ".claude" / "runtime" / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _legacy_cron_root() -> Path:
    """Return the legacy cron_logs directory (may not exist)."""
    return project_root() / ".claude" / "runtime" / "memory" / "cron_logs"


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
            metadata, _body = _parse_frontmatter(text)

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
