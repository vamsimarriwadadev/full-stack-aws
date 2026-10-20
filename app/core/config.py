from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # AWS Credentials (Optional for ECS Fargate / IAM Role)
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_DEFAULT_REGION: str
    AWS_REGION: str

    # Amazon Bedrock
    BEDROCK_MODEL_ID: str
    BEDROCK_EMBED_MODEL_ID: str
    BEDROCK_EMBED_DIM: int

    # DynamoDB Tables
    DYNAMODB_CHECKPOINT_TABLE: str = "chat-checkpoints"
    DYNAMODB_SESSIONS_TABLE: str = "sessions"
    DYNAMODB_TICKETS_TABLE: str = "tickets"

    # ITSM Integration (Mock / API Gateway)
    ITSM_URL: str = "http://localhost:8000/mock-itsm/tickets"
    ITSM_API_KEY: str = "mock-secret-key-123"

    # RDS PostgreSQL (Aurora IAM Auth)
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str
    DB_USER: str
    DB_SSLMODE: str

    # App Config
    APP_NAME: str
    ENVIRONMENT: str
    DEBUG: bool

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
