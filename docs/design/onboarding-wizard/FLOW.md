# Company Onboarding Wizard — Complete Step-by-Step Flow

**Goal:** Take a company from "I have an API" to "I'm live on the AgentCafe Menu" in under 10 minutes, with zero cost and maximum safety.

---

## Flow Overview

```
┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  1. Welcome  │───▶│ 2. Upload    │───▶│ 3. Smart     │───▶│ 4. Policy    │───▶│ 5. Live      │───▶│ 6. Publish   │
│  & Sign Up   │    │  Your Spec   │    │  Review      │    │  & Safety    │    │  Preview     │    │  (One Click) │
└─────────────┘    └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
```

---

## Step 1: Welcome & Company Account

**What happens:** The company representative arrives at the onboarding page. They create a free company account (email + password, or SSO). No credit card. No contract. No commitment.

**Key messaging:**
- "List your service on the AgentCafe Menu — free forever"
- "Agents discover you. You stay in full control."
- "Takes about 5 minutes"

**Data collected:**
- Company name
- Contact email
- Password (or SSO)
- Company website (optional — for display only)

**Exit:** Account created → proceed to Step 2.

---

## Step 2: Upload Your API Spec

**What happens:** The company uploads their existing OpenAPI spec (YAML or JSON). This is the only technical artifact required. The wizard parses it instantly.

**Accepted formats:**
- OpenAPI 3.0.x or 3.1.x (YAML or JSON)
- Paste raw text, upload a file, or provide a URL

**What the wizard does under the hood:**
1. Validates the spec (syntax, required fields)
2. Extracts all operations (paths + methods + schemas)
3. Uses LiteLLM to generate a candidate `service_id` slug, service `name`, and service `description`
4. Uses LiteLLM to generate candidate `action_id` slugs, action descriptions, `required_inputs` arrays, and `example_response` previews for each operation
5. Detects which operations are read-only vs. write/mutating (for default authorization settings)

**If validation fails:** Clear, specific error message with line number and suggestion. "Your spec has an issue on line 47: the `requestBody` schema is missing a `type` field. Here's how to fix it →"

**Exit:** Spec parsed successfully → candidate Menu entry generated → proceed to Step 3.

---

## Step 3: Smart Review (AI-Assisted)

**What happens:** The wizard shows the company what it understood from their spec and asks a small number of targeted questions to fill gaps or confirm choices. This is the "guided questions" step — minimal, smart, and conversational.

**What the company sees:**
- The auto-generated `service_id`, `name`, and `description` — editable
- For each detected action: the auto-generated `action_id`, `description`, `required_inputs`, and `example_response` — all editable
- A clear list of any fields the wizard couldn't confidently fill (highlighted in amber)

**Questions the wizard asks (only as needed):**

1. **Service identity:** "We named your service `stayright-hotels` — does that look right?" (editable inline)
2. **Action descriptions:** "We described this action as 'Search for available hotel rooms by city and dates.' Want to refine this?" (editable inline)
3. **Required vs. optional inputs:** "Your spec lists `amenities` as an optional filter. Should agents see this on the Menu, or keep it hidden?" (toggle per field)
4. **Example responses:** "Here's a sample response we generated. Does this represent a realistic result?" (editable JSON)
5. **Action grouping:** "We found 6 endpoints. Should all of them be on the Menu, or would you like to exclude any?" (checkbox per action)

**Key principles:**
- Never ask a question the wizard can answer from the spec
- Default to sensible choices (the company only intervenes if they want to)
- Every auto-generated field is editable — the company has final say
- The wizard highlights anything it's unsure about

**Exit:** Company confirms or edits all fields → proceed to Step 4.

---

## Step 4: Policy & Safety Configuration

**What happens:** The wizard guides the company through safety settings for each action. This is where the company defines how much control they want over agent access.

**For each action, the company sets:**

