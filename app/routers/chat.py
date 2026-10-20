import uuid
import numpy as np
from langchain_core.tools import tool
from langchain.agents import create_agent
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from langchain_aws import ChatBedrockConverse, BedrockEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph_checkpoint_aws import DynamoDBSaver

from app.core.aws import get_aws_clients, get_aws_session
from app.core.config import settings
from app.core.db import pool
from app.utils.schemas import ChatInput
from app.utils.helpers import _text


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

llm = ChatBedrockConverse(
    model_id=settings.BEDROCK_MODEL_ID,
    client=bedrock_runtime_client,
)

@tool
def search_knowledge_base(query: str, top_k: int = 4) -> str:
    """Search the internal knowledge base. Use for any question that may be
    answered by ingested documents. Rephrase and call again if results are weak."""
    qvec = np.array(embeddings.embed_query(query))
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT source, content, embedding <=> %s AS dist
               FROM documents ORDER BY embedding <=> %s LIMIT %s""",
            (qvec, qvec, top_k),
        ).fetchall()
    if not rows:
        return "No relevant documents found."
    return "\n\n".join(f"[{s} | dist={d:.3f}]\n{c}" for s, c, d in rows)

# DynamoDB checkpointer for multi-turn conversational persistence
session = get_aws_session(settings.AWS_REGION)

checkpointer = DynamoDBSaver(
    table_name=settings.DYNAMODB_CHECKPOINT_TABLE,  # "chat-checkpoints"
    session=session,
    region_name=settings.AWS_REGION,
    ttl_seconds=60 * 60 * 24 * 30,  # auto-expire sessions after 30 days
)

agent = create_agent(
    model=llm,
    tools=[search_knowledge_base],
    system_prompt=(
        "You are a helpful assistant. For factual questions, call search_knowledge_base "
        "first and answer only from the results, citing the source. If nothing relevant "
        "is found, say so. Don't search for greetings or small talk."
    ),
    checkpointer=checkpointer,
)

router = APIRouter()


@router.post("/chat")
def chat(payload: ChatInput):  # sync def: FastAPI runs it in a worker thread
    try:
        sid = payload.session_id or uuid.uuid4().hex
        result = agent.invoke(
            {"messages": [{"role": "user", "content": payload.input}]},
            config={"configurable": {"thread_id": sid}},
        )
        return {"response": _text(result["messages"][-1].content), "session_id": sid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))