"""FastAPI router providing token generation and current user profile endpoints."""

from __future__ import annotations

from datetime import timedelta
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from backend.app.core.config import settings
from backend.app.core.security import (
    authenticate_user,
    create_access_token,
    get_current_user,
    UserAccount,
)
from backend.app.schemas.auth import Token, UserLogin, UserResponse

logger = logging.getLogger("urban_intelligence.routers.auth")

router = APIRouter(tags=["Authentication"])


@router.post("/auth/token", response_model=Token, summary="Obtain operator JWT access token")
async def login_for_access_token(credentials: UserLogin) -> Token:
    """Authenticate an operator and return an RFC 7519 HS256 access token."""
    user = authenticate_user(credentials.username, credentials.password)
    if not user:
        logger.warning("Failed login attempt for username: %s", credentials.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token_str = create_access_token(
        data={"sub": user.username, "role": user.role, "name": user.full_name, "email": user.email},
        expires_delta=expires_delta,
    )

    return Token(
        access_token=token_str,
        token_type="bearer",
        role=user.role,
        username=user.username,
        expires_in=int(expires_delta.total_seconds()),
    )


@router.get("/auth/me", response_model=UserResponse, summary="Retrieve current authenticated operator profile")
async def read_current_user(current_user: UserAccount = Depends(get_current_user)) -> UserResponse:
    """Return profile and permissions for the currently authenticated operator."""
    return UserResponse(
        username=current_user.username,
        role=current_user.role,
        full_name=current_user.full_name,
        email=current_user.email,
        is_active=current_user.is_active,
    )
