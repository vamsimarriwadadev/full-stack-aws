import json
from unittest.mock import MagicMock, patch
from itsm_lambda.handler import lambda_handler


def test_lambda_unauthorized_without_api_key():
    event = {
        "httpMethod": "POST",
        "path": "/tickets",
        "headers": {},
        "body": json.dumps({"issue_summary": "Gate reader offline", "reporter_email": "agent@indian.com"}),
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 401
    assert "Unauthorized" in json.loads(response["body"])["error"]


@patch("itsm_lambda.handler.table")
def test_lambda_post_ticket_success(mock_table):
    event = {
        "httpMethod": "POST",
        "path": "/tickets",
        "headers": {"x-api-key": "mock-secret-key-123"},
        "body": json.dumps({
            "issue_summary": "Gate 12 scanner offline",
            "priority": "P2",
            "reporter_email": "ops@indian.com",
        }),
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["ticket_id"].startswith("INC-")
    assert body["priority"] == "P2"
    assert body["status"] == "OPEN"
    mock_table.put_item.assert_called_once()


@patch("itsm_lambda.handler.table")
def test_lambda_get_ticket_success(mock_table):
    mock_table.get_item.return_value = {
        "Item": {
            "ticket_id": "INC-123456",
            "issue_summary": "Jetbridge door jammed",
            "priority": "P1",
            "status": "OPEN",
        }
    }
    event = {
        "httpMethod": "GET",
        "path": "/tickets/INC-123456",
        "pathParameters": {"id": "INC-123456"},
        "headers": {"x-api-key": "mock-secret-key-123"},
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["ticket_id"] == "INC-123456"
    assert body["priority"] == "P1"


@patch("itsm_lambda.handler.table")
def test_lambda_get_ticket_not_found(mock_table):
    mock_table.get_item.return_value = {}
    event = {
        "httpMethod": "GET",
        "path": "/tickets/INC-999999",
        "pathParameters": {"id": "INC-999999"},
        "headers": {"x-api-key": "mock-secret-key-123"},
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 404
