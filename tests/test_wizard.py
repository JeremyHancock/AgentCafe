"""Tests for the Company Onboarding Wizard (Phase 3).

Tests cover: spec parsing, rule-based enrichment, review engine,
publisher, and the full wizard API flow via the router.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agentcafe.db.engine import close_db, init_db
from agentcafe.main import create_cafe_app
from agentcafe.wizard.router import configure_wizard
from agentcafe.wizard.spec_parser import SpecParseError, parse_openapi_spec
from agentcafe.wizard.ai_enricher import _enrich_rule_based

# pylint: disable=redefined-outer-name,unused-argument

_TEST_SIGNING_SECRET = "test-wizard-secret-minimum-32-bytes!"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_OPENAPI_JSON = json.dumps({
    "openapi": "3.1.0",
    "info": {
        "title": "PetStore API",
        "version": "1.0.0",
        "description": "A sample pet store API for testing."
    },
    "servers": [{"url": "https://api.petstore.example.com/v1"}],
    "paths": {
        "/pets": {
            "get": {
                "operationId": "listPets",
                "summary": "List all pets",
                "description": "Returns all available pets.",
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "integer"},
                        "description": "Max results"
                    }
                ],
                "responses": {
                    "200": {
                        "description": "A list of pets",
                        "content": {
                            "application/json": {
                                "example": {
                                    "pets": [{"id": 1, "name": "Buddy", "species": "dog"}],
                                    "total": 1
                                }
                            }
                        }
                    }
                }
            },
            "post": {
                "operationId": "createPet",
                "summary": "Create a pet",
                "description": "Add a new pet to the store.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name", "species"],
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "Pet name",
                                        "example": "Buddy"
                                    },
                                    "species": {
                                        "type": "string",
                                        "description": "Animal species",
                                        "example": "dog"
                                    },
                                    "age": {
                                        "type": "integer",
                                        "description": "Pet age in years",
                                        "example": 3
                                    }
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "201": {
                        "description": "Pet created",
                        "content": {
                            "application/json": {
                                "example": {"id": 2, "name": "Buddy", "species": "dog", "age": 3}
                            }
                        }
                    }
                }
            }
        },
        "/pets/{pet_id}": {
            "get": {
                "operationId": "getPet",
                "summary": "Get pet details",
                "parameters": [
                    {
                        "name": "pet_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                        "description": "Pet ID",
                        "example": 1
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Pet details",
                        "content": {
                            "application/json": {
                                "example": {"id": 1, "name": "Buddy", "species": "dog", "age": 3}
                            }
                        }
                    }
                }
            },
            "delete": {
                "operationId": "deletePet",
                "summary": "Delete a pet",
                "parameters": [
                    {
                        "name": "pet_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                        "description": "Pet ID",
                        "example": 1
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Pet deleted",
                        "content": {
                            "application/json": {
                                "example": {"deleted": True}
                            }
                        }
                    }
                }
            }
        }
    }
})

SAMPLE_OPENAPI_YAML = """
openapi: "3.0.3"
info:
  title: SimpleService
  version: "0.1.0"
  description: A minimal service for testing YAML parsing.
servers:
  - url: https://api.simple.example.com
paths:
  /items:
    get:
      operationId: listItems
      summary: List items
      responses:
        "200":
          description: OK
