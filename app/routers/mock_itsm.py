import re
import uuid
from datetime import datetime, timezone
from typing import Dict, Any
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter(prefix="/mock-itsm", tags=["Mock ITSM"])

# In-memory ticket store for local development/stubbing
_LOCAL_TICKETS: Dict[str, Dict[str, Any]] = {}


class TicketCreateRequest(BaseModel):
    issue_summary: str = Field(..., min_length=3)
    priority: str = Field("P3", pattern="^(P1|P2|P3|P4)$")
    reporter_email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/tickets", status_code=201)
def create_ticket(
    payload: TicketCreateRequest,
    x_api_key: str = Header(..., alias="x-api-key"),
) -> Dict[str, Any]:
    """Stubbed ITSM endpoint that creates an IT incident ticket."""
    if x_api_key != settings.ITSM_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API key.")

    ticket_id = f"INC-{uuid.uuid4().hex[:6].upper()}"
    ticket = {
        "ticket_id": ticket_id,
        "issue_summary": payload.issue_summary,
        "priority": payload.priority.upper(),
        "reporter_email": payload.reporter_email,
        "status": "OPEN",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _LOCAL_TICKETS[ticket_id] = ticket
    return ticket


@router.get("/tickets/{ticket_id}")
def get_ticket(
    ticket_id: str,
    x_api_key: str = Header(..., alias="x-api-key"),
) -> Dict[str, Any]:
    """Stubbed ITSM endpoint that retrieves ticket details."""
    if x_api_key != settings.ITSM_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API key.")

    ticket = _LOCAL_TICKETS.get(ticket_id.upper())
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found.")
    return ticket
