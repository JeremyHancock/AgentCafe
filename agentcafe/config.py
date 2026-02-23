"""Environment-based configuration for AgentCafe."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

# Project root = directory containing pyproject.toml (parent of this package)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class CafeConfig:
    """Central configuration — reads from env vars with sensible defaults."""

    # Main Cafe server
    cafe_host: str = "127.0.0.1"
    cafe_port: int = 8000

    # Demo backend ports (internal only — never exposed to agents)
    hotel_backend_port: int = 8001
    lunch_backend_port: int = 8002
    home_service_backend_port: int = 8003

    # Database
    db_path: str = "agentcafe.db"

    # Path to design files (single source of truth for Menu entries)
    design_dir: str = ""

    # Passport system (Phase 2)
    passport_signing_secret: str = ""
    issuer_api_key: str = ""
    use_real_passport: bool = False

    # Logging
    log_level: str = "INFO"

    @property
    def hotel_backend_url(self) -> str:
        return f"http://127.0.0.1:{self.hotel_backend_port}"

    @property
    def lunch_backend_url(self) -> str:
        return f"http://127.0.0.1:{self.lunch_backend_port}"

    @property
    def home_service_backend_url(self) -> str:
        return f"http://127.0.0.1:{self.home_service_backend_port}"


def load_config() -> CafeConfig:
    """Load config from environment variables, falling back to defaults."""
    default_design_dir = str(_PROJECT_ROOT / "docs" / "design")
    return CafeConfig(
        cafe_host=os.getenv("CAFE_HOST", "127.0.0.1"),
        cafe_port=int(os.getenv("CAFE_PORT", "8000")),
        hotel_backend_port=int(os.getenv("HOTEL_BACKEND_PORT", "8001")),
        lunch_backend_port=int(os.getenv("LUNCH_BACKEND_PORT", "8002")),
        home_service_backend_port=int(os.getenv("HOME_SERVICE_BACKEND_PORT", "8003")),
        db_path=os.getenv("CAFE_DB_PATH", "agentcafe.db"),
        design_dir=os.getenv("CAFE_DESIGN_DIR", default_design_dir),
        passport_signing_secret=os.getenv("PASSPORT_SIGNING_SECRET", secrets.token_urlsafe(32)),
        issuer_api_key=os.getenv("ISSUER_API_KEY", ""),
        use_real_passport=os.getenv("USE_REAL_PASSPORT", "false").lower() == "true",
        log_level=os.getenv("CAFE_LOG_LEVEL", "INFO"),
    )
