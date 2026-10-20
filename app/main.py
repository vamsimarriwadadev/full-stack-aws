from websockets.version import tag
import time
from typing import Dict, Any
from fastapi import FastAPI, HTTPException
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.aws import get_aws_clients

from app.routers import chat, ingest

clients = get_aws_clients()

s3_client = clients.s3
bedrock_runtime_client = clients.bedrock_runtime

app = FastAPI(
    title=settings.APP_NAME,
    description="SkyAssist Agentic AI API with AWS Bedrock integration",
    version="0.1.0",
    debug=settings.DEBUG,
)

app.include_router(chat.router, tags=["Chats"])

app.include_router(ingest.router, tags=["Ingest"])

@app.get("/health", tags=["Health"])
def health_check() -> Dict[str, str]:
    """Health check endpoint for ALB and container orchestration."""
    return {"status": "healthy", "app": settings.APP_NAME, "environment": settings.ENVIRONMENT}


@app.get("/s3/buckets", tags=["S3"])
def list_s3_buckets() -> Dict[str, Any]:
    """Fetch all S3 buckets from the connected AWS account."""
    try:
        response = s3_client.list_buckets()
        buckets = [
            {
                "name": bucket.get("Name"),
                "creation_date": bucket.get("CreationDate").isoformat() if bucket.get("CreationDate") else None,
            }
            for bucket in response.get("Buckets", [])
        ]
        owner = response.get("Owner", {}).get("DisplayName") or response.get("Owner", {}).get("ID")
        
        return {
            "count": len(buckets),
            "owner": owner,
            "buckets": buckets,
        }
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"AWS S3 error ({error_code}): {error_message}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error retrieving S3 buckets: {str(e)}",
        )





if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
