from datetime import date, timedelta
from unittest.mock import MagicMock, patch
import httpx
import pytest

from app.tools.flights import (
    _normalize_and_validate_flight_number,
    _normalize_and_validate_date,
    _normalize_and_validate_booking_ref,
    get_flight_status,
    change_booking,
)
from app.tools.itsm import (
    _validate_ticket_inputs,
    create_it_ticket,
)


# ==========================================
# 1. Flight Tools Input Validation Tests
# ==========================================

def test_flight_number_validation():
    # Digits only gets WN prefix
    fn, err = _normalize_and_validate_flight_number("1234")
    assert fn == "WN1234"
    assert err is None

    # Spaces and lowercase
    fn, err = _normalize_and_validate_flight_number("  wn 456  ")
    assert fn == "WN456"
    assert err is None

    # Empty
    fn, err = _normalize_and_validate_flight_number("")
    assert fn is None
    assert "empty" in err.lower()

    # Invalid characters
    fn, err = _normalize_and_validate_flight_number("WN@123")
    assert fn is None
    assert "invalid" in err.lower()


def test_date_validation():
    # Today
    d, err = _normalize_and_validate_date("today")
    assert d == date.today()
    assert err is None

    # Tomorrow
    d, err = _normalize_and_validate_date("tomorrow")
    assert d == date.today() + timedelta(days=1)
    assert err is None

    # Specific ISO date
    d, err = _normalize_and_validate_date("2026-10-20")
    assert d == date(2026, 10, 20)
    assert err is None

    # Invalid date
    d, err = _normalize_and_validate_date("not-a-date")
    assert d is None
    assert "invalid" in err.lower()


def test_booking_ref_validation():
    # Standard PNR
    ref, err = _normalize_and_validate_booking_ref("conf1a")
    assert ref == "CONF1A"
    assert err is None

    # Empty
    ref, err = _normalize_and_validate_booking_ref("")
    assert ref is None
    assert "empty" in err.lower()

    # Too short
    ref, err = _normalize_and_validate_booking_ref("ab")
    assert ref is None
    assert "invalid" in err.lower()


def test_flight_tool_invocations_catch_invalid_input():
    # Tool catches bad flight number without DB call
    res = get_flight_status.invoke({"flight_number": "??bad??", "date": "today"})
    assert "invalid" in res.lower()

    # Tool catches bad date without DB call
    res = get_flight_status.invoke({"flight_number": "WN1234", "date": "31-02-2026"})
    assert "invalid" in res.lower()

    # Tool catches bad booking reference
    res = change_booking.invoke({"booking_ref": "!", "new_flight_number": "WN1234"})
    assert "invalid" in res.lower()


# ==========================================
# 2. Flight Tools Mocked Database Tests
# ==========================================

@patch("app.tools.flights.pool.connection")
def test_get_flight_status_success(mock_connection):
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (
        "WN1234", date(2026, 10, 20), "DAL", "HOU", "08:00", "09:10", "ON TIME", "A12", 0
    )
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connection.return_value.__enter__.return_value = mock_conn

    result = get_flight_status.invoke({"flight_number": "WN1234", "date": "2026-10-20"})
    assert "Flight WN1234 on 2026-10-20: ON TIME" in result
    assert "DAL -> HOU" in result
    assert "Gate A12" in result


@patch("app.tools.flights.pool.connection")
def test_change_booking_success(mock_connection):
    mock_cursor = MagicMock()
    # 1st fetch: existing booking
    # 2nd fetch: target flight
    mock_cursor.fetchone.side_effect = [
        ("CONF1A", "Vamsi Marriwada", "WN1234", date(2026, 10, 20), "A18", "CONFIRMED"),
        ("WN2044", date(2026, 10, 20), "DAL", "HOU", "16:00", "ON TIME", "A14"),
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connection.return_value.__enter__.return_value = mock_conn

    result = change_booking.invoke({"booking_ref": "CONF1A", "new_flight_number": "WN2044"})
    assert "successfully changed" in result.lower()
    assert "WN2044" in result
    assert "Gate A14" in result


# ==========================================
# 3. ITSM Tool Input Validation & Execution Tests
# ==========================================

def test_itsm_ticket_input_validation():
    # Valid
    payload, err = _validate_ticket_inputs("Gate scanner offline", "P2", "agent@indian.com")
    assert err is None
    assert payload["priority"] == "P2"

    # Too short summary
    payload, err = _validate_ticket_inputs("No", "P2", "agent@Indian.com")
    assert payload is None
    assert "summary" in err.lower()

    # Invalid priority
    payload, err = _validate_ticket_inputs("Baggage belt jammed", "P9", "agent@indian.com")
    assert payload is None
    assert "priority" in err.lower()

    # Invalid email
    payload, err = _validate_ticket_inputs("Baggage belt jammed", "P3", "bad-email")
    assert payload is None
    assert "email" in err.lower()


@patch("httpx.Client.post")
def test_create_it_ticket_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "ticket_id": "INC-AB12CD",
        "priority": "P2",
        "status": "OPEN",
    }
    mock_post.return_value = mock_resp

    result = create_it_ticket.invoke({
        "issue_summary": "Gate 14 boarding pass printer malfunctioning",
        "priority": "P2",
        "reporter_email": "ops@indian.com",
    })

    assert "IT ticket created successfully: INC-AB12CD" in result
    assert "P2" in result


@patch("httpx.Client.post")
def test_create_it_ticket_service_error(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Service Error"
    mock_resp.json.side_effect = Exception("Not JSON")
    mock_post.return_value = mock_resp

    result = create_it_ticket.invoke({
        "issue_summary": "Baggage carousel error",
        "priority": "P3",
        "reporter_email": "ops@indian.com",
    })

    assert "Failed to create IT ticket" in result
    assert "500" in result
