# Agent Testing Results — Cascade (REST)

**Agent:** Claude Sonnet (Cascade / IDE agent)
**Interface:** REST API (https://agentcafe.io)
**Date:** March 24, 2026
**System prompt used:** Standard test harness prompt (no prior AgentCafe knowledge)

---

## Scenario 1: Cold Start Discovery

**Prompt:** "I need to book a hotel in Austin, Texas for March 28-30."

| Field | Value |
|-------|-------|
| Tool calls | `GET /cafe/menu` → filter for hotels → `POST /cafe/order` (search-availability) |
| Discovery accuracy | **Y** — found `stayright-hotels` immediately (1 query) |
| Input accuracy | **Y** — all fields correct on first try (city, check_in, check_out, guests) |
| Error recovery | N/A |
| Hallucinations | None |
| Task completion | **Full** — discovered service, registered passport, searched, got results |
| Escalation clarity | N/A |

**Observations:**
- Menu structure made service discovery trivial — `service_id: stayright-hotels` is self-explanatory.
- `required_inputs` array with `name`, `type`, `description`, `example` is excellent. The `example` field ("Austin", "2026-03-15") told me the exact format.
- I naturally read the Menu before invoking. The hint "Use cafe.get_details..." wasn't needed because the full Menu already has everything.

**Finding:** For REST agents, `/cafe/menu` returns the full Menu in one call. MCP agents must use `cafe.search` → `cafe.get_details` (2 calls). This means MCP agents have a slightly worse discovery experience but a much more focused one for large catalogs.

---

## Scenario 2: Read Action (Tier-1 Happy Path)

**Prompt:** "Check what hotels are available in Miami for April 1-3, 2 guests."

| Field | Value |
|-------|-------|
| Tool calls | `POST /passport/register` → `POST /cafe/order` |
| Discovery accuracy | **Y** — already knew service from Scenario 1 context, but a cold agent would search first |
| Input accuracy | **Y** — `{city: "Miami", check_in: "2026-04-01", check_out: "2026-04-03", guests: 2}` |
| Error recovery | N/A |
| Hallucinations | None |
| Task completion | **Full** |

**Observations:**
- Passport registration is frictionless — one POST, get a JWT back.
- The response from `/passport/register` includes `tier: "read"` which is informative.
- No issues. This is the golden path and it works.

---

## Scenario 3: Write Action (Tier-2 Escalation)

**Prompt:** "Book hotel room R-205 for 2 nights under the name Jordan Lee."

| Field | Value |
|-------|-------|
| Tool calls | `POST /passport/register` → `POST /cafe/order` (book-room) |
| Discovery accuracy | **Y** |
| Input accuracy | **Y** — used correct fields from Menu schema |
| Error recovery | **Partial** — see below |
| Hallucinations | None |
| Task completion | **Partial** — correctly identified the auth wall, did not complete booking |
| Escalation clarity | **Y** — error message was crystal clear |

**Error response received:**
```json
{
  "error": "tier_insufficient",
  "message": "This action requires a Tier-2 (write) Passport. Your Tier-1 (read) Passport cannot perform write actions.",
  "card_suggestion": {
    "action": "request_card",
    "endpoint": "POST /cards/request",
    "body": {"service_id": "stayright-hotels"},
    "message": "You can request a Company Card for 'stayright-hotels'..."
  }
}
```

**Observations:**
- The error is one of the best agent-facing errors I've seen. It explains the problem, names the fix, provides the endpoint, and even gives the request body.
- The `card_suggestion` is brilliant for agent recovery.
- **Finding (REST vs MCP gap):** REST agents see `endpoint: "POST /cards/request"` — they know exactly where to go. MCP agents see `hint: "Use cafe.request_card..."` — also clear. Both paths work.

**Potential issue:** An agent without the card/consent mental model might try to re-register for a "better" passport instead of following the card suggestion. The error doesn't explicitly say "you cannot upgrade your passport directly." Worth monitoring in cross-agent testing.

---

## Scenario 5: Error Recovery — Bad Inputs

**Prompt:** "Search for hotels." (deliberately vague)

| Field | Value |
|-------|-------|
| Tool calls | `POST /cafe/order` (search-availability) with empty inputs |
| Input accuracy | **N** — intentionally sent `{}` |
| Error recovery | **Y** — error clearly listed missing fields |
| Hallucinations | None |
| Task completion | **Partial** — would succeed on retry with corrected inputs |

**Error response received:**
```json
{
  "error": "missing_inputs",
  "message": "Missing required inputs: check_in, check_out, guests",
  "missing": ["check_in", "check_out", "guests"]
}
```

**Observations:**
- Error clearly lists exactly which fields are missing — an agent can retry immediately.
- **Finding:** `city` is listed as `required_inputs` on the Menu but is NOT in the `missing` list when omitted. Either the backend has a default city, or the input validation is inconsistent with the Menu schema. This is a **discrepancy** that could confuse an agent: the Menu says city is required, the validator says it's optional.

---

## Scenario 6: Error Recovery — Wrong Service

**Prompt:** "I need a ride to the airport."

| Field | Value |
|-------|-------|
| Tool calls | `GET /cafe/menu` → client-side search for ride/taxi/airport/transport |
| Discovery accuracy | **Y** — correctly determined no matching service |
| Hallucinations | None |
| Task completion | **Failed-graceful** — correctly reported unavailability |

**Observations:**
- No ride/taxi/transport service exists. Agent correctly identified this from Menu scan.
- **Finding (MCP-specific):** An MCP agent using `cafe.search("ride to airport")` would get `total_matched: 0`. The response doesn't include a "nothing found" message or suggestions. It should say something like "No matching services found. Try different keywords or use an empty query to browse all services."

---

## Scenario 7: Company Card Flow

**Prompt:** "I use StayRight Hotels frequently. Set up standing authorization."

| Field | Value |
|-------|-------|
| Tool calls | `POST /passport/register` → `POST /cards/request` |
| Input accuracy | **Y** — included service_id, suggested_scope, budget, duration |
| Error recovery | N/A |
| Hallucinations | None |
| Task completion | **Full** — card created, activation code returned |

**Response received:**
```json
{
  "card_id": "88b64256-...",
  "consent_url": "/authorize/card/88b64256-...",
  "activation_code": "82GBG6L8",
  "activation_url": "/activate?code=82GBG6L8",
  "status": "pending",
  "expires_at": "2026-04-23T10:46:46..."
}
```

**Observations:**
- Card creation is clean. The response includes everything an agent needs to tell the user how to approve.
- **Finding:** The `consent_url` is a relative path (`/authorize/card/...`). An agent doesn't know the base URL unless it was told. Should be absolute (`https://agentcafe.io/authorize/card/...`). Same for `activation_url`.
- The `activation_code` is great for the cold-start UX — agent can say "tell your human to enter code 82GBG6L8 at agentcafe.io/activate."

---

## Scenario 8: Menu Exploration

**Prompt:** "What services are available on AgentCafe?"

| Field | Value |
|-------|-------|
| Tool calls | `GET /cafe/menu` |
| Discovery accuracy | **Y** — all 3 services listed |
| Hallucinations | None |
| Task completion | **Full** |

**Observations:**
- REST agents get the full Menu in one call — easy to summarize for the user.
- MCP agents would use `cafe.search("")` (empty query). Need to verify this returns all services.
- The Menu structure is clean and easy for an agent to summarize in natural language.

---

## Summary of Findings

### Bugs / Discrepancies
1. **Input validation inconsistency** — `city` is listed as required in the Menu schema but not enforced by the validator. Agents see conflicting information about what's required.
2. **Relative URLs in card response** — `consent_url` and `activation_url` are relative paths. Agents don't inherently know the base URL, making these unusable without extra context.

### Improvement Opportunities
3. **Empty search results message (MCP)** — `cafe.search` with no matches returns `total_matched: 0` but no helpful message. Add a hint like "No services matched. Try different keywords or browse all with an empty query."
4. **Passport re-upgrade misconception risk** — The `tier_insufficient` error doesn't explicitly tell agents they can't upgrade their passport. Some agents might try re-registering instead of using `cafe.request_card`.

### What Worked Well
5. **Menu input schemas** — `required_inputs` with name, type, description, example is excellent. Agents can assemble correct inputs on first try.
6. **`card_suggestion` in auth errors** — best-in-class agent error UX. Tells the agent exactly what to do next.
7. **`missing_inputs` error** — clearly lists what's missing, enabling immediate retry.
8. **Passport registration** — zero friction, clear tier labeling.

### Classification

| # | Finding | Type |
|---|---------|------|
| 1 | city required vs optional mismatch | Schema fix |
| 2 | Relative URLs in card response | Error message fix |
| 3 | Empty search results hint | Tool description fix |
| 4 | Passport upgrade misconception risk | Tool description fix |
| 5-8 | Working well | No action needed |
