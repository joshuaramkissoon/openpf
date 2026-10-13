"""Encryption at rest for connection secrets (API keys, tokens).

Secrets for connections added through Settings → Connections are stored as a
Fernet token, never as plain text. The key comes from ``OPENPF_SECRETS_KEY``
when set, else from a key file created next to the SQLite database (mode 0600;
for another database, in the instance's runtime folder). Back the file up with
the database: without it, saved keys can't be read and the connections show
"Reconnect".

Agent processes (``OPENPF_MCP_CONTEXT``) can never decrypt: ``open_secret``
returns nothing there, and the key file and env var are scrubbed / blocked.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KEY_ENV = "OPENPF_SECRETS_KEY"
KEY_FILE = "openpf-secrets.key"
_PREFIX = "fernet:"


class SecretUnavailable(RuntimeError):
    """The stored secret can't be decrypted (key missing/changed)."""


def _in_agent_process() -> bool:
    return bool(os.environ.get("OPENPF_MCP_CONTEXT", "").strip())


def key_path() -> Path:
    from app.services.mcp_registry import resolved_database_url

    url = resolved_database_url()
    prefix = "sqlite:///"
    if url.startswith(prefix) and ":memory:" not in url:
        return Path(url[len(prefix):]).expanduser().resolve().parent / KEY_FILE
    from app.services.claude_sdk_config import runtime_dir

    return runtime_dir() / KEY_FILE


def _fernet(create: bool):
    from cryptography.fernet import Fernet

    raw = os.environ.get(KEY_ENV, "").strip()
    if raw:
        return Fernet(raw.encode())
    path = key_path()
    if path.is_file():
        return Fernet(path.read_bytes().strip())
    if not create:
        raise SecretUnavailable("the secrets key file is missing")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    logger.info("created connection secrets key at %s", path)
    return Fernet(key)


def seal(secret: dict[str, Any]) -> str:
    """Encrypt a dict of secret fields into an opaque string for storage."""
    if _in_agent_process():
        raise SecretUnavailable("agent processes can't store secrets")
    token = _fernet(create=True).encrypt(json.dumps(secret, separators=(",", ":")).encode())
    return _PREFIX + token.decode()


def open_secret(blob: str | None) -> dict[str, Any]:
    """Decrypt a stored secret. Empty dict inside an agent process or when nothing
    is stored; raises :class:`SecretUnavailable` when it can't be decrypted."""
    if not blob or _in_agent_process():
        return {}
    if not str(blob).startswith(_PREFIX):
        raise SecretUnavailable("unrecognised secret format")
    from cryptography.fernet import InvalidToken

    try:
        data = _fernet(create=False).decrypt(str(blob)[len(_PREFIX):].encode())
    except InvalidToken as exc:
        raise SecretUnavailable("the secrets key changed since this connection was saved") from exc
    return json.loads(data)
