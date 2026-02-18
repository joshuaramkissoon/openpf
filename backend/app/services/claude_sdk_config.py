from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from app.core.config import get_settings

settings = get_settings()

_SETTING_SOURCE_ALLOWED = {"user", "project", "local"}


def project_root() -> Path:
    # In Docker the directory layout differs (no `backend/` prefix),
    # so allow an explicit override via PROJECT_ROOT env var.
    env = os.environ.get("PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    # backend/app/services -> backend -> repo root
    return Path(__file__).resolve().parents[3]


def parse_setting_sources(raw: str | None, *, require_project: bool = True) -> list[str]:
    if raw is None:
        raw = settings.claude_setting_sources

    values = [v.strip().lower() for v in str(raw).split(",") if v.strip()]
    picked = [v for v in values if v in _SETTING_SOURCE_ALLOWED]

    if not picked:
        picked = ["project"] if require_project else []

    if require_project and "project" not in picked:
        picked.append("project")

    return picked


def _resolve_cwd_candidate() -> Path:
    root = project_root()
    raw = str(settings.claude_project_cwd or ".").strip() or "."
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()

    # Keep SDK cwd scoped to this repo tree.
    try:
        candidate.relative_to(root)
    except ValueError:
        candidate = root

    return candidate


def resolve_sdk_cwd() -> Path:
    root = project_root()
    cwd = _resolve_cwd_candidate()
    cwd.mkdir(parents=True, exist_ok=True)

    src_hidden = root / ".claude"
    dst_hidden = cwd / ".claude"
    src_skills = src_hidden / "skills"
    dst_skills = dst_hidden / "skills"
    dst_skills.mkdir(parents=True, exist_ok=True)

    # If SDK cwd differs from repo root, seed project skills/CLAUDE.md there.
    if cwd != root and src_skills.exists():
        for skill_dir in src_skills.iterdir():
            if not skill_dir.is_dir():
                continue
            if not (skill_dir / "SKILL.md").exists():
                continue
            target = dst_skills / skill_dir.name
            if target.exists():
                continue
            shutil.copytree(skill_dir, target)

    src_claude_md = src_hidden / "CLAUDE.md"
    dst_claude_md = dst_hidden / "CLAUDE.md"
    if src_claude_md.exists() and not dst_claude_md.exists():
        dst_claude_md.parent.mkdir(parents=True, exist_ok=True)
        dst_claude_md.write_text(src_claude_md.read_text(encoding="utf-8"), encoding="utf-8")

    return cwd


def list_skill_files(cwd: Path | None = None) -> list[str]:
    base = cwd or resolve_sdk_cwd()
    out: list[str] = []
    skills_root = base / ".claude" / "skills"
    if not skills_root.exists():
        return out

    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        try:
            out.append(str(skill_md.relative_to(base)))
        except ValueError:
            out.append(str(skill_md))
    return out


def runtime_info() -> dict[str, Any]:
    cwd = resolve_sdk_cwd()
    source_memory_file = project_root() / ".claude" / "CLAUDE.md"
    runtime_memory_file = cwd / ".claude" / "CLAUDE.md"
    return {
        "project_root": str(project_root()),
        "cwd": str(cwd),
        "setting_sources": parse_setting_sources(settings.claude_setting_sources, require_project=True),
        "skills_dir": str(cwd / ".claude" / "skills"),
        "skill_files": list_skill_files(cwd),
        "claude_model": settings.claude_model,
        "claude_memory_model": settings.claude_memory_model,
        "memory_file": str(runtime_memory_file),
        "memory_source_file": str(source_memory_file),
        "memory_strategy": settings.claude_memory_strategy,
    }
