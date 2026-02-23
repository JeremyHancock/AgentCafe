# Company Onboarding Wizard — UI Screens & Sample Text

Each screen is described in markdown wireframe style. These are the exact screens a company representative sees during onboarding.

---

## Screen 1: Welcome

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│                         AgentCafe                               │
│                                                                 │
│            List your service on the Menu.                       │
│            Agents discover you. You stay in control.            │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Company Name       [ StayRight Hotels                  ] │  │
│  │  Work Email          [ api-team@stayright.example.com   ] │  │
│  │  Password            [ ••••••••••••                     ] │  │
│  │  Company Website     [ https://stayright.example.com    ] │  │
│  │                                (optional)                 │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│               [ Create Free Account → ]                         │
│                                                                 │
│  Already have an account? Sign in                               │
│                                                                 │
│  ─────────────────────────────────────────────────────────────  │
│                                                                 │
│  ✓ Completely free — no credit card, no contract                │
│  ✓ Full control — pause or unpublish anytime                    │
│  ✓ Maximum safety — double validation on every request          │
│  ✓ Takes about 5 minutes                                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Screen 2: Upload Your Spec

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  Step 2 of 6 — Upload Your API Spec                             │
│  ━━━━━━━━━━●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   │
│                                                                 │
│  We'll read your OpenAPI spec and auto-generate your            │
│  Menu entry. You'll review everything before it goes live.      │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                                                           │  │
│  │       ┌─────────────────────────────────┐                 │  │
│  │       │   Drop your OpenAPI file here   │                 │  │
│  │       │      (.yaml or .json)           │                 │  │
│  │       │                                 │                 │  │
│  │       │      [ Browse Files ]           │                 │  │
│  │       └─────────────────────────────────┘                 │  │
│  │                                                           │  │
│  │  — or —                                                   │  │
│  │                                                           │  │
│  │  [ Paste spec URL ]    [ Paste raw YAML/JSON ]            │  │
│  │                                                           │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Supported: OpenAPI 3.0.x and 3.1.x                             │
│  Your spec is only used to generate the Menu entry.             │
│  It is never shared with agents.                                │
│                                                                 │
│  [ ← Back ]                          [ Parse & Continue → ]     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Validation success state:**

```
┌───────────────────────────────────────────────────────────────┐
│  ✓ Spec parsed successfully                                   │
│                                                               │
│  Title:       HotelBookingService Internal API                │
│  Version:     1.0.0                                           │
│  Operations:  4 detected                                      │
│    • POST /availability/search                                │
│    • GET  /rooms/{room_id}                                    │
│    • POST /bookings                                           │
│    • POST /bookings/{booking_id}/cancel                       │
│                                                               │
│  [ Parse & Continue → ]                                       │
└───────────────────────────────────────────────────────────────┘
```

**Validation error state:**

```
┌───────────────────────────────────────────────────────────────┐
│  ✗ We found an issue with your spec                           │
│                                                               │
│  Line 47: The requestBody schema is missing a required        │
│  `type` field.                                                │
│                                                               │
│  Suggested fix:                                               │
│  Add `type: object` to the schema at line 47.                 │
│                                                               │
│  [ Fix and Re-upload ]                                        │
└───────────────────────────────────────────────────────────────┘
```

---

## Screen 3: Smart Review

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  Step 3 of 6 — Review Your Menu Entry                           │
│  ━━━━━━━━━━━━━━━━━━━━●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   │
│                                                                 │
│  We auto-generated your Menu entry from your spec.              │
│  Review and edit anything below. All fields are editable.       │
│                                                                 │
│  ┌─── SERVICE IDENTITY ──────────────────────────────────────┐  │
│  │                                                           │  │
│  │  Service ID    [ stayright-hotels ]         [edit] │  │
│  │  Display Name  [ StayRight Hotels ]         [edit] │  │
│  │  Description   [ Search and book hotel rooms       [edit] │  │
│  │                  worldwide. Find availability by           │  │
│  │                  city, dates, and preferences. ]           │  │
│  │                                                           │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─── ACTIONS (4 detected) ──────────────────────────────────┐  │
│  │                                                           │  │
│  │  ☑  search-availability                                   │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │ Description: Search for available hotel rooms by    │  │  │
│  │  │ city, dates, number of guests, and optional         │  │  │
│  │  │ filters like max price and amenities.        [edit] │  │  │
│  │  │                                                     │  │  │
│  │  │ Required Inputs:                                    │  │  │
│  │  │   • city — City to search for hotels         [edit] │  │  │
│  │  │   • check_in — Check-in date (ISO 8601)     [edit] │  │  │
│  │  │   • check_out — Check-out date (ISO 8601)   [edit] │  │  │
│  │  │   • guests — Number of guests (1-10)        [edit] │  │  │
│  │  │                                                     │  │  │
│  │  │ Example Response:                            [edit] │  │  │
│  │  │   { "results": [...], "total_results": 1 }         │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  │                                                           │  │
│  │  ☑  get-room-details                                      │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │ Description: Get full details for a specific room   │  │  │
│  │  │ including description, amenities, cancellation      │  │  │
│  │  │ policy, and real-time pricing.               [edit] │  │  │
│  │  │ ...                                                 │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  │                                                           │  │
│  │  ☑  book-room                                             │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │ ...                                                 │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  │                                                           │  │
│  │  ☑  cancel-booking                                        │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │ ...                                                 │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  │                                                           │  │
│  │  Uncheck any actions you don't want on the Menu.          │  │
│  │                                                           │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ⓘ Everything above was auto-generated from your spec.          │
│    Amber fields need your attention. All fields are editable.   │
│                                                                 │
│  [ ← Back ]                          [ Looks Good → ]          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Screen 4: Policy & Safety

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  Step 4 of 6 — Safety & Access Control                          │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━●━━━━━━━━━━━━━━━━━━━━━━━━━━━━   │
│                                                                 │
│  Configure how agents can access each action.                   │
│  We've set smart defaults — adjust anything you'd like.         │
│                                                                 │
│  ┌─── search-availability ───────────────────────────────────┐  │
│  │  Type: READ (auto-detected)                               │  │
│  │                                                           │  │
│  │  Scopes:       [ hotel:search ]                    [edit] │  │
│  │  Human auth:   ( ) Required  (●) Not required             │  │
│  │  Rate limit:   [ 60 ] requests per minute          [edit] │  │
│  │                                                           │  │
│  │  ⓘ Read-only actions are safe with lower restrictions.    │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─── get-room-details ──────────────────────────────────────┐  │
│  │  Type: READ (auto-detected)                               │  │
│  │                                                           │  │
│  │  Scopes:       [ hotel:search ]                    [edit] │  │
│  │  Human auth:   ( ) Required  (●) Not required             │  │
│  │  Rate limit:   [ 60 ] requests per minute          [edit] │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─── book-room ─────────────────────────────────────────────┐  │
│  │  Type: WRITE (auto-detected)                      ⚠      │  │
│  │                                                           │  │
│  │  Scopes:       [ hotel:book ]                      [edit] │  │
│  │  Human auth:   (●) Required  ( ) Not required             │  │
│  │  Rate limit:   [ 10 ] requests per minute          [edit] │  │
│  │                                                           │  │
│  │  ⚠ This action creates a financial commitment.            │  │
│  │    We strongly recommend requiring human authorization.   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─── cancel-booking ────────────────────────────────────────┐  │
│  │  Type: WRITE (auto-detected)                      ⚠      │  │
│  │                                                           │  │
│  │  Scopes:       [ hotel:cancel ]                    [edit] │  │
│  │  Human auth:   (●) Required  ( ) Not required             │  │
│  │  Rate limit:   [ 10 ] requests per minute          [edit] │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─── BACKEND CONNECTION ────────────────────────────────────┐  │
│  │                                                           │  │
│  │  Backend URL   [ https://api.stayright-hotels.example.com │  │
│  │                  /v1                                ]      │  │
│  │                                                           │  │
│  │  Auth Header   [ Authorization: Bearer sk-stay-•••••••• ] │  │
│  │                                                           │  │
│  │  🔒 This URL and credential are stored securely by        │  │
│  │     AgentCafe and NEVER exposed to any agent.             │  │
│  │     All agent requests are proxied through the Cafe.      │  │
│  │                                                           │  │
│  │  [ Test Connection ]  ✓ Backend reachable                 │  │
│  │                                                           │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  [ ← Back ]                         [ Save & Preview → ]       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Screen 5: Live Preview

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  Step 5 of 6 — Preview Your Menu Entry                          │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━●━━━━━━━━━━━━━━━━━━   │
│                                                                 │
│  This is exactly how agents will see your service on the Menu.  │
│                                                                 │
│  ┌─── AGENT VIEW ────────────────────────────────────────────┐  │
│  │                                                           │  │
│  │  StayRight Hotels                                  │  │
│  │  stayright-hotels                                  │  │
│  │                                                           │  │
│  │  Search and book hotel rooms worldwide. Find availability │  │
│  │  by city, dates, and preferences. Book rooms, view        │  │
│  │  details, and manage cancellations.                       │  │
│  │                                                           │  │
│  │  4 actions available:                                     │  │
│  │                                                           │  │
│  │  ┌ search-availability ─────────────────────────────────┐ │  │
│  │  │ Search for available hotel rooms by city, dates,     │ │  │
│  │  │ number of guests, and optional filters.              │ │  │
│  │  │ Scopes: hotel:search | Auth: not required            │ │  │
│  │  │ Inputs: city, check_in, check_out, guests            │ │  │
│  │  └──────────────────────────────────────────────────────┘ │  │
│  │                                                           │  │
│  │  ┌ get-room-details ────────────────────────────────────┐ │  │
│  │  │ Get full details for a specific room.                │ │  │
│  │  │ Scopes: hotel:search | Auth: not required            │ │  │
│  │  │ Inputs: room_id                                      │ │  │
│  │  └──────────────────────────────────────────────────────┘ │  │
│  │                                                           │  │
│  │  ┌ book-room ───────────────────────────────────────────┐ │  │
│  │  │ Book a specific hotel room. Creates a confirmed      │ │  │
│  │  │ reservation with a financial commitment.             │ │  │
│  │  │ Scopes: hotel:book | Auth: ⚠ REQUIRED               │ │  │
│  │  │ Inputs: room_id, check_in, check_out, guest_name,   │ │  │
│  │  │         guest_email                                  │ │  │
│  │  └──────────────────────────────────────────────────────┘ │  │
│  │                                                           │  │
│  │  ┌ cancel-booking ─────────────────────────────────────┐  │  │
│  │  │ Cancel an existing hotel booking.                   │  │  │
│  │  │ Scopes: hotel:cancel | Auth: ⚠ REQUIRED            │  │  │
│  │  │ Inputs: booking_id                                  │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  │                                                           │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─── RAW MENU JSON ─────────────────────────────────────────┐  │
│  │  {                                                        │  │
│  │    "service_id": "stayright-hotels",               │  │
│  │    "name": "StayRight Hotels",                     │  │
│  │    "description": "Search and book hotel rooms...",       │  │
│  │    "actions": [ ... ]                                     │  │
│  │  }                                                        │  │
│  │                                          [ Expand JSON ]  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  [ Test Dry Run ] — simulate an agent calling each action       │
│  [ ← Edit Something ]                  [ Publish to Menu → ]   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Dry Run result (inline):**

```
┌───────────────────────────────────────────────────────────────┐
│  Dry Run Results                                              │
│                                                               │
│  ✓ search-availability    proxy mapping OK                    │
│  ✓ get-room-details       proxy mapping OK                    │
│  ✓ book-room              proxy mapping OK                    │
│  ✓ cancel-booking         proxy mapping OK                    │
│                                                               │
│  All 4 actions are correctly mapped to your backend.          │
│  Ready to publish!                                            │
└───────────────────────────────────────────────────────────────┘
```

---

## Screen 6: Publish & Confirmation

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  Step 6 of 6 — Publish                                          │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━●━━   │
│                                                                 │
│                                                                 │
│               [ Publish to Menu ]                               │
│                                                                 │
│                                                                 │
│  By publishing, you confirm:                                    │
│  • Your service will be discoverable by all agents on the Menu  │
│  • All requests will be proxied through AgentCafe               │
│  • You can pause or unpublish at any time from your dashboard   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**After clicking Publish:**

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│                    ✓ You're Live!                                │
│                                                                 │
│  StayRight Hotels is now on the AgentCafe Menu.          │
│  Agents can discover and use your service immediately.          │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                                                           │  │
│  │  What's next:                                             │  │
│  │                                                           │  │
│  │  → View your service on the Menu                          │  │
│  │  → Open your Company Dashboard                            │  │
│  │    (view request logs, edit settings, manage actions)      │  │
│  │  → Read the Company Guide                                 │  │
│  │                                                           │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  A confirmation email has been sent to                           │
│  api-team@stayright.example.com                                 │
│                                                                 │
│  ─────────────────────────────────────────────────────────────  │
│                                                                 │
│  Remember:                                                      │
│  ✓ You're in full control — pause or unpublish anytime          │
│  ✓ Every request is double-validated (Passport + your policy)   │
│  ✓ Your backend URL is never exposed to agents                  │
│  ✓ This is free — no charges, no commission                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Company Dashboard (Post-Onboarding)

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  AgentCafe — Company Dashboard                                  │
│  StayRight Hotels          Status: ● LIVE                │
│                                                                 │
│  ┌─── REQUEST ACTIVITY (last 24h) ───────────────────────────┐  │
│  │                                                           │  │
│  │  search-availability    ████████████████████  847 calls   │  │
│  │  get-room-details       ████████████          412 calls   │  │
│  │  book-room              ███                    89 calls   │  │
│  │  cancel-booking         █                      12 calls   │  │
│  │                                                           │  │
│  │  All requests double-validated. 0 policy violations.      │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─── QUICK ACTIONS ─────────────────────────────────────────┐  │
│  │                                                           │  │
│  │  [ Edit Menu Entry ]     [ View Request Logs ]            │  │
│  │  [ Edit Safety Policy ]  [ Pause Service ]                │  │
│  │  [ Unpublish ]           [ Rotate API Key ]               │  │
│  │                                                           │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```
