# SkyAssist: Agentic AI on AWS

## Project Overview

**SkyAssist** is a production-grade Agentic AI service built for airline operations and customer support workflows. Powered by Python, FastAPI, LangChain, and Amazon Bedrock, SkyAssist acts as an intelligent enterprise assistant capable of autonomously handling operational inquiries, booking assistance, and IT ticketing while maintaining enterprise security and context awareness.

Key functional capabilities:
- **Flight Operations & Support:** Real-time flight status checks, schedules, gate assignments, and delay tracking.
- **Booking & Reservations:** Passenger itinerary lookups, travel modifications, and customer assistance.
- **Enterprise IT & Operations Support:** Integration with IT Service Management (ITSM) workflows to triage and create operational incident tickets.
- **Multi-Turn Context & RAG:** Stateful conversational memory with document retrieval-augmented generation (RAG) over operational airline knowledge.

---

## AWS Services & Architecture

SkyAssist leverages native AWS managed services for security, scalability, and AI orchestration:

- **Amazon Bedrock (Bedrock Runtime):**
  - **LLM Orchestration:** `ChatBedrockConverse` running foundation models (e.g., Anthropic Claude) for multi-turn reasoning and tool calling.
  - **Vector Embeddings:** `BedrockEmbeddings` (e.g., Amazon Titan) generating normalized vector representations for documents and search queries.
- **Amazon RDS PostgreSQL (pgvector):**
  - High-performance vector database storing document chunks and high-dimensional embeddings.
  - HNSW indexing with cosine similarity for fast vector search.
  - **Passwordless Security:** Connects using short-lived IAM database authentication tokens (`generate_db_auth_token`), refreshed automatically by the connection pool.
- **Amazon DynamoDB:**
  - Session state and chat checkpoint persistence using `DynamoDBSaver` (`langgraph-checkpoint-aws`), featuring auto-expiring sessions via TTL (30 days).
- **Amazon S3:**
  - Cloud object storage for operational documents, datasets, and knowledge base files.
- **AWS Secrets Manager & SQS:**
  - Pre-configured client integrations for credential isolation and asynchronous ingestion pipelines.
- **AWS ECS Fargate & ALB (Target Deployment):**
  - Containerized deployment with Docker on serverless containers managed by an Application Load Balancer.

---

## Codebase Architecture

The project is structured as a modular FastAPI microservice:

```
southwest/
├── app/
│   ├── main.py              # Application entrypoint, route inclusion, /health & /s3 endpoints
│   ├── core/
│   │   ├── aws.py           # Centralized Boto3 session & AWSClients factory with retry configs
│   │   ├── config.py        # Environment variables & runtime settings (Pydantic BaseSettings)
│   │   └── db.py            # RDS PostgreSQL connection pool with dynamic IAM auth & pgvector init
│   ├── routers/
│   │   ├── chat.py          # Bedrock LangChain agent, search_knowledge_base tool & DynamoDB checkpointer
│   │   └── ingest.py        # Document chunking, Bedrock embedding, and pgvector storage pipeline
│   └── utils/
│       ├── helpers.py       # Message formatting and response extractors
│       └── schemas.py       # Pydantic request/response models (ChatInput, IngestInput)
├── BUILD_PLAN.md            # Comprehensive architecture roadmap, scope tiers, and operational runbook
├── pyproject.toml           # Project dependencies managed via uv / pip
├── run.bat                  # Local development startup script
└── README.md                # Project documentation
```

---

## Primary API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/chat` | Multi-turn conversational agent endpoint with session persistence and knowledge base retrieval |
| `POST` | `/ingest` | Splits text into overlapping chunks, generates Bedrock embeddings, and saves to RDS `pgvector` |
| `GET` | `/health` | Health check endpoint for container orchestrators and ALB target groups |
| `GET` | `/s3/buckets` | Verifies AWS connectivity and lists S3 buckets in the connected account |

---

## Tech Stack Summary

- **Language & Web:** Python 3.13, FastAPI, Uvicorn, Pydantic Settings
- **AI & Agent Orchestration:** LangChain, LangChain-AWS, LangGraph, Amazon Bedrock
- **Database & Vectors:** PostgreSQL, `pgvector`, `psycopg-pool`, AWS RDS (IAM Auth)
- **State & Memory:** AWS DynamoDB (`langgraph-checkpoint-aws`)
- **AWS SDK:** `boto3`, `botocore`
