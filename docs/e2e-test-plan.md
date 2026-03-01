# AgentCafe End-to-End Test Plan

**Status:** Updated & aligned — February 28, 2026
**Scope:** Full-stack scenarios spanning company onboarding, agent lifecycle, security enforcement, and cross-cutting integration.
**Relation to DEVELOPMENT-PLAN:** Phase 5 is now 80 % complete (demo_agent ✅, Wizard Dashboard ✅, Wave 1 quick wins ✅). This plan focuses remaining gaps while documenting what is already proven by the new artifacts.

> Existing unit/integration tests (≈170) + demo_agent CLI + Next.js dashboard cover the majority of happy paths.
> This document keeps the high-signal multi-step scenarios that cross module boundaries.

---

## 1. Company Onboarding (Wizard → Menu)

### E2E-ONB-01: Golden happy path — paste/upload/fetch → review → publish → Menu (P0)

**Covered by:** Next.js Dashboard + demo_agent `--service` flow + `GET /cafe/menu`

| Step | Action | Expected | Covered |
|------|--------|----------|---------|
| 1 | Register company (dashboard or API) | 200 + session_token | Dashboard |
| 2 | Spec input: paste / upload / URL fetch | 200 + candidate_menu with confidence scores | Upload/fetch endpoints + dashboard |
| 3 | Review page: edit fields (partial merge preserves AI data) | 200 | Dashboard (ADR-021 fixed) |
| 4 | Policy config + quarantine notice shown | 200 | Dashboard |
| 5 | Preview & publish | 200 + service live with `quarantine_until` | Dashboard |
| 6 | `GET /cafe/menu` | New service appears with correct fields | ✅ |

### E2E-ONB-02: Quarantine UI integration (P0 — remaining gap)
- Publish new service → dashboard shows "In quarantine (30 days)" badge + forces Tier-2 notice.
- After manual lift or expiry → badge disappears.

**Covered by:** Dashboard (pending final UI polish)

### E2E-ONB-03: Partial edits preserve confidence & x-agentcafe-* (P1)
- Parse → confidence + extensions present.
- Edit only description → preview still shows original confidence + merged extensions.

**Covered by:** Wave 1 + dashboard merge logic.

(Other ONB scenarios like duplicate service_id, ownership, upload limits are already covered by existing wizard tests + dashboard.)

---

## 2. Agent Lifecycle (Passport → Order → Audit)

### E2E-AGT-01: Full read → consent → write lifecycle (P0 — golden path)

**Covered by:** `python -m agentcafe.demo_agent --headless --service stayright-hotels --write-action book-room`

All 9 steps (Tier-1 register → read → consent → Tier-2 exchange → write → refresh → audit chain) now run in <15 s.
**Add to CI:** daily smoke test.

Remaining gaps:
- E2E-AGT-03 Token expiry / refresh with revoked policy (add explicit wait + revocation step to demo_agent).
- E2E-AGT-05 Full rate-limit cycle (extend demo_agent with `--stress` flag).

---

## 3. Security Gates

### E2E-SEC-01: Quarantine forces Tier-2 on new services (P0)
**Covered by:** demo_agent + new service publish (quarantine_until check now live).

### E2E-SEC-02: Suspended service 503 (P1)
- Admin `POST /cafe/services/{id}/suspend` → agent sees 503 + Menu security_status.

**Covered by:** Existing tests + future admin dashboard.

### E2E-SEC-03: Identity verification (read-before-write) (P1)
Already covered by unit tests; demo_agent can add `--no-prior-read` negative case.

(SEC-04 injection, SEC-05 audit tamper, SEC-06 consent privacy all fully covered by existing tests.)

---

## 4. Cross-Cutting Integration

### E2E-INT-01: Company publishes → agent discovers → full order cycle (P0 — THE smoke test)
**Covered by:** Dashboard publish + demo_agent happy path.
Run this end-to-end every CI build.

### E2E-INT-02/03: Pause / resume / unpublish lifecycle (P1)
Extend demo_agent with `--pause` / `--resume` flags that call wizard endpoints.

### E2E-INT-04: Multi-tenant isolation (P2)
Covered by wizard ownership checks + JWT audience separation.

### E2E-INT-05: Risk-tier ceiling enforcement (P1)
Covered by Wave 1 + consent flow (human-chosen lifetime now capped correctly).

---

## 5. Dashboard-Specific Scenarios (New — P0)

### E2E-DASH-01: Full browser wizard flow (P0)
- Login → spec input (paste/upload/URL) → review (confidence badges, partial edits) → policy → preview (quarantine notice) → publish.

**Tool:** Playwright (add to `npm test`).

### E2E-DASH-02: Auth guard & token refresh
- Unauthenticated → redirect to /login.
- Token expiry → auto-refresh or re-login.

### E2E-DASH-03: Quarantine & security indicators
- New service shows yellow "Quarantine active" banner.
- Suspended service shows red banner + disabled publish.

### E2E-DASH-04: Confidence & extension visibility
- AI candidate shows per-action confidence % badges.
- x-agentcafe-* presets pre-fill fields (risk_tier, human_identifier_field, etc.).

---

## 6. Error Path Scenarios
(Already well-covered by unit tests; no changes needed.)

---

## Updated Priority for Implementation

| Priority | Scenarios | Owner | Status |
|----------|-----------|-------|--------|
| **P0** | E2E-INT-01 + E2E-DASH-01 (golden paths) | Claude | Demo_agent + dashboard ✅ |
| **P0** | E2E-SEC-01 quarantine UI | Claude | Dashboard pending final polish |
| **P0** | E2E-DASH-03/04 security indicators | Claude | Dashboard |
| **P1** | Pause/resume/unpublish + expiry/revocation | — | Extend demo_agent |
| **P2** | Full multi-tenant + risk-ceiling edge cases | — | Existing coverage + demo_agent flags |
| **P3** | Local admin dashboard tests | — | Future |

---

## Test Infrastructure Notes (Updated)

- **Backend E2E**: Use `pytest-asyncio` + `httpx.AsyncClient`. `demo_agent --headless` is now the primary daily smoke test (add to CI).
- **Dashboard E2E**: Playwright + Next.js dev server. Run with `npm run test:e2e`.
- **Golden command**:
  `python -m agentcafe.demo_agent --headless --service stayright-hotels --write-action book-room && echo "✅ FULL STACK GOLDEN PATH PASSED"`

---

**Next actions**
1. Finish quarantine UI badges in dashboard (P0).
2. Add Playwright suite for E2E-DASH-01/03/04.
3. Extend demo_agent with pause/revoke/stress flags (P1).
