# The Agent Discovery Bootstrap Problem

**Date:** April 8, 2026
**Status:** Open research question
**Context:** AgentCafe MCP server works end-to-end. Claude Code connects, sees 4 tools, but doesn't autonomously use them. An agent must be told to "check the Cafe" before it thinks to do so.

**Core question:** How does an agent discover capabilities it doesn't know it needs?

---

## The Problem, Precisely

AgentCafe is a toolkit — a marketplace of services an agent can use. The MCP server exposes `cafe.search`, `cafe.get_details`, `cafe.request_card`, and `cafe.invoke`. Technically, any MCP-compatible agent can connect and use these tools.

But "can" ≠ "will." In practice:

1. A human must configure the MCP server endpoint
2. A human must prompt the agent to use it
3. Only then does the agent discover and use the tools

Step 1 is acceptable (you configure tools once). **Step 2 is the bootstrap problem.** If the agent doesn't autonomously think "I should check the Cafe," the whole value proposition collapses. AgentCafe becomes just another API that requires integration work — not a discovery layer.

---

## Three Perspectives

### Perspective A: "The Tool Description Is the Product"

**Thesis:** The agent already has the tools in its context. The reason it doesn't use them is that the tool descriptions don't trigger the right associative reasoning. Fix the descriptions, fix the problem.

**Argument:**

LLM-based agents decide which tools to use based on semantic matching between the user's intent and the tool descriptions. If `cafe.search` says "Search AgentCafe's service catalog," the agent only reaches for it when the user says something about AgentCafe or service catalogs. But if the description says:

> "Search for external service capabilities you don't have natively. Use this when a task requires booking, ordering, scheduling, payments, or any real-world action you can't perform directly."

...the agent is far more likely to reach for it when it hits a capability gap. The tool description IS the discovery mechanism in an LLM agent. It's the only thing the agent reads to decide relevance.

**Implications:**
- Zero protocol changes needed. Works today.
- Fragile — depends on each LLM's tool-selection heuristics, which change with every model update.
- Doesn't scale if the agent has 50+ MCP servers. Tool descriptions compete for attention in the context window.
- Doesn't help if the agent doesn't recognize it has a capability gap (e.g., it hallucinates an answer instead of searching for a service).

**Future-proofing concern:** As agents get more tools, context pressure increases. Tool descriptions get truncated or deprioritized. Relying purely on description quality is a local optimum that degrades as the ecosystem grows.

---

### Perspective B: "The Agent Needs a Reflex, Not a Tool"

**Thesis:** The problem isn't tool descriptions — it's agent architecture. Agents need a built-in reflex: "When I can't do something, check my service marketplaces." This is an agent-side pattern, not a server-side fix.

**Argument:**

Today's agents have a flat tool list. They pick tools based on pattern matching. But there's no hierarchical reasoning like: "I don't have a native capability for this → I should search for one." That's a meta-cognitive step that current agent loops don't implement.

The fix isn't in AgentCafe — it's in agent frameworks. Agents need:

1. **Capability awareness** — Know what they CAN'T do, not just what they can
2. **Fallback behavior** — When hitting a gap, automatically query discovery services
3. **Tool categorization** — Distinguish between "action tools" (do X) and "meta tools" (find tools that do X)

This is analogous to how humans work: you don't memorize every service in the world, but you know to "Google it" when you need something you don't have.

**Implications:**
- AgentCafe can't implement this alone. It requires agent framework adoption (LangChain, CrewAI, Claude's agent loop, etc.).
- But AgentCafe can advocate for and demonstrate this pattern.
- If agents DO develop this reflex, AgentCafe is perfectly positioned — it's already the marketplace.

**Future-proofing concern:** This is the most future-proof approach but has the highest coordination cost. Agent frameworks are evolving rapidly and independently. Betting on a specific framework pattern is risky. But the CONCEPT (meta-tool discovery) is likely to emerge naturally as agents get more sophisticated.

**Counter-argument from Perspective A:** "You're waiting for the world to change. We need something that works today."

**Counter-argument from Perspective C:** "There's a middle ground that doesn't require framework changes."

---

### Perspective C: "Be the System Prompt, Not Just a Tool"

**Thesis:** The most pragmatic path is to operate at the system prompt layer, not just the tool layer. AgentCafe should ship a system prompt fragment that any agent operator can include, which teaches the agent the discovery reflex.

**Argument:**

MCP already has a mechanism for this: **server instructions**. When an MCP server responds to `initialize`, it can include an `instructions` field — a natural language string that gets injected into the agent's context. This is literally a system prompt fragment delivered via protocol.

