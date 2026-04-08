"""Tests for MCP OAuth 2.0 integration (backlog 1.18).

Validates that spec-compliant MCP clients can authenticate via OAuth
and access AgentCafe's MCP tools.
"""

from __future__ import annotations

# pylint: disable=redefined-outer-name

import time

import pytest
import pytest_asyncio

from agentcafe.cafe.mcp_oauth import (
    AgentCafeOAuthProvider,
    ACCESS_TOKEN_TTL,
)
from agentcafe.db.engine import get_db
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client_info(client_id: str = "test-client") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="test-secret-1234",
        redirect_uris=[AnyUrl("http://localhost:3000/callback")],
        client_name="Test MCP Client",
        scope="cafe.search cafe.invoke",
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )


def _make_auth_params(**overrides) -> AuthorizationParams:
    defaults = dict(
        state="test-state-abc",
        scopes=["cafe.search", "cafe.invoke"],
        code_challenge="E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
    )
    defaults.update(overrides)
    return AuthorizationParams(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def _ensure_db(seeded_db):  # pylint: disable=unused-argument
    """Ensure the seeded database is available (includes migration 0015)."""
    yield


@pytest.fixture
def provider():
    return AgentCafeOAuthProvider()


# ---------------------------------------------------------------------------
# Client registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_and_get_client(provider):
    info = _make_client_info("reg-test-1")
    await provider.register_client(info)

    loaded = await provider.get_client("reg-test-1")
    assert loaded is not None
    assert loaded.client_id == "reg-test-1"
    assert loaded.client_name == "Test MCP Client"


@pytest.mark.asyncio
async def test_get_nonexistent_client(provider):
    result = await provider.get_client("does-not-exist")
    assert result is None


# ---------------------------------------------------------------------------
# Authorization flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_authorize_returns_redirect_with_code(provider):
    client = _make_client_info("auth-test-1")
    await provider.register_client(client)
    params = _make_auth_params()

    redirect_url = await provider.authorize(client, params)

    assert "code=" in redirect_url
    assert "state=test-state-abc" in redirect_url
    assert redirect_url.startswith("http://localhost:3000/callback")


@pytest.mark.asyncio
async def test_load_authorization_code(provider):
    client = _make_client_info("auth-test-2")
    await provider.register_client(client)
    params = _make_auth_params()

    redirect_url = await provider.authorize(client, params)
    # Extract code from URL
    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect_url).query)["code"][0]

    loaded = await provider.load_authorization_code(client, code)
    assert loaded is not None
    assert loaded.client_id == "auth-test-2"
    assert loaded.code_challenge == params.code_challenge
    assert loaded.scopes == ["cafe.search", "cafe.invoke"]


