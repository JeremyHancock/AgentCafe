"""StayRight Hotel Booking — demo backend (port 8001).

This is a realistic mock of what a hotel company would run behind their firewall.
AgentCafe proxies requests to this backend. Agents never see this server directly.
"""

from __future__ import annotations

import random
import string
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="StayRight Hotel Booking — Internal Backend", version="1.0.0")

# ---------------------------------------------------------------------------
# In-memory data store (demo)
# ---------------------------------------------------------------------------

ROOMS = [
    {
        "room_id": "sr-austin-k420",
        "hotel_name": "StayRight Austin Downtown",
        "address": "200 Congress Ave, Austin, TX 78701",
        "room_type": "King Suite",
        "description": "Spacious king suite with city views, work desk, and rainfall shower.",
        "price_per_night": 189.00,
        "currency": "USD",
        "amenities": ["wifi", "pool", "gym", "breakfast", "parking"],
        "cancellation_policy": "Free cancellation up to 24 hours before check-in",
        "rating": 4.5,
        "review_count": 1247,
    },
    {
        "room_id": "sr-austin-d212",
        "hotel_name": "StayRight Austin Downtown",
        "address": "200 Congress Ave, Austin, TX 78701",
        "room_type": "Double Queen",
        "description": "Comfortable room with two queen beds, great for families or friends.",
        "price_per_night": 149.00,
        "currency": "USD",
        "amenities": ["wifi", "gym"],
        "cancellation_policy": "Free cancellation up to 24 hours before check-in",
        "rating": 4.5,
        "review_count": 1247,
    },
    {
        "room_id": "sr-austin-s101",
        "hotel_name": "StayRight Austin South",
        "address": "500 S Lamar Blvd, Austin, TX 78704",
        "room_type": "Standard King",
        "description": "Clean, modern room with king bed near South Lamar dining and nightlife.",
        "price_per_night": 119.00,
        "currency": "USD",
        "amenities": ["wifi", "parking"],
        "cancellation_policy": "Free cancellation up to 48 hours before check-in",
        "rating": 4.2,
        "review_count": 589,
    },
]

# Active bookings (demo state)
bookings: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    city: str
    check_in: str
    check_out: str
    guests: int
    max_price_per_night: float | None = None
    amenities: list[str] | None = None


class BookRequest(BaseModel):
    room_id: str
    check_in: str
    check_out: str
    guest_name: str
    guest_email: str
    special_requests: str | None = None


class CancelRequest(BaseModel):
    reason: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/availability/search")
async def search_availability(req: SearchRequest):
    """Search available rooms — always returns Austin demo data."""
    results = []
    for room in ROOMS:
        # Apply max price filter
        if req.max_price_per_night and room["price_per_night"] > req.max_price_per_night:
            continue
        # Apply amenities filter
        if req.amenities:
            if not all(a in room["amenities"] for a in req.amenities):
                continue
        results.append({
            "room_id": room["room_id"],
            "hotel_name": room["hotel_name"],
            "room_type": room["room_type"],
            "price_per_night": room["price_per_night"],
            "currency": room["currency"],
            "amenities": room["amenities"],
            "rating": room["rating"],
            "available": True,
        })
    return {"results": results, "total_results": len(results)}


@app.get("/rooms/{room_id}")
async def get_room_details(room_id: str):
    """Get detailed room information."""
    for room in ROOMS:
        if room["room_id"] == room_id:
            return room
    raise HTTPException(status_code=404, detail="Room not found")


@app.post("/bookings")
async def book_room(req: BookRequest):
    """Book a room — creates a demo booking."""
    # Verify room exists
    room = None
    for r in ROOMS:
        if r["room_id"] == req.room_id:
            room = r
            break
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    # Calculate total
    check_in = datetime.fromisoformat(req.check_in)
    check_out = datetime.fromisoformat(req.check_out)
    nights = (check_out - check_in).days
    if nights <= 0:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    total_price = round(room["price_per_night"] * nights, 2)

    # Generate booking ID
    booking_id = f"BK-{datetime.now(timezone.utc).strftime('%Y-%m%d')}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

    cancellation_deadline = (check_in - timedelta(days=1)).isoformat() + "Z"

    booking = {
        "booking_id": booking_id,
        "status": "confirmed",
        "hotel_name": room["hotel_name"],
        "room_type": room["room_type"],
        "check_in": req.check_in,
        "check_out": req.check_out,
        "total_price": total_price,
        "currency": "USD",
        "cancellation_deadline": cancellation_deadline,
        "confirmation_email_sent": True,
    }
    bookings[booking_id] = booking
    return booking


@app.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: str, _req: CancelRequest | None = None):
    """Cancel a booking."""
    if booking_id not in bookings:
        raise HTTPException(status_code=404, detail="Booking not found")

    booking = bookings[booking_id]
    if booking["status"] == "cancelled":
        raise HTTPException(status_code=422, detail="Booking already cancelled")

    booking["status"] = "cancelled"
    return {
        "booking_id": booking_id,
        "status": "cancelled",
        "refund_amount": booking["total_price"],
        "currency": "USD",
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "stayright-hotel-booking"}