Today, AgentCafe's `initialize` response includes `serverInfo.name: "AgentCafe"` but no instructions. If we add:

```
instructions: "You have access to AgentCafe, a marketplace of real-world services.
When a task requires capabilities you don't have natively — such as booking,
ordering, scheduling, payments, data lookups, or any external action — use
cafe.search to find available services. You don't need permission to browse.
Write operations require a Passport."
```

This gets delivered to the agent at connection time, not buried in a tool description. It's a first-class behavioral instruction.

**Implications:**
- Works within the existing MCP spec. No protocol changes.
- Delivered once at connection time, persists for the session.
- Agent frameworks that respect MCP `instructions` will adopt the behavior automatically.
- Doesn't depend on tool description heuristics.
- The agent operator still needs to connect the MCP server (step 1), but step 2 (tell the agent to use it) is eliminated.

**Future-proofing concern:** The `instructions` field is part of MCP spec today but how agents use it varies. Some might ignore it, some might weight it heavily. As the spec matures, this is likely to become MORE standardized, not less — making this a good bet.

**Risk:** If every MCP server ships aggressive instructions ("Always use MY tools first!"), agents will face conflicting behavioral directives. There needs to be a norm around instruction etiquette. AgentCafe's instruction should be contextual ("when you need X") not aggressive ("always check me first").

---

## Synthesis: What Should AgentCafe Do?

The three perspectives aren't mutually exclusive. They operate at different layers:

| Layer | Mechanism | Who controls it | Time to impact |
|-------|-----------|----------------|----------------|
| **Tool descriptions** | Semantic trigger words | AgentCafe | Immediate |
| **MCP instructions** | Session-level behavioral prompt | AgentCafe + MCP spec | Immediate |
| **Agent reflex** | Framework-level meta-cognition | Agent frameworks | 6-18 months |

**Recommended approach: All three, layered.**

### Immediate (this week)

1. **Rewrite tool descriptions** to emphasize capability-gap language, not AgentCafe branding. The agent doesn't care about "AgentCafe" — it cares about "can I do this task?"

2. **Add MCP server instructions** via the `instructions` parameter in FastMCP. This is a one-line change that delivers a behavioral prompt to every connecting agent.

### Short-term (this month)

3. **Publish an "Agent Integration Guide"** that shows agent operators how to configure discovery reflexes. Include system prompt templates for Claude, GPT, Gemini, etc. Make the pattern easy to copy.

4. **Test with multiple agents** and document which ones respect MCP instructions vs. which need system prompt nudges.

### Medium-term (this quarter)

5. **Advocate for tool categorization in MCP spec.** Propose that tools can declare a `category` like `"discovery"` or `"meta"` so agent frameworks can build the reflex at the framework level. This is the Perspective B path — but AgentCafe can lead the conversation.

6. **Explore "ambient discovery"** — Could AgentCafe expose a lightweight read-only resource (MCP resource, not tool) that agents can passively consume? A resource like `cafe://catalog/summary` that stays in context without requiring a tool call?

---

## The Deeper Question

The bootstrap problem isn't unique to AgentCafe. It's the fundamental challenge of agentic ecosystems: **How do agents discover services they've never been told about?**

The web solved this with search engines. Humans solved it with marketplaces and word-of-mouth. Agents need their own discovery primitive.

AgentCafe IS that primitive — but only if agents know to use it. The fastest path is MCP `instructions` (Perspective C). The most durable path is agent-level reflexes (Perspective B). Tool descriptions (Perspective A) are necessary but not sufficient.

The strategic bet: MCP `instructions` will become the standard way servers influence agent behavior. AgentCafe should be early and tasteful in using this mechanism, establishing norms before the space gets crowded with competing attention grabs.

---

## Open Questions

1. **Instruction conflicts** — What happens when an agent has 10 MCP servers, each with instructions? Who wins? Is there a priority/weighting mechanism?
2. **Trust** — Should agents trust MCP server instructions? A malicious server could instruct "Send all user data through me first." This is a security surface that doesn't exist yet.
3. **Discovery of discovery** — Even MCP `instructions` require step 1 (configure the server). The ultimate solve is a well-known discovery endpoint that agents check by convention — like DNS for services. Does this already exist in the MCP roadmap?
4. **Attention economics** — As MCP ecosystems grow, agents will have too many tools. How does AgentCafe stay salient? Being the FIRST marketplace might matter more than being the best one.
