# Wizard Fix Progress Tracker

Tracks the 12-fix improvement plan for the Onboarding Wizard.
**STATUS: ALL 12 FIXES COMPLETE — 77 tests passing (up from 70).**

## Implementation Order

- **Batch 1** (spec_parser.py): Fix #1, Fix #2
- **Batch 2** (pyproject.toml + router.py + models.py): Fix #3, Fix #4, Fix #8
- **Batch 3** (router.py + publisher.py + review_engine.py + ai_enricher.py): Fix #5, Fix #6, Fix #7, Fix #9
- **Batch 4** (test_wizard.py): Fix #10, Fix #11
- **Batch 5**: Fix #12

## Status

### Batch 1 — COMPLETE
- [x] **Fix #1**: `$ref` Resolution in Spec Parser
  - `_resolve_refs()` in spec_parser.py (recursive, depth-limited to 15)
  - Called after `_detect_and_parse()` before any operation extraction
- [x] **Fix #2**: Filter Body Properties by `required` Array
  - `_extract_required_inputs()` tracks `required_props` set from schema
  - Final filter: path params always included, body props only if in `required`

### Batch 2 — COMPLETE
- [x] **Fix #3**: Company Auth + Draft Ownership (JWT session tokens)
  - `configure_wizard(signing_secret)` — reuses PASSPORT_SIGNING_SECRET
  - `_create_session_token(company_id)` — 8-hour JWT with `iss=agentcafe-wizard`
  - `_get_company_id_from_token(authorization)` — decodes Bearer token
  - All draft endpoints (review, policy, preview, dry-run, publish) require auth + ownership check
  - `specs/parse` gets company_id from token, not request body
  - `main.py` calls `configure_wizard()` at startup
- [x] **Fix #4**: bcrypt for Passwords
  - `bcrypt>=4.0.0` in pyproject.toml base deps
  - `create_company` uses `bcrypt.hashpw()` + `bcrypt.gensalt()`
  - `login_company` uses `bcrypt.checkpw()`
- [x] **Fix #8**: Input Validation on Company Create
  - Pydantic `field_validator` for name (1-200 chars), email (regex), password (min 8)

### Batch 3 — COMPLETE
- [x] **Fix #5**: Dry-Run Improvements
  - `httpx` imported at top of router.py
  - Single `httpx.AsyncClient` created outside loop
  - HEADs `backend_url + resolved_path` with template params replaced by examples
- [x] **Fix #6**: Remove Dead Code in Publisher
  - Removed `excluded` variable and `if action_id in excluded: continue`
- [x] **Fix #7**: Preserve `company_edits_json`
  - `save_review` writes to `company_edits_json` (not `candidate_menu_json`)
  - `generate_preview` reads `company_edits_json` if present, else `candidate_menu_json`
- [x] **Fix #9**: LLM Prompt Parameter Truncation
  - Removed `[:5]` slice — all params now included
  - Warning added if >10 params per operation

### Batch 4 — COMPLETE
- [x] **Fix #10**: 3 tests against real hotel-booking/openapi.yaml
  - Operations: 4 found, correct read/write classification
  - `$ref` resolution: RoomResult schema resolved with real properties
  - Required inputs: only `required` body props (city, check_in, check_out, guests)
- [x] **Fix #11**: 4 additional coverage tests
  - Draft ownership: company B → 403 on company A's draft
  - Publish without preview → 400
  - specs/parse without auth → 401
  - Dry-run with unreachable backend → error results

### Batch 5 — COMPLETE
- [x] **Fix #12**: Full suite — **77 tests passing** (48 pre-existing + 29 wizard)

## Files Modified
- `agentcafe/wizard/router.py` — auth, bcrypt, dry-run rewrite
- `agentcafe/wizard/models.py` — session_token fields, validators, SpecParseRequest simplified
- `agentcafe/wizard/spec_parser.py` — `_resolve_refs()`, required filtering
- `agentcafe/wizard/review_engine.py` — company_edits_json preservation
- `agentcafe/wizard/publisher.py` — dead code removal
- `agentcafe/wizard/ai_enricher.py` — param truncation fix + warning
- `agentcafe/main.py` — `configure_wizard()` call at startup
- `pyproject.toml` — bcrypt + PyJWT base deps
- `tests/test_wizard.py` — all tests updated for auth, 7 new tests added
