"""Shared test fixtures for AgentCafe tests."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agentcafe.db.engine import close_db, init_db
from agentcafe.db.seed import seed_demo_data
from agentcafe.config import load_config
from agentcafe.main import create_cafe_app

# pylint: disable=redefined-outer-name,unused-argument


@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def seeded_db():
    """Initialize an in-memory database and seed demo data."""
    db = await init_db(":memory:")
    cfg = load_config()
    await seed_demo_data(db, cfg)
    yield db
    await close_db()


@pytest_asyncio.fixture(scope="session")
async def cafe_client(seeded_db):
    """Async HTTP client for the Cafe app (no real server needed)."""
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