"""


@pytest_asyncio.fixture
async def wizard_db():
    """Isolated in-memory DB for wizard tests."""
    configure_wizard(_TEST_SIGNING_SECRET)
    db = await init_db(":memory:")
    yield db
    await close_db()


@pytest_asyncio.fixture
async def wizard_client(wizard_db):
    """Async HTTP client with wizard routes available."""
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# ===========================================================================
# Spec Parser tests
# ===========================================================================

def test_parse_json_spec():
    """Parsing a valid JSON OpenAPI 3.1 spec extracts all operations."""
    result = parse_openapi_spec(SAMPLE_OPENAPI_JSON)
    assert result.title == "PetStore API"
    assert result.version == "1.0.0"
    assert result.base_url == "https://api.petstore.example.com/v1"
    assert len(result.operations) == 4


def test_parse_yaml_spec():
    """Parsing a valid YAML OpenAPI 3.0 spec works."""
    result = parse_openapi_spec(SAMPLE_OPENAPI_YAML)
    assert result.title == "SimpleService"
    assert len(result.operations) == 1
    assert result.operations[0].method == "GET"


def test_parse_operations_classified_correctly():
    """Write operations are correctly classified based on HTTP method."""
    result = parse_openapi_spec(SAMPLE_OPENAPI_JSON)
    ops = {op.operation_id: op for op in result.operations}

    # GET operations are reads
    assert not ops["listPets"].is_write
    assert not ops["getPet"].is_write
    # POST createPet is a write
    assert ops["createPet"].is_write
    # DELETE is a write
    assert ops["deletePet"].is_write


def test_parse_search_post_classified_as_read():
    """A POST with 'search' in operationId is classified as read."""
    spec = json.dumps({
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/search": {
                "post": {
                    "operationId": "searchAvailability",
                    "summary": "Search available rooms",
                    "responses": {"200": {"description": "OK"}}
                }
            }
        }
    })
    result = parse_openapi_spec(spec)
    assert len(result.operations) == 1
    assert not result.operations[0].is_write


def test_parse_empty_spec_raises():
    """Empty spec raises SpecParseError."""
    with pytest.raises(SpecParseError, match="Empty spec"):
        parse_openapi_spec("")


def test_parse_no_paths_raises():
    """Spec with no paths raises SpecParseError."""
    spec = json.dumps({
        "openapi": "3.1.0",
        "info": {"title": "Empty", "version": "1.0.0"},
        "paths": {}
    })
    with pytest.raises(SpecParseError, match="no paths"):
        parse_openapi_spec(spec)


def test_parse_swagger_2_raises():
    """Swagger 2.0 spec raises with helpful message."""
    spec = json.dumps({"swagger": "2.0", "info": {"title": "Old", "version": "1.0"}})
    with pytest.raises(SpecParseError, match="Swagger"):
        parse_openapi_spec(spec)


def test_parse_invalid_json_raises():
    """Invalid JSON raises with line number."""
    with pytest.raises(SpecParseError, match="syntax error"):
        parse_openapi_spec('{"openapi": "3.1.0",}')


def test_parse_extracts_agentcafe_extensions():
    """x-agentcafe-* extensions are extracted from operations."""
    spec = json.dumps({
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/test": {
                "get": {
                    "operationId": "testOp",
                    "summary": "Test",
                    "x-agentcafe-scope": "custom:scope",
                    "x-agentcafe-human-auth": True,
                    "x-agentcafe-rate-limit": "30/minute",
                    "responses": {"200": {"description": "OK"}}
                }
            }
        }
    })
    result = parse_openapi_spec(spec)
    op = result.operations[0]
    assert op.preset_scope == "custom:scope"
    assert op.preset_human_auth is True
    assert op.preset_rate_limit == "30/minute"


# ===========================================================================
# AI Enricher (rule-based fallback) tests
# ===========================================================================

def test_enrich_rule_based_generates_actions():
    """Rule-based enrichment generates actions for all operations."""
    parsed = parse_openapi_spec(SAMPLE_OPENAPI_JSON)
    candidate = _enrich_rule_based(parsed)

    assert candidate.service_id  # Not empty
    assert candidate.name == "PetStore API"
    assert len(candidate.actions) == 4


def test_enrich_rule_based_action_ids_are_slugs():
    """Action IDs are properly slugified from operationIds."""
    parsed = parse_openapi_spec(SAMPLE_OPENAPI_JSON)
    candidate = _enrich_rule_based(parsed)
    action_ids = {a.action_id for a in candidate.actions}

    assert "list-pets" in action_ids
    assert "create-pet" in action_ids
    assert "get-pet" in action_ids
    assert "delete-pet" in action_ids


def test_enrich_rule_based_write_actions_suggest_human_auth():
    """Write actions suggest human_auth = True."""
    parsed = parse_openapi_spec(SAMPLE_OPENAPI_JSON)
    candidate = _enrich_rule_based(parsed)
    actions = {a.action_id: a for a in candidate.actions}

    assert not actions["list-pets"].suggested_human_auth
    assert actions["create-pet"].suggested_human_auth
    assert actions["delete-pet"].suggested_human_auth


def test_enrich_rule_based_extracts_inputs():
    """Rule-based enrichment extracts required inputs from request bodies."""
    parsed = parse_openapi_spec(SAMPLE_OPENAPI_JSON)
    candidate = _enrich_rule_based(parsed)
    create_action = next(a for a in candidate.actions if a.action_id == "create-pet")

    input_names = {i.name for i in create_action.required_inputs}
    assert "name" in input_names
    assert "species" in input_names


def test_enrich_rule_based_uses_preset_extensions():
    """Preset x-agentcafe-* values override defaults."""
    spec = json.dumps({
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/test": {
                "post": {
                    "operationId": "doThing",
                    "summary": "Do thing",
                    "x-agentcafe-scope": "custom:do",
                    "x-agentcafe-human-auth": False,
                    "x-agentcafe-rate-limit": "5/hour",
                    "responses": {"200": {"description": "OK"}}
                }
            }
        }
    })
    parsed = parse_openapi_spec(spec)
    candidate = _enrich_rule_based(parsed)
    action = candidate.actions[0]

    assert action.suggested_scope == "custom:do"
    assert action.suggested_human_auth is False
    assert action.suggested_rate_limit == "5/hour"


# ===========================================================================
# Wizard API integration tests (full flow)
# ===========================================================================

@pytest.mark.asyncio
async def test_wizard_create_company(wizard_client):
    """POST /wizard/companies creates a company account with session token."""
    resp = await wizard_client.post("/wizard/companies", json={
        "name": "TestCorp",
        "email": "test@testcorp.example.com",
        "password": "secretpass123",
        "website": "https://testcorp.example.com"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "TestCorp"
    assert data["email"] == "test@testcorp.example.com"
    assert "company_id" in data
    assert "session_token" in data


@pytest.mark.asyncio
async def test_wizard_create_company_duplicate_email(wizard_client):
    """Duplicate email returns 409."""
    payload = {
        "name": "TestCorp",
        "email": "dupe@testcorp.example.com",
        "password": "secretpass123",
    }
    resp1 = await wizard_client.post("/wizard/companies", json=payload)
    assert resp1.status_code == 200

    resp2 = await wizard_client.post("/wizard/companies", json=payload)
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_wizard_login(wizard_client):
    """POST /wizard/companies/login authenticates correctly and returns session token."""
    # Create account
    await wizard_client.post("/wizard/companies", json={
        "name": "LoginCorp",
        "email": "login@test.example.com",
        "password": "mypassword",
    })

    # Login
    resp = await wizard_client.post("/wizard/companies/login", json={
        "email": "login@test.example.com",
        "password": "mypassword",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "LoginCorp"
    assert "session_token" in data


@pytest.mark.asyncio
async def test_wizard_login_bad_password(wizard_client):
    """Wrong password returns 401."""
    await wizard_client.post("/wizard/companies", json={
        "name": "LoginCorp2",
        "email": "login2@test.example.com",
        "password": "correctpass",
    })

    resp = await wizard_client.post("/wizard/companies/login", json={
        "email": "login2@test.example.com",
        "password": "wrongpass",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wizard_parse_spec(wizard_client):
    """POST /wizard/specs/parse parses a spec and creates a draft."""
    # Create company first
    company_resp = await wizard_client.post("/wizard/companies", json={
        "name": "SpecCorp",
        "email": "spec@test.example.com",
        "password": "pass1234",
    })
    token = company_resp.json()["session_token"]

    # Parse spec
    resp = await wizard_client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_OPENAPI_JSON,
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "draft_id" in data
    assert data["parsed_spec"]["title"] == "PetStore API"
    assert len(data["candidate_menu"]["actions"]) == 4


@pytest.mark.asyncio
async def test_wizard_parse_invalid_spec(wizard_client):
    """Invalid spec returns 422 with helpful error."""
    company_resp = await wizard_client.post("/wizard/companies", json={
        "name": "BadSpecCorp",
        "email": "badspec@test.example.com",
        "password": "pass1234",
    })
    token = company_resp.json()["session_token"]

    resp = await wizard_client.post("/wizard/specs/parse", json={
        "raw_spec": '{"swagger": "2.0", "info": {"title": "Old"}}',
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422
    assert "Swagger" in resp.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_wizard_full_flow(wizard_client):
    """Full wizard flow: create company → parse → review → policy → preview → publish."""
    # Step 1: Create company
    company_resp = await wizard_client.post("/wizard/companies", json={
        "name": "FullFlowCorp",
        "email": "flow@test.example.com",
        "password": "pass1234",
    })
    token = company_resp.json()["session_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # Step 2: Parse spec
    parse_resp = await wizard_client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_OPENAPI_JSON,
    }, headers=auth)
    assert parse_resp.status_code == 200
    draft_id = parse_resp.json()["draft_id"]
    candidate = parse_resp.json()["candidate_menu"]

    # Step 3: Review (accept mostly as-is, rename service_id)
    review_resp = await wizard_client.put(f"/wizard/drafts/{draft_id}/review", json={
        "service_id": "testpets-store",
        "name": "TestPets Store",
        "category": "pets",
        "capability_tags": ["pets", "animals", "store"],
        "description": "A test pet store for wizard testing.",
        "actions": candidate["actions"],
        "excluded_actions": ["delete-pet"],  # Exclude delete
    }, headers=auth)
    assert review_resp.status_code == 200
    assert review_resp.json()["wizard_step"] == 3

    # Step 4: Policy
    policy_actions = {}
    for action in candidate["actions"]:
        aid = action["action_id"]
        if aid == "delete-pet":
            continue
        policy_actions[aid] = {
            "scope": f"testpets-store:{aid}",
            "human_auth": action["is_write"],
            "rate_limit": "10/minute" if action["is_write"] else "60/minute",
        }

    policy_resp = await wizard_client.put(f"/wizard/drafts/{draft_id}/policy", json={
        "actions": policy_actions,
        "backend_url": "https://api.petstore.example.com/v1",
        "backend_auth_header": "Bearer test-key-123",
    }, headers=auth)
    assert policy_resp.status_code == 200

    # Step 5: Preview
    preview_resp = await wizard_client.get(f"/wizard/drafts/{draft_id}/preview", headers=auth)
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    final_menu = preview["final_menu_entry"]
    assert final_menu["service_id"] == "testpets-store"
    assert final_menu["name"] == "TestPets Store"
    # delete-pet should be excluded
    action_ids = [a["action_id"] for a in final_menu["actions"]]
    assert "delete-pet" not in action_ids
    assert len(action_ids) == 3

    # Step 6: Publish
    publish_resp = await wizard_client.post(f"/wizard/drafts/{draft_id}/publish", headers=auth)
    assert publish_resp.status_code == 200
    pub = publish_resp.json()
    assert pub["service_id"] == "testpets-store"
    assert pub["actions_published"] == 3

    # Verify it appears on the Menu
    menu_resp = await wizard_client.get("/cafe/menu")
    assert menu_resp.status_code == 200
    menu_services = menu_resp.json()["services"]
    service_ids = [s["service_id"] for s in menu_services]
    assert "testpets-store" in service_ids


@pytest.mark.asyncio
async def test_wizard_publish_duplicate_service_id_rejected(wizard_client):
    """Publishing a duplicate service_id returns an error."""
    # Create company and two drafts with the same service_id
    company_resp = await wizard_client.post("/wizard/companies", json={
        "name": "DupeCorp",
        "email": "dupe-svc@test.example.com",
        "password": "pass1234",
    })
    token = company_resp.json()["session_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # First draft — full flow to publish
    parse1 = await wizard_client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_OPENAPI_JSON,
    }, headers=auth)
    draft_id_1 = parse1.json()["draft_id"]
    candidate = parse1.json()["candidate_menu"]

    await wizard_client.put(f"/wizard/drafts/{draft_id_1}/review", json={
        "service_id": "unique-pets",
        "name": "Unique Pets",
        "actions": candidate["actions"],
        "excluded_actions": [],
    }, headers=auth)
    policy = {a["action_id"]: {"scope": f"unique-pets:{a['action_id']}", "human_auth": False, "rate_limit": "60/minute"} for a in candidate["actions"]}
    await wizard_client.put(f"/wizard/drafts/{draft_id_1}/policy", json={
        "actions": policy,
        "backend_url": "https://api.example.com",
    }, headers=auth)
    await wizard_client.get(f"/wizard/drafts/{draft_id_1}/preview", headers=auth)
    pub1 = await wizard_client.post(f"/wizard/drafts/{draft_id_1}/publish", headers=auth)
    assert pub1.status_code == 200

    # Second draft — same service_id should fail
    parse2 = await wizard_client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_OPENAPI_JSON,
    }, headers=auth)
    draft_id_2 = parse2.json()["draft_id"]

    await wizard_client.put(f"/wizard/drafts/{draft_id_2}/review", json={
        "service_id": "unique-pets",
        "name": "Unique Pets Again",
        "actions": candidate["actions"],
        "excluded_actions": [],
    }, headers=auth)
    await wizard_client.put(f"/wizard/drafts/{draft_id_2}/policy", json={
        "actions": policy,
        "backend_url": "https://api.example.com",
    }, headers=auth)
    await wizard_client.get(f"/wizard/drafts/{draft_id_2}/preview", headers=auth)
    pub2 = await wizard_client.post(f"/wizard/drafts/{draft_id_2}/publish", headers=auth)
    assert pub2.status_code == 400


# ===========================================================================
# Fix #10: Real Hotel OpenAPI YAML test
# ===========================================================================

def test_parse_hotel_openapi_yaml():
    """Parse the real hotel-booking OpenAPI YAML and verify extraction."""
    hotel_spec_path = Path(__file__).parent.parent / "docs" / "design" / "services" / "hotel-booking" / "openapi.yaml"
    raw = hotel_spec_path.read_text()
    result = parse_openapi_spec(raw)

    assert result.title == "HotelBookingService Internal API"
    assert len(result.operations) == 4

    ops = {op.operation_id: op for op in result.operations}

    # searchAvailability is a POST but classified as read (keyword override)
    assert "searchAvailability" in ops
    assert not ops["searchAvailability"].is_write

    # bookRoom is a write
    assert "bookRoom" in ops
    assert ops["bookRoom"].is_write

    # getRoomDetails is a read
    assert not ops["getRoomDetails"].is_write

    # cancelBooking is a write
    assert ops["cancelBooking"].is_write


def test_hotel_spec_ref_resolution():
    """$ref schemas in the hotel spec resolve to real data."""
    hotel_spec_path = Path(__file__).parent.parent / "docs" / "design" / "services" / "hotel-booking" / "openapi.yaml"
    raw = hotel_spec_path.read_text()
    result = parse_openapi_spec(raw)

    # The searchAvailability response schema references RoomResult via $ref
    # After resolution, the raw_responses should contain resolved schema properties
    search_op = next(op for op in result.operations if op.operation_id == "searchAvailability")
    resp_200 = search_op.raw_responses.get("200", {})
    schema = resp_200.get("content", {}).get("application/json", {}).get("schema", {})
    items_schema = schema.get("properties", {}).get("results", {}).get("items", {})
    # Should be resolved (not a $ref), should have properties like room_id, hotel_name
    assert "properties" in items_schema, f"$ref not resolved: {items_schema}"
    assert "room_id" in items_schema["properties"]
    assert "hotel_name" in items_schema["properties"]


def test_hotel_spec_required_inputs():
    """Required inputs extracted correctly from the hotel spec."""
    hotel_spec_path = Path(__file__).parent.parent / "docs" / "design" / "services" / "hotel-booking" / "openapi.yaml"
    raw = hotel_spec_path.read_text()
    result = parse_openapi_spec(raw)
    candidate = _enrich_rule_based(result)

    search_action = next(a for a in candidate.actions if a.action_id == "search-availability")
    input_names = {i.name for i in search_action.required_inputs}
    # city, check_in, check_out, guests are required; max_price_per_night and amenities are optional
    assert "city" in input_names
    assert "check_in" in input_names
    assert "check_out" in input_names
    assert "guests" in input_names
    assert "max_price_per_night" not in input_names
    assert "amenities" not in input_names


# ===========================================================================
# Fix #11: Additional test coverage
# ===========================================================================

@pytest.mark.asyncio
async def test_wizard_draft_ownership_403(wizard_client):
    """Company B cannot operate on Company A's draft."""
    # Company A
    resp_a = await wizard_client.post("/wizard/companies", json={
        "name": "CompanyA",
        "email": "a@ownership.example.com",
        "password": "pass1234",
    })
    token_a = resp_a.json()["session_token"]
    auth_a = {"Authorization": f"Bearer {token_a}"}

    # Company B
    resp_b = await wizard_client.post("/wizard/companies", json={
        "name": "CompanyB",
        "email": "b@ownership.example.com",
        "password": "pass1234",
    })
    token_b = resp_b.json()["session_token"]
    auth_b = {"Authorization": f"Bearer {token_b}"}

    # A creates a draft
    parse_resp = await wizard_client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_OPENAPI_JSON,
    }, headers=auth_a)
    draft_id = parse_resp.json()["draft_id"]

    # B tries to review A's draft → 403
    review_resp = await wizard_client.put(f"/wizard/drafts/{draft_id}/review", json={
        "service_id": "stolen",
        "name": "Stolen",
        "actions": [],
        "excluded_actions": [],
    }, headers=auth_b)
    assert review_resp.status_code == 403

    # B tries to publish A's draft → 403
    publish_resp = await wizard_client.post(f"/wizard/drafts/{draft_id}/publish", headers=auth_b)
    assert publish_resp.status_code == 403


