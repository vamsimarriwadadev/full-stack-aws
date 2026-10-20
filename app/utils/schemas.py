from pydantic import BaseModel

class ChatInput(BaseModel):
    input: str
    session_id: str | None = None

class IngestInput(BaseModel):
    text: str
    source: str = "manual"