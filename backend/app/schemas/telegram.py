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
    chat_id: str = Field(max_length=64)
    bot_token: str | None = Field(default=None, max_length=256)
    high_conviction_threshold: float = Field(ge=0, le=1)
    notify_general_updates: bool
    allowed_user_ids: list[int] = Field(default_factory=list, max_length=50)


class TelegramTestRequest(BaseModel):
    message: str = Field(default="OpenPF test notification", max_length=4000)


class TelegramTestResponse(BaseModel):
    sent: bool
    detail: str


class TelegramPollResponse(BaseModel):
    processed_updates: int


class TelegramAskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)


class TelegramAskResponse(BaseModel):
    reply: str
