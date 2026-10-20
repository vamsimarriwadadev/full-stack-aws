import logging
from typing import List, Dict, Any
from botocore.exceptions import ClientError
from app.core.aws import get_aws_clients
from app.core.config import settings

logger = logging.getLogger("skyassist.dynamodb")


def create_dynamodb_tables() -> List[str]:
    """
    Idempotently creates the required DynamoDB tables in on-demand (PAY_PER_REQUEST) mode:
    1. 'sessions' (hash key: session_id)
    2. 'tickets' (hash key: ticket_id)
    3. 'chat-checkpoints' (hash key: session_id / thread_id for LangGraph checkpointer)
    """
    clients = get_aws_clients()
    dynamodb = clients.dynamodb

    tables_to_create: List[Dict[str, Any]] = [
        {
            "TableName": settings.DYNAMODB_SESSIONS_TABLE,  # "sessions"
            "KeySchema": [{"AttributeName": "session_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "session_id", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": settings.DYNAMODB_TICKETS_TABLE,  # "tickets"
            "KeySchema": [{"AttributeName": "ticket_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "ticket_id", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": settings.DYNAMODB_CHECKPOINT_TABLE,  # "chat-checkpoints"
            "KeySchema": [{"AttributeName": "session_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "session_id", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
    ]

    created_or_verified = []

    for table_def in tables_to_create:
        t_name = table_def["TableName"]
        try:
            dynamodb.create_table(**table_def)
            logger.info(f"DynamoDB table '{t_name}' created successfully (PAY_PER_REQUEST).")
            created_or_verified.append(f"created: {t_name}")
        except ClientError as e:
            err_code = e.response.get("Error", {}).get("Code")
            if err_code in ("ResourceInUseException", "TableAlreadyExists"):
                logger.info(f"DynamoDB table '{t_name}' already exists.")
                created_or_verified.append(f"exists: {t_name}")
            else:
                logger.error(f"Failed to create DynamoDB table '{t_name}': {e}")
                raise
        except Exception as e:
            logger.error(f"Unexpected error creating DynamoDB table '{t_name}': {e}")
            raise

    return created_or_verified


def upsert_session(session_id: str, title: str, user_id: str = "vamsi") -> None:
    """Creates or updates a session metadata record in the DynamoDB sessions table with 30-day TTL."""
    try:
        import time
        from datetime import datetime, timezone
        clients = get_aws_clients()
        dynamodb = clients.dynamodb
        now = datetime.now(timezone.utc).isoformat()
        ttl_epoch = int(time.time()) + (60 * 60 * 24 * 30)  # 30-day automatic expiration

        dynamodb.update_item(
            TableName=settings.DYNAMODB_SESSIONS_TABLE,
            Key={"session_id": {"S": session_id}},
            UpdateExpression="SET #t = if_not_exists(#t, :title), #u = :user_id, #up = :updated, #c = if_not_exists(#c, :created), #ttl = :ttl",
            ExpressionAttributeNames={
                "#t": "title",
                "#u": "user_id",
                "#up": "updated_at",
                "#c": "created_at",
                "#ttl": "ttl",
            },
            ExpressionAttributeValues={
                ":title": {"S": title[:60]},
                ":user_id": {"S": user_id},
                ":updated": {"S": now},
                ":created": {"S": now},
                ":ttl": {"N": str(ttl_epoch)},
            },
        )
    except Exception as e:
        logger.warning(f"Could not upsert session metadata for {session_id}: {e}")


def get_user_sessions(user_id: str = "vamsi", limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieves all past chat sessions for a user from DynamoDB."""
    try:
        clients = get_aws_clients()
        dynamodb = clients.dynamodb
        resp = dynamodb.scan(
            TableName=settings.DYNAMODB_SESSIONS_TABLE,
            Limit=limit,
        )
        items = resp.get("Items", [])
        sessions = [
            {
                "session_id": it.get("session_id", {}).get("S", ""),
                "title": it.get("title", {}).get("S", "New Conversation"),
                "user_id": it.get("user_id", {}).get("S", user_id),
                "updated_at": it.get("updated_at", {}).get("S", ""),
                "created_at": it.get("created_at", {}).get("S", ""),
            }
            for it in items
        ]
        # Sort newest first
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return sessions
    except Exception as e:
        logger.warning(f"Could not scan user sessions: {e}")
        return []


def delete_session(session_id: str) -> bool:
    """Removes a session record from the DynamoDB sessions table."""
    try:
        clients = get_aws_clients()
        dynamodb = clients.dynamodb
        dynamodb.delete_item(
            TableName=settings.DYNAMODB_SESSIONS_TABLE,
            Key={"session_id": {"S": session_id}},
        )
        return True
    except Exception as e:
        logger.warning(f"Could not delete session {session_id}: {e}")
        return False



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Creating / verifying DynamoDB tables...")
    results = create_dynamodb_tables()
    for res in results:
        print(f" - {res}")
