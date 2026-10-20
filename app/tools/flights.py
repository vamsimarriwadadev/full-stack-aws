import re
import logging
from datetime import date as dt_date, datetime, timedelta
from typing import Optional
from langchain_core.tools import tool

from app.core.db import pool

logger = logging.getLogger("skyassist.tools.flights")


def _normalize_and_validate_flight_number(raw_flight: str) -> tuple[Optional[str], Optional[str]]:
    """
    Normalizes flight number (e.g. '1234' -> 'WN1234', 'wn 456' -> 'WN456').
    Returns (normalized_flight_number, error_message).
    """
    cleaned = re.sub(r"\s+", "", (raw_flight or "").strip().upper())
    if not cleaned:
        return None, "Flight number cannot be empty."

    # If user provided numbers only (e.g. 1234), assume standard Indian WN prefix
    if cleaned.isdigit():
        cleaned = f"WN{cleaned}"

    if not re.match(r"^[A-Z0-9]{2,8}$", cleaned):
        return None, f"Invalid flight number format: '{raw_flight}'. Expected format like 'WN1234'."

    return cleaned, None


def _normalize_and_validate_date(raw_date: Optional[str], default_date: Optional[dt_date] = None) -> tuple[Optional[dt_date], Optional[str]]:
    """
    Parses and validates date input ('today', 'tomorrow', 'YYYY-MM-DD').
    Returns (date_obj, error_message).
    """
    if not raw_date or not raw_date.strip():
        if default_date:
            return default_date, None
        return dt_date.today(), None

    date_str = raw_date.strip().lower()

    if date_str == "today":
        return dt_date.today(), None
    if date_str == "tomorrow":
        return dt_date.today() + timedelta(days=1), None

    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d").date()
        return parsed, None
    except ValueError:
        return None, f"Invalid date '{raw_date}'. Please provide date in YYYY-MM-DD format (e.g. '2026-10-20') or 'today'."


def _normalize_and_validate_booking_ref(raw_ref: str) -> tuple[Optional[str], Optional[str]]:
    """
    Normalizes and validates booking reference (PNR confirmation code).
    Returns (cleaned_ref, error_message).
    """
    cleaned = (raw_ref or "").strip().upper()
    if not cleaned:
        return None, "Booking reference cannot be empty."

    if not re.match(r"^[A-Z0-9]{4,10}$", cleaned):
        return None, f"Invalid booking reference: '{raw_ref}'. Expected a 6-character alphanumeric code (e.g., 'CONF1A')."

    return cleaned, None