@pytest.mark.asyncio
async def test_wizard_publish_without_preview_400(wizard_client):
    """Publishing a draft that hasn't been previewed returns 400."""
    company_resp = await wizard_client.post("/wizard/companies", json={
        "name": "NoPreviewCorp",
        "email": "nopreview@test.example.com",
        "password": "pass1234",
    })
    token = company_resp.json()["session_token"]
    auth = {"Authorization": f"Bearer {token}"}

    parse_resp = await wizard_client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_OPENAPI_JSON,
    }, headers=auth)
    draft_id = parse_resp.json()["draft_id"]

    # Skip review, policy, preview — go straight to publish
    resp = await wizard_client.post(f"/wizard/drafts/{draft_id}/publish", headers=auth)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_wizard_specs_parse_no_auth_401(wizard_client):
    """Calling specs/parse without an Authorization header returns 401."""
    resp = await wizard_client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_OPENAPI_JSON,
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wizard_dry_run_unreachable_backend(wizard_client):
    """Dry-run with unreachable backend returns error results."""
    company_resp = await wizard_client.post("/wizard/companies", json={
        "name": "DryRunCorp",
        "email": "dryrun@test.example.com",
        "password": "pass1234",
    })
    token = company_resp.json()["session_token"]
    auth = {"Authorization": f"Bearer {token}"}

    parse_resp = await wizard_client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_OPENAPI_JSON,
    }, headers=auth)
    draft_id = parse_resp.json()["draft_id"]
    candidate = parse_resp.json()["candidate_menu"]

    await wizard_client.put(f"/wizard/drafts/{draft_id}/review", json={
        "service_id": "dryrun-pets",
        "name": "DryRun Pets",
        "actions": candidate["actions"],
        "excluded_actions": [],
    }, headers=auth)
    policy = {a["action_id"]: {"scope": f"dryrun-pets:{a['action_id']}", "human_auth": False, "rate_limit": "60/minute"} for a in candidate["actions"]}
    await wizard_client.put(f"/wizard/drafts/{draft_id}/policy", json={
        "actions": policy,
        "backend_url": "http://127.0.0.1:19999",
    }, headers=auth)
    await wizard_client.get(f"/wizard/drafts/{draft_id}/preview", headers=auth)

    # Dry run — backend unreachable
    resp = await wizard_client.post(f"/wizard/drafts/{draft_id}/dry-run", headers=auth)
    assert resp.status_code == 200
    data = resp.json()
    assert not data["all_ok"]
    assert len(data["results"]) > 0
    assert all(r["status"] == "error" for r in data["results"])


