# HotelBookingService — Why a Hotel Company Would Join AgentCafe

## The Business Case

Hotels and OTAs (Online Travel Agencies) already spend billions on distribution — OTA commissions, metasearch ads, direct booking campaigns. The next wave of bookings will come from **AI agents acting on behalf of travelers**.

When a user tells their agent "Book me a hotel in Austin for SXSW, under $250/night, close to the convention center," that agent needs a way to search availability, compare options, and book — without the user manually visiting five websites.

## Why AgentCafe Specifically?

- **Zero integration cost**: Upload an existing OpenAPI spec, answer a few questions, publish. No SDK to build, no agent-specific API to maintain.
- **New distribution channel**: Every agent that browses the Cafe Menu can discover your hotel. You're visible to millions of agents without marketing spend.
- **Full control**: You define exactly what agents can do (search only? book? cancel?), set your own scopes, require human authorization for high-value actions, and set rate limits.
- **Maximum safety**: All requests come through AgentCafe's secure proxy. You never expose backend URLs to agents. Every request is double-validated (human Passport + your company policy).
- **Completely free**: No listing fee, no commission on the Cafe side. You keep your existing pricing and revenue model.

## What They Register

| Action | Why |
|--------|-----|
| `search_availability` | Let agents discover your rooms — this is free demand |
| `get_room_details` | Give agents rich info to help users decide |
| `book_room` | Convert agent intent into revenue (human authorization required) |
| `cancel_booking` | Reduce support costs by letting agents handle cancellations programmatically |

## The Bottom Line

A hotel that joins AgentCafe gets a free, safe, zero-effort distribution channel into the emerging agent economy. They lose nothing and gain access to every agent that walks into the Cafe.
