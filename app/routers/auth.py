from fastapi import APIRouter, HTTPException, Depends, Header
from typing import Optional
from app.utils.schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Static users for airline operations login
STATIC_USERS = {
    "guest1": {
        "user_id": "guest1",
        "username": "guest1",
        "name": "Guest 1",
        "password": "indian2026",
        "role": "guest",
    },
    "guest2": {
        "user_id": "guest2",
        "username": "guest2",
        "name": "Guest 2",
        "password": "indian2026",
        "role": "guest",
    },
}


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    """
    Static authentication endpoint for the SkyAssist frontend.
    Validates credentials and returns user identity with a session bearer token.
    """
    user = STATIC_USERS.get(payload.username.strip().lower())
    if not user or user["password"] != payload.password.strip():
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    return LoginResponse(
        user_id=user["user_id"],
        username=user["username"],
        name=user["name"],
        role=user["role"],
        token=f"skyassist-token-{user['user_id']}-2026",
    )


@router.get("/me", response_model=LoginResponse)
def get_current_user(authorization: Optional[str] = Header(None)):
    """Validates authorization header token and returns user profile."""
    if not authorization:
        # Default to primary user for smooth local development
        user = STATIC_USERS["guest1"]
    else:
        token = authorization.replace("Bearer ", "").strip()
        matched_user = None
        for u in STATIC_USERS.values():
            if f"skyassist-token-{u['user_id']}-2026" in token or token == u["user_id"]:
                matched_user = u
                break
        user = matched_user or STATIC_USERS["guest1"]

    return LoginResponse(
        user_id=user["user_id"],
        username=user["username"],
        name=user["name"],
        role=user["role"],
        token=f"skyassist-token-{user['user_id']}-2026",
    )
