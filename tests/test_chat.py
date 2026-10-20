from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_chat_endpoint_with_mocked_llm():
    """
    Test POST /chat with mocked LangChain agent.invoke response.
    Verifies response structure, message content, and preserved session_id.
    """
    with patch("app.routers.chat.agent.invoke") as mock_invoke:
        mock_invoke.return_value = {
            "messages": [
                AIMessage(content="Flight WN1234 is currently ON TIME departing at 08:00 from Gate A12.")
            ]
        }

        payload = {
            "input": "What is the status of flight WN1234?",
            "session_id": "test-session-123",
        }
        response = client.post("/chat", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test-session-123"
        assert "Flight WN1234 is currently ON TIME" in data["response"]

        mock_invoke.assert_called_once()
        args, kwargs = mock_invoke.call_args
        assert kwargs["config"]["configurable"]["thread_id"] == "test-session-123"


def test_chat_endpoint_generates_session_id_when_omitted():
    """
    Test POST /chat automatically generates a unique session_id when not provided by client.
    """
    with patch("app.routers.chat.agent.invoke") as mock_invoke:
        mock_invoke.return_value = {
            "messages": [
                AIMessage(content="Hello! How can I assist you today?")
            ]
        }

        payload = {"input": "Hello"}
        response = client.post("/chat", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert len(data["session_id"]) > 10
        assert "Hello!" in data["response"]


def test_chat_endpoint_error_handling():
    """
    Test POST /chat returns HTTP 500 when the agent encounters an unrecoverable exception.
    """
    with patch("app.routers.chat.agent.invoke") as mock_invoke:
        mock_invoke.side_effect = RuntimeError("Bedrock throttling exception")

        payload = {"input": "Help me with my booking", "session_id": "err-session"}
        response = client.post("/chat", json=payload)

        assert response.status_code == 500
        assert "Bedrock throttling exception" in response.json()["detail"]


def test_auth_login_success():
    """Test POST /auth/login with valid credentials."""
    resp = client.post("/auth/login", json={"username": "guest1", "password": "indian2026"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "guest1"
    assert "token" in data


def test_auth_login_invalid():
    """Test POST /auth/login with bad password."""
    resp = client.post("/auth/login", json={"username": "guest1", "password": "wrongpassword"})
    assert resp.status_code == 401


def test_get_sessions_list():
    """Test GET /sessions returns a list of sessions."""
    with patch("app.routers.chat.get_user_sessions") as mock_sessions:
        mock_sessions.return_value = [
            {"session_id": "s-1", "title": "Flight status", "user_id": "vamsi", "updated_at": "2026-10-20T12:00:00Z", "created_at": "2026-10-20T12:00:00Z"}
        ]
        resp = client.get("/sessions?user_id=vamsi")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["session_id"] == "s-1"


def test_get_chat_history():
    """Test GET /chat/{session_id} formats previous messages from state when PG returns empty."""
    with patch("app.routers.chat.get_session_messages", return_value=[]):
        with patch("app.routers.chat.agent.get_state") as mock_state:
            mock_val = MagicMock()
            mock_val.values = {
                "messages": [
                    HumanMessage(content="Is flight WN1234 on time?"),
                    AIMessage(content="Yes, flight WN1234 is on time."),
                ]
            }
            mock_state.return_value = mock_val

            resp = client.get("/chat/hist-session-1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["session_id"] == "hist-session-1"
            assert len(data["messages"]) == 2
            assert data["messages"][0]["role"] == "user"
            assert data["messages"][1]["role"] == "assistant"


def test_get_chat_history_from_postgres():
    """Test GET /chat/{session_id} prioritizes human-readable chat history from PostgreSQL."""
    mock_messages = [
        {"id": "msg-1", "role": "user", "content": "Book flight WN1234", "tool_calls": None, "timestamp": "2026-10-20T12:00:00Z"},
        {"id": "msg-2", "role": "assistant", "content": "Booking confirmed!", "tool_calls": [{"name": "change_booking"}], "timestamp": "2026-10-20T12:00:05Z"},
    ]
    with patch("app.routers.chat.get_session_messages", return_value=mock_messages):
        resp = client.get("/chat/pg-session-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "pg-session-1"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["content"] == "Book flight WN1234"
        assert data["messages"][1]["tool_calls"][0]["name"] == "change_booking"


def test_delete_chat_session():
    """Test DELETE /chat/{session_id} removes session from DynamoDB and PostgreSQL."""
    with patch("app.routers.chat.delete_session", return_value=True), \
         patch("app.routers.chat.delete_session_messages", return_value=True):
        resp = client.delete("/chat/test-del-1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert resp.json()["session_id"] == "test-del-1"


def test_chat_stream_events():
    """Test POST /chat/stream SSE endpoint emits session, token, and done events."""
    async def fake_astream_events(*args, **kwargs):
        chunk_mock = MagicMock()
        chunk_mock.content = "Flight WN1234 is on time."
        yield {"event": "on_chat_model_stream", "data": {"chunk": chunk_mock}}

    with patch("app.routers.chat.agent.astream_events", side_effect=fake_astream_events), \
         patch("app.routers.chat.save_chat_message") as mock_save, \
         patch("app.routers.chat.upsert_session"):
        resp = client.post("/chat/stream", json={"input": "Status of WN1234", "session_id": "stream-sess-1"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = resp.text
        assert '"type": "session"' in body
        assert '"type": "token"' in body
        assert "Flight WN1234 is on time." in body
        assert '"type": "done"' in body
        # Verified save_chat_message was called for both user and assistant
        assert mock_save.call_count == 2


def test_chat_rehydrates_from_postgres_when_dynamo_checkpoint_missing():
    """
    Test that when a session's DynamoDB checkpoint is expired/empty (e.g. after 2 months),
    POST /chat automatically re-hydrates prior messages from PostgreSQL and passes full
    conversation history to the LLM agent.
    """
    mock_history = [
        {"role": "user", "content": "My booking code is CONF1A"},
        {"role": "assistant", "content": "Found your booking CONF1A for flight WN1234."},
    ]

    with patch("app.routers.chat.agent.get_state") as mock_get_state, \
         patch("app.routers.chat.get_session_messages", return_value=mock_history), \
         patch("app.routers.chat.agent.invoke") as mock_invoke, \
         patch("app.routers.chat.save_chat_message"), \
         patch("app.routers.chat.upsert_session"):

        # Simulate empty DynamoDB state (expired checkpoint)
        mock_empty_state = MagicMock()
        mock_empty_state.values = {"messages": []}
        mock_get_state.return_value = mock_empty_state

        mock_invoke.return_value = {
            "messages": [AIMessage(content="Rescheduled to flight WN5678 successfully.")]
        }

        resp = client.post("/chat", json={"input": "Change it to tomorrow", "session_id": "two-months-old-sess"})
        assert resp.status_code == 200
        assert "Rescheduled" in resp.json()["response"]

        # Verify agent.invoke received the re-hydrated history + current input
        mock_invoke.assert_called_once()
        invoked_payload = mock_invoke.call_args[0][0]
        assert len(invoked_payload["messages"]) == 3
        assert invoked_payload["messages"][0]["content"] == "My booking code is CONF1A"
        assert invoked_payload["messages"][1]["content"] == "Found your booking CONF1A for flight WN1234."
        assert invoked_payload["messages"][2]["content"] == "Change it to tomorrow"


