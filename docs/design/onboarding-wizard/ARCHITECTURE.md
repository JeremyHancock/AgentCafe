# Company Onboarding Wizard — Technical Architecture

How the wizard ingests an OpenAPI spec, guides the company through minimal questions, generates a live Menu preview, and publishes with one click.

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        ONBOARDING WIZARD                                │
│                                                                          │
│  ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌────────────────┐  │
│  │   Spec     │──▶│   AI       │──▶│   Review   │──▶│   Publisher    │  │
│  │   Parser   │   │   Enricher │   │   Engine   │   │   (one-click)  │  │
│  └────────────┘   └────────────┘   └────────────┘   └────────────────┘  │
│        │                │                │                  │            │
│        ▼                ▼                ▼                  ▼            │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                     SQLite Database                                │  │
│  │  companies | draft_services | published_services | proxy_configs   │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                    │                    │
│                                                    ▼                    │
│                                           ┌────────────────┐            │
│                                           │  Cafe Menu     │            │
│                                           │  Endpoint      │            │
│                                           │  GET /cafe/menu│            │
│                                           └────────────────┘            │
│                                                    │                    │
│                                                    ▼                    │
│                                           ┌────────────────┐            │
│                                           │  Proxy Layer   │            │
│                                           │  POST /cafe/   │            │
│                                           │  order         │            │
│                                           └────────────────┘            │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Component 1: Spec Parser

**Responsibility:** Validate and extract structured data from an uploaded OpenAPI spec.

**Tech:** Pure Python — no LLM needed for this step. Uses `pyyaml` + `jsonschema` for parsing and validation.

### Input
- Raw OpenAPI spec (YAML or JSON string, file upload, or URL fetch)

### Processing
1. **Format detection**: YAML vs JSON (auto-detect)
2. **Version validation**: Must be OpenAPI 3.0.x or 3.1.x
3. **Schema validation**: Validate against the official OpenAPI JSON Schema
4. **Operation extraction**: Walk all `paths` → extract each `{path, method, operationId, summary, description, requestBody, parameters, responses}`
5. **Read/Write classification**: Classify each operation:
   - `GET` → read
   - `POST/PUT/PATCH/DELETE` → write (unless `operationId` or `summary` contains "search", "list", "browse", "get", "fetch" → then read)
6. **Input extraction**: For each operation, extract required parameters and request body properties → build candidate `required_inputs` array
7. **Response extraction**: For each operation, extract the `200`/`201` response example or schema → build candidate `example_response`

### Output
```python
@dataclass
class ParsedOperation:
    path: str                      # e.g., "/availability/search"
    method: str                    # e.g., "POST"
    operation_id: str              # e.g., "searchAvailability"
    summary: str                   # from spec
    description: str               # from spec
    is_write: bool                 # auto-classified
    raw_parameters: list           # from spec
    raw_request_body: dict | None  # from spec
    raw_responses: dict            # from spec
    # x-agentcafe-* extensions if present (company pre-configured)
    preset_scope: str | None
    preset_human_auth: bool | None
    preset_rate_limit: str | None

@dataclass
class ParsedSpec:
    title: str
    version: str
    description: str
    base_url: str                  # from servers[0]
    operations: list[ParsedOperation]
    raw_spec: dict                 # full parsed spec for reference
    warnings: list[str]            # non-blocking issues
```

### Error Handling
- **Syntax errors**: Report line number + context + suggested fix
- **Missing required fields**: List what's missing with examples
- **Unsupported version**: "We support OpenAPI 3.0.x and 3.1.x. Your spec appears to be Swagger 2.0. Here's how to convert →"
- **No operations found**: "Your spec has no paths/operations. At least one is required."

---

## Component 2: AI Enricher (LiteLLM)

**Responsibility:** Transform raw parsed operations into polished, agent-friendly Menu entries. This is where the "magic" happens — the wizard feels smart because LiteLLM generates natural, clear descriptions.

**Tech:** LiteLLM (as specified in locked tech stack). Single prompt per service, batching all operations.

### Prompt Design

