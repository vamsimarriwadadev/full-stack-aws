import logging
import re
from typing import Optional
import httpx
from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger("skyassist.tools.itsm")


def _validate_ticket_inputs(
    issue_summary: str, priority: str, reporter_email: str
) -> tuple[Optional[dict], Optional[str]]:
    """
    Validates ticket fields.
    Returns (cleaned_dict, error_message).
    """
    clean_summary = (issue_summary or "").strip()
    if not clean_summary or len(clean_summary) < 3:
        return None, "Issue summary is required and must be at least 3 characters."

    clean_priority = (priority or "P3").strip().upper()
    if clean_priority not in ("P1", "P2", "P3", "P4"):
        return None, f"Invalid priority '{priority}'. Must be one of P1, P2, P3, or P4."

    clean_email = (reporter_email or "ops@indian.com").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", clean_email):
        return None, f"Invalid reporter email address: '{reporter_email}'."

    return {
        "issue_summary": clean_summary,
        "priority": clean_priority,
        "reporter_email": clean_email,
    }, None


@tool
def create_it_ticket(
    issue_summary: str,
    priority: str = "P3",
    reporter_email: str = "ops@indian.com",
) -> str:
    """
    Create an internal IT service management (ITSM) incident ticket for operational issues,
    hardware/software errors, gate scanner malfunctions, or baggage system defects.

    Args:
        issue_summary: Brief description of the problem (e.g. 'Gate 14 boarding pass scanner offline').
        priority: Incident priority level: 'P1' (Critical), 'P2' (High), 'P3' (Medium), or 'P4' (Low). Default: 'P3'.
        reporter_email: Indian Airlines corporate email of the reporting employee. Default: 'ops@indian.com'.
    """
    cleaned, err = _validate_ticket_inputs(issue_summary, priority, reporter_email)
    if err:
        return f"Input validation error: {err}"

    headers = {
        "x-api-key": settings.ITSM_API_KEY,
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(settings.ITSM_URL, json=cleaned, headers=headers)

            if resp.status_code in (200, 201):
                data = resp.json()
                t_id = data.get("ticket_id", "UNKNOWN")
                prio = data.get("priority", cleaned["priority"])
                status = data.get("status", "OPEN")
                return (
                    f"IT ticket created successfully: {t_id} (Priority: {prio}, Status: {status}). "
                    f"Summary: '{cleaned['issue_summary']}'. Assigned to ETO operations triage."
                )
            else:
                try:
                    detail = resp.json().get("error") or resp.json().get("detail") or resp.text
                except Exception:
                    detail = resp.text
                logger.error(f"ITSM service error ({resp.status_code}): {detail}")
                return f"Failed to create IT ticket (HTTP {resp.status_code}): {detail}"

    except httpx.ConnectError:
        logger.error(f"Could not connect to ITSM service at {settings.ITSM_URL}")
        return f"ITSM service connection error: Could not reach ITSM system at {settings.ITSM_URL}."
    except httpx.TimeoutException:
        logger.error(f"Timeout calling ITSM service at {settings.ITSM_URL}")
        return "ITSM service error: Request timed out while creating ticket."
    except Exception as e:
        logger.error(f"Unexpected error creating IT ticket: {e}")
        return f"Unexpected error creating IT ticket: {str(e)}"
