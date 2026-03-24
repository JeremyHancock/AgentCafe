# Agent User Testing Research Plan

**Goal:** Validate AgentCafe's end-to-end agent experience with the three demo services before onboarding the first real service (Human Memory). Identify friction points in discovery, authentication, invocation, and error recovery.

**Date:** March 24, 2026
**Environment:** Live production at agentcafe.io (MCP endpoint + REST API)

---

## 1. Why Test Now

Human Memory onboarding is imminent. Once a real service is live, agent-facing UX bugs become customer-facing bugs. The demo services (StayRight Hotels, QuickBite Delivery, FixRight Home) provide a safe sandbox to stress-test every path an agent will take.

Our users are AI agents. They're cooperative, articulate, and will narrate their reasoning at every step. We get think-aloud protocol for free — every failed attempt comes with an explanation of *why* the agent made that choice.

## 2. Research Questions

| # | Question | Success Criteria |
|---|----------|-----------------|
| RQ1 | Can an agent discover the right service from a natural language goal? | Agent finds correct service_id + action_id in ≤ 2 search queries |
| RQ2 | Can an agent assemble correct inputs from `cafe.get_details` schemas? | Agent produces valid inputs on first attempt ≥ 80% of the time |
| RQ3 | Does the Tier-1 → Tier-2 escalation path make sense to agents? | Agent correctly identifies it needs human auth and follows the hint |
| RQ4 | Do agents recover from `HUMAN_AUTH_REQUIRED` using `cafe.request_card`? | Agent calls `cafe.request_card` (not retry `cafe.invoke`) after auth error |
| RQ5 | Can agents complete multi-step tasks end-to-end? | Agent completes a 3+ step task (search → details → invoke) without human coaching |
| RQ6 | Are MCP tool descriptions sufficient, or do agents need supplementary context? | No agent asks "what tools are available?" after connecting |
| RQ7 | Where do agents hallucinate or misinterpret? | Catalog of hallucinated field names, wrong service_ids, misread risk tiers |

## 3. Test Subjects

| Agent | Interface | Notes |
|-------|-----------|-------|
| Claude (Sonnet) | MCP (via Claude Desktop / API) | Native MCP support, strong tool-use |
| Claude (Sonnet) | REST (function-calling) | Using `examples/claude_agent.py` pattern |
| GPT-4o | REST (function-calling) | Using `examples/openai_agent.py` pattern |
| GPT-4o | MCP (via OpenAI Agents SDK) | If SDK supports remote MCP by test date |
| Gemini 2.5 | REST (function-calling) | Third model family for diversity |

Minimum 3 agent families. Each runs all scenarios via both MCP and REST where possible.

## 4. Test Scenarios

### Scenario 1: Cold Start Discovery
**Prompt:** "I need to book a hotel in Austin, Texas for March 28-30."
**Expected path:** `cafe.search("hotel Austin")` → find `stayright-hotels` → `cafe.get_details("stayright-hotels")` → identify `search-availability` action
**Measures:**
- Number of search queries before finding the right service
- Whether agent reads details before attempting to invoke
- Whether agent correctly identifies read vs. write actions

### Scenario 2: Read Action (Tier-1 Happy Path)
**Prompt:** "Check what hotels are available in Miami for April 1-3, 2 guests."
**Expected path:** Register Passport → `cafe.invoke` with `search-availability`
**Measures:**
- Does agent register a Passport first?
- Are input fields correct (field names, types, date format)?
- Does agent interpret the response correctly?

### Scenario 3: Write Action (Tier-2 Escalation)
**Prompt:** "Book hotel room R-205 for 2 nights under the name Jordan Lee."
**Expected path:** Invoke with `book-room` → receive `HUMAN_AUTH_REQUIRED` → call `cafe.request_card` or explain consent is needed
**Measures:**
- Does agent attempt the write without checking risk tier first?
- Does agent correctly interpret `HUMAN_AUTH_REQUIRED` error?
- Does agent follow the `hint` field to `cafe.request_card`?
- Does agent communicate to the user that human approval is needed?

### Scenario 4: Multi-Service Task
**Prompt:** "I'm visiting Austin March 28-30. Find me a hotel, order lunch delivery for arrival day, and schedule a plumber for a leaky faucet at my home while I'm away."
**Expected path:** Three separate service interactions across all three demo backends
**Measures:**
- Does agent correctly map sub-tasks to different services?
- Does agent reuse the same Passport across services?
- How does agent handle mixed read/write across services?
- Does agent attempt to parallelize or serialize?