```python
ENRICHMENT_PROMPT = """
You are helping a company list their API on AgentCafe — a marketplace where AI agents
discover and use services.

Given the following parsed API spec, generate a Menu entry in the AgentCafe format.

Rules:
- service_id: lowercase slug in {brand}-{category} format (e.g., "stayright-hotels")
- name: clean display name (e.g., "StayRight Hotels")
- description: 1-2 sentences describing what the service does, written for an AI agent
- For each operation, generate:
  - action_id: lowercase slug (e.g., "search-availability")
  - description: clear sentence describing what this action accomplishes
  - required_inputs: array of {name, description, example} for each required parameter
  - example_response: realistic JSON example based on the response schema
- Keep descriptions concise, factual, and agent-friendly
- Never include HTTP methods, paths, or technical implementation details

API Title: {title}
API Description: {description}
Operations:
{operations_json}

Return a JSON object matching the AgentCafe Menu format.
"""
```

### Processing
1. **Build prompt**: Insert parsed spec data into the enrichment prompt
2. **Call LiteLLM**: Single call, structured output (JSON mode)
3. **Validate output**: Ensure returned JSON matches the locked Menu schema
4. **Merge with preset values**: If the company included `x-agentcafe-*` extensions in their spec, prefer those over AI-generated values
5. **Flag low-confidence fields**: If the AI couldn't generate a good example_response (e.g., no schema in spec), flag it as amber (needs human review)

### Output
```python
@dataclass
class CandidateMenuEntry:
    service_id: str
    name: str
    description: str
    actions: list[CandidateAction]
    confidence: dict[str, float]  # per-field confidence scores

@dataclass
class CandidateAction:
    action_id: str
    description: str
    required_inputs: list[dict]   # [{name, description, example}]
    example_response: dict
    suggested_scope: str
    suggested_human_auth: bool
    suggested_rate_limit: str
    is_write: bool
    confidence: dict[str, float]  # per-field confidence scores
    source_operation: ParsedOperation  # link back to spec
```

### Fallback
If LiteLLM is unavailable or returns invalid output:
- Fall back to rule-based generation (operationId → slug, summary → description)
- Flag all fields as amber (needs review)
- The wizard still works — just requires more manual editing

---

## Component 3: Review Engine

**Responsibility:** Present the candidate Menu entry to the company, highlight fields needing attention, collect edits, and produce the final validated Menu entry.

**Tech:** FastAPI endpoints serving a step-by-step UI. State stored in SQLite as a `draft_service`.

### Data Model

```python
# SQLite schema for wizard state

CREATE TABLE companies (
    id TEXT PRIMARY KEY,           -- UUID
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    website TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE draft_services (
    id TEXT PRIMARY KEY,           -- UUID
    company_id TEXT NOT NULL REFERENCES companies(id),
    wizard_step INTEGER NOT NULL DEFAULT 2,  -- current step (2-6)
    
    -- Step 2: Parsed spec
    raw_spec_text TEXT,
    parsed_spec_json TEXT,         -- ParsedSpec as JSON
    
    -- Step 3: Candidate + edits
    candidate_menu_json TEXT,      -- CandidateMenuEntry as JSON
    company_edits_json TEXT,       -- company's overrides as JSON
    excluded_actions TEXT,         -- JSON array of excluded action_ids
    
    -- Step 4: Policy
    policy_json TEXT,              -- {action_id: {scope, human_auth, rate_limit}}
    backend_url TEXT,
    backend_auth_header TEXT,      -- encrypted
    backend_reachable BOOLEAN,
    
    -- Step 5: Final preview
    final_menu_json TEXT,          -- the exact Menu entry JSON
    dry_run_results_json TEXT,
    
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE published_services (
    id TEXT PRIMARY KEY,           -- UUID
    company_id TEXT NOT NULL REFERENCES companies(id),
    service_id TEXT NOT NULL UNIQUE,  -- the slug
    menu_entry_json TEXT NOT NULL,    -- the locked Menu format JSON
    published_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'live',  -- live | paused | unpublished
    updated_at TEXT NOT NULL
);

CREATE TABLE proxy_configs (
    id TEXT PRIMARY KEY,           -- UUID
    service_id TEXT NOT NULL REFERENCES published_services(service_id),
    action_id TEXT NOT NULL,
    backend_url TEXT NOT NULL,
    backend_path TEXT NOT NULL,    -- e.g., "/availability/search"
    backend_method TEXT NOT NULL,  -- e.g., "POST"
    backend_auth_header TEXT NOT NULL,  -- encrypted
    scope TEXT NOT NULL,
    human_auth_required BOOLEAN NOT NULL,
    rate_limit TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(service_id, action_id)
);
```

