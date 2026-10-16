"""Pydantic v2 schemas for operator authentication and role representation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UserLogin(BaseModel):
    """User credentials submitted for token generation."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=2, max_length=64, description="Operator username")
    password: str = Field(..., min_length=4, max_length=128, description="Operator password")


class Token(BaseModel):
    """Bearer access token payload returned upon successful authentication."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
    role: str
    username: str
    expires_in: int


class UserResponse(BaseModel):
    """Public operator account profile."""

    model_config = ConfigDict(extra="forbid")

    username: str
    role: str
    full_name: str
    email: str
    is_active: bool
