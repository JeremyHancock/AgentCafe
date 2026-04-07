# AC Response to HM Phase 2 Reply

**Date:** 2026-03-27
**From:** AgentCafe team
**Re:** Reply to HM's `phase-2-feedback-response-reply.md`

Thank you for the thoughtful reply. Both issues you raised led to real design work on our side — the SQLite migration point improved our onboarding guidance, and the namespace fragmentation question triggered an architectural decision (ADR-032) that affects every future integration.

---

## Issue 1: Schema migration complexity — agreed, guidance updated

You're right, and your point extends beyond SQLite. Our original recommendation included PostgreSQL-specific syntax (`ALTER COLUMN DROP NOT NULL`) that doesn't apply to HM and won't apply to many future services. The deeper issue: we were being too prescriptive about schema changes on systems we don't own.

**What we've changed:**

Our onboarding guide now gives concept-level guidance only. We describe *what* the schema must support:
- Accept account creation without human-facing identity fields
- Use `ac_human_id_hash` as the stable lookup key
- Distinguish brokered accounts from direct accounts in admin tooling

We no longer prescribe *how* to implement the migration. Whether you use nullable columns, a separate table, a view, or something else is your decision — you know your database engine, your data model, and your risk tolerance.

The onboarding guide retains the Python code example for `account-create` handling (since the API contract is ours to specify), but the SQL examples have been removed.

**For HM specifically:** Your SQLite table recreation plan (create new → copy → drop → rename → reindex) is the correct approach for your engine. You know your WAL backup and rollback infrastructure better than we do. Proceed as you see fit.

---

## Issue 2: Namespace fragmentation — design decision made (ADR-032)

Your question — "How is AC thinking about this?" — prompted a significant internal design discussion.

### The problem is real and existential

We agree with your framing completely. A human who uses HM directly AND through AC should not end up with siloed memories. For a service like a bank, creating a shadow account when the human already has one would be unacceptable. If using AgentCafe makes a human's experience worse than using the service directly, we're creating a problem, not a solution.

### Our answer: hybrid model based on `has_direct_signup`

We've adopted a hybrid approach (ADR-032) where the account creation strategy depends on whether the service has existing users:

**Services where `has_direct_signup: false`** (like HM today):
- Brokered `account-create` remains the correct mechanism
- There are no existing accounts to fragment against
- No changes needed on HM's side

**Services where `has_direct_signup: true`** (banks, SaaS tools, any service with its own registration):
- During consent, AC asks the human: "Do you already have a {Service} account?"
- If yes → linking flow (your Option 2 — service-initiated claim). The human authenticates on the service, the service confirms the match, AC binds the existing account.
- If no → `account-create` proceeds normally. Genuinely new user, no fragmentation.
- Recovery path: if the human gets it wrong, they can link later from the AC dashboard.

### Why we chose this over eliminating brokered accounts

We initially considered removing brokered accounts entirely for jointly-verified services. We pressure-tested this with adversarial reviewers who found it too broad:
- It would break AC-owned services (HM has no login UI for a redirect-based linking flow)
- Autonomous agents running without a human present can't complete browser redirects
- Voice-only agents can't navigate linking flows
- Headless/API-only services have no web UI to redirect to

The `has_direct_signup` flag makes fragmentation prevention a service-level declaration tied to consent-time behavior, not a universal protocol restriction.

### What this means for HM

**Today:** HM has `has_direct_signup: false`. Brokered `account-create` is correct. No fragmentation risk. No changes needed.

**When HM adds direct signup (your Phase 3 roadmap):** HM would update to `has_direct_signup: true`. At that point:
- HM would implement the linking flow endpoints (Service Contract §A.4–A.5)
- AC's consent flow would automatically add the "Do you already have a Human Memory account?" question
- Existing brokered accounts from the `has_direct_signup: false` era would get a dashboard-accessible linking flow so humans can consolidate

**The account linking options you proposed:**
- **Option 1 (AC-initiated linking):** This is how the consent-time question works — AC knows the human's identity, and directs the linking flow.
- **Option 2 (Service-initiated claim):** This is the mechanical implementation of the redirect — the human authenticates on the service's site, the service confirms the match. Already specified in §A.4–A.5.
- **Option 3 (Namespace portability):** We're not pursuing this. It's a data migration problem that's harder than account linking and solves a narrower case.

### Agent fragmentation signal

For `has_direct_signup: true` services, when a human's account was created via `account-create` (not linked), AC includes `X-AgentCafe-Account-Status: unlinked` on proxied requests. Agents can use this to surface a linking prompt. This is the "agent signal" concept — agents don't need to know why, just that the human should link.

---

## Item 4: PAT issuance — aligned

Your position is correct: enforce PAT restrictions on brokered accounts via a route-level policy check, not a schema constraint. This is now our official guidance in the onboarding guide. Whether brokered accounts can issue PATs is a service-level policy decision — AC recommends against it today, but the policy should be evolvable as account linking matures.

Your proposed implementation:
```python
if account_type == 'ac_brokered': return 403
```

is exactly right.

---

## Your implementation plan — go ahead

Your items 1–3 and 5–6 can proceed as stated. Item 4 (PAT creation blocked via route-level policy) is aligned. We have no objections to any item.

---

## SDK test helpers

Noted. `make_test_artifact()` is a great suggestion — generating valid artifacts for a given action/scope/hash is exactly the kind of boilerplate the SDK should eliminate. We'll include it alongside `_inject_key()` and `_clear()` when the SDK ships.

If you're willing to share your `conftest.py` as a reference implementation, we'd welcome it. It'll help us design the SDK helpers to match what real integrators actually need.

---

## Summary

| Item | Resolution |
|------|-----------|
| Issue 1 (SQLite migration) | Onboarding guidance updated: concept-level only. HM proceeds with its own migration plan. |
| Issue 2 (Namespace fragmentation) | ADR-032 adopted: hybrid model with `has_direct_signup` flag. HM unaffected today; linking deferred to HM's Phase 3. |
| PAT issuance policy | Aligned: route-level enforcement, not schema constraint. |
| `make_test_artifact()` | Added to SDK design backlog. `conftest.py` reference welcome. |
| HM implementation plan (items 1–6) | Approved. Proceed. |

---

**Referenced documents:**
- ADR-032: `docs/architecture/decisions.md`
- Updated onboarding guide: `onboarding-guide.md` (Section 3 revised)
- Updated configuration template: `onboarding-configuration-template.md` (Section 3 revised)
- Service Contract linking flow: `service-contract-identity-binding-protocol.md` §A.4–A.5
