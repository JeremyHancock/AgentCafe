"""AgentCafe — main entry point.

Starts the Cafe server (port 8000) and three demo backends (ports 8001-8003).
All four servers run concurrently in one process using asyncio.
"""

from __future__ import annotations

import asyncio
import logging
import uvicorn
from fastapi import FastAPI

from agentcafe.cafe.passport import configure_passport, passport_router
from agentcafe.cafe.router import close_http_client, configure_router, router as cafe_router
from agentcafe.config import load_config
from agentcafe.db.engine import close_db, init_db
from agentcafe.db.seed import seed_demo_data

logger = logging.getLogger("agentcafe")


def create_cafe_app() -> FastAPI:
    """Create the main AgentCafe FastAPI application."""
    app = FastAPI(
        title="AgentCafe",
        version="0.1.0",
        description="The Cafe for Agents. Browse the Menu, present your Passport, and order.",
    )
    app.include_router(cafe_router)
    app.include_router(passport_router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "agentcafe"}

    return app


async def run_server(app, host: str, port: int, name: str) -> None:
    """Run a uvicorn server as an async task."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info("Starting %s on %s:%s", name, host, port)
    await server.serve()


async def main() -> None:
    """Start all servers: Cafe + 3 demo backends."""
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

    # Configure Passport system
    configure_passport(cfg.passport_signing_secret, cfg.issuer_api_key)
    configure_router(cfg.use_real_passport)
    if cfg.use_real_passport:
        logger.info("Passport mode: REAL JWT validation")
    else:
        logger.info("Passport mode: MVP (demo-passport only)")

    # Import demo backend apps
    from agentcafe.demo_backends.hotel import app as hotel_app
    from agentcafe.demo_backends.lunch import app as lunch_app
    from agentcafe.demo_backends.home_service import app as home_service_app

    cafe_app = create_cafe_app()

    print("\n" + "=" * 60)
    print("  AgentCafe ☕ — The Cafe for Agents")
    print("=" * 60)
    print(f"  Menu:    http://{cfg.cafe_host}:{cfg.cafe_port}/cafe/menu")
    print(f"  Order:   POST http://{cfg.cafe_host}:{cfg.cafe_port}/cafe/order")
    print("-" * 60)
    print(f"  Hotel backend:        http://127.0.0.1:{cfg.hotel_backend_port}")
    print(f"  Lunch backend:        http://127.0.0.1:{cfg.lunch_backend_port}")
    print(f"  Home Service backend: http://127.0.0.1:{cfg.home_service_backend_port}")
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
