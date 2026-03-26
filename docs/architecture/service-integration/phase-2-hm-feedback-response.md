# Phase 2 Feedback Response from AgentCafe Team

**Date:** 2026-03-25
**From:** AgentCafe team
**Re:** Response to HM's integration notes (`phase-2-integration-notes.md`)

Thank you for the detailed integration notes. This is exactly the kind of friction-point feedback we need from the first onboarding. We're addressing all five suggestions — most as documentation improvements, one as a design recommendation that requires a change on HM's side.

---

## Feedback items — what we're doing

### 1. Configuration agreement template

**Action:** Created. See `onboarding-configuration-template.md` in this directory.

Future service integrations will start by filling out this template collaboratively. It covers action IDs, backend paths, scopes, credential format, identity matching mode, and integration endpoint declarations. This eliminates the blocking Q&A round — the template *is* the Q&A, pre-structured.

### 2. Data flow diagram

**Action:** Created. See the "Identity Binding Lifecycle" section in `onboarding-guide.md` in this directory.

Single diagram showing how `ac_human_id_hash` originates (from the human's AC account), flows through `account-create` (Artifact 2), gets stored as a binding (`human_service_accounts`), and appears in the artifact's `sub` claim (Artifact 1) on every proxied request (Artifact 0, Gate 3-4).

### 3. Opaque_id example requests

**Action:** Created. See the "Opaque ID Mode" section in `onboarding-guide.md`.

Request/response examples for `account-check` and `account-create` in `opaque_id` mode, where `identity_claim` is absent and `ac_human_id_hash` is the sole identity correlator. We'll also note this gap in the spec examples for the next spec revision.

### 4. Synthetic account guidance — **design recommendation for HM**

**Action:** We'd like to recommend a different approach than the synthetic email pattern.

#### The issue

HM's current implementation creates accounts with synthetic emails (`ac_{hash[:16]}@ac.internal`) to satisfy the `email` NOT NULL constraint in HM's account model. This works today, but we think it's the wrong pattern to standardize:

- **It misrepresents the identity layer.** The service's account table now contains rows that look like email-based accounts but aren't. Any feature that assumes accounts have reachable emails (notifications, password reset, admin lookup) will silently fail or produce confusing results.
- **It conflates identity spaces.** AC deliberately chose `opaque_id` for HM to avoid sharing the human's real email. Creating a synthetic email re-introduces an email-shaped field that carries no identity — it's a schema workaround, not identity.
- **It won't generalize.** A future service that has no concept of email at all would need a similar workaround. We'd rather the pattern be clean from the start.
- **It creates phantom records.** Database rows with synthetic emails are indistinguishable from real accounts without additional metadata. This complicates admin tooling, support, and debugging.

#### Our recommendation

HM should introduce a distinct concept for AC-brokered accounts:

1. **Make `email` nullable** on the accounts/users table for AC-brokered accounts, OR add an `account_type` column (`direct` | `ac_brokered`) and allow null email when `account_type = 'ac_brokered'`.

2. **Use `ac_human_id_hash` as the primary identifier** for brokered accounts. This is already the stable correlator — it's what AC sends, what the binding stores, and what the artifact's identity chain traces back to. It should be the account's identity, not a derived synthetic email.

3. **Display label (optional).** If HM's admin UI needs something human-readable for brokered accounts, use a display label like `"AC-brokered account (a1b2c3d4)"` rather than a fake email. This is honest about what the account is.

4. **Scope isolation.** Brokered accounts should not be eligible for PAT issuance or direct login. They exist only in the AC-brokered auth path. This is likely already true in practice, but making it explicit prevents accidental cross-path leakage.

This is a relatively small schema change (nullable email + account type flag) but keeps the identity model clean. The synthetic email pattern is documented in our onboarding guide as a **fallback for legacy systems that truly cannot modify their schema**, not as the recommended approach.

#### Impact on existing HM code

- Schema migration: `ALTER TABLE accounts ALTER COLUMN email DROP NOT NULL` (or equivalent), add `account_type TEXT DEFAULT 'direct'`.
- `create_account()`: Accept `account_type` parameter; skip email validation when `ac_brokered`.
- `POST /integration/account-create`: Set `account_type = 'ac_brokered'`, leave email null, use `ac_human_id_hash` as the stable key.
- Existing PAT-path accounts: Unaffected (`account_type = 'direct'`, email remains required).
- Existing tests: Unaffected (all use direct accounts with real emails).

We're happy to discuss this further or help with the migration if useful.

### 5. SDK test helpers

**Action:** Noted for SDK design. See the "Testing Patterns" section in `onboarding-guide.md`.

The `_inject_key()` / `_clear()` patterns HM developed for JWKS fetcher and replay guard will be first-class SDK features with prominent documentation. HM's implementation validated the pattern — we'll adopt it as-is.

---

## What HM did well

We want to acknowledge:

- **The dual auth middleware is exactly right.** Header-based routing (`X-AgentCafe-Authorization` present → artifact path, absent → PAT) with zero PAT regression is the gold standard for this integration.
- **Timing-safe `request_hash` comparison.** `hmac.compare_digest` is the right call. We'll add a SHOULD recommendation for this in the next spec revision.
- **65 new tests with clear coverage mapping.** The 1:1 correspondence between spec MUST checks and test cases is what we want to see from every integrator.
- **The integration notes themselves.** Detailed, honest, actionable. This document directly improved AC's onboarding documentation.

---

## Summary of actions

| Item | Owner | Action |
|------|-------|--------|
| Configuration template | AC | Created (`onboarding-configuration-template.md`) |
| Data flow diagram | AC | Created (in `onboarding-guide.md`) |
| Opaque_id examples | AC | Created (in `onboarding-guide.md`) |
| Brokered account model | **HM** | Recommended: nullable email + account_type flag (replaces synthetic emails) |
| SDK test helpers | AC | Noted for SDK design phase |
