"""QuickBite Lunch Delivery — demo backend (port 8002).

This is a realistic mock of what a food delivery company would run behind their firewall.
AgentCafe proxies requests to this backend. Agents never see this server directly.
"""

from __future__ import annotations

import random
import string
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="QuickBite Lunch Delivery — Internal Backend", version="1.0.0")

# ---------------------------------------------------------------------------
# In-memory data store (demo)
# ---------------------------------------------------------------------------

MENU_ITEMS = [
    {
        "item_id": "qb-ceasar-lg-001",
        "restaurant_name": "Green Fork Kitchen",
        "item_name": "Chicken Caesar Salad",
        "description": "Grilled chicken breast, romaine, parmesan, house-made Caesar dressing, croutons",
        "price": 13.50,
        "currency": "USD",
        "estimated_delivery_minutes": 25,
        "dietary_tags": ["high-protein"],
        "rating": 4.7,
    },
    {
        "item_id": "qb-wrap-med-044",
        "restaurant_name": "Green Fork Kitchen",
        "item_name": "Mediterranean Veggie Wrap",
        "description": "Hummus, falafel, mixed greens, tomato, cucumber, tahini sauce",
        "price": 11.00,
        "currency": "USD",
        "estimated_delivery_minutes": 25,
        "dietary_tags": ["vegetarian", "vegan-option"],
        "rating": 4.5,
    },
    {
        "item_id": "qb-burger-cls-012",
        "restaurant_name": "Austin Burger Co",
        "item_name": "Classic Smash Burger",
        "description": "Double smash patties, American cheese, pickles, special sauce, brioche bun",
        "price": 14.00,
        "currency": "USD",
        "estimated_delivery_minutes": 30,
        "dietary_tags": [],
        "rating": 4.6,
    },
    {
        "item_id": "qb-poke-reg-077",
        "restaurant_name": "Aloha Poke Bar",
        "item_name": "Salmon Poke Bowl",
        "description": "Fresh salmon, sushi rice, avocado, edamame, seaweed salad, spicy mayo",
        "price": 15.50,
        "currency": "USD",
        "estimated_delivery_minutes": 20,
        "dietary_tags": ["high-protein", "gluten-free"],
        "rating": 4.8,
    },
]

# Active orders (demo state)
orders: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class MenuSearchRequest(BaseModel):
    delivery_address: str
    cuisine: str | None = None
    dietary: list[str] | None = None
    max_price: float | None = None
    max_delivery_minutes: int | None = None


class OrderItem(BaseModel):
    item_id: str
    quantity: int


class PlaceOrderRequest(BaseModel):
    items: list[OrderItem]
    delivery_address: str
    contact_name: str
    contact_phone: str
    delivery_instructions: str | None = None
    tip_amount: float | None = None


class CancelRequest(BaseModel):
    reason: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/menu/search")
async def browse_menu(req: MenuSearchRequest):
    """Search available lunch options."""
    results = []
    for item in MENU_ITEMS:
        # Apply price filter
        if req.max_price and item["price"] > req.max_price:
            continue
        # Apply delivery time filter
        if req.max_delivery_minutes and item["estimated_delivery_minutes"] > req.max_delivery_minutes:
            continue
        # Apply dietary filter
        if req.dietary:
            if not any(d in item["dietary_tags"] for d in req.dietary):
                continue
        results.append(item)
    return {"items": results, "total_results": len(results)}


@app.post("/orders")
async def place_order(req: PlaceOrderRequest):
    """Place a delivery order."""
    # Look up items and calculate totals
    order_items = []
    subtotal = 0.0
    restaurant_name = None
    for order_item in req.items:
        found = None
        for menu_item in MENU_ITEMS:
            if menu_item["item_id"] == order_item.item_id:
                found = menu_item
                break
        if found is None:
            raise HTTPException(status_code=400, detail=f"Item not found: {order_item.item_id}")
        restaurant_name = found["restaurant_name"]
        line_total = found["price"] * order_item.quantity
        subtotal += line_total
        order_items.append({
            "item_name": found["item_name"],
            "quantity": order_item.quantity,
            "price": found["price"],
        })

    subtotal = round(subtotal, 2)
    delivery_fee = 2.99
    tip = round(req.tip_amount or 0.0, 2)
    tax = round(subtotal * 0.0825, 2)  # Texas sales tax
    total = round(subtotal + delivery_fee + tip + tax, 2)

    now = datetime.now(timezone.utc)
    order_id = f"QB-{now.strftime('%Y%m%d')}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

    order = {
        "order_id": order_id,
        "status": "confirmed",
        "restaurant_name": restaurant_name,
        "items": order_items,
        "subtotal": subtotal,
        "delivery_fee": delivery_fee,
        "tip": tip,
        "tax": tax,
        "total": total,
        "currency": "USD",
        "estimated_delivery": (now + timedelta(minutes=25)).isoformat().replace("+00:00", "Z"),
        "tracking_available": True,
    }
    orders[order_id] = order
    return order


@app.get("/orders/{order_id}/status")
async def track_order(order_id: str):
    """Track order status."""
    if order_id not in orders:
        raise HTTPException(status_code=404, detail="Order not found")

    order = orders[order_id]
    # Simulate status progression based on order state
    status_map = {
        "confirmed": ("confirmed", "Order confirmed, preparing soon"),
        "preparing": ("preparing", "Your food is being prepared"),
        "in_transit": ("in_transit", "Your driver is on the way"),
        "delivered": ("delivered", "Order delivered"),
        "cancelled": ("cancelled", "Order was cancelled"),
    }
    status, message = status_map.get(order["status"], ("confirmed", "Processing"))

    return {
        "order_id": order_id,
        "status": status,
        "status_message": message,
        "estimated_arrival": order.get("estimated_delivery"),
        "driver_name": "Carlos M." if status == "in_transit" else None,
        "last_updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


@app.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, _req: CancelRequest | None = None):
    """Cancel a pending order."""
    if order_id not in orders:
        raise HTTPException(status_code=404, detail="Order not found")

    order = orders[order_id]
    if order["status"] in ("in_transit", "delivered"):
        raise HTTPException(status_code=422, detail="Order already in transit or delivered — cannot cancel")
    if order["status"] == "cancelled":
        raise HTTPException(status_code=422, detail="Order already cancelled")

    order["status"] = "cancelled"
    return {
        "order_id": order_id,
        "status": "cancelled",
        "refund_amount": order["total"],
        "currency": "USD",
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "quickbite-lunch-delivery"}