### Scenario 5: Error Recovery — Bad Inputs
**Prompt:** "Search for hotels" (deliberately vague — no city, dates, or guests)
**Expected path:** Agent either adds reasonable defaults or receives a validation error and retries
**Measures:**
- Does agent fill in required fields from context or ask the user?
- If the API returns an error, does agent retry with corrected inputs?
- How many retries before success or giving up?

### Scenario 6: Error Recovery — Wrong Service
**Prompt:** "I need a ride to the airport."
**Expected path:** `cafe.search("ride airport")` → no results → agent reports unavailability
**Measures:**
- Does agent correctly identify that no matching service exists?
- Does agent hallucinate a service_id or try to force-fit?
- How does agent communicate the limitation?

### Scenario 7: Company Card Flow
**Prompt:** "I use StayRight Hotels frequently. Can you set up standing authorization so you don't have to ask me every time?"
**Expected path:** `cafe.request_card` with `stayright-hotels` and suggested scope
**Measures:**
- Does agent understand the Company Card concept from the tool description?
- Does agent suggest reasonable scope/budget/duration?
- Does agent explain the async approval process to the user?

### Scenario 8: Menu Exploration (No Specific Goal)
**Prompt:** "What services are available on AgentCafe?"
**Expected path:** `cafe.search("")` (empty query) → browse results → optionally `cafe.get_details` on interesting services
**Measures:**
- Does agent use empty-query search or try something else?
- Does agent summarize results in a useful way?
- Does agent proactively suggest actions the user might want?

## 5. Data Collection

For each test run, capture:

1. **Full transcript** — every tool call, response, and agent reasoning
2. **Tool call sequence** — ordered list of MCP/REST calls made
3. **Input accuracy** — did the agent produce valid inputs on first try?
4. **Error encounters** — every error and what the agent did next
5. **Hallucinations** — any invented field names, service_ids, or action_ids
6. **Task completion** — did the agent achieve the goal? Fully, partially, or not at all?
7. **Agent self-assessment** — ask the agent at the end: "Rate your experience using AgentCafe from 1-10. What was confusing?"

### Logging
The `mcp_request_log` table (migration 0011) automatically captures every MCP tool call with query, service_id, action_id, outcome, and latency. Use `GET /cafe/admin/mcp-analytics` to pull aggregate data after each test batch.

For REST tests, the `audit_log` table captures all orders.

## 6. Analysis Framework

### Per-Scenario Scorecard

| Metric | Definition |
|--------|-----------|
| **Discovery accuracy** | Correct service_id found / total attempts |
| **Input accuracy** | Valid inputs on first call / total invoke attempts |
| **Error recovery rate** | Correct recovery action / total errors encountered |
| **Hallucination rate** | Invented fields or IDs / total tool calls |
| **Task completion** | Full (3), Partial (2), Failed with graceful exit (1), Failed with hallucination (0) |
| **Escalation clarity** | Agent correctly explains human auth requirement (yes/no) |

### Cross-Agent Comparison
- Which model family performs best at each scenario?
- MCP vs REST: does the structured tool interface improve accuracy?
- Where do all agents struggle? (These are UX bugs, not agent bugs.)

### Actionable Outputs
For each finding, classify as:
- **Tool description fix** — better wording solves it
- **Schema fix** — field names or types are misleading
- **Error message fix** — agent couldn't interpret the error
- **Missing feature** — agent needed something we don't provide
- **Agent limitation** — not fixable on our side

## 7. Test Execution Plan

| Phase | What |
|-------|------|
| **Phase A: Setup** | Configure Claude Desktop + MCP, prepare REST test scripts, verify analytics endpoint |
| **Phase B: Single-agent sweep** | Run all 8 scenarios with Claude Sonnet via MCP. Record transcripts. |
| **Phase C: Cross-agent** | Run key scenarios (1, 3, 4, 6) with GPT-4o and Gemini. Compare. |
| **Phase D: Analysis** | Score all runs, identify top friction points, draft fixes |
| **Phase E: Fix & Retest** | Implement tool description / error message fixes, rerun failed scenarios |

## 8. Success Gate for Human Memory Onboarding

Human Memory onboarding proceeds when:
- [ ] All 3 agent families complete Scenarios 1-3 without human coaching
- [ ] Error recovery rate ≥ 75% across all agents
- [ ] Hallucination rate < 10% of tool calls
- [ ] No blocking UX bugs in tool descriptions or error messages
- [ ] `HUMAN_AUTH_REQUIRED` → `cafe.request_card` path works for ≥ 2/3 agents

---

*This plan treats agents as first-class users. Their confusion is our bug.*
