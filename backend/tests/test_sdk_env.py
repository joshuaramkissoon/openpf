"""Regression: every MCP subprocess env must put the backend root on PYTHONPATH.

The MCP servers (mcp_servers/*.py) do `from app.services...`, so they crash on
import unless PYTHONPATH includes the backend root — which manifests as
"T212 MCP not registered / unavailable" in agent runs. The runtimes used to build
this env separately and drifted (the scheduled one omitted PYTHONPATH); they now
all go through app.services.mcp_registry, so lock the invariant there.
"""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest

from app.services import mcp_registry


@pytest.fixture(autouse=True)
def _no_db_creds(monkeypatch):
    from app.services import claude_sdk_config

    monkeypatch.setattr(claude_sdk_config, "resolve_t212_env", lambda: {"T212_BASE_ENV": "demo"})


@pytest.mark.parametrize("context", sorted(mcp_registry.ALL_CONTEXTS))
def test_mcp_server_envs_put_backend_root_on_pythonpath(context):
    servers, _ = mcp_registry.build_mcp_servers(context)
    assert servers, f"{context} has no MCP servers"
    for name, cfg in servers.items():
        env = cfg["env"]
        assert env.get("PYTHONPATH"), f"{context}/{name} omits PYTHONPATH"
        assert (Path(env["PYTHONPATH"]) / "app").is_dir(), f"{context}/{name} PYTHONPATH lacks 'app'"


def test_sdk_env_puts_backend_root_on_pythonpath():
    env = mcp_registry.sdk_env()
    assert (Path(env["PYTHONPATH"]) / "app").is_dir()