# ===========================================================================
# Post-publish management tests
# ===========================================================================

async def _publish_service(wizard_client, service_id="mgmt-pets", email="mgmt@test.example.com"):
    """Helper: create company, parse, review, policy, preview, publish. Returns (auth_headers, service_id)."""
    company_resp = await wizard_client.post("/wizard/companies", json={
        "name": "MgmtCorp",
        "email": email,
        "password": "pass1234",
    })
    token = company_resp.json()["session_token"]
    auth = {"Authorization": f"Bearer {token}"}

    parse_resp = await wizard_client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_OPENAPI_JSON,
    }, headers=auth)
    draft_id = parse_resp.json()["draft_id"]
    candidate = parse_resp.json()["candidate_menu"]

    await wizard_client.put(f"/wizard/drafts/{draft_id}/review", json={
        "service_id": service_id,
        "name": "MgmtPets Store",
        "actions": candidate["actions"],
        "excluded_actions": [],
    }, headers=auth)
    policy = {
        a["action_id"]: {"scope": f"{service_id}:{a['action_id']}", "human_auth": False, "rate_limit": "60/minute"}
        for a in candidate["actions"]
    }
    await wizard_client.put(f"/wizard/drafts/{draft_id}/policy", json={
        "actions": policy,
        "backend_url": "https://api.example.com",
    }, headers=auth)
    await wizard_client.get(f"/wizard/drafts/{draft_id}/preview", headers=auth)
    pub = await wizard_client.post(f"/wizard/drafts/{draft_id}/publish", headers=auth)
    assert pub.status_code == 200
    return auth, service_id


