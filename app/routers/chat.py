import json
import logging
import uuid
from typing import List, Dict, Any, Optional
import numpy as np

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain_aws import ChatBedrockConverse, BedrockEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph_checkpoint_aws import DynamoDBSaver
from langgraph.checkpoint.memory import MemorySaver

from app.core.aws import get_aws_clients, get_aws_session
from app.core.config import settings
from app.core.db import pool, save_chat_message, get_session_messages, delete_session_messages
from app.core.dynamodb import upsert_session, get_user_sessions, delete_session
from app.utils.schemas import ChatInput, SessionItem, MessageItem, SessionDetail
from app.tools.flights import get_flight_status, change_booking
from app.tools.itsm import create_it_ticket

logger = logging.getLogger("skyassist.chat")

clients = get_aws_clients()
bedrock_runtime_client = clients.bedrock_runtime

EMBED_MODEL_ID = settings.BEDROCK_EMBED_MODEL_ID
EMBED_DIM = settings.BEDROCK_EMBED_DIM

embeddings = BedrockEmbeddings(
    client=bedrock_runtime_client,
    model_id=EMBED_MODEL_ID,
    model_kwargs={"dimensions": EMBED_DIM, "normalize": True},
)
splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)

# Enable streaming=True for real-time token delivery
llm = ChatBedrockConverse(
    model_id=settings.BEDROCK_MODEL_ID,
    client=bedrock_runtime_client,
    streaming=True,
)


@tool
def search_knowledge_base(query: str, top_k: int = 4) -> str:
    """Search the internal airline knowledge base. Use for general policies,
    baggage allowances, or questions answered by ingested documentation."""
    try:
        qvec = np.array(embeddings.embed_query(query))
        with pool.connection(timeout=10) as conn:
            rows = conn.execute(
                """SELECT source, content, embedding <=> %s AS dist
                   FROM documents ORDER BY embedding <=> %s LIMIT %s""",
                (qvec, qvec, top_k),
            ).fetchall()
        if not rows:
            return "No relevant documents found."
        return "\n\n".join(f"[{s} | dist={d:.3f}]\n{c}" for s, c, d in rows)
    except Exception as e:
        logger.warning(f"Knowledge base search unavailable: {e}")
        return "Internal knowledge base is currently unreachable."


# DynamoDB Checkpointer for persistent multi-turn conversational session history
try:
    session = get_aws_session(settings.AWS_REGION)
    checkpointer = DynamoDBSaver(
        table_name=settings.DYNAMODB_CHECKPOINT_TABLE,  # "chat-checkpoints" with PK/SK schema
        session=session,
        region_name=settings.AWS_REGION,
        ttl_seconds=60 * 60 * 24 * 30,  # 30-day session retention
    )
except Exception as e:
    logger.warning(f"Could not connect DynamoDBSaver ({e}), falling back to in-memory checkpointer.")
    checkpointer = MemorySaver()

# 3 Core Tools + Knowledge Base
tools = [get_flight_status, change_booking, create_it_ticket, search_knowledge_base]

system_prompt = (
    "You are SkyAssist, the enterprise Agentic AI assistant for Southwest Airlines. "
    "You assist passengers and airline personnel with operations, reservations, and IT support.\n"
    "You have access to 3 primary operational tools:\n"
    "1. `get_flight_status`: Query real-time flight schedules, delay times, gates, and statuses.\n"
    "2. `change_booking`: Modify a passenger's confirmed booking to a different flight or date.\n"
    "3. `create_it_ticket`: Open an IT Service Management incident ticket for equipment errors, scanner issues, or system outages.\n"
    "4. `search_knowledge_base`: Retrieve company policies, baggage guidelines, or general travel FAQs.\n"
    "Be professional, accurate, and concise. Always confirm actions with clear details. "
    "If required information (e.g., booking code, flight number) is missing, politely ask the user."
)

agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt=system_prompt,
    checkpointer=checkpointer,
)

router = APIRouter()


# =========================================================================
# 1. Standard Chat (Synchronous / Blocking)
# =========================================================================