@tool
def get_flight_status(flight_number: str, date: str = "today") -> str:
    """
    Look up real-time flight status, schedule, delay, and gate for an airline flight.

    Args:
        flight_number: The flight number, e.g., 'WN1234' or '1234'.
        date: The flight date in YYYY-MM-DD format, or 'today' / 'tomorrow'. Defaults to 'today'.
    """
    # 1. Input validation
    clean_flight, err = _normalize_and_validate_flight_number(flight_number)
    if err:
        return err

    valid_date, err = _normalize_and_validate_date(date)
    if err:
        return err

    target_date_str = valid_date.isoformat()

    # 2. Query database
    try:
        with pool.connection(timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT flight_number, flight_date, origin, destination,
                           TO_CHAR(departure_time, 'HH24:MI') AS dep_time,
                           TO_CHAR(arrival_time, 'HH24:MI') AS arr_time,
                           status, gate, delay_minutes
                    FROM flights
                    WHERE flight_number = %s AND flight_date = %s;
                    """,
                    (clean_flight, valid_date),
                )
                row = cur.fetchone()

                if row:
                    fn, fdate, origin, dest, dep, arr, status, gate, delay = row
                    gate_info = f"Gate {gate}" if gate else "Gate TBD"
                    delay_info = f", delayed by {delay} mins" if delay > 0 else ""
                    return (
                        f"Flight {fn} on {fdate}: {status}{delay_info}. "
                        f"Route: {origin} -> {dest}. "
                        f"Scheduled Departure: {dep}, Scheduled Arrival: {arr}. {gate_info}."
                    )

                # Check if flight exists on other dates to give helpful feedback
                cur.execute(
                    """
                    SELECT flight_date, origin, destination, status
                    FROM flights
                    WHERE flight_number = %s
                    ORDER BY flight_date ASC LIMIT 3;
                    """,
                    (clean_flight,),
                )
                alt_rows = cur.fetchall()
                if alt_rows:
                    alt_dates = ", ".join(r[0].isoformat() for r in alt_rows)
                    return (
                        f"No flight found for {clean_flight} on {target_date_str}. "
                        f"Flight {clean_flight} is scheduled on: {alt_dates}."
                    )

                return f"No scheduled flights found matching '{clean_flight}' in the system."
    except Exception as e:
        logger.error(f"Error checking flight status for {clean_flight} on {target_date_str}: {e}")
        return f"Unable to fetch flight status due to a database error: {str(e)}"


@tool
def change_booking(booking_ref: str, new_flight_number: str, new_flight_date: str = "") -> str:
    """
    Change an existing passenger booking to a new flight number and optional date.

    Args:
        booking_ref: The 6-character booking reference code (e.g., 'CONF1A').
        new_flight_number: The target flight number to change to (e.g., 'WN2044').
        new_flight_date: Optional new flight date in YYYY-MM-DD format, 'today', or 'tomorrow'.
                         If omitted, retains the current booking's travel date.
    """
    # 1. Input validation
    clean_ref, err = _normalize_and_validate_booking_ref(booking_ref)
    if err:
        return err

    clean_new_flight, err = _normalize_and_validate_flight_number(new_flight_number)
    if err:
        return err

    # 2. Database update transaction
    try:
        with pool.connection(timeout=5) as conn:
            with conn.cursor() as cur:
                # A. Look up existing booking (with row lock)
                cur.execute(
                    """
                    SELECT booking_ref, passenger_name, flight_number, flight_date, seat, status
                    FROM bookings
                    WHERE booking_ref = %s
                    FOR UPDATE;
                    """,
                    (clean_ref,),
                )
                booking = cur.fetchone()
                if not booking:
                    return f"Booking '{clean_ref}' not found. Please verify the confirmation code."

                _, passenger_name, cur_flight, cur_date, seat, cur_status = booking

                # B. Determine and validate target flight date
                target_date, err = _normalize_and_validate_date(new_flight_date, default_date=cur_date)
                if err:
                    return err

                # Check if already booked on this exact flight & date
                if cur_flight == clean_new_flight and cur_date == target_date:
                    return (
                        f"Booking {clean_ref} for {passenger_name} is already confirmed on "
                        f"Flight {clean_new_flight} on {target_date.isoformat()}."
                    )

                # C. Check if the target flight exists and is operable
                cur.execute(
                    """
                    SELECT flight_number, flight_date, origin, destination,
                           TO_CHAR(departure_time, 'HH24:MI') AS dep_time,
                           status, gate
                    FROM flights
                    WHERE flight_number = %s AND flight_date = %s;
                    """,
                    (clean_new_flight, target_date),
                )
                flight_row = cur.fetchone()
                if not flight_row:
                    return (
                        f"Flight '{clean_new_flight}' is not scheduled on {target_date.isoformat()}. "
                        f"Please check the flight schedule or pick another date."
                    )

                _, _, origin, dest, dep_time, f_status, gate = flight_row
                if f_status.upper() == "CANCELLED":
                    return (
                        f"Cannot change booking: Flight {clean_new_flight} on {target_date.isoformat()} "
                        f"is CANCELLED."
                    )

                # D. Perform the update
                cur.execute(
                    """
                    UPDATE bookings
                    SET flight_number = %s,
                        flight_date = %s,
                        status = 'CONFIRMED',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE booking_ref = %s;
                    """,
                    (clean_new_flight, target_date, clean_ref),
                )

                gate_text = f" (Gate {gate})" if gate else ""
                return (
                    f"Booking {clean_ref} for {passenger_name} has been successfully changed! "
                    f"Previous: Flight {cur_flight} on {cur_date.isoformat()}. "
                    f"New: Flight {clean_new_flight} on {target_date.isoformat()} "
                    f"({origin} -> {dest}, Departs: {dep_time}{gate_text}). Seat: {seat}."
                )
    except Exception as e:
        logger.error(f"Error modifying booking {clean_ref} to {clean_new_flight}: {e}")
        return f"Unable to modify booking due to a database error: {str(e)}"
