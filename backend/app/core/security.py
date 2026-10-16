"""Cryptographic security utilities, pure Python RFC 7519 JWT handling, and RBAC enforcement.

Uses standard Python libraries (hashlib, hmac, secrets, json, time) to provide:
1. NIST-compliant PBKDF2-HMAC-SHA256 password hashing.
2. Constant-time digest comparison.
3. Fully compliant HS256 JWT encoding and decoding with expiration checks.
4. Edge machine X-Bus-API-Key and Operator Role-Based Access Control (RBAC).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, Header, HTTPException, Query, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.core.config import settings

logger = logging.getLogger("urban_intelligence.security")

# HTTP Bearer scheme with auto_error=False to allow fallback to query parameter or API key
http_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class AuthenticationError(Exception):
    """Base class for authentication failures."""
    pass


class TokenExpiredError(AuthenticationError):
    """Raised when an access token has expired."""
    pass


class InvalidTokenError(AuthenticationError):
    """Raised when an access token is malformed or signature fails."""
    pass


# ---------------------------------------------------------------------------
# Password Hashing & Verification (PBKDF2-HMAC-SHA256)
# ---------------------------------------------------------------------------
PBKDF2_ITERATIONS = 100_000


def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with a 16-byte cryptographically secure salt."""
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2:sha256:{PBKDF2_ITERATIONS}${salt.hex()}${key.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain password against PBKDF2-HMAC-SHA256 hash using constant-time comparison."""
    try:
        parts = hashed_password.split("$")
        if len(parts) != 3:
            return False
        header, salt_hex, key_hex = parts
        scheme, alg, iters_str = header.split(":")
        if scheme != "pbkdf2" or alg != "sha256":
            return False
        iterations = int(iters_str)
        salt = bytes.fromhex(salt_hex)
        expected_key = bytes.fromhex(key_hex)
        candidate_key = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(candidate_key, expected_key)
    except Exception as exc:
        logger.debug("Password verification exception: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Base64URL Encoding & Decoding (RFC 7515 / RFC 7519)
# ---------------------------------------------------------------------------
def _b64url_encode(data: bytes) -> str:
    """Encode bytes into URL-safe base64 string without trailing padding."""
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    """Decode URL-safe base64 string, restoring missing padding characters."""
    rem = len(data) % 4
    if rem > 0:
        data += "=" * (4 - rem)
    return base64.urlsafe_b64decode(data.encode("utf-8"))


# ---------------------------------------------------------------------------
# Pure Python RFC 7519 HS256 JWT Implementation
# ---------------------------------------------------------------------------
def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
    secret_key: Optional[str] = None,
) -> str:
    """Create a signed JWT token with exp, iat, and custom payload claims."""
    key = (secret_key or settings.SECRET_KEY).encode("utf-8")
    now_epoch = int(time.time())

    if expires_delta is not None:
        expire_epoch = now_epoch + int(expires_delta.total_seconds())
    else:
        expire_epoch = now_epoch + (settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)

    payload = dict(data)
    payload["iat"] = now_epoch
    payload["exp"] = expire_epoch

    header = {"alg": settings.JWT_ALGORITHM, "typ": "JWT"}

    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_access_token(
    token: str,
    secret_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Decode and cryptographically verify signature and expiration of an HS256 JWT."""
    key = (secret_key or settings.SECRET_KEY).encode("utf-8")

    parts = token.strip().split(".")
    if len(parts) != 3:
        raise InvalidTokenError("JWT must contain exactly three dot-separated components.")

    header_b64, payload_b64, sig_b64 = parts

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected_sig = hmac.new(key, signing_input, hashlib.sha256).digest()

    try:
        actual_sig = _b64url_decode(sig_b64)
    except Exception as exc:
        raise InvalidTokenError("Malformed base64 signature in token.") from exc

    if not hmac.compare_digest(actual_sig, expected_sig):
        raise InvalidTokenError("Invalid token cryptographic signature.")

    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception as exc:
        raise InvalidTokenError("Malformed JSON in token header or payload.") from exc

    if header.get("alg") != settings.JWT_ALGORITHM:
        raise InvalidTokenError(f"Unsupported algorithm '{header.get('alg')}'. Expected {settings.JWT_ALGORITHM}.")

    now_epoch = int(time.time())
    exp = payload.get("exp")
    if exp is None or not isinstance(exp, (int, float)):
        raise InvalidTokenError("Token missing required 'exp' claim.")

    if now_epoch > exp:
        raise TokenExpiredError("Token has expired.")

    return payload


# ---------------------------------------------------------------------------
# Operator User Model & Provisioning
# ---------------------------------------------------------------------------
class UserAccount:
    """In-memory representation of an authenticated operator account."""

    def __init__(
        self,
        username: str,
        role: str,
        hashed_password: str,
        full_name: str,
        email: str,
        is_active: bool = True,
    ) -> None:
        self.username = username
        self.role = role.upper()
        self.hashed_password = hashed_password
        self.full_name = full_name
        self.email = email
        self.is_active = is_active

    def to_dict(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "role": self.role,
            "full_name": self.full_name,
            "email": self.email,
            "is_active": self.is_active,
        }


# Initial operator users provisioned for local and testing workflows
INITIAL_USERS: Dict[str, UserAccount] = {
    "admin": UserAccount(
        username="admin",
        role="ADMIN",
        hashed_password=hash_password("admin123"),
        full_name="Municipal Chief Administrator",
        email="admin@pmc.punecivic.gov.in",
    ),
    "dispatcher": UserAccount(
        username="dispatcher",
        role="DISPATCHER",
        hashed_password=hash_password("dispatch123"),
        full_name="Transit Operations Dispatcher",
        email="dispatch@pmc.punecivic.gov.in",
    ),
    "viewer": UserAccount(
        username="viewer",
        role="VIEWER",
        hashed_password=hash_password("view123"),
        full_name="Public Audit Viewer",
        email="viewer@punecivic.gov.in",
    ),
}


def authenticate_user(username: str, password: str) -> Optional[UserAccount]:
    """Validate operator username and password."""
    user = INITIAL_USERS.get(username.lower())
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user


# ---------------------------------------------------------------------------
# FastAPI Dependency Injection & Role Enforcement
# ---------------------------------------------------------------------------
async def get_current_user(
    request: Request,
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
) -> UserAccount:
    """Extract and validate operator JWT from Authorization header or 'token' query param."""
    raw_token = None
    if auth_header and auth_header.credentials:
        raw_token = auth_header.credentials
    elif "token" in request.query_params:
        raw_token = request.query_params["token"]

    if not raw_token:
        if settings.AUTH_ENFORCED:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. Provide a valid Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return UserAccount(
            username="dev_operator",
            role="DISPATCHER",
            hashed_password="",
            full_name="Development Dispatcher",
            email="dev@fleetfusion.local",
        )

    try:
        payload = decode_access_token(raw_token)
    except TokenExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\", error_description=\"Token expired\""},
        ) from exc
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid credentials token: {exc}",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        ) from exc

    username = payload.get("sub") or payload.get("username")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject identifier.",
        )

    user = INITIAL_USERS.get(str(username).lower())
    if not user:
        # Fallback dynamic user object from token claims if valid
        user_role = payload.get("role", "VIEWER")
        user = UserAccount(
            username=str(username),
            role=str(user_role),
            hashed_password="",
            full_name=payload.get("name", str(username)),
            email=payload.get("email", f"{username}@fleetfusion.local"),
        )

    return user


