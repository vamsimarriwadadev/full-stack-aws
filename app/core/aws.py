from functools import lru_cache
import boto3
from botocore.config import Config
from app.core.config import settings


class AWSClients:
    def __init__(self, session: boto3.Session):
        standard_config = Config(
            retries={
                "max_attempts": 3,
                "mode": "standard",
            }
        )

        bedrock_config = Config(
            retries={
                "max_attempts": 5,
                "mode": "adaptive",  # Handles LLM rate limiting gracefully
            },
            connect_timeout=5,
            read_timeout=60,
        )

        # Core SkyAssist Services
        self.bedrock_runtime = session.client("bedrock-runtime", config=bedrock_config)
        self.dynamodb = session.client("dynamodb", region_name=settings.AWS_REGION, config=standard_config)
        self.s3 = session.client("s3", config=standard_config)
        self.secretsmanager = session.client("secretsmanager", config=standard_config)
        self.sqs = session.client("sqs", config=standard_config)


def get_aws_session(region_name: str | None = None) -> boto3.Session:
    """
    Creates a boto3.Session using environment/settings credentials.
    """
    session_kwargs = {"region_name": region_name or settings.AWS_DEFAULT_REGION}

    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        session_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        session_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY

    return boto3.Session(**session_kwargs)


@lru_cache()
def get_aws_clients() -> AWSClients:
    """
    Creates and caches the AWSClients instance.
    - Local: Uses credentials in settings.
    - Production (ECS Fargate): Automatically uses the ECS Task IAM Role.
    """
    session = get_aws_session()
    return AWSClients(session)
