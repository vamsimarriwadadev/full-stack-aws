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
    and sets up the pgvector documents table.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                version = cur.fetchone()[0]
                logger.info(f"Connected to RDS PostgreSQL: {version}")

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
            logger.info("pgvector extension and documents table verified successfully.")
    except Exception as e:
        logger.warning(
            f"Database init skipped or unreachable ({e}). "
            f"If RDS is private, ensure VPN or security group allows access."
        )


# Global connection pool with dynamic IAM auth token generation
# max_lifetime=600 (10 min) recycles connections before 15-minute IAM token expiry
# min_size=0 avoids eager connection failure loops on startup if DB is offline/unauthenticated
pool = ConnectionPool(
    conninfo="",
    kwargs=get_connection_kwargs,
    configure=register_vector,
    min_size=0,
    max_size=10,
    max_lifetime=600,
    open=True,
)
