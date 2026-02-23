"""Tests for the GET /cafe/menu endpoint."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_menu_returns_all_three_services(cafe_client):
    """The Menu should contain all three demo services."""
    resp = await cafe_client.get("/cafe/menu")
    assert resp.status_code == 200

    data = resp.json()
    assert data["cafe"] == "AgentCafe"
    assert data["version"] == "1.0.0"
    assert "services" in data

    services = data["services"]
    assert len(services) == 3

    service_ids = {s["service_id"] for s in services}
    assert service_ids == {
        "stayright-hotels",
        "quickbite-delivery",
        "fixright-home",
    }


@pytest.mark.asyncio
async def test_menu_locked_format(cafe_client):
    """Every service and action must conform to the locked Menu format."""
    resp = await cafe_client.get("/cafe/menu")
    data = resp.json()

    # Top-level keys
    assert "order_endpoint" in data
    assert data["order_endpoint"] == "POST /cafe/order"
    assert "order_format" in data

    for service in data["services"]:
        # Service-level keys
        assert "service_id" in service
        assert "name" in service
        assert "description" in service
        assert "actions" in service
        assert len(service["actions"]) > 0

        for action in service["actions"]:
            # Action-level keys (locked format)
            assert "action_id" in action, f"Missing action_id in {service['service_id']}"
            assert "description" in action
            assert "required_inputs" in action
            assert "cost" in action
            assert "example_response" in action

            # Cost object structure
            cost = action["cost"]
            assert "required_scopes" in cost
            assert "human_authorization_required" in cost
            assert "limits" in cost
            assert isinstance(cost["required_scopes"], list)

            # Required inputs structure
            for inp in action["required_inputs"]:
                assert "name" in inp
                assert "description" in inp
                assert "example" in inp


@pytest.mark.asyncio
async def test_menu_hotel_actions(cafe_client):
    """Hotel service should have exactly 4 actions."""
    resp = await cafe_client.get("/cafe/menu")
    services = resp.json()["services"]

    hotel = next(s for s in services if s["service_id"] == "stayright-hotels")
    action_ids = [a["action_id"] for a in hotel["actions"]]
    assert action_ids == ["search-availability", "get-room-details", "book-room", "cancel-booking"]


@pytest.mark.asyncio
async def test_menu_lunch_actions(cafe_client):
    """Lunch service should have exactly 4 actions."""
    resp = await cafe_client.get("/cafe/menu")
    services = resp.json()["services"]

    lunch = next(s for s in services if s["service_id"] == "quickbite-delivery")
    action_ids = [a["action_id"] for a in lunch["actions"]]
    assert action_ids == ["browse-menu", "place-order", "track-order", "cancel-order"]


@pytest.mark.asyncio
async def test_menu_home_service_actions(cafe_client):
    """Home service should have exactly 4 actions."""
    resp = await cafe_client.get("/cafe/menu")
    services = resp.json()["services"]

    home = next(s for s in services if s["service_id"] == "fixright-home")
    action_ids = [a["action_id"] for a in home["actions"]]
    assert action_ids == ["search-providers", "book-appointment", "reschedule-appointment", "cancel-appointment"]


@pytest.mark.asyncio
async def test_menu_write_actions_require_human_auth(cafe_client):
    """All write/financial actions must require human authorization."""
    resp = await cafe_client.get("/cafe/menu")
    services = resp.json()["services"]

    write_actions = [
        ("stayright-hotels", "book-room"),
        ("stayright-hotels", "cancel-booking"),
        ("quickbite-delivery", "place-order"),
        ("quickbite-delivery", "cancel-order"),
        ("fixright-home", "book-appointment"),
        ("fixright-home", "reschedule-appointment"),
        ("fixright-home", "cancel-appointment"),
    ]

    for svc_id, act_id in write_actions:
        service = next(s for s in services if s["service_id"] == svc_id)
        action = next(a for a in service["actions"] if a["action_id"] == act_id)
        assert action["cost"]["human_authorization_required"] is True, (
            f"{svc_id}/{act_id} should require human authorization"
        )


@pytest.mark.asyncio
async def test_menu_read_actions_dont_require_human_auth(cafe_client):
    """Read-only actions should not require human authorization."""
    resp = await cafe_client.get("/cafe/menu")
    services = resp.json()["services"]

    read_actions = [
        ("stayright-hotels", "search-availability"),
        ("stayright-hotels", "get-room-details"),
        ("quickbite-delivery", "browse-menu"),
        ("quickbite-delivery", "track-order"),
        ("fixright-home", "search-providers"),
    ]

    for svc_id, act_id in read_actions:
        service = next(s for s in services if s["service_id"] == svc_id)
        action = next(a for a in service["actions"] if a["action_id"] == act_id)
        assert action["cost"]["human_authorization_required"] is False, (
            f"{svc_id}/{act_id} should NOT require human authorization"
        )
