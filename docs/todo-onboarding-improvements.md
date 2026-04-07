# Onboarding Wizard Improvements — TODO

**Source:** Human Memory onboarding (2026-04-07), first real service.
**Contributors:** Jeremy (operator), Claude (HM advisor).
**Status:** Backlog — evaluate priority before starting work.

---

## Company Management

- [ ] **Transfer / disassociate / close a company account.** No way to clean up or hand off an old company registration.

## Step 1: Spec Input

- [ ] **Prettify JSON in the spec input window.** Auto-format pasted/fetched JSON so it's readable.
- [ ] **"Upload your OpenAPI spec" needs an example and docs.** Add a link to documentation explaining what to provide and what the wizard does with it.
- [ ] **"Try a sample" button doesn't do anything.** Either wire it up or remove it.
- [ ] **Endpoint filtering after parse.** Let service owners include/exclude endpoints, or support OpenAPI tag-based filtering. Framework-generated specs include internal routes that shouldn't be on the Menu.
- [ ] **Curated spec guidance.** Document that framework-generated specs often include internal endpoints and recommend providing a curated agent-only spec.

## Step 3: Policy

- [ ] **Backend URL pre-populates with email.** Should not autofill; likely a browser autofill issue but the field name/autocomplete attr may be contributing.
- [ ] **Auth header pre-populates with spec file input URL.** Same category — wrong autofill.
- [ ] **Navigation back resets integration mode, backend URL, and auth header.** These fields should preserve entered values when navigating back and forward in the wizard.
- [ ] **POST != WRITE.** The wizard classifies all POST endpoints as WRITE/MEDIUM RISK/Tier-2. Many APIs use POST for read operations (request body). Allow overriding READ/WRITE classification via UI toggle or `x-ac-read-only` extension field.
- [ ] **Scope strings are garbled.** Generated scopes like `human-memory:retrievememoryretrievepost` instead of `human-memory:retrieve`. Derivation concatenates operationId fragments. Support `x-ac-scope` extension field, or derive from path, or make derivation logic visible.
- [ ] **Action policy names/IDs also garbled (not editable).** Same root cause as scopes — derived from raw operationId. Need either smarter derivation (warn on auto-generated operationIds, offer path-based alternatives) or inline editing.
- [ ] **Shared scopes across actions.** Two actions using the same scope (e.g., `retrieve` and `retrieve-batch` both using `human-memory:retrieve`) works — confirm this is intentional and tested.

## Step 3b: JV Integration

- [ ] **Integration base URL and auth header ask again.** The JV integration page re-asks for base URL and auth header when these were already provided in the policy step. Either carry them forward or explain why they differ.
- [ ] **Fields pre-populate with email and spec URL (again).** Same autofill issue as policy step.

## General UX

- [ ] **Info hoverables everywhere.** Add `(?)` tooltips or info icons with contextual help on every non-obvious field, linking to docs with detailed explanations and examples.
- [ ] **Copy button on "View Raw JSON" window.** The preview and publish pages show the raw Menu JSON but there's no way to copy it.
- [ ] **Confidence score explanation.** Scores show 0.5–0.6 for descriptions, 0.4 for example_responses. Unclear how they're derived or what value they provide to agents. Document the methodology and what inputs improve them. If spec completeness drives them, tell the service owner what's missing.
- [ ] **operationId heuristic warning.** Detect auto-generated operationIds (contain path segments or HTTP methods like `store_memory_store_post`) and warn the service owner, offering path-based alternatives.
- [ ] **Identity mode explanation.** The opaque ID vs email choice doesn't explain downstream effects — e.g., what gets sent in artifact `sub` claim. Add a one-line explanation.
- [ ] **Extension field documentation.** Publish supported `x-ac-*` extensions (`x-ac-read-only`, `x-ac-scope`, etc.) so services can annotate their specs before uploading.
