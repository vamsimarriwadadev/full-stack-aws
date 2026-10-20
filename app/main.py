import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request, Response, HTTPException

from app.core.config import settings
from app.core.logging import setup_logging, request_id_var
from app.core.db import pool, init_db
from app.routers import chat, ingest, mock_itsm
from app.core.dynamodb import create_dynamodb_tables

# Initialize structured JSON logging
setup_logging("DEBUG" if settings.DEBUG else "INFO")
logger = logging.getLogger("skyassist.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database extension, verify tables, and ensure DynamoDB tables exist
    init_db()
    try:
        create_dynamodb_tables()
    except Exception as e:
        logger.warning(f"DynamoDB tables initialization skipped/deferred: {e}")
    yield
    # Shutdown: gracefully drain and close database connection pool
    pool.close()


app = FastAPI(
    title=settings.APP_NAME,
    description="SkyAssist Agentic AI API with AWS Bedrock integration",
    version="0.1.0",
    debug=settings.DEBUG,
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """
    Assigns or preserves X-Request-ID, binds it to contextvars for JSON logs,
    times request execution, and attaches the header to responses.
    """
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = request_id
    token = request_id_var.set(request_id)
    start_time = time.perf_counter()

    try:
        response: Response = await call_next(request)
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Request-ID"] = request_id

        logger.info(
            f"{request.method} {request.url.path} {response.status_code}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
            },
        )
        return response
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.exception(
            f"Unhandled exception during {request.method} {request.url.path}: {exc}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "latency_ms": latency_ms,
                "error": str(exc),
            },
        )
        raise
    finally:
        request_id_var.reset(token)


from app.routers import chat, ingest, mock_itsm, auth

app.include_router(auth.router)
app.include_router(chat.router, tags=["Chats"])
app.include_router(ingest.router, tags=["Ingest"])
app.include_router(mock_itsm.router)


@app.get("/health", tags=["Health"])
def health_check() -> Dict[str, str]:
    """
    Liveness probe: verifies process is alive and responsive.
    Used by ALB and ECS to avoid terminating containers on external dependency delays.
    """
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "environment": settings.ENVIRONMENT,
    }


@app.get("/ready", tags=["Health"])
def readiness_check() -> Dict[str, Any]:
    """
    Readiness probe: validates critical dependencies (PostgreSQL database connectivity).
    Returns 200 when ready to receive traffic, or 503 if dependencies fail.
    """
    try:
        with pool.connection(timeout=3) as conn:
            conn.execute("SELECT 1;")
        return {
            "status": "ready",
            "database": "connected",
            "app": settings.APP_NAME,
        }
    except Exception as e:
        logger.error(f"Readiness check failed: {e}", extra={"error": str(e)})
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "database": f"error: {str(e)}",
            },
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