### a) Required Scopes
- The wizard auto-suggests scopes based on the operation (e.g., read operations → `service:search`, write operations → `service:book`)
- Company can rename, add, or remove scopes
- Scopes are the first layer of access control — a Passport must include the required scope for the Cafe to forward the request

### b) Human Authorization Required
- **Auto-default:** `true` for any action that creates, modifies, or deletes data (write operations). `false` for read-only actions.
- Company can override in either direction
- Clear explanation: "When enabled, AgentCafe will only forward this request if the human has explicitly authorized it via their Passport. This is your strongest safety control."

### c) Rate Limits
- Sensible defaults: 60/min for reads, 10/min for writes
- Company can adjust per action
- Explanation: "This limits how many times any single Passport can call this action per minute. Protects against runaway agents."

### d) Backend Connection
- The company provides their backend base URL (e.g., `https://api.stayright-hotels.example.com/v1`)
- The company provides an API key or auth header for AgentCafe to use when proxying
- **Critical messaging:** "This URL and credential are stored securely and NEVER exposed to any agent. All agent requests go through AgentCafe's proxy."

**Exit:** All policy settings confirmed → proceed to Step 5.

---

## Step 5: Live Preview

**What happens:** The wizard renders an exact preview of how this service will appear on the AgentCafe Menu. The company sees exactly what agents will see — the locked Menu format, fully rendered.

**What the company sees:**
- The complete Menu entry in the locked JSON format (service_id, name, description, actions with all fields)
- A "How agents see this" section showing a natural-language summary: "An agent browsing the Menu will see: *'StayRight Hotels — Search and book hotel rooms worldwide...'* with 4 available actions."
- A "Test it" button that simulates an agent ordering each action (dry run, no real backend call) to verify the proxy mapping is correct
- An "Edit" link on every field that takes the company back to the relevant step

**Key principles:**
- WYSIWYG — what you see is exactly what goes live
- Nothing is hidden or abstracted away
- The company can go back and tweak anything before publishing

**Exit:** Company is satisfied with the preview → proceed to Step 6.

---

## Step 6: Publish (One Click)

**What happens:** The company clicks "Publish to Menu" and their service goes live instantly.

**What happens on click:**
1. Menu entry is written to the database
2. Proxy routing config is activated (backend URL + auth mapped to service_id/action_id pairs)
3. Company receives a confirmation email with:
   - A link to their service on the Menu
   - Their company dashboard URL (to manage settings, view logs, pause/unpublish)
   - A reminder that they can unpublish or modify at any time

**Post-publish:**
- Service is immediately browsable by any agent
- The company dashboard shows real-time request logs (anonymized — no Passport details, just counts and action types)
- Company can pause, unpublish, or edit any action at any time

**Key messaging:**
- "You're live! Agents can now discover your service on the Menu."
- "You're in full control. Pause or unpublish anytime from your dashboard."
- "Every request is protected by double validation — human Passport + your company policy."

---

## Error Handling & Edge Cases

| Scenario | Handling |
|----------|----------|
| Invalid OpenAPI spec | Clear error with line number, suggestion, and "fix and retry" |
| Spec with no operations | "We couldn't find any API operations. Make sure your spec includes at least one path with a method." |
| Spec with 50+ operations | "You have 52 operations. We recommend starting with your most important 5-10 and adding more later." |
| Company wants to exclude an action | Checkbox in Step 3 — unchecked actions are not published |
| Company wants to change something after publish | Dashboard → Edit → re-enters wizard at the relevant step |
| Backend URL unreachable during test | Warning (not blocking): "We couldn't reach your backend. This won't block publishing, but agents won't get responses until it's reachable." |

---

## Flow Timing Targets

| Step | Target Time |
|------|-------------|
| 1. Welcome & Sign Up | 1 minute |
| 2. Upload Spec | 30 seconds |
| 3. Smart Review | 2-3 minutes |
| 4. Policy & Safety | 1-2 minutes |
| 5. Live Preview | 1 minute |
| 6. Publish | 5 seconds |
| **Total** | **~5-7 minutes** |