### API Endpoints (Wizard Backend)

```python
# All wizard endpoints are company-facing, not agent-facing

POST   /wizard/companies              # Create account (Step 1)
POST   /wizard/companies/login        # Sign in

POST   /wizard/specs/parse            # Upload + parse spec (Step 2)
       # Body: {raw_spec: string} or multipart file upload
       # Returns: ParsedSpec + CandidateMenuEntry

PUT    /wizard/drafts/{draft_id}/review    # Save Step 3 edits
       # Body: {service_id, name, description, actions: [...], excluded_actions: [...]}

PUT    /wizard/drafts/{draft_id}/policy    # Save Step 4 policy
       # Body: {actions: {action_id: {scope, human_auth, rate_limit}}, backend_url, backend_auth}

GET    /wizard/drafts/{draft_id}/preview   # Generate Step 5 preview
       # Returns: final Menu entry JSON + dry run availability

POST   /wizard/drafts/{draft_id}/dry-run   # Test proxy mapping (Step 5)
       # Returns: per-action connectivity results

POST   /wizard/drafts/{draft_id}/publish   # Publish to Menu (Step 6)
       # Creates published_service + proxy_configs
       # Returns: confirmation + dashboard URL

# Post-publish management
GET    /wizard/services/{service_id}/dashboard   # Company dashboard
PUT    /wizard/services/{service_id}/pause       # Pause service
PUT    /wizard/services/{service_id}/unpublish   # Remove from Menu
PUT    /wizard/services/{service_id}/edit        # Re-enter wizard
GET    /wizard/services/{service_id}/logs        # Request logs (anonymized)
```

---

## Component 4: Publisher

**Responsibility:** Take the finalized Menu entry and make it live — write to database, activate proxy routing, and confirm.

### Publish Sequence

```
Company clicks "Publish to Menu"
        │
        ▼
1. Validate final_menu_json against locked Menu schema
        │
        ▼
2. Check service_id uniqueness (no collision with existing services)
        │
        ▼
3. INSERT into published_services (status = 'live')
        │
        ▼
4. For each action:
   INSERT into proxy_configs (service_id, action_id, backend mapping)
        │
        ▼
5. The GET /cafe/menu endpoint now includes this service
   (it reads from published_services WHERE status = 'live')
        │
        ▼
6. The POST /cafe/order endpoint can now route requests for this service
   (it reads from proxy_configs to find the backend mapping)
        │
        ▼
7. Send confirmation email to company
        │
        ▼
8. Return success response with dashboard URL
```

### Publish is Atomic
- Steps 3-4 run in a single SQLite transaction
- If any step fails, nothing is published
- The company sees a clear error and can retry

---

## How It All Connects to the Locked Architecture

### Menu Discovery (Agent-Facing)

```
Agent ──GET /cafe/menu──▶ AgentCafe
                              │
                              ▼
                    SELECT menu_entry_json
                    FROM published_services
                    WHERE status = 'live'
                              │
                              ▼
                    Return combined Menu JSON
                    (the full-menu.json format)
```

### Order Execution (Agent-Facing)

```
Agent ──POST /cafe/order──▶ AgentCafe
        {                       │
          service_id,           ▼
          action_id,    1. Validate Passport
          passport,        (Human authorization check)
          inputs               │
        }                      ▼
                        2. Look up proxy_config
                           for (service_id, action_id)
                               │
                               ▼
                        3. Check Company Policy
                           (scope match, rate limit, human_auth)
                               │
                               ▼
                        4. Forward to backend
                           using proxy_config.backend_url
                           + proxy_config.backend_path
                           + proxy_config.backend_auth_header
                               │
                               ▼
                        5. Return backend response to agent
                           (with audit log entry)
```

### Double Validation Enforcement

Every order goes through two gates:

1. **Human Passport Validation**
   - Is the Passport token valid and not expired?
   - Does the Passport include the required scope for this action?
   - If `human_authorization_required` is true, has the human explicitly authorized this specific action?