def require_role(allowed_roles: List[str]) -> Callable[[UserAccount], UserAccount]:
    """Dependency factory enforcing server-side Role-Based Access Control."""
    allowed_set = {r.upper() for r in allowed_roles}

    def role_checker(user: UserAccount = Depends(get_current_user)) -> UserAccount:
        if user.role not in allowed_set:
            logger.warning(
                "Access denied for user '%s' (role %s). Required one of: %s",
                user.username,
                user.role,
                allowed_set,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access forbidden: requires one of {sorted(list(allowed_set))} role(s). Current role is {user.role}.",
            )
        return user

    return role_checker


async def verify_bus_auth(
    x_bus_api_key: Optional[str] = Header(None, alias="X-Bus-API-Key"),
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
) -> bool:
    """Authorize edge sensing devices via X-Bus-API-Key header or operator Bearer token."""
    # 1. Check API key header
    if x_bus_api_key and hmac.compare_digest(x_bus_api_key.strip(), settings.BUS_API_KEY):
        return True

    # 2. Check Bearer token
    if auth_header and hasattr(auth_header, "credentials") and getattr(auth_header, "credentials", None):
        try:
            payload = decode_access_token(auth_header.credentials)
            role = payload.get("role", "").upper()
            if role in {"ADMIN", "DISPATCHER"}:
                return True
        except Exception:
            pass

    return False
