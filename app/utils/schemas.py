from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ChatInput(BaseModel):
    input: str
    session_id: Optional[str] = None
    user_id: Optional[str] = "vamsi"


class IngestInput(BaseModel):
    text: str
    source: str = "manual"


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    user_id: str
    username: str
    name: str
    role: str
    token: str


class SessionItem(BaseModel):
    session_id: str
    title: str
    user_id: str
    updated_at: str
    created_at: str


class MessageItem(BaseModel):
    id: Optional[str] = None
    role: str  # "user" | "assistant" | "tool"
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    timestamp: Optional[str] = None


class SessionDetail(BaseModel):
    session_id: str
    messages: List[MessageItem]