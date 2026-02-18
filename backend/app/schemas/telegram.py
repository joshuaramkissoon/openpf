from pydantic import BaseModel, Field


class TelegramConfigView(BaseModel):
    enabled: bool
    poll_enabled: bool
    chat_id: str
    high_conviction_threshold: float = Field(ge=0, le=1)
    notify_general_updates: bool
    allowed_user_ids: list[int] = Field(default_factory=list)
    bot_token_configured: bool


class TelegramConfigUpdate(BaseModel):
    enabled: bool
    poll_enabled: bool
    chat_id: str
    bot_token: str | None = None
    high_conviction_threshold: float = Field(ge=0, le=1)
    notify_general_updates: bool
    allowed_user_ids: list[int] = Field(default_factory=list)


class TelegramTestRequest(BaseModel):
    message: str = "MyPF test notification"


class TelegramTestResponse(BaseModel):
    sent: bool
    detail: str


class TelegramPollResponse(BaseModel):
    processed_updates: int


class TelegramAskRequest(BaseModel):
    message: str


class TelegramAskResponse(BaseModel):
    reply: str
