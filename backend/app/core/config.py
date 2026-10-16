"""Application configuration, security parameters, and operator credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    """Core security and application settings."""

    SECRET_KEY: str = os.environ.get("FLEETFUSION_SECRET_KEY", "fleetfusion-insecure-dev-secret-key-change-in-production-2026")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.environ.get("FLEETFUSION_TOKEN_EXPIRE_MINUTES", "120"))
    BUS_API_KEY: str = os.environ.get("BUS_API_KEY", "bus_sec_pune_transit_2026")
    API_V1_PREFIX: str = "/api/v1"
    AUTH_ENFORCED: bool = os.environ.get("FLEETFUSION_AUTH_ENFORCED", "false").lower() in ("true", "1", "yes")


settings = Settings()