2. **Company Policy Validation**
   - Is the service live (not paused/unpublished)?
   - Does the action exist and is it enabled?
   - Is the request within the rate limit?
   - Do the provided inputs match the required_inputs schema?

Both gates must pass. If either fails, the request is rejected with a clear error code, and the agent is told why (e.g., "scope_missing", "human_auth_required", "rate_limit_exceeded").

---

## Security Considerations

### What is stored securely
- **Backend URLs**: Stored in `proxy_configs`, never exposed via any agent-facing endpoint
- **Backend auth headers**: Encrypted at rest in SQLite (AES-256, key from environment variable)
- **Company passwords**: Hashed with bcrypt

### What agents never see
- Backend URLs
- Backend auth credentials
- Company email or account details
- Raw OpenAPI specs
- Internal operation IDs, paths, or HTTP methods

### What agents do see
- The locked Menu format only: service_id, name, description, actions with action_id, description, required_inputs, cost, example_response
- Error messages when validation fails (no internal details leaked)

### Audit trail
Every `POST /cafe/order` creates an audit log entry:
```python
CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    service_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    passport_hash TEXT NOT NULL,    -- hash of the Passport (not the Passport itself)
    inputs_hash TEXT NOT NULL,      -- hash of the inputs (not the inputs themselves)
    outcome TEXT NOT NULL,          -- success | passport_invalid | policy_denied | backend_error
    response_code INTEGER,
    latency_ms INTEGER
);
```

---

## LiteLLM Integration Details

### When LiteLLM is used
- **Step 2→3 transition only**: Enriching parsed operations into agent-friendly Menu entries
- Not used at order time (zero LLM latency in the hot path)
- Not used for validation or policy enforcement

### Configuration
```python
# wizard/ai_enricher.py

import litellm

ENRICHMENT_MODEL = "gpt-4o-mini"  # fast, cheap, good enough for structured extraction
ENRICHMENT_TEMPERATURE = 0.2       # low creativity, high consistency
ENRICHMENT_MAX_TOKENS = 4000       # enough for a full Menu entry

async def enrich_spec(parsed_spec: ParsedSpec) -> CandidateMenuEntry:
    prompt = build_enrichment_prompt(parsed_spec)
    
    response = await litellm.acompletion(
        model=ENRICHMENT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=ENRICHMENT_TEMPERATURE,
        max_tokens=ENRICHMENT_MAX_TOKENS,
        response_format={"type": "json_object"},
    )
    
    raw_json = json.loads(response.choices[0].message.content)
    return validate_and_build_candidate(raw_json, parsed_spec)
```

### Cost
- One LLM call per onboarding (not per request)
- Estimated cost: ~$0.01 per company onboarded
- This is a company-facing cost, not an agent-facing cost

---

## File Structure (Phase 3 Implementation Target)

```
agentcafe/
├── wizard/
│   ├── __init__.py
│   ├── router.py              # FastAPI router for all /wizard/* endpoints
│   ├── spec_parser.py         # Component 1: OpenAPI parsing + validation
│   ├── ai_enricher.py         # Component 2: LiteLLM enrichment
│   ├── review_engine.py       # Component 3: Draft management + editing
│   ├── publisher.py           # Component 4: Publish + proxy config
│   ├── models.py              # Pydantic models for all wizard data
│   └── templates/             # Email templates (confirmation, etc.)
├── cafe/
│   ├── __init__.py
│   ├── router.py              # FastAPI router for /cafe/menu and /cafe/order
│   ├── menu.py                # Menu assembly from published_services
│   ├── proxy.py               # Secure proxy forwarding
│   ├── passport.py            # Passport validation
│   └── policy.py              # Company policy enforcement
├── db/
│   ├── __init__.py
│   ├── models.py              # SQLite table definitions
│   ├── migrations/            # Schema migrations
│   └── encryption.py          # AES-256 for backend credentials
├── main.py                    # FastAPI app entry point
├── config.py                  # Environment-based configuration
└── tests/
    ├── test_spec_parser.py
    ├── test_ai_enricher.py
    ├── test_review_engine.py
    ├── test_publisher.py
    ├── test_menu.py
    ├── test_proxy.py
    └── test_passport.py
```
