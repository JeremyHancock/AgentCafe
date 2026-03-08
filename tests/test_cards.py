"""Tests for Company Cards — standing policies for service-level relationships.

Covers: card request, approval, token issuance, revocation, first-use confirmation,
risk tier enforcement, excluded actions, expired cards, budget checks.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import agentcafe.cafe.router as router_module
import agentcafe.cafe.passport as passport_module
import agentcafe.cafe.human as human_module
import agentcafe.cafe.consent as consent_module
import agentcafe.cafe.cards as cards_module
import agentcafe.cafe.pages as pages_module
from agentcafe.demo_backends.hotel import app as hotel_app
from agentcafe.demo_backends.lunch import app as lunch_app
from agentcafe.demo_backends.home_service import app as home_service_app
from agentcafe.keys import configure_keys, decode_passport_token

# pylint: disable=redefined-outer-name,protected-access

TEST_SECRET = "test-secret-key-for-cards-testing"
TEST_API_KEY = "test-issuer-api-key"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _mock_verify_passkey(challenge_id, credential):  # pylint: disable=unused-argument
    """Test mock: challenge_id is the user_id, credential is ignored."""
    return {"user_id": challenge_id, "email": "mock@example.com"}


@pytest_asyncio.fixture(autouse=True)
async def _configure_modules(monkeypatch):
    """Configure all modules with test secrets."""
    monkeypatch.setattr(router_module._state, "use_real_passport", True)
    monkeypatch.setattr(passport_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(passport_module._state, "issuer_api_key", TEST_API_KEY)
    monkeypatch.setattr(human_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(consent_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(cards_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(pages_module._state, "signing_secret", TEST_SECRET)
    # Mock passkey verification
    monkeypatch.setattr(cards_module, "verify_passkey_assertion", _mock_verify_passkey)
    configure_keys(legacy_hs256_secret=TEST_SECRET)
    passport_module._register_hits.clear()
    yield


_BACKEND_APPS = {
    "http://127.0.0.1:8001": hotel_app,
    "http://127.0.0.1:8002": lunch_app,
    "http://127.0.0.1:8003": home_service_app,
}


class _MultiBackendTransport:
    def __init__(self):
        self._transports = {
            base: ASGITransport(app=app) for base, app in _BACKEND_APPS.items()
        }

    async def handle_async_request(self, request):
        url = str(request.url)
        for base, transport in self._transports.items():
            if url.startswith(base):
                return await transport.handle_async_request(request)
        raise RuntimeError(f"No backend transport for URL: {url}")

    async def aclose(self) -> None:
        for transport in self._transports.values():
            await transport.aclose()


@pytest_asyncio.fixture(autouse=True)
async def _mock_http_client(monkeypatch):
    mock_client = AsyncClient(transport=_MultiBackendTransport())
    monkeypatch.setattr(router_module._state, "http_client", mock_client)
    yield
    await mock_client.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _register_agent(cafe_client) -> str:
    """Register an agent and return its Tier-1 token."""
    resp = await cafe_client.post("/passport/register", json={"agent_tag": "cards-test-bot"})
    assert resp.status_code == 200
    return resp.json()["passport"]


async def _register_human(cafe_client) -> tuple[str, str]:
    """Register a human and return (user_id, session_token)."""
    resp = await cafe_client.post("/human/register", json={
        "email": f"cards-{__import__('uuid').uuid4().hex[:6]}@example.com",
        "password": "secure-password-123",
        "display_name": "CardTestUser",
    })
    assert resp.status_code == 200
    data = resp.json()
    return data["user_id"], data["session_token"]


async def _request_and_approve_card(
    cafe_client,
    service_id="stayright-hotels",
    first_use_confirmation=False,
    excluded_action_ids=None,
    allowed_action_ids=None,
    budget_limit_cents=None,
    budget_period=None,
    duration_days=30,
):
    """Run the full card request + approval flow, return (agent_token, user_id, session, card_id)."""
    agent_token = await _register_agent(cafe_client)
    user_id, human_session = await _register_human(cafe_client)

    # Agent requests card
    resp = await cafe_client.post(
        "/cards/request",
        json={"service_id": service_id},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    card_data = resp.json()
    card_id = card_data["card_id"]
    assert card_data["status"] == "pending"

    # Human approves
    approve_body = {
        "passkey_challenge_id": user_id,
        "passkey_credential": {},
        "first_use_confirmation": first_use_confirmation,
        "duration_days": duration_days,
    }
    if excluded_action_ids is not None:
        approve_body["excluded_action_ids"] = excluded_action_ids
    if allowed_action_ids is not None:
        approve_body["allowed_action_ids"] = allowed_action_ids
    if budget_limit_cents is not None:
        approve_body["budget_limit_cents"] = budget_limit_cents
    if budget_period is not None:
        approve_body["budget_period"] = budget_period

    resp = await cafe_client.post(
        f"/cards/{card_id}/approve",
        json=approve_body,
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    return agent_token, user_id, human_session, card_id


# ---------------------------------------------------------------------------
# Card Request Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_request(cafe_client):
    """POST /cards/request should create a pending card and return consent URL."""
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/cards/request",
        json={"service_id": "stayright-hotels"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert "card_id" in data
    assert "consent_url" in data
    assert "activation_code" in data
    assert len(data["activation_code"]) == 8


@pytest.mark.asyncio
async def test_card_request_invalid_service(cafe_client):
    """POST /cards/request with unknown service should return 404."""
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/cards/request",
        json={"service_id": "nonexistent-service"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_card_request_no_passport(cafe_client):
    """POST /cards/request without passport should return 401."""
    resp = await cafe_client.post(
        "/cards/request",
        json={"service_id": "stayright-hotels"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Card Status Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_status_pending(cafe_client):
    """GET /cards/{card_id}/status should return pending for unapproved card."""
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/cards/request",
        json={"service_id": "stayright-hotels"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    card_id = resp.json()["card_id"]

    resp = await cafe_client.get(f"/cards/{card_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_card_status_not_found(cafe_client):
    """GET /cards/{card_id}/status with bogus ID should return 404."""
    resp = await cafe_client.get("/cards/nonexistent-card-id/status")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Card Approval Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_approve(cafe_client):
    """POST /cards/{card_id}/approve should activate the card."""
    _, _, _, card_id = await _request_and_approve_card(cafe_client)

    # Verify status is now active
    resp = await cafe_client.get(f"/cards/{card_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_card_approve_with_excluded_actions(cafe_client):
    """Approving a card with excluded actions should be stored."""
    _, _, session, card_id = await _request_and_approve_card(
        cafe_client,
        excluded_action_ids=["cancel-booking"],
    )

    # List cards to verify
    resp = await cafe_client.get(
        "/cards",
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 200
    cards = resp.json()["cards"]
    card = next(c for c in cards if c["card_id"] == card_id)
    assert "cancel-booking" in card["excluded_action_ids"]


@pytest.mark.asyncio
async def test_card_approve_no_session(cafe_client):
    """Approving without session should return 401."""
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/cards/request",
        json={"service_id": "stayright-hotels"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    card_id = resp.json()["card_id"]

    resp = await cafe_client.post(
        f"/cards/{card_id}/approve",
        json={"passkey_challenge_id": "fake", "passkey_credential": {}, "duration_days": 30},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_card_approve_invalid_excluded_action(cafe_client):
    """Approving with a nonexistent excluded action should return 422."""
    agent_token = await _register_agent(cafe_client)
    _user_id, session = await _register_human(cafe_client)

    resp = await cafe_client.post(
        "/cards/request",
        json={"service_id": "stayright-hotels"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    card_id = resp.json()["card_id"]

    resp = await cafe_client.post(
        f"/cards/{card_id}/approve",
        json={
            "passkey_challenge_id": _user_id,
            "passkey_credential": {},
            "excluded_action_ids": ["nonexistent-action"],
            "duration_days": 30,
        },
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Card Token Issuance Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_token_issuance(cafe_client):
    """POST /cards/{card_id}/token should return a Tier-2 write token."""
    agent_token, _, _, card_id = await _request_and_approve_card(cafe_client)

    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tier"] == "write"
    assert data["card_id"] == card_id
    assert "stayright-hotels:search-availability" in data["scopes"]

    # Decode and verify token claims
    payload = decode_passport_token(data["token"])
    assert payload["tier"] == "write"
    assert payload["granted_by"] == "company_card"
    assert payload["card_id"] == card_id


@pytest.mark.asyncio
async def test_card_token_multiple_issuance(cafe_client):
    """Agent should be able to get multiple tokens from the same card without re-approval."""
    agent_token, _, _, card_id = await _request_and_approve_card(cafe_client)

    for _ in range(3):
        resp = await cafe_client.post(
            f"/cards/{card_id}/token",
            json={"action_id": "search-availability"},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["tier"] == "write"


@pytest.mark.asyncio
async def test_card_token_excluded_action(cafe_client):
    """Token request for an excluded action should return 403."""
    agent_token, _, _, card_id = await _request_and_approve_card(
        cafe_client,
        excluded_action_ids=["book-room"],
    )

    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "book-room"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "action_excluded"


@pytest.mark.asyncio
async def test_card_token_not_in_allowed_scope(cafe_client):
    """Token request for an action not in allowed list should return 403."""
    agent_token, _, _, card_id = await _request_and_approve_card(
        cafe_client,
        allowed_action_ids=["search-availability"],
    )

    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "book-room"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "action_not_in_scope"


@pytest.mark.asyncio
async def test_card_token_high_risk_rejected(cafe_client):
    """Token request for a high-risk action should be rejected (card doesn't cover it).

    cancel-booking is seeded as risk_tier='high' — cards only cover low/medium.
    """
    agent_token, _, _, card_id = await _request_and_approve_card(cafe_client)

    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "cancel-booking"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "risk_tier_exceeds_card"


@pytest.mark.asyncio
async def test_card_token_invalid_action(cafe_client):
    """Token request for a nonexistent action should return 404."""
    agent_token, _, _, card_id = await _request_and_approve_card(cafe_client)

    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "nonexistent-action"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_card_token_revoked_card(cafe_client):
    """Token request on a revoked card should return 401."""
    agent_token, _, session, card_id = await _request_and_approve_card(cafe_client)

    # Revoke the card
    resp = await cafe_client.post(
        f"/cards/{card_id}/revoke",
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 200

    # Try to get a token
    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "card_revoked"


# ---------------------------------------------------------------------------
# First-Use Confirmation Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_first_use_confirmation_required(cafe_client):
    """Card with first_use_confirmation=True should reject tokens until confirmed."""
    agent_token, _, session, card_id = await _request_and_approve_card(
        cafe_client,
        first_use_confirmation=True,
    )

    # Token request should fail — first use not confirmed
    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "first_use_confirmation_required"

    # Human confirms first use
    resp = await cafe_client.post(
        f"/cards/{card_id}/confirm-first-use",
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 200

    # Now token should work
    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["tier"] == "write"


@pytest.mark.asyncio
async def test_card_no_first_use_confirmation(cafe_client):
    """Card with first_use_confirmation=False should issue tokens immediately."""
    agent_token, _, _, card_id = await _request_and_approve_card(
        cafe_client,
        first_use_confirmation=False,
    )

    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Card Revocation Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_revoke(cafe_client):
    """POST /cards/{card_id}/revoke should revoke the card."""
    _, _, session, card_id = await _request_and_approve_card(cafe_client)

    resp = await cafe_client.post(
        f"/cards/{card_id}/revoke",
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"

    # Verify status
    resp = await cafe_client.get(f"/cards/{card_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"


@pytest.mark.asyncio
async def test_card_revoke_not_owner(cafe_client):
    """Revoking someone else's card should return 403."""
    _, _, _, card_id = await _request_and_approve_card(cafe_client)

    # Register a different human
    _, other_session = await _register_human(cafe_client)

    resp = await cafe_client.post(
        f"/cards/{card_id}/revoke",
        headers={"Authorization": f"Bearer {other_session}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_card_double_revoke(cafe_client):
    """Revoking an already-revoked card should return 409."""
    _, _, session, card_id = await _request_and_approve_card(cafe_client)

    resp = await cafe_client.post(
        f"/cards/{card_id}/revoke",
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 200

    resp = await cafe_client.post(
        f"/cards/{card_id}/revoke",
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Card Listing (Tab) Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_cards(cafe_client):
    """GET /cards should return the human's active cards."""
    _, _, session, card_id = await _request_and_approve_card(cafe_client)

    resp = await cafe_client.get(
        "/cards",
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "cards" in data
    card_ids = [c["card_id"] for c in data["cards"]]
    assert card_id in card_ids


@pytest.mark.asyncio
async def test_list_cards_no_session(cafe_client):
    """GET /cards without session should return 401."""
    resp = await cafe_client.get("/cards")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Card Token Used in Order Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_token_works_in_order(cafe_client):
    """A token issued from a card should work in POST /cafe/order."""
    agent_token, _, _, card_id = await _request_and_approve_card(cafe_client)

    # Get a token for search-availability
    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    write_token = resp.json()["token"]

    # Use it to place an order
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": write_token,
        "inputs": {"city": "Miami", "check_in": "2026-04-01", "check_out": "2026-04-05", "guests": 2},
    })
    assert resp.status_code == 200  # card token works in /cafe/order


# ---------------------------------------------------------------------------
# Card Edit Constraints Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_edit_excluded_actions(cafe_client):
    """PATCH /cards/{card_id} should update excluded actions."""
    _, _, session, card_id = await _request_and_approve_card(cafe_client)

    resp = await cafe_client.patch(
        f"/cards/{card_id}",
        json={"excluded_action_ids": ["book-room"]},
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"

    # Verify via list
    resp = await cafe_client.get("/cards", headers={"Authorization": f"Bearer {session}"})
    cards = resp.json()["cards"]
    card = next(c for c in cards if c["card_id"] == card_id)
    assert "book-room" in card["excluded_action_ids"]


@pytest.mark.asyncio
async def test_card_edit_budget(cafe_client):
    """PATCH /cards/{card_id} should update budget constraints."""
    _, _, session, card_id = await _request_and_approve_card(cafe_client)

    resp = await cafe_client.patch(
        f"/cards/{card_id}",
        json={"budget_limit_cents": 5000, "budget_period": "monthly"},
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_card_edit_not_owner(cafe_client):
    """PATCH by non-owner should return 403."""
    _, _, _, card_id = await _request_and_approve_card(cafe_client)
    _, other_session = await _register_human(cafe_client)

    resp = await cafe_client.patch(
        f"/cards/{card_id}",
        json={"excluded_action_ids": ["book-room"]},
        headers={"Authorization": f"Bearer {other_session}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_card_edit_revoked_card(cafe_client):
    """PATCH on a revoked card should return 409."""
    _, _, session, card_id = await _request_and_approve_card(cafe_client)

    # Revoke first
    await cafe_client.post(f"/cards/{card_id}/revoke", headers={"Authorization": f"Bearer {session}"})

    resp = await cafe_client.patch(
        f"/cards/{card_id}",
        json={"excluded_action_ids": ["book-room"]},
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_card_edit_no_changes(cafe_client):
    """PATCH with no editable fields should return 422."""
    _, _, session, card_id = await _request_and_approve_card(cafe_client)

    resp = await cafe_client.patch(
        f"/cards/{card_id}",
        json={},
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_card_edit_invalid_excluded_action(cafe_client):
    """PATCH with nonexistent excluded action should return 422."""
    _, _, session, card_id = await _request_and_approve_card(cafe_client)

    resp = await cafe_client.patch(
        f"/cards/{card_id}",
        json={"excluded_action_ids": ["nonexistent-action"]},
        headers={"Authorization": f"Bearer {session}"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tab Page Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tab_page_redirects_without_session(cafe_client):
    """GET /tab without session should redirect to login."""
    resp = await cafe_client.get("/tab", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_tab_page_renders_with_session(cafe_client):
    """GET /tab with session should render the tab page."""
    _, _, session, _ = await _request_and_approve_card(cafe_client)

    resp = await cafe_client.get("/tab", cookies={"cafe_session": session})
    assert resp.status_code == 200
    assert "Your Tab" in resp.text


@pytest.mark.asyncio
async def test_tab_approve_page_renders(cafe_client):
    """GET /tab/approve/{card_id} should render the approval page for pending cards."""
    agent_token = await _register_agent(cafe_client)
    _, session = await _register_human(cafe_client)

    resp = await cafe_client.post(
        "/cards/request",
        json={"service_id": "stayright-hotels"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    card_id = resp.json()["card_id"]

    resp = await cafe_client.get(
        f"/tab/approve/{card_id}",
        cookies={"cafe_session": session},
    )
    assert resp.status_code == 200
    assert "Approve Card" in resp.text


@pytest.mark.asyncio
async def test_tab_revoke_via_page(cafe_client):
    """POST /tab/{card_id}/revoke should revoke card and redirect."""
    _, _, session, card_id = await _request_and_approve_card(cafe_client)

    # Need CSRF token — get it from /tab page
    tab_resp = await cafe_client.get("/tab", cookies={"cafe_session": session})
    assert tab_resp.status_code == 200

    # Extract CSRF token from page
    import re
    csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', tab_resp.text)
    assert csrf_match, "CSRF token not found in tab page"
    csrf_token = csrf_match.group(1)

    resp = await cafe_client.post(
        f"/tab/{card_id}/revoke",
        data={"csrf_token": csrf_token},
        cookies={"cafe_session": session},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "action=revoked" in resp.headers["location"]

    # Verify card is revoked
    status_resp = await cafe_client.get(f"/cards/{card_id}/status")
    assert status_resp.json()["status"] == "revoked"


@pytest.mark.asyncio
async def test_tab_confirm_first_use_via_page(cafe_client):
    """POST /tab/{card_id}/confirm should confirm first use and redirect."""
    agent_token, _, session, card_id = await _request_and_approve_card(
        cafe_client, first_use_confirmation=True,
    )

    # Get CSRF token
    tab_resp = await cafe_client.get("/tab", cookies={"cafe_session": session})
    import re
    csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', tab_resp.text)
    assert csrf_match
    csrf_token = csrf_match.group(1)

    resp = await cafe_client.post(
        f"/tab/{card_id}/confirm",
        data={"csrf_token": csrf_token},
        cookies={"cafe_session": session},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "action=confirmed" in resp.headers["location"]

    # Now token should work after first-use confirmation
    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Card Suggestion on 403 Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_403_includes_card_suggestion(cafe_client):
    """403 responses from /cafe/order should include card_suggestion."""
    # Register agent with Tier-1 passport (read-only)
    resp = await cafe_client.post("/passport/register", json={"agent_tag": "card-suggest-agent"})
    assert resp.status_code == 200
    tier1_token = resp.json()["passport"]

    # Try to order with Tier-1 token on a write action (book-room requires human auth)
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "book-room",
        "passport": tier1_token,
        "inputs": {"hotel_id": "H001", "check_in": "2026-04-01", "check_out": "2026-04-05", "guests": 2},
    })
    # Tier-1 (read) passport must be rejected for a write action requiring human auth
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "card_suggestion" in detail
    suggestion = detail["card_suggestion"]
    assert suggestion["action"] == "request_card"
    assert suggestion["endpoint"] == "POST /cards/request"
    assert suggestion["body"]["service_id"] == "stayright-hotels"


# ---------------------------------------------------------------------------
# Budget Tracking Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_spend(cafe_client):
    """POST /cards/{card_id}/report-spend should increment budget."""
    agent_token, _, _, card_id = await _request_and_approve_card(
        cafe_client, budget_limit_cents=10000, budget_period="monthly",
    )

    resp = await cafe_client.post(
        f"/cards/{card_id}/report-spend",
        json={"amount_cents": 2500, "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["budget_spent_cents"] == 2500
    assert data["budget_exceeded"] is False

    # Spend more
    resp = await cafe_client.post(
        f"/cards/{card_id}/report-spend",
        json={"amount_cents": 8000},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["budget_spent_cents"] == 10500
    assert data["budget_exceeded"] is True
    assert "warning" in data


@pytest.mark.asyncio
async def test_budget_enforcement_blocks_token(cafe_client):
    """Token issuance should be blocked when card budget is exceeded."""
    agent_token, _, _, card_id = await _request_and_approve_card(
        cafe_client, budget_limit_cents=1000, budget_period="monthly",
    )

    # Exceed the budget
    resp = await cafe_client.post(
        f"/cards/{card_id}/report-spend",
        json={"amount_cents": 1001},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["budget_exceeded"] is True

    # Try to get a token — should be blocked
    resp = await cafe_client.post(
        f"/cards/{card_id}/token",
        json={"action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "budget_exceeded"


@pytest.mark.asyncio
async def test_report_spend_not_found(cafe_client):
    """report-spend on nonexistent card should return 404."""
    agent_token = await _register_agent(cafe_client)

    resp = await cafe_client.post(
        "/cards/nonexistent/report-spend",
        json={"amount_cents": 100},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_report_spend_revoked_card(cafe_client):
    """report-spend on revoked card should return 409."""
    agent_token, _, session, card_id = await _request_and_approve_card(cafe_client)

    # Revoke
    await cafe_client.post(f"/cards/{card_id}/revoke", headers={"Authorization": f"Bearer {session}"})

    resp = await cafe_client.post(
        f"/cards/{card_id}/report-spend",
        json={"amount_cents": 100},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 409