@pytest.mark.asyncio
async def test_expired_auth_code_returns_none(provider):
    client = _make_client_info("auth-test-3")
    await provider.register_client(client)

    # Insert expired code directly
    db = await get_db()
    await db.execute(
        """INSERT INTO oauth_auth_codes
           (code, client_id, scopes, code_challenge, redirect_uri,
            redirect_uri_provided_explicitly, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("expired-code", "auth-test-3", "", "challenge", "http://localhost/cb", 1, time.time() - 100),
    )
    await db.commit()

    loaded = await provider.load_authorization_code(client, "expired-code")
    assert loaded is None


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exchange_auth_code_for_tokens(provider):
    client = _make_client_info("token-test-1")
    await provider.register_client(client)
    params = _make_auth_params()
    redirect_url = await provider.authorize(client, params)

    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect_url).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, code)
    assert auth_code is not None

    token = await provider.exchange_authorization_code(client, auth_code)
    assert token.access_token
    assert token.refresh_token
    assert token.token_type == "Bearer"
    assert token.expires_in == ACCESS_TOKEN_TTL

    # Auth code should be consumed (single-use)
    reloaded = await provider.load_authorization_code(client, code)
    assert reloaded is None


@pytest.mark.asyncio
async def test_access_token_loads(provider):
    client = _make_client_info("token-test-2")
    await provider.register_client(client)
    params = _make_auth_params()
    redirect_url = await provider.authorize(client, params)

    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect_url).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, code)
    token = await provider.exchange_authorization_code(client, auth_code)

    loaded = await provider.load_access_token(token.access_token)
    assert loaded is not None
    assert loaded.client_id == "token-test-2"
    assert "cafe.search" in loaded.scopes


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_token_flow(provider):
    client = _make_client_info("refresh-test-1")
    await provider.register_client(client)
    params = _make_auth_params()
    redirect_url = await provider.authorize(client, params)

    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect_url).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, code)
    token = await provider.exchange_authorization_code(client, auth_code)

    refresh = await provider.load_refresh_token(client, token.refresh_token)
    assert refresh is not None

    new_token = await provider.exchange_refresh_token(client, refresh, ["cafe.search"])
    assert new_token.access_token != token.access_token
    assert new_token.refresh_token != token.refresh_token
    assert new_token.token_type == "Bearer"

    # Old refresh token should be consumed
    old_refresh = await provider.load_refresh_token(client, token.refresh_token)
    assert old_refresh is None


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revoke_access_token(provider):
    client = _make_client_info("revoke-test-1")
    await provider.register_client(client)
    params = _make_auth_params()
    redirect_url = await provider.authorize(client, params)

    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect_url).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, code)
    token = await provider.exchange_authorization_code(client, auth_code)

    access = await provider.load_access_token(token.access_token)
    assert access is not None

    await provider.revoke_token(access)

    # Both access and refresh should be gone
    assert await provider.load_access_token(token.access_token) is None
    refresh = await provider.load_refresh_token(client, token.refresh_token)
    assert refresh is None


# ---------------------------------------------------------------------------
# MCP server configuration
# ---------------------------------------------------------------------------

def test_mcp_server_has_auth():
    """Verify the MCP server was created with OAuth auth settings."""
    from agentcafe.cafe.mcp_adapter import mcp_server
    assert mcp_server.settings.auth is not None


def test_mcp_server_client_registration_enabled():
    from agentcafe.cafe.mcp_adapter import mcp_server
    assert mcp_server.settings.auth.client_registration_options is not None
    assert mcp_server.settings.auth.client_registration_options.enabled is True


# ---------------------------------------------------------------------------
# OAuth discovery endpoints (HTTP-level)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_protected_resource_metadata_at_root(seeded_db):  # pylint: disable=unused-argument
    """RFC 9728: /.well-known/oauth-protected-resource/mcp must be at the root."""
    from httpx import ASGITransport, AsyncClient
    from agentcafe.main import create_cafe_app
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert resp.status_code == 200
        data = resp.json()
        assert "authorization_servers" in data
        assert "resource" in data


@pytest.mark.asyncio
async def test_as_metadata_under_mcp(seeded_db):  # pylint: disable=unused-argument
    """RFC 8414: /.well-known/oauth-authorization-server must be under /mcp."""
    from httpx import ASGITransport, AsyncClient
    from agentcafe.main import create_cafe_app
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/mcp/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = resp.json()
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "registration_endpoint" in data


@pytest.mark.asyncio
async def test_mcp_oauth_urls_use_public_url(seeded_db):  # pylint: disable=unused-argument
    """Verify MCP OAuth metadata serves production URLs, not localhost defaults.

    The MCP SDK snapshots issuer_url into Starlette routes when
    streamable_http_app() is called. configure_mcp_server() must run
    BEFORE create_cafe_app() so the correct URLs are baked in.
    """
    from agentcafe.cafe.mcp_adapter import configure_mcp_server
    from agentcafe.main import create_cafe_app
    from httpx import ASGITransport, AsyncClient

    configure_mcp_server("https://example.com")
    try:
        app = create_cafe_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Check the authorization server metadata baked into the MCP sub-app
            resp = await client.get("/mcp/.well-known/oauth-authorization-server")
            assert resp.status_code == 200
            data = resp.json()
            assert data["issuer"] == "https://example.com/mcp"
            assert "https://example.com/mcp" in data["token_endpoint"]
            assert "https://example.com/mcp" in data["authorization_endpoint"]

            # Check the root-level protected resource metadata
            resp2 = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2["resource"] == "https://example.com/mcp"
            assert "https://example.com/mcp" in str(data2["authorization_servers"])
    finally:
        # Restore default for other tests
        from agentcafe.cafe.mcp_adapter import _DEFAULT_ISSUER, mcp_server as _mcp
        _mcp.settings.auth.issuer_url = _DEFAULT_ISSUER
        _mcp.settings.auth.resource_server_url = _DEFAULT_ISSUER
