import uuid
import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from langchain_aws import BedrockEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.aws import get_aws_clients
from app.core.config import settings
from app.core.db import pool
from app.utils.schemas import IngestInput


router = APIRouter()

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




@router.post("/ingest")
def ingest(payload: IngestInput):
    try:
        chunks = splitter.split_text(payload.text)
        vecs = embeddings.embed_documents(chunks)
        rows = [
            (uuid.uuid4(), payload.source, c, np.array(v))
            for c, v in zip(chunks, vecs)
        ]
        with pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO documents (id, source, content, embedding) VALUES (%s, %s, %s, %s)",
                rows,
            )
        return {"chunks_indexed": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))