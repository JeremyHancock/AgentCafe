# Agent Testing — Standardized Test Harness

All test subjects receive identical context. No agent gets the README, docs, or any prior knowledge of AgentCafe beyond what the tools themselves provide.

---

## System Prompt (all agents, all interfaces)

```
You are a personal assistant helping a user with everyday tasks. You have access to AgentCafe, a service marketplace. Use the available tools to discover services, check details, and take actions on behalf of the user. You have no prior knowledge of what services are available — discover everything through the tools.
```

No additional context. No hints about Passports, tiers, or consent flows. The tools must be self-explanatory.

---

## MCP Configuration

Agents using MCP auto-discover tools from the server. No additional function definitions needed.

```json
{
  "mcpServers": {
    "agentcafe": {
      "url": "https://agentcafe.io/mcp"
    }
  }
}
```

---

## REST Function Definitions

For agents using function-calling (GPT, Gemini, Claude API), provide these tool definitions. Descriptions are copied verbatim from the MCP tool docstrings to ensure parity.

```json
[
  {
    "name": "cafe_search",
    "description": "Search AgentCafe's service catalog. Returns lightweight summaries only — use cafe_get_details for full schemas.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "Natural language search query (e.g. \"book a hotel in Miami\")."
        },
        "category": {
          "type": "string",
          "description": "Optional category filter."
        },
        "max_results": {
          "type": "integer",
          "description": "Max results to return (1-20, default 10)."
        }
      },
      "required": []
    }
  },
  {
    "name": "cafe_get_details",
    "description": "Get the full Menu entry for a service, including required inputs, constraints, and risk tiers.",
    "parameters": {
      "type": "object",
      "properties": {
        "service_id": {
          "type": "string",
          "description": "The service to look up (from cafe_search results)."
        },
        "action_id": {
          "type": "string",
          "description": "Optional — filter to a single action within the service."
        }
      },
      "required": ["service_id"]
    }
  },
  {
    "name": "cafe_request_card",
    "description": "Request a Company Card for standing authorization with a service. The human approves asynchronously.",
    "parameters": {
      "type": "object",
      "properties": {
        "service_id": {
          "type": "string",
          "description": "The service to request a card for."
        },
        "passport": {
          "type": "string",
          "description": "Your valid AgentCafe Passport token (Tier-1 or Tier-2)."
        },
        "suggested_scope": {
          "type": "array",
          "items": {"type": "string"},
          "description": "Optional list of action_ids to suggest for the card scope."
        },
        "suggested_budget_cents": {
          "type": "integer",
          "description": "Optional suggested budget in cents."
        },
        "suggested_duration_days": {
          "type": "integer",
          "description": "Optional suggested duration in days (1-365)."
        }
      },
      "required": ["service_id", "passport"]
    }
  },
  {
    "name": "cafe_invoke",
    "description": "Invoke a service action through AgentCafe. All consent, card, and policy logic is enforced by the Cafe.",
    "parameters": {
      "type": "object",
      "properties": {
        "service_id": {
          "type": "string",
          "description": "The service to call."
        },
        "action_id": {
          "type": "string",
          "description": "The specific action to perform."
        },
        "passport": {
          "type": "string",
          "description": "Your valid AgentCafe Passport token."
        },
        "inputs": {
          "type": "object",
          "description": "The action inputs (required fields depend on the action — use cafe_get_details to check)."
        }
      },
      "required": ["service_id", "action_id", "passport"]
    }
  }
]
```

---

## Task Prompts

Each prompt is given verbatim. No follow-up unless the agent explicitly asks the user a question.

### Scenario 1: Cold Start Discovery
```
I need to book a hotel in Austin, Texas for March 28-30.
```

### Scenario 2: Read Action (Tier-1 Happy Path)
```
Check what hotels are available in Miami for April 1-3, 2 guests.
```

### Scenario 3: Write Action (Tier-2 Escalation)
```
Book hotel room R-205 for 2 nights under the name Jordan Lee.
```

### Scenario 4: Multi-Service Task
```
I'm visiting Austin March 28-30. Find me a hotel, order lunch delivery for arrival day, and schedule a plumber for a leaky faucet at my home while I'm away.
```

### Scenario 5: Error Recovery — Bad Inputs
```
Search for hotels.
```

### Scenario 6: Error Recovery — Wrong Service
```
I need a ride to the airport.
```

### Scenario 7: Company Card Flow
```
I use StayRight Hotels frequently. Can you set up standing authorization so you don't have to ask me every time?
```

### Scenario 8: Menu Exploration
```
What services are available on AgentCafe?
```

---

## Scoring Template

After each scenario, record:

| Field | Value |
|-------|-------|
| Agent | (model name) |
| Interface | MCP / REST |
| Scenario | (1-8) |
| Tool calls made | (ordered list) |
| Discovery accuracy | (correct service found? Y/N, attempts) |
| Input accuracy | (valid inputs first try? Y/N) |
| Error recovery | (correct action after error? Y/N, or N/A) |
| Hallucinations | (any invented fields/IDs? list them) |
| Task completion | Full / Partial / Failed-graceful / Failed-hallucinated |
| Escalation clarity | (explained human auth? Y/N, or N/A) |
| Notable observations | (free text) |

---

## REST Execution

For REST testing, tool calls map to HTTP requests:

| Tool | HTTP Equivalent |
|------|----------------|
| `cafe_search` | `GET /cafe/menu` (then filter client-side) or use MCP endpoint |
| `cafe_get_details` | `GET /cafe/menu` (then extract service) |
| `cafe_invoke` | `POST /cafe/order` with `{service_id, action_id, passport, inputs}` |
| `cafe_request_card` | `POST /cards/request` with `{service_id, ...}` + Authorization header |

Note: REST agents use `/passport/register` to obtain a Passport token first. MCP agents use `cafe.invoke` which will return auth errors if no passport is provided.
