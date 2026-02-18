from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatSessionCreate(BaseModel):
    title: str = Field(default="Portfolio Chat", max_length=240)


class ChatSessionItem(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    title: str


class ChatMessageItem(BaseModel):
    id: int
    session_id: str
    created_at: datetime
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None


class ChatSendRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    account_kind: Literal["all", "invest", "stocks_isa"] = "all"
    display_currency: Literal["GBP", "USD"] = "GBP"
    redact_values: bool = False


class ChatSendResponse(BaseModel):
    session: ChatSessionItem
    user_message: ChatMessageItem
    assistant_message: ChatMessageItem


class ChatDeleteResponse(BaseModel):
    id: str
    deleted: bool


class ChatRuntimeInfo(BaseModel):
    project_root: str
    cwd: str
    setting_sources: list[str]
    skills_dir: str
    skill_files: list[str]
    claude_model: str
    claude_memory_model: str
    memory_file: str
    memory_source_file: str | None = None
    memory_strategy: str | None = None
    mcp_servers: list[str] = []
    allowed_tools: list[str] = []
    permission_mode: str | None = None
    runtime: str = "chat"
