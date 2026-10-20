import logging
from typing import Dict, Any
import boto3
import psycopg
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector

from app.core.config import settings

logger = logging.getLogger("skyassist.db")

EMBED_DIM = settings.BEDROCK_EMBED_DIM


def generate_rds_auth_token() -> str:
    """
    Generates an ephemeral IAM authentication token for AWS RDS.
    Tokens are valid for 15 minutes.
    """
    client_kwargs = {"region_name": settings.AWS_REGION}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        client_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        client_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY

    rds_client = boto3.client("rds", **client_kwargs)
    return rds_client.generate_db_auth_token(
        DBHostname=settings.DB_HOST,
        Port=settings.DB_PORT,
        DBUsername=settings.DB_USER,
        Region=settings.AWS_REGION,
    )


def get_connection_kwargs() -> Dict[str, Any]:
    """
    Dynamically generates connection parameters for psycopg.
    Callable passed to ConnectionPool so every new connection gets
    a fresh RDS IAM auth token.
    """
    return {
        "host": settings.DB_HOST,
        "port": settings.DB_PORT,
        "dbname": settings.DB_NAME,
        "user": settings.DB_USER,
        "password": generate_rds_auth_token(),
        "sslmode": settings.DB_SSLMODE,
    }


def get_connection() -> psycopg.Connection:
    """Creates a direct standalone psycopg connection."""
    return psycopg.connect(**get_connection_kwargs(), autocommit=True)


