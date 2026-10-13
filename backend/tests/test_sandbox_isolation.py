"""Sandbox (OPENPF_SANDBOX=1) and live instances must never see each other's
data, and a sandbox must never reach a live broker or use real keys.

Each scenario runs in a fresh interpreter (settings are cached per process)
against a throwaway fixture repo via PROJECT_ROOT — never the real runtime."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
_SCRUB = ("DATABASE_URL", "VIEWS_DATA_DIR", "OPENPF_RUNTIME_DIR", "OPENPF_SANDBOX", "OPENPF_SANDBOX_STATE",
          "T212_BASE_ENV", "T212_SANDBOX_URL", "CLAUDE_PROJECT_CWD", "AGENT_WORKSPACE", "PROJECT_ROOT")


@pytest.fixture()
def repo(tmp_path):
    """A fake checkout holding a LIVE instance's private runtime data."""
    root = tmp_path / "repo"
    live = root / ".claude" / "runtime"
    (live / ".claude").mkdir(parents=True)
    (live / ".claude" / "CLAUDE.md").write_text("# Archie identity (tracked template)\n")
    (live / "memory").mkdir()
    (live / "memory" / "user-profile.md").write_text("LIVE-PROFILE real name, real balances\n")
    weekly = live / "artifacts" / "scheduled" / "weekly_review"
    weekly.mkdir(parents=True)
    weekly.joinpath("20261012-090243.md").write_text("---\ntype: scheduled\n---\nLIVE-REVIEW £123,456\n")
    (root / "backend").mkdir()
    return root


def _run(repo: Path, code: str, **env: str) -> dict:
    base = {k: v for k, v in os.environ.items() if k not in _SCRUB and not k.startswith("T212_")}
    base.update({"PROJECT_ROOT": str(repo), "PORT": "8099", **env})
    # cwd=fixture backend so no real .env is picked up.
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=repo / "backend", env={**base, "PYTHONPATH": str(BACKEND)}, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


_SANDBOX_PROBE = """
import asyncio, json
from app.core.config import get_settings
from app.core.database import SessionLocal, init_db
from app.core.sandbox import verify_isolation, SandboxViolation
from app.services import artifact_service, claude_sdk_config, execution_service
from app.services.config_store import ConfigStore
from app.services.t212_client import T212Client, T212Error, build_t212_client

init_db()
out = {}
s = get_settings()
out["db"] = s.database_url
out["runtime"] = str(claude_sdk_config.runtime_dir())
out["artifacts"] = [a["path"] for a in artifact_service.list_artifacts()]
out["memory_files"] = sorted(p.name for p in claude_sdk_config.memory_dir().rglob("*")) if claude_sdk_config.memory_dir().exists() else []

# Real keys sitting in the DB are ignored.
with SessionLocal() as db:
    store = ConfigStore(db)
    store.set("credentials", {"invest": {"t212_api_key": "REAL-KEY", "t212_api_secret": "REAL-SECRET",
                                         "exec_api_key": "REAL-EXEC", "exec_api_secret": "REAL-EXEC-S"}})
    store.set("broker_config", {"t212_base_env": "live", "broker_mode": "live"})
    out["creds"] = store.get_account_credentials("invest")
    out["base_env"] = store.get_broker()["t212_base_env"]
    out["client_url"] = build_t212_client(store, "invest", purpose="execute").base_url
    out["verify"] = verify_isolation()

# A client explicitly built for live is pinned to the mock broker anyway...
out["live_client_url"] = T212Client(api_key="k", api_secret="s", base_env="live").base_url
# ...and a request to a live host is refused before any network I/O.
from app.core.sandbox import assert_sandbox_url
try:
    assert_sandbox_url("https://live.trading212.com/api/v0/equity/account/summary")
    out["live_call"] = "ALLOWED"
except SandboxViolation as exc:
    out["live_call"] = str(exc)

guard = lambda tool, inp: asyncio.run(claude_sdk_config._file_guard({"tool_name": tool, "tool_input": inp}, None, None))
live_mem = str(claude_sdk_config.project_root() / ".claude/runtime/memory/user-profile.md")
out["read_live_memory"] = guard("Read", {"file_path": live_mem})
out["glob_live"] = guard("Glob", {"pattern": "**/*.md", "path": str(claude_sdk_config.project_root() / ".claude/runtime")})
out["write_own_artifact"] = guard("Write", {"file_path": "artifacts/chat/x.md"})
out["bash_live"] = asyncio.run(claude_sdk_config._bash_guard({"tool_input": {"command": "cat ../../.claude/runtime/memory/user-profile.md"}}, None, None))
out["mcp_env"] = claude_sdk_config.resolve_t212_env()
print(json.dumps(out, default=str))
"""


