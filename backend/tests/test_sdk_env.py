"""Regression: the MCP subprocess env must put the backend root on PYTHONPATH.

The trading212 MCP server (mcp_servers/t212.py) does `from app.services...`, so it
crashes on import unless PYTHONPATH includes the backend root — which manifests as
"T212 MCP not registered / unavailable" in agent runs. Three runtimes build this env
(chat, scheduled tasks, analyst cycle); they drifted (the scheduled one omitted
PYTHONPATH), silently breaking T212 for every scheduled job. Lock the invariant for
all three so they can't drift again.
"""

from pathlib import Path
import importlib
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest

_RUNTIME_MODULES = [
    "app.services.claude_chat_runtime",
    "app.services.task_scheduler_service",
    "app.services.claude_agent_runtime",
]


@pytest.mark.parametrize("module_name", _RUNTIME_MODULES)
def test_build_sdk_env_puts_backend_root_on_pythonpath(module_name):
    mod = importlib.import_module(module_name)
    env = mod._build_sdk_env()
    assert "PYTHONPATH" in env and env["PYTHONPATH"], f"{module_name} omits PYTHONPATH"
    # PYTHONPATH must point at the backend root so the t212 MCP can `import app`.
    assert (Path(env["PYTHONPATH"]) / "app").is_dir(), (
        f"{module_name} PYTHONPATH does not contain the 'app' package"
    )