def init_db() -> None:
    """
    Initializes PostgreSQL extensions, verifies connectivity,
    and idempotently creates and seeds the documents, flights, and bookings tables.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                version = cur.fetchone()[0]
                logger.info(f"Connected to RDS PostgreSQL: {version}")

            # 1. pgvector extension and documents table (for RAG)
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS documents (
                    id UUID PRIMARY KEY,
                    source TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding vector({EMBED_DIM}) NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS documents_embedding_idx "
                "ON documents USING hnsw (embedding vector_cosine_ops)"
            )

            # 2. Flights table
            conn.execute(
                """CREATE TABLE IF NOT EXISTS flights (
                    flight_number VARCHAR(10) NOT NULL,
                    flight_date DATE NOT NULL,
                    origin VARCHAR(4) NOT NULL,
                    destination VARCHAR(4) NOT NULL,
                    departure_time TIME NOT NULL,
                    arrival_time TIME NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'ON TIME',
                    gate VARCHAR(10),
                    delay_minutes INT NOT NULL DEFAULT 0,
                    PRIMARY KEY (flight_number, flight_date)
                )"""
            )

            # 3. Bookings table
            conn.execute(
                """CREATE TABLE IF NOT EXISTS bookings (
                    booking_ref VARCHAR(10) PRIMARY KEY,
                    passenger_name VARCHAR(100) NOT NULL,
                    flight_number VARCHAR(10) NOT NULL,
                    flight_date DATE NOT NULL,
                    seat VARCHAR(10) NOT NULL DEFAULT 'OPEN',
                    status VARCHAR(20) NOT NULL DEFAULT 'CONFIRMED',
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (flight_number, flight_date)
                        REFERENCES flights (flight_number, flight_date)
                        ON UPDATE CASCADE
                )"""
            )

            # 4. Permanent Chat Messages table for UI history
            conn.execute(
                """CREATE TABLE IF NOT EXISTS chat_messages (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    session_id VARCHAR(64) NOT NULL,
                    user_id VARCHAR(64) NOT NULL DEFAULT 'vamsi',
                    role VARCHAR(20) NOT NULL,
                    content TEXT NOT NULL,
                    tool_calls JSONB,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_chat_messages_session
                    ON chat_messages (session_id, created_at ASC)"""
            )

            # 5. Idempotent seed data for flights
            conn.execute(
                """INSERT INTO flights (
                    flight_number, flight_date, origin, destination,
                    departure_time, arrival_time, status, gate, delay_minutes
                ) VALUES
                    ('WN1234', CURRENT_DATE, 'DAL', 'HOU', '08:00:00', '09:10:00', 'ON TIME', 'A12', 0),
                    ('WN456',  CURRENT_DATE, 'MDW', 'DEN', '10:30:00', '12:05:00', 'DELAYED', 'B7', 45),
                    ('WN789',  CURRENT_DATE, 'PHX', 'LAS', '14:15:00', '15:20:00', 'ON TIME', 'C22', 0),
                    ('WN2044', CURRENT_DATE, 'DAL', 'HOU', '16:00:00', '17:10:00', 'ON TIME', 'A14', 0),
                    ('WN3105', CURRENT_DATE, 'MDW', 'DEN', '18:45:00', '20:20:00', 'ON TIME', 'B10', 0),
                    ('WN1234', CURRENT_DATE + 1, 'DAL', 'HOU', '08:00:00', '09:10:00', 'ON TIME', 'A12', 0),
                    ('WN456',  CURRENT_DATE + 1, 'MDW', 'DEN', '10:30:00', '12:05:00', 'ON TIME', 'B7', 0),
                    ('WN789',  CURRENT_DATE + 1, 'PHX', 'LAS', '14:15:00', '15:20:00', 'ON TIME', 'C22', 0),
                    ('WN2044', CURRENT_DATE + 1, 'DAL', 'HOU', '16:00:00', '17:10:00', 'ON TIME', 'A14', 0)
                ON CONFLICT (flight_number, flight_date) DO NOTHING;"""
            )

            # 6. Idempotent seed data for bookings
            conn.execute(
                """INSERT INTO bookings (
                    booking_ref, passenger_name, flight_number, flight_date, seat, status
                ) VALUES
                    ('CONF1A', 'Vamsi Marriwada', 'WN1234', CURRENT_DATE, 'A18', 'CONFIRMED'),
                    ('CONF2B', 'Jane Doe', 'WN456', CURRENT_DATE, 'B22', 'CONFIRMED'),
                    ('CONF3C', 'John Smith', 'WN789', CURRENT_DATE, 'A05', 'CONFIRMED')
                ON CONFLICT (booking_ref) DO NOTHING;"""
            )

            logger.info("Database initialized successfully (extensions, tables, chat_messages, and seed data verified).")
    except Exception as e:
        logger.warning(
            f"Database init skipped or unreachable ({e}). "
            f"If RDS is private, ensure VPN or security group allows access."
        )


def save_chat_message(
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    tool_calls: Any = None,
) -> None:
    """Persists a human-readable chat message to the PostgreSQL chat_messages table."""
    try:
        import json
        tool_json = json.dumps(tool_calls) if tool_calls else None
        with pool.connection(timeout=10) as conn:
            conn.execute(
                """INSERT INTO chat_messages (session_id, user_id, role, content, tool_calls)
                   VALUES (%s, %s, %s, %s, %s)""",
                (session_id, user_id, role, content, tool_json),
            )
    except Exception as e:
        logger.warning(f"Failed to persist chat message to database: {e}")


def get_session_messages(session_id: str) -> list[dict[str, Any]]:
    """Retrieves chronological messages for a session from the PostgreSQL chat_messages table."""
    try:
        import json
        with pool.connection(timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id::text, role, content, tool_calls, created_at::text
                       FROM chat_messages
                       WHERE session_id = %s
                       ORDER BY created_at ASC""",
                    (session_id,),
                )
                rows = cur.fetchall()
                messages = []
                for row in rows:
                    msg_id, role, content, raw_tool_calls, created_at = row
                    tool_calls = json.loads(raw_tool_calls) if isinstance(raw_tool_calls, str) else raw_tool_calls
                    messages.append({
                        "id": msg_id,
                        "role": role,
                        "content": content,
                        "tool_calls": tool_calls,
                        "timestamp": created_at,
                    })
                return messages
    except Exception as e:
        logger.warning(f"Could not load messages from PostgreSQL ({e})")
        return []


def delete_session_messages(session_id: str) -> bool:
    """Deletes all messages for a session from the PostgreSQL chat_messages table."""
    try:
        with pool.connection(timeout=10) as conn:
            conn.execute("DELETE FROM chat_messages WHERE session_id = %s", (session_id,))
        return True
    except Exception as e:
        logger.warning(f"Could not delete session messages from PostgreSQL: {e}")
        return False


# Global connection pool with dynamic IAM auth token generation
# max_lifetime=600 (10 min) recycles connections before 15-minute IAM token expiry
# min_size=0 avoids eager connection failure loops on startup if DB is offline/unauthenticated
import atexit

pool = ConnectionPool(
    conninfo="",
    kwargs=get_connection_kwargs,
    configure=register_vector,
    min_size=0,
    max_size=10,
    max_lifetime=600,
    open=True,
)
atexit.register(pool.close)