@pytest.mark.asyncio
async def test_service_dashboard(wizard_client):
    """GET /wizard/services/{id}/dashboard returns service info."""
    auth, svc_id = await _publish_service(wizard_client, "dash-pets", "dash@test.example.com")

    resp = await wizard_client.get(f"/wizard/services/{svc_id}/dashboard", headers=auth)
    assert resp.status_code == 200
    data = resp.json()
    assert data["service_id"] == svc_id
    assert data["name"] == "MgmtPets Store"
    assert data["status"] == "live"
    assert data["actions_count"] == 4
    assert data["total_requests"] == 0
    assert data["recent_requests"] == 0


@pytest.mark.asyncio
async def test_pause_service(wizard_client):
    """PUT /wizard/services/{id}/pause pauses a live service."""
    auth, svc_id = await _publish_service(wizard_client, "pause-pets", "pause@test.example.com")

    resp = await wizard_client.put(f"/wizard/services/{svc_id}/pause", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"

    # Verify service no longer on menu
    menu_resp = await wizard_client.get("/cafe/menu")
    service_ids = [s["service_id"] for s in menu_resp.json()["services"]]
    assert svc_id not in service_ids


@pytest.mark.asyncio
async def test_pause_already_paused_409(wizard_client):
    """Pausing an already-paused service returns 409."""
    auth, svc_id = await _publish_service(wizard_client, "pause2-pets", "pause2@test.example.com")

    await wizard_client.put(f"/wizard/services/{svc_id}/pause", headers=auth)
    resp = await wizard_client.put(f"/wizard/services/{svc_id}/pause", headers=auth)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_resume_paused_service(wizard_client):
    """PUT /wizard/services/{id}/resume restores a paused service to live."""
    auth, svc_id = await _publish_service(wizard_client, "resume-pets", "resume@test.example.com")

    await wizard_client.put(f"/wizard/services/{svc_id}/pause", headers=auth)
    resp = await wizard_client.put(f"/wizard/services/{svc_id}/resume", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["status"] == "live"

    # Verify service back on menu
    menu_resp = await wizard_client.get("/cafe/menu")
    service_ids = [s["service_id"] for s in menu_resp.json()["services"]]
    assert svc_id in service_ids


@pytest.mark.asyncio
async def test_unpublish_service(wizard_client):
    """PUT /wizard/services/{id}/unpublish removes a service permanently."""
    auth, svc_id = await _publish_service(wizard_client, "unpub-pets", "unpub@test.example.com")

    resp = await wizard_client.put(f"/wizard/services/{svc_id}/unpublish", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["status"] == "unpublished"

    # Cannot resume an unpublished service
    resume_resp = await wizard_client.put(f"/wizard/services/{svc_id}/resume", headers=auth)
    assert resume_resp.status_code == 409


@pytest.mark.asyncio
async def test_service_logs_empty(wizard_client):
    """GET /wizard/services/{id}/logs returns empty logs for a new service."""
    auth, svc_id = await _publish_service(wizard_client, "logs-pets", "logs@test.example.com")

    resp = await wizard_client.get(f"/wizard/services/{svc_id}/logs", headers=auth)
    assert resp.status_code == 200
    data = resp.json()
    assert data["service_id"] == svc_id
    assert data["total_entries"] == 0
    assert data["entries"] == []


@pytest.mark.asyncio
async def test_service_management_ownership_403(wizard_client):
    """Company B cannot manage Company A's published service."""
    _auth_a, svc_id = await _publish_service(wizard_client, "own-pets", "own-a@test.example.com")

    # Company B
    resp_b = await wizard_client.post("/wizard/companies", json={
        "name": "CompanyB",
        "email": "own-b@test.example.com",
        "password": "pass1234",
    })
    auth_b = {"Authorization": f"Bearer {resp_b.json()['session_token']}"}

    # B tries to pause A's service
    assert (await wizard_client.put(f"/wizard/services/{svc_id}/pause", headers=auth_b)).status_code == 403
    # B tries to get A's dashboard
    assert (await wizard_client.get(f"/wizard/services/{svc_id}/dashboard", headers=auth_b)).status_code == 403
    # B tries to get A's logs
    assert (await wizard_client.get(f"/wizard/services/{svc_id}/logs", headers=auth_b)).status_code == 403


@pytest.mark.asyncio
async def test_service_not_found_404(wizard_client):
    """Accessing a non-existent service returns 404."""
    auth, _ = await _publish_service(wizard_client, "exist-pets", "exist@test.example.com")

    resp = await wizard_client.get("/wizard/services/no-such-service/dashboard", headers=auth)
    assert resp.status_code == 404