def test_sandbox_sees_no_live_data_and_cannot_reach_live(repo):
    out = _run(repo, _SANDBOX_PROBE, OPENPF_SANDBOX="1")
    demo = repo / ".demo"
    assert out["db"].endswith(str(demo / "openpf-demo.db"))
    assert out["runtime"] == str((demo / "runtime").resolve())
    assert not any("weekly_review" in p for p in out["artifacts"]), out["artifacts"]
    assert "user-profile.md" not in out["memory_files"]
    # identity template is seeded, private data is not
    assert (demo / "runtime" / ".claude" / "CLAUDE.md").is_file()
    assert not (demo / "runtime" / "memory" / "user-profile.md").exists()

    assert out["creds"]["t212_api_key"].startswith("sandbox-")
    assert "REAL" not in json.dumps(out["creds"])
    assert out["base_env"] == "sandbox"
    assert out["client_url"] == "http://127.0.0.1:8099/sandbox-t212/api/v0"
    assert any("SANDBOX" in line or "mock" in line for line in out["verify"])
    assert out["live_client_url"].startswith("http://127.0.0.1:8099/sandbox-t212")
    assert "refusing" in out["live_call"]

    for key in ("read_live_memory", "glob_live", "bash_live"):
        assert out[key]["hookSpecificOutput"]["permissionDecision"] == "deny", key
    assert out["write_own_artifact"] == {}

    env = out["mcp_env"]
    assert env["T212_BASE_ENV"] == "sandbox" and "127.0.0.1" in env["T212_SANDBOX_URL"]
    assert all(v.startswith("sandbox") or k in {"T212_BASE_ENV", "T212_SANDBOX_URL", "OPENPF_SANDBOX"}
               for k, v in env.items())


def test_live_instance_sees_no_sandbox_data(repo):
    sbx = repo / ".demo" / "runtime"
    (sbx / "artifacts" / "chat").mkdir(parents=True)
    (sbx / "artifacts" / "chat" / "demo.md").write_text("SANDBOX-ONLY\n")
    out = _run(repo, """
        import asyncio, json
        from app.services import artifact_service, claude_sdk_config
        guard = lambda inp: asyncio.run(claude_sdk_config._file_guard({"tool_name": "Read", "tool_input": inp}, None, None))
        print(json.dumps({
            "runtime": str(claude_sdk_config.runtime_dir()),
            "artifacts": [a["path"] for a in artifact_service.list_artifacts()],
            "read_demo": guard({"file_path": str(claude_sdk_config.project_root() / ".demo/runtime/artifacts/chat/demo.md")}),
            "read_own": guard({"file_path": "memory/user-profile.md"}),
        }))
    """)
    assert out["runtime"] == str((repo / ".claude" / "runtime").resolve())  # existing installs unchanged
    assert any("weekly_review" in p for p in out["artifacts"])
    assert not any("demo.md" in p for p in out["artifacts"])
    assert out["read_demo"]["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["read_own"] == {}


@pytest.mark.parametrize(
    "env",
    [
        {"DATABASE_URL": "sqlite:///{repo}/backend/openpf.db"},
        {"DATABASE_URL": "sqlite:///{repo}/backend/mypf.db"},
        {"OPENPF_RUNTIME_DIR": "{repo}/.claude/runtime"},
        {"OPENPF_RUNTIME_DIR": "{repo}/.claude/runtime/memory"},
    ],
)
def test_sandbox_refuses_live_folders(repo, env):
    env = {k: v.format(repo=repo) for k, v in env.items()}
    base = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    proc = subprocess.run(
        [sys.executable, "-c", "from app.core.config import get_settings; get_settings()"],
        cwd=repo / "backend", capture_output=True, text=True, timeout=60,
        env={**base, "PYTHONPATH": str(BACKEND), "PROJECT_ROOT": str(repo), "OPENPF_SANDBOX": "1", **env},
    )
    assert proc.returncode != 0
    assert "SandboxViolation" in proc.stderr


def test_sandbox_mcp_t212_refuses_non_loopback_url(repo):
    out = _run(repo, """
        import json, sys
        sys.path.insert(0, ".")
        from app.core.sandbox import SandboxViolation
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location("t212mcp", pathlib.Path(%r) / "mcp_servers" / "t212.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        try:
            mod._base_url(); res = "ALLOWED"
        except SandboxViolation as exc:
            res = str(exc)
        print(json.dumps({"res": res}))
    """ % str(BACKEND), OPENPF_SANDBOX="1", T212_BASE_ENV="live", T212_SANDBOX_URL="https://live.trading212.com/api/v0",
        MCP_LOG_DIR=str(repo / "logs"))
    assert "refusing" in out["res"]
