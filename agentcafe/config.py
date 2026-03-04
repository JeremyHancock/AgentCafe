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

    # Demo backend hosts and ports (internal only — never exposed to agents)
    hotel_backend_host: str = "127.0.0.1"
    hotel_backend_port: int = 8001
    lunch_backend_host: str = "127.0.0.1"
    lunch_backend_port: int = 8002
    home_service_backend_host: str = "127.0.0.1"
    home_service_backend_port: int = 8003

    # Database
    db_path: str = "agentcafe.db"

    # Path to service data files (single source of truth for Menu entries)
    design_dir: str = ""

    # Passport system (Phase 2 HS256 sessions, Phase 6 RS256 passports)
    passport_signing_secret: str = ""  # HS256 — internal session tokens (human, wizard)
    issuer_api_key: str = ""
    use_real_passport: bool = False

    # RS256 passport signing (Phase 6)
    passport_rsa_private_key: str = ""  # PEM string (e.g. from env var)
    passport_rsa_key_file: str = ""     # path to PEM file (alternative)

    # Backend credential encryption
    encryption_key: str = ""

    # Service quarantine (days after publish before Tier-1 access allowed)
    quarantine_days: int = 7

    # WebAuthn passkeys
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "AgentCafe"
    webauthn_origin: str = "http://localhost:8000"
    allow_password_auth: bool = True

    # CORS
    cors_allowed_origins: str = "*"

    # Logging
    log_level: str = "INFO"

    @property
    def hotel_backend_url(self) -> str:
        return f"http://{self.hotel_backend_host}:{self.hotel_backend_port}"

    @property
    def lunch_backend_url(self) -> str:
        return f"http://{self.lunch_backend_host}:{self.lunch_backend_port}"

    @property
    def home_service_backend_url(self) -> str:
        return f"http://{self.home_service_backend_host}:{self.home_service_backend_port}"


def load_config() -> CafeConfig:
    """Load config from environment variables, falling back to defaults."""
    default_design_dir = str(_PROJECT_ROOT / "agentcafe" / "db" / "services")
    return CafeConfig(
        cafe_host=os.getenv("CAFE_HOST", "127.0.0.1"),
        cafe_port=int(os.getenv("CAFE_PORT", "8000")),
        hotel_backend_host=os.getenv("HOTEL_BACKEND_HOST", "127.0.0.1"),
        hotel_backend_port=int(os.getenv("HOTEL_BACKEND_PORT", "8001")),
        lunch_backend_host=os.getenv("LUNCH_BACKEND_HOST", "127.0.0.1"),
        lunch_backend_port=int(os.getenv("LUNCH_BACKEND_PORT", "8002")),
        home_service_backend_host=os.getenv("HOME_SERVICE_BACKEND_HOST", "127.0.0.1"),
        home_service_backend_port=int(os.getenv("HOME_SERVICE_BACKEND_PORT", "8003")),
        db_path=os.getenv("CAFE_DB_PATH", "agentcafe.db"),
        design_dir=os.getenv("CAFE_DESIGN_DIR", default_design_dir),
        passport_signing_secret=os.getenv("PASSPORT_SIGNING_SECRET", secrets.token_urlsafe(32)),
        issuer_api_key=os.getenv("ISSUER_API_KEY", ""),
        use_real_passport=os.getenv("USE_REAL_PASSPORT", "false").lower() == "true",
        passport_rsa_private_key=os.getenv("PASSPORT_RSA_PRIVATE_KEY", ""),
        passport_rsa_key_file=os.getenv("PASSPORT_RSA_KEY_FILE", ""),
        encryption_key=os.getenv("CAFE_ENCRYPTION_KEY", ""),
        quarantine_days=int(os.getenv("QUARANTINE_DAYS", "7")),
        webauthn_rp_id=os.getenv("WEBAUTHN_RP_ID", "localhost"),
        webauthn_rp_name=os.getenv("WEBAUTHN_RP_NAME", "AgentCafe"),
        webauthn_origin=os.getenv("WEBAUTHN_ORIGIN", "http://localhost:8000"),
        allow_password_auth=os.getenv("ALLOW_PASSWORD_AUTH", "true").lower() == "true",
        cors_allowed_origins=os.getenv("CORS_ALLOWED_ORIGINS", "*"),
        log_level=os.getenv("CAFE_LOG_LEVEL", "INFO"),
    )
