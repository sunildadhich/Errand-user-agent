from pydantic import BaseModel, Field
from typing import Any


class ChatRequest(BaseModel):
    user_id: str = "local-user"
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    state: str
    data: dict[str, Any] = {}
