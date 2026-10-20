import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict
import boto3
from botocore.exceptions import ClientError

TABLE_NAME = os.environ.get("DYNAMODB_TICKETS_TABLE", "tickets")
EXPECTED_API_KEY = os.environ.get("ITSM_API_KEY", "mock-secret-key-123")

# AWS Lambda automatically provides AWS_REGION at runtime; fallback for local execution
region = os.environ.get("AWS_REGION", "ap-southeast-2")
dynamodb = boto3.resource("dynamodb", region_name=region)
table = dynamodb.Table(TABLE_NAME)


def _response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,x-api-key,X-API-Key",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body),
    }


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    AWS Lambda entrypoint behind API Gateway.
    Implements:
      - POST /tickets (creates a ticket in DynamoDB)
      - GET /tickets/{id} (retrieves ticket details)
    """
    # 1. Handle CORS Preflight OPTIONS
    http_method = (
        event.get("httpMethod")
        or event.get("requestContext", {}).get("http", {}).get("method")
        or ""
    ).upper()

    if http_method == "OPTIONS":
        return _response(200, {"message": "OK"})

    # 2. Authenticate API Key
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    provided_key = headers.get("x-api-key")

    if EXPECTED_API_KEY and provided_key != EXPECTED_API_KEY:
        return _response(401, {"error": "Unauthorized: invalid or missing API key."})

    raw_path = event.get("path") or event.get("rawPath") or ""
    path_parameters = event.get("pathParameters") or {}

    try:
        # 3. POST /tickets
        if http_method == "POST":
            raw_body = event.get("body") or "{}"
            if isinstance(raw_body, str):
                try:
                    payload = json.loads(raw_body)
                except json.JSONDecodeError:
                    return _response(400, {"error": "Invalid JSON payload."})
            else:
                payload = raw_body

            issue_summary = (payload.get("issue_summary") or "").strip()
            priority = (payload.get("priority") or "P3").strip().upper()
            reporter_email = (payload.get("reporter_email") or "").strip().lower()

            # Validation
            if not issue_summary:
                return _response(400, {"error": "'issue_summary' is required."})

            if priority not in ("P1", "P2", "P3", "P4"):
                return _response(400, {"error": "Invalid 'priority'. Must be one of P1, P2, P3, P4."})

            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", reporter_email):
                return _response(400, {"error": "Invalid 'reporter_email' address."})

            ticket_id = f"INC-{uuid.uuid4().hex[:6].upper()}"
            created_at = datetime.now(timezone.utc).isoformat()

            ticket_item = {
                "ticket_id": ticket_id,
                "issue_summary": issue_summary,
                "priority": priority,
                "reporter_email": reporter_email,
                "status": "OPEN",
                "created_at": created_at,
            }

            table.put_item(Item=ticket_item)
            return _response(201, ticket_item)

        # 4. GET /tickets/{id}
        elif http_method == "GET":
            ticket_id = path_parameters.get("id") or path_parameters.get("ticket_id")
            if not ticket_id:
                # Try parsing from raw path: e.g. /tickets/INC-123456
                parts = [p for p in raw_path.strip("/").split("/") if p]
                if parts and parts[-1] != "tickets":
                    ticket_id = parts[-1]

            if not ticket_id or ticket_id == "tickets":
                return _response(400, {"error": "Ticket ID must be specified in the URL path."})

            response = table.get_item(Key={"ticket_id": ticket_id.upper()})
            item = response.get("Item")
            if not item:
                return _response(404, {"error": f"Ticket '{ticket_id}' not found."})

            return _response(200, item)

        else:
            return _response(405, {"error": f"Method '{http_method}' not allowed."})

    except ClientError as e:
        return _response(500, {"error": f"DynamoDB error: {e.response.get('Error', {}).get('Message', str(e))}"})
    except Exception as e:
        return _response(500, {"error": f"Internal server error: {str(e)}"})