@router.post("/chat")
def chat(payload: ChatInput):
    """
    Standard synchronous chat endpoint.
    Maintains multi-turn context across requests using session_id in DynamoDB
    and permanently stores human-readable chat history in PostgreSQL.
    """
    try:
        sid = payload.session_id or uuid.uuid4().hex
        uid = payload.user_id or "vamsi"
        upsert_session(session_id=sid, title=payload.input, user_id=uid)
        save_chat_message(session_id=sid, user_id=uid, role="user", content=payload.input)

        result = agent.invoke(
            {"messages": [{"role": "user", "content": payload.input}]},
            config={"configurable": {"thread_id": sid}},
        )

        last_message = result["messages"][-1]
        content = last_message.content
        if isinstance(content, list):
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)

        tool_calls = None
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            tool_calls = [
                {"name": tc.get("name"), "args": tc.get("args"), "id": tc.get("id")}
                for tc in last_message.tool_calls
            ]

        save_chat_message(
            session_id=sid,
            user_id=uid,
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        )

        return {"response": content, "session_id": sid}
    except Exception as e:
        logger.error(f"Error executing chat for session {payload.session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# 2. Real-Time Streaming Chat (Server-Sent Events)
# =========================================================================

@router.post("/chat/stream")
async def chat_stream(payload: ChatInput):
    """
    Server-Sent Events (SSE) streaming endpoint.
    Streams real-time token deltas, tool invocation events, and token usage metrics to the UI.
    Persists user & assistant messages into PostgreSQL for UI chat history reload.

    Event types:
      - 'session': Initial session identifier
      - 'token': Incremental LLM token chunk
      - 'tool_start': Fired when a tool begins execution with arguments
      - 'tool_end': Fired when a tool completes with results
      - 'metadata': Token count and latency metrics
      - 'done': Final completion signal
    """
    sid = payload.session_id or uuid.uuid4().hex
    uid = payload.user_id or "vamsi"
    upsert_session(session_id=sid, title=payload.input, user_id=uid)
    save_chat_message(session_id=sid, user_id=uid, role="user", content=payload.input)

    async def event_generator():
        yield f"data: {json.dumps({'type': 'session', 'session_id': sid})}\n\n"

        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "latency_ms": 0}
        accumulated_text = ""
        captured_tool_calls: List[Dict[str, Any]] = []
        streamed_model_tokens = False

        try:
            async for event in agent.astream_events(
                {"messages": [{"role": "user", "content": payload.input}]},
                config={"configurable": {"thread_id": sid}},
                version="v2",
            ):
                kind = event["event"]

                # 1. Stream tokens as they are generated by Bedrock
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    text = ""
                    if isinstance(chunk.content, str):
                        text = chunk.content
                    elif isinstance(chunk.content, list):
                        for block in chunk.content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text += block.get("text", "")
                            elif isinstance(block, str):
                                text += block

                    if text:
                        streamed_model_tokens = True
                        accumulated_text += text
                        yield f"data: {json.dumps({'type': 'token', 'content': text})}\n\n"

                elif kind == "on_chain_stream" and event.get("name") == "model":
                    chunk_data = event["data"].get("chunk")
                    text_to_stream = ""
                    if isinstance(chunk_data, list):
                        for item in chunk_data:
                            update = getattr(item, "update", {})
                            if isinstance(update, dict):
                                for m in update.get("messages", []):
                                    if hasattr(m, "content") and not getattr(m, "tool_calls", None):
                                        text_to_stream += m.content if isinstance(m.content, str) else str(m.content)
                    elif isinstance(chunk_data, dict):
                        for m in chunk_data.get("messages", []):
                            if hasattr(m, "content") and not getattr(m, "tool_calls", None):
                                text_to_stream += m.content if isinstance(m.content, str) else str(m.content)

                    # Only emit fallback word chunks if direct model stream didn't already stream
                    if text_to_stream and not streamed_model_tokens:
                        accumulated_text += text_to_stream
                        import asyncio
                        words = text_to_stream.split(" ")
                        for idx, word in enumerate(words):
                            token_piece = word + (" " if idx < len(words) - 1 else "")
                            yield f"data: {json.dumps({'type': 'token', 'content': token_piece})}\n\n"
                            await asyncio.sleep(0.012)

                # 2. Tool call started (UI can display: 'Executing get_flight_status...')
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "tool")
                    tool_input = event["data"].get("input", {})
                    captured_tool_calls.append({"name": tool_name, "args": tool_input})
                    yield f"data: {json.dumps({'type': 'tool_start', 'tool': tool_name, 'input': tool_input})}\n\n"

                # 3. Tool call finished (UI can display: 'Received response from get_flight_status')
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "tool")
                    raw_output = event["data"].get("output")
                    out_text = getattr(raw_output, "content", str(raw_output))
                    if captured_tool_calls and captured_tool_calls[-1].get("name") == tool_name:
                        captured_tool_calls[-1]["output"] = out_text
                    yield f"data: {json.dumps({'type': 'tool_end', 'tool': tool_name, 'output': out_text})}\n\n"

                # 4. Model finished turn: capture tokens and latency
                elif kind == "on_chat_model_end":
                    output = event["data"].get("output")
                    if output and hasattr(output, "usage_metadata") and output.usage_metadata:
                        um = output.usage_metadata
                        total_usage["input_tokens"] += um.get("input_tokens", 0)
                        total_usage["output_tokens"] += um.get("output_tokens", 0)
                        total_usage["total_tokens"] += um.get("total_tokens", 0)

                    if output and hasattr(output, "response_metadata"):
                        rm = output.response_metadata
                        latency = rm.get("metrics", {}).get("latencyMs", 0)
                        if latency:
                            total_usage["latency_ms"] = latency

            # Save the final accumulated response and captured tool calls in PostgreSQL
            save_chat_message(
                session_id=sid,
                user_id=uid,
                role="assistant",
                content=accumulated_text,
                tool_calls=captured_tool_calls if captured_tool_calls else None,
            )

            # Emit final metadata and done event
            yield f"data: {json.dumps({'type': 'metadata', 'usage': total_usage})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'session_id': sid})}\n\n"

        except Exception as e:
            logger.error(f"Error in streaming chat session {sid}: {e}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# =========================================================================
# 3. Session History & Previous Chats (For UI Sidebar & Re-loading)
# =========================================================================

@router.get("/sessions", response_model=List[SessionItem])
def list_sessions(user_id: str = Query("vamsi", description="User ID to filter sessions")):
    """
    Lists past chat conversations from DynamoDB for the UI sidebar.
    Returns session_id, conversation title, and updated_at timestamp.
    """
    return get_user_sessions(user_id=user_id)


@router.get("/chat/{session_id}", response_model=SessionDetail)
def get_chat_history(session_id: str):
    """
    Retrieves full chronological message history for a given session_id.
    Prioritizes persistent human-readable records from PostgreSQL (chat_messages),
    with automatic fallback to LangGraph agent state / DynamoDB checkpointer.
    """
    # 1. Check PostgreSQL persistent human-readable message history
    pg_messages = get_session_messages(session_id)
    if pg_messages:
        formatted = [
            MessageItem(
                id=m.get("id"),
                role=m.get("role", "assistant"),
                content=m.get("content", ""),
                tool_calls=m.get("tool_calls"),
                timestamp=m.get("timestamp"),
            )
            for m in pg_messages
        ]
        return SessionDetail(session_id=session_id, messages=formatted)

    # 2. Fallback to LangGraph checkpoint state
    try:
        state = agent.get_state(config={"configurable": {"thread_id": session_id}})
        raw_messages = state.values.get("messages", [])

        formatted: List[MessageItem] = []
        for idx, msg in enumerate(raw_messages):
            cls_name = msg.__class__.__name__
            if cls_name == "HumanMessage":
                role = "user"
            elif cls_name == "AIMessage":
                role = "assistant"
            elif cls_name == "ToolMessage":
                role = "tool"
            else:
                role = "system"

            content = msg.content
            if isinstance(content, list):
                content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)

            tool_calls = None
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls = [
                    {"name": tc.get("name"), "args": tc.get("args"), "id": tc.get("id")}
                    for tc in msg.tool_calls
                ]

            formatted.append(
                MessageItem(
                    id=getattr(msg, "id", None) or f"{session_id}-{idx}",
                    role=role,
                    content=content,
                    tool_calls=tool_calls,
                )
            )

        return SessionDetail(session_id=session_id, messages=formatted)
    except Exception as e:
        logger.error(f"Failed to fetch chat history for session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not load session history: {str(e)}")


@router.delete("/chat/{session_id}")
def delete_chat_session(session_id: str):
    """Deletes session metadata from DynamoDB and persistent messages from PostgreSQL."""
    success_dyn = delete_session(session_id)
    success_pg = delete_session_messages(session_id)
    return {"status": "deleted" if (success_dyn or success_pg) else "failed", "session_id": session_id}