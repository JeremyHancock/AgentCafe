"""OAuth 2.0 Authorization Server Provider for MCP SDK compatibility.

Implements the OAuthAuthorizationServerProvider protocol from the MCP SDK,
mapping OAuth tokens to AgentCafe's Passport system. This allows spec-compliant
MCP clients (Claude Code, Cursor, etc.) to authenticate via standard OAuth 2.0
and interact with AgentCafe's MCP tools.

See backlog item 1.18 and GitHub issue #18.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from typing import Any

from pydantic import AnyUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from agentcafe.db.engine import get_db

logger = logging.getLogger("agentcafe.mcp_oauth")

# Token lifetimes
ACCESS_TOKEN_TTL = 3 * 3600     # 3 hours (matches Tier-1 Passport TTL)
REFRESH_TOKEN_TTL = 7 * 86400   # 7 days
AUTH_CODE_TTL = 600              # 10 minutes


def _hash_secret(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class AgentCafeOAuthProvider:
    """Maps MCP OAuth to AgentCafe Passport-style access.

    - Dynamic client registration creates an OAuth client.
    - Authorization creates an auth code immediately (no interactive login
      required — MCP agents self-register, matching Passport Tier-1 semantics).
    - Token exchange returns a bearer token stored in oauth_access_tokens.
    - MCP tools that need Passport auth still accept passport= parameter;
      the OAuth layer gates transport-level access so MCP SDKs can connect.
    """

    # -- Client management ---------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        db = await get_db()
        row = await db.execute(
            "SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)
        )
        row = await row.fetchone()
        if not row:
            return None
        return OAuthClientInformationFull(
            client_id=row["client_id"],
            client_secret=None,   # never reveal the secret
            redirect_uris=[AnyUrl(u) for u in json.loads(row["redirect_uris"])],
            client_name=row["client_name"],
            scope=row["scopes"] or None,
            token_endpoint_auth_method=row["token_endpoint_auth_method"],
            grant_types=json.loads(row["grant_types"]),
            response_types=json.loads(row["response_types"]),
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        db = await get_db()
        secret_hash = _hash_secret(client_info.client_secret) if client_info.client_secret else None
        await db.execute(
            """INSERT INTO oauth_clients
               (client_id, client_secret_hash, redirect_uris, client_name,
                scopes, token_endpoint_auth_method, grant_types, response_types)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                client_info.client_id,
                secret_hash,
                json.dumps([str(u) for u in (client_info.redirect_uris or [])]),
                client_info.client_name,
                client_info.scope or "",
                client_info.token_endpoint_auth_method or "client_secret_post",
                json.dumps(client_info.grant_types or ["authorization_code", "refresh_token"]),
                json.dumps(client_info.response_types or ["code"]),
            ),
        )
        await db.commit()
        logger.info("Registered OAuth client %s", client_info.client_id)

    # -- Authorization -------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Issue an authorization code and redirect back immediately.

        AgentCafe MCP access is self-service (like Passport Tier-1 registration),
        so there is no interactive login step. We generate an auth code and
        redirect the client back with it.
        """
        code = secrets.token_urlsafe(30)  # ~240 bits entropy
        expires_at = time.time() + AUTH_CODE_TTL

        db = await get_db()
        await db.execute(
            """INSERT INTO oauth_auth_codes
               (code, client_id, scopes, code_challenge, redirect_uri,
                redirect_uri_provided_explicitly, resource, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                code,
                client.client_id,
                " ".join(params.scopes) if params.scopes else "",
                params.code_challenge,
                str(params.redirect_uri),
                1 if params.redirect_uri_provided_explicitly else 0,
                params.resource,
                expires_at,
            ),
        )
        await db.commit()

        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code,
            state=params.state,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        db = await get_db()
        row = await db.execute(
            "SELECT * FROM oauth_auth_codes WHERE code = ? AND client_id = ?",
            (authorization_code, client.client_id),
        )
        row = await row.fetchone()
        if not row:
            return None
        if row["expires_at"] < time.time():
            await db.execute("DELETE FROM oauth_auth_codes WHERE code = ?", (authorization_code,))
            await db.commit()
            return None
        return AuthorizationCode(
            code=row["code"],
            scopes=row["scopes"].split() if row["scopes"] else [],
            expires_at=row["expires_at"],
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=bool(row["redirect_uri_provided_explicitly"]),
            resource=row["resource"],
        )

    # -- Token exchange ------------------------------------------------------

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        db = await get_db()
        # Delete used auth code (single use)
        await db.execute("DELETE FROM oauth_auth_codes WHERE code = ?", (authorization_code.code,))

        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        now = time.time()
        scopes_str = " ".join(authorization_code.scopes) if authorization_code.scopes else ""

        await db.execute(
            """INSERT INTO oauth_access_tokens (token, client_id, scopes, resource, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (access_token, client.client_id, scopes_str, authorization_code.resource, now + ACCESS_TOKEN_TTL),
        )
        await db.execute(
            """INSERT INTO oauth_refresh_tokens (token, client_id, scopes, expires_at)
               VALUES (?, ?, ?, ?)""",
            (refresh_token, client.client_id, scopes_str, now + REFRESH_TOKEN_TTL),
        )
        await db.commit()

        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            refresh_token=refresh_token,
            scope=scopes_str or None,
        )

    # -- Refresh tokens ------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        db = await get_db()
        row = await db.execute(
            "SELECT * FROM oauth_refresh_tokens WHERE token = ? AND client_id = ?",
            (refresh_token, client.client_id),
        )
        row = await row.fetchone()
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < time.time():
            await db.execute("DELETE FROM oauth_refresh_tokens WHERE token = ?", (refresh_token,))
            await db.commit()
            return None
        return RefreshToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=row["scopes"].split() if row["scopes"] else [],
            expires_at=int(row["expires_at"]) if row["expires_at"] else None,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        db = await get_db()
        # Rotate: delete old refresh token
        await db.execute("DELETE FROM oauth_refresh_tokens WHERE token = ?", (refresh_token.token,))
        # Delete old access tokens for this client (rotation)
        await db.execute("DELETE FROM oauth_access_tokens WHERE client_id = ?", (client.client_id,))

        new_access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        now = time.time()
        scopes_str = " ".join(scopes) if scopes else " ".join(refresh_token.scopes)

        await db.execute(
            "INSERT INTO oauth_access_tokens (token, client_id, scopes, expires_at) VALUES (?, ?, ?, ?)",
            (new_access, client.client_id, scopes_str, now + ACCESS_TOKEN_TTL),
        )
        await db.execute(
            "INSERT INTO oauth_refresh_tokens (token, client_id, scopes, expires_at) VALUES (?, ?, ?, ?)",
            (new_refresh, client.client_id, scopes_str, now + REFRESH_TOKEN_TTL),
        )
        await db.commit()

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            refresh_token=new_refresh,
            scope=scopes_str or None,
        )

    # -- Token introspection -------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        db = await get_db()
        row = await db.execute(
            "SELECT * FROM oauth_access_tokens WHERE token = ?", (token,)
        )
        row = await row.fetchone()
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < time.time():
            await db.execute("DELETE FROM oauth_access_tokens WHERE token = ?", (token,))
            await db.commit()
            return None
        return AccessToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=row["scopes"].split() if row["scopes"] else [],
            expires_at=int(row["expires_at"]) if row["expires_at"] else None,
            resource=row["resource"],
        )

    # -- Revocation ----------------------------------------------------------

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        db = await get_db()
        if isinstance(token, AccessToken):
            await db.execute("DELETE FROM oauth_access_tokens WHERE token = ?", (token.token,))
            # Also revoke refresh tokens for this client
            await db.execute("DELETE FROM oauth_refresh_tokens WHERE client_id = ?", (token.client_id,))
        else:
            await db.execute("DELETE FROM oauth_refresh_tokens WHERE token = ?", (token.token,))
            # Also revoke access tokens for this client
            await db.execute("DELETE FROM oauth_access_tokens WHERE client_id = ?", (token.client_id,))
        await db.commit()
