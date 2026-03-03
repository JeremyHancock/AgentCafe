"""OpenAI GPT function-calling agent for AgentCafe.

This script shows how a GPT-4 agent can discover services on AgentCafe,
register for a Passport, and place orders — all via function calling.

Usage:
    export OPENAI_API_KEY=sk-...
    python examples/openai_agent.py

    # Or with a custom prompt:
    python examples/openai_agent.py "Find me a hotel in Austin for March 15-18"
"""

from __future__ import annotations

import json
import os
import sys

import httpx
from openai import OpenAI

CAFE_BASE_URL = os.getenv("AGENTCAFE_URL", "https://agentcafe.io")

# --- AgentCafe tools for GPT function calling ---

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "browse_menu",
            "description": (
                "Browse the AgentCafe Menu to discover available services and their actions. "
                "Returns a list of services with their capabilities, required inputs, and scopes."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_passport",
            "description": (
                "Register for a Tier-1 (read-only) Passport. This is required before "
                "placing any orders. Returns a JWT token valid for ~1 hour."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_order",
            "description": (
                "Place an order through AgentCafe. Requires a valid Passport token. "
                "For read actions, a Tier-1 Passport is sufficient. "
                "For write actions, a Tier-2 Passport (with human consent) is needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {
                        "type": "string",
                        "description": "The service_id from the Menu (e.g., 'stayright-hotels')",
                    },
                    "action_id": {
                        "type": "string",
                        "description": "The action_id from the Menu (e.g., 'search-availability')",
                    },
                    "passport": {
                        "type": "string",
                        "description": "The Passport JWT token from register_passport",
                    },
                    "inputs": {
                        "type": "object",
                        "description": "The required inputs for this action, as defined in the Menu",
                    },
                },
                "required": ["service_id", "action_id", "passport", "inputs"],
            },
        },
    },
]


# --- Tool execution ---

def execute_tool(name: str, args: dict) -> str:
    """Execute an AgentCafe tool and return the result as a string."""
    with httpx.Client(base_url=CAFE_BASE_URL, timeout=30.0) as client:
        if name == "browse_menu":
            resp = client.get("/cafe/menu")
            return json.dumps(resp.json(), indent=2)

        if name == "register_passport":
            resp = client.post(
                "/passport/register",
                json={"agent_tag": "openai-gpt-agent"},
            )
            return json.dumps(resp.json(), indent=2)

        if name == "place_order":
            resp = client.post("/cafe/order", json=args)
            return json.dumps(resp.json(), indent=2)

    return json.dumps({"error": f"Unknown tool: {name}"})


# --- Agent loop ---

SYSTEM_PROMPT = """\
You are a helpful assistant that can use AgentCafe to access real-world services \
on behalf of the user.

Workflow:
1. Call browse_menu to see what services are available.
2. Call register_passport to get a Tier-1 (read) token.
3. Call place_order with the token and the right inputs to fulfill the user's request.

Always browse the menu first to discover the exact service_id, action_id, and \
required_inputs before placing an order. Use the Passport token from step 2.\
"""


def run_agent(user_message: str) -> None:
    """Run the GPT agent loop with tool calling."""
    client = OpenAI()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    print(f"\n{'='*60}")
    print(f"User: {user_message}")
    print(f"{'='*60}\n")

    for _ in range(10):  # max 10 turns to prevent infinite loops
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        message = response.choices[0].message

        # If the model wants to call tools
        if message.tool_calls:
            messages.append(message)
            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)
                print(f"  [Tool] {fn_name}({json.dumps(fn_args)[:100]}...)")

                result = execute_tool(fn_name, fn_args)
                print(f"  [Result] {result[:150]}...\n")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })
            continue

        # Final text response
        print(f"Assistant: {message.content}\n")
        break


if __name__ == "__main__":
    if not os.getenv("OPENAI_API_KEY"):
        print("Error: Set OPENAI_API_KEY environment variable")
        sys.exit(1)

    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "Search for hotels in Austin, TX for March 15-18, 2026 for 2 guests"
    )
    run_agent(prompt)
