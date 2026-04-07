"""FixRight Home Services — demo backend (port 8003).

This is a realistic mock of what a home services marketplace would run behind their firewall.
AgentCafe proxies requests to this backend. Agents never see this server directly.
"""

from __future__ import annotations

import random
import string
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="FixRight Home Services — Internal Backend", version="1.0.0")

# ---------------------------------------------------------------------------
# In-memory data store (demo)
# ---------------------------------------------------------------------------

PROVIDERS = [
    {
        "provider_id": "fr-plumb-austin-047",
        "provider_name": "Mike's Reliable Plumbing",
        "service_type": "plumbing",
        "rating": 4.8,
        "review_count": 342,
        "service_fee": 89.00,
        "currency": "USD",
        "estimated_duration_minutes": 60,
        "license_verified": True,
        "insured": True,
    },
    {
        "provider_id": "fr-plumb-austin-112",
        "provider_name": "Austin Premier Plumbing",
        "service_type": "plumbing",
        "rating": 4.6,
        "review_count": 198,
        "service_fee": 120.00,
        "currency": "USD",
        "estimated_duration_minutes": 45,
        "license_verified": True,
        "insured": True,
    },
    {
        "provider_id": "fr-elec-austin-023",
        "provider_name": "BrightSpark Electric",
        "service_type": "electrical",
        "rating": 4.9,
        "review_count": 411,
        "service_fee": 110.00,
        "currency": "USD",
        "estimated_duration_minutes": 90,
        "license_verified": True,
        "insured": True,
    },
    {
        "provider_id": "fr-clean-austin-088",
        "provider_name": "Sparkle Clean Austin",
        "service_type": "cleaning",
        "rating": 4.7,
        "review_count": 567,
        "service_fee": 75.00,
        "currency": "USD",
        "estimated_duration_minutes": 120,
        "license_verified": True,
        "insured": True,
    },
]

# Active appointments (demo state)
appointments: dict[str, dict] = {}


def _generate_slots(base_date: str) -> list[str]:
    """Generate demo available time slots for a given date."""
    try:
        dt = datetime.fromisoformat(base_date)
    except ValueError:
        dt = datetime.now(timezone.utc)
    slots = []
    for hour in [9, 11, 13, 15, 17]:
        slot_dt = dt.replace(hour=hour, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        if slot_dt > datetime.now(timezone.utc):
            slots.append(slot_dt.isoformat().replace("+00:00", "Z"))
    # Always return at least 2 slots
    if len(slots) < 2:
        tomorrow = dt + timedelta(days=1)
        for hour in [9, 11, 13]:
            slot_dt = tomorrow.replace(hour=hour, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
            slots.append(slot_dt.isoformat().replace("+00:00", "Z"))
    return slots[:4]


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    service_type: str
    address: str
    preferred_date: str | None = None
    urgency: str | None = None
    max_service_fee: float | None = None
    problem_description: str | None = None


class BookRequest(BaseModel):
    provider_id: str
    appointment_time: str
    service_address: str
    contact_name: str
    contact_phone: str
    problem_description: str
    access_instructions: str | None = None


class RescheduleRequest(BaseModel):
    new_appointment_time: str
    reason: str | None = None


class CancelRequest(BaseModel):
    reason: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/providers/search")
async def search_providers(req: SearchRequest):
    """Search available service providers."""
    preferred_date = req.preferred_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    results = []
    for provider in PROVIDERS:
        # Filter by service type
        if provider["service_type"] != req.service_type:
            continue
        # Filter by max fee
        if req.max_service_fee and provider["service_fee"] > req.max_service_fee:
            continue

        slots = _generate_slots(preferred_date)
        results.append({
            **provider,
            "next_available": slots[0] if slots else None,
            "available_slots": slots,
        })

    return {"providers": results, "total_results": len(results)}


@app.post("/appointments")
async def book_appointment(req: BookRequest):
    """Book a service appointment."""
    # Verify provider exists
    provider = None
    for p in PROVIDERS:
        if p["provider_id"] == req.provider_id:
            provider = p
            break
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    now = datetime.now(timezone.utc)
    appointment_id = f"FR-{now.strftime('%Y%m%d')}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

    appointment = {
        "appointment_id": appointment_id,
        "status": "confirmed",
        "provider_name": provider["provider_name"],
        "service_type": provider["service_type"],
        "appointment_time": req.appointment_time,
        "estimated_duration_minutes": provider["estimated_duration_minutes"],
        "service_fee": provider["service_fee"],
        "currency": provider["currency"],
        "cancellation_policy": "Free cancellation up to 2 hours before appointment",
        "provider_phone": f"+1-512-555-{random.randint(1000,9999):04d}",
        "confirmation_sent": True,
    }
    appointments[appointment_id] = appointment
    return appointment


@app.post("/appointments/{appointment_id}/reschedule")
async def reschedule_appointment(appointment_id: str, req: RescheduleRequest):
    """Reschedule an existing appointment."""
    if appointment_id not in appointments:
        raise HTTPException(status_code=404, detail="Appointment not found")

    appointment = appointments[appointment_id]
    if appointment["status"] in ("completed", "cancelled"):
        raise HTTPException(status_code=422, detail=f"Cannot reschedule — appointment is {appointment['status']}")

    appointment["appointment_time"] = req.new_appointment_time
    appointment["status"] = "rescheduled"
    appointment["confirmation_sent"] = True
    return appointment


@app.post("/appointments/{appointment_id}/cancel")
async def cancel_appointment(appointment_id: str, _req: CancelRequest | None = None):
    """Cancel an appointment."""
    if appointment_id not in appointments:
        raise HTTPException(status_code=404, detail="Appointment not found")

    appointment = appointments[appointment_id]
    if appointment["status"] == "cancelled":
        raise HTTPException(status_code=422, detail="Appointment already cancelled")

    appointment["status"] = "cancelled"
    return {
        "appointment_id": appointment_id,
        "status": "cancelled",
        "refund_amount": appointment["service_fee"],
        "currency": appointment["currency"],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "fixright-home-services"}
