"""AgentCafe — main entry point.

Starts the Cafe server (port 8000) and three demo backends (ports 8001-8003).
All four servers run concurrently in one process using asyncio.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentcafe.cafe.consent import configure_consent, consent_router
from agentcafe.cafe.human import configure_human, human_router
from agentcafe.cafe.pages import configure_pages, pages_router
from agentcafe.cafe.passport import configure_passport, passport_router
from agentcafe.cafe.router import close_http_client, configure_router, router as cafe_router
from agentcafe.config import load_config
from agentcafe.crypto import configure_crypto
from agentcafe.db.engine import close_db, init_db
from agentcafe.db.seed import seed_demo_data
from agentcafe.wizard.router import configure_wizard, wizard_router

logger = logging.getLogger("agentcafe")


@asynccontextmanager
async def _cafe_lifespan(_app: FastAPI):  # noqa: unused but required by FastAPI lifespan protocol
    """Startup/shutdown for standalone Cafe (used by Docker and uvicorn CLI)."""
    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger.info("Initializing database...")
    db = await init_db(cfg.db_path)
    logger.info("Seeding demo services...")
    await seed_demo_data(db, cfg)
    logger.info("Database ready.")
    configure_crypto(cfg.encryption_key)
    configure_passport(cfg.passport_signing_secret, cfg.issuer_api_key)
    configure_human(cfg.passport_signing_secret)
    configure_consent(cfg.passport_signing_secret)
    configure_pages(cfg.passport_signing_secret)
    configure_wizard(cfg.passport_signing_secret)
    configure_router(cfg.use_real_passport)
    if cfg.use_real_passport:
        logger.info("Passport mode: REAL JWT validation")
    else:
        logger.info("Passport mode: MVP (demo-passport only)")
    yield
    await close_http_client()
    await close_db()


def create_cafe_app(lifespan=None, cors_origins: str = "*") -> FastAPI:
    """Create the main AgentCafe FastAPI application.

    Args:
        lifespan: Optional async context manager for startup/shutdown.
                  Pass None for tests (they manage DB lifecycle separately).
        cors_origins: Comma-separated allowed origins, or "*" for all.
    """
    app = FastAPI(  # pylint: disable=redefined-outer-name
        title="AgentCafe",
        version="0.1.0",
        description="The Cafe for Agents. Browse the Menu, present your Passport, and order.",
        lifespan=lifespan,
    )
    origins = [o.strip() for o in cors_origins.split(",")] if cors_origins != "*" else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(cafe_router)
    app.include_router(passport_router)
    app.include_router(consent_router)
    app.include_router(human_router)
    app.include_router(pages_router)
    app.include_router(wizard_router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "agentcafe"}

    return app


# Module-level app for standalone deployment (uvicorn agentcafe.main:app)
# Tests use create_cafe_app() directly without lifespan.
app = create_cafe_app(lifespan=_cafe_lifespan)


async def run_server(server_app, host: str, port: int, name: str) -> None:
    """Run a uvicorn server as an async task."""
    config = uvicorn.Config(
        server_app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info("Starting %s on %s:%s", name, host, port)
    await server.serve()


async def main() -> None:
    """Start all servers in one process: Cafe + 3 demo backends.

    This is the local development mode. For Docker, each service runs
    independently (see docker-compose.yml).
    """
    cfg = load_config()

    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Initialize database and seed demo data
    logger.info("Initializing database...")
    db = await init_db(cfg.db_path)
    logger.info("Seeding demo services...")
    await seed_demo_data(db, cfg)
    logger.info("Database ready.")

    # Configure encryption and Passport system
    configure_crypto(cfg.encryption_key)
    configure_passport(cfg.passport_signing_secret, cfg.issuer_api_key)
    configure_human(cfg.passport_signing_secret)
    configure_consent(cfg.passport_signing_secret)
    configure_pages(cfg.passport_signing_secret)
    configure_wizard(cfg.passport_signing_secret)
    configure_router(cfg.use_real_passport)
    if cfg.use_real_passport:
        logger.info("Passport mode: REAL JWT validation")
    else:
        logger.info("Passport mode: MVP (demo-passport only)")

    # Import demo backend apps
    from agentcafe.demo_backends.hotel import app as hotel_app
    from agentcafe.demo_backends.lunch import app as lunch_app
    from agentcafe.demo_backends.home_service import app as home_service_app

    cafe_app = create_cafe_app(cors_origins=cfg.cors_allowed_origins)

    print("\n" + "=" * 60)
    print("  AgentCafe ☕ — The Cafe for Agents")
    print("=" * 60)
    print(f"  Menu:    http://{cfg.cafe_host}:{cfg.cafe_port}/cafe/menu")
    print(f"  Order:   POST http://{cfg.cafe_host}:{cfg.cafe_port}/cafe/order")
    print("-" * 60)
    print(f"  Hotel backend:        {cfg.hotel_backend_url}")
    print(f"  Lunch backend:        {cfg.lunch_backend_url}")
    print(f"  Home Service backend: {cfg.home_service_backend_url}")
    print("=" * 60 + "\n")

    # Run all four servers concurrently
    try:
        await asyncio.gather(
            run_server(cafe_app, cfg.cafe_host, cfg.cafe_port, "AgentCafe"),
            run_server(hotel_app, "127.0.0.1", cfg.hotel_backend_port, "HotelBookingService"),
            run_server(lunch_app, "127.0.0.1", cfg.lunch_backend_port, "LunchDeliveryService"),
            run_server(home_service_app, "127.0.0.1", cfg.home_service_backend_port, "HomeServiceAppointmentService"),
        )
    finally:
        await close_http_client()
        await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down AgentCafe...")
