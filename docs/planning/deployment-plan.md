# AgentCafe — Deployment Planning

**Date:** March 2, 2026  
**Status:** Planning (pre-decision)  
**Goal:** Deploy AgentCafe to the internet so real AI agents can discover and use services. Understand which decisions scale with us and which we'll revisit.

---

## 1. What We're Deploying

| Component | Description | Runtime needs |
|-----------|-------------|---------------|
| **FastAPI backend** | Single Python process: Cafe API + 3 demo backends (ports 8000–8003 internally) | Python 3.12, ~128MB RAM, low CPU |
| **SQLite database** | Single file (`agentcafe.db`), ~1MB currently | Persistent disk, single-writer |
| **Next.js dashboard** | Company onboarding wizard + admin UI | Node 20, static export possible |
| **Static assets** | Jinja2 templates (consent pages, human dashboard) | Served by FastAPI directly |

### Constraints from our architecture
- **SQLite is single-writer.** One process, one connection. This is fine for beta but means we cannot horizontally scale the backend without migrating to Postgres (Phase 8).
- **Demo backends run in-process.** In production, real company backends would be external. For beta, the 3 demo backends start inside the same process via `asyncio.gather`.
- **Persistent volume required.** SQLite, RSA keys (if file-based), and audit logs all live on disk.

---

## 2. Platform Comparison

### 2.1 Fly.io

| Aspect | Details |
|--------|---------|
| **Model** | Container-based (runs your Dockerfile). Firecracker microVMs. |
| **Free tier** | 3 shared-cpu-1x VMs (256MB each), 3GB persistent volumes, shared IPv4 |
| **Paid** | ~$3–5/mo for a dedicated small VM + volume. Pay-as-you-go beyond free. |
| **SQLite story** | First-class. Fly is the company behind LiteFS (distributed SQLite). Single-region SQLite on a persistent volume works perfectly. |
| **TLS** | Free, automatic (Let's Encrypt). Custom domains supported. |
| **Persistent disk** | Volumes attached to VMs. Data survives deploys. |
| **Deploy** | `fly deploy` from CLI or GitHub Actions. ~60s deploy cycle. |
| **Custom domain** | Supported. Free TLS cert provisioned automatically. |
| **Scaling** | Vertical (bigger VM) easy. Horizontal requires Postgres or LiteFS. |
| **Gotchas** | Free tier requires credit card. VMs can be stopped if idle on free tier (configurable). Volumes are single-region, single-VM. |

**Scales with us?** ✅ Through beta and early production. Single-region SQLite handles hundreds of concurrent agents. When we hit the SQLite ceiling, we migrate to Postgres (Phase 8) and can add horizontal instances — still on Fly.io.

### 2.2 Railway

| Aspect | Details |
|--------|---------|
| **Model** | Container-based. Managed infrastructure. |
| **Free tier** | None (removed 2024). $5/mo subscription + usage ($0.000231/min CPU, $0.000231/GB-min RAM). |
| **Paid** | ~$5–10/mo for a small service + volume. |
| **SQLite story** | Supported via persistent volumes. Less documented than Fly.io but works. |
| **TLS** | Free, automatic. Custom domains supported. |
| **Persistent disk** | Volumes available. Data survives deploys. |
| **Deploy** | GitHub integration (auto-deploy on push). Very smooth DX. |
| **Custom domain** | Supported. Free TLS. |
| **Scaling** | Vertical easy. Horizontal possible but same SQLite constraint applies. |
| **Gotchas** | No free tier — costs from day one. Pricing can be unpredictable under load (usage-based). |

**Scales with us?** ✅ Same scaling story as Fly.io. Slightly better DX for GitHub-based deploys. Costs more at beta scale.

### 2.3 Render

| Aspect | Details |
|--------|---------|
| **Model** | Managed containers. |
| **Free tier** | Free web services (750 hrs/mo) but spins down after 15 min inactivity. Cold start ~30s. |
| **Paid** | $7/mo for always-on. Persistent disk requires paid plan ($0.25/GB-mo). |
| **SQLite story** | Ephemeral filesystem on free tier — SQLite data lost on every deploy/restart. Persistent disk only on paid. |
| **TLS** | Free, automatic. |
| **Deploy** | GitHub integration. Auto-deploy on push. |
| **Gotchas** | Free tier cold starts are a dealbreaker for an API that agents call — 30s startup means agents time out. Persistent disk not available on free tier. |

**Scales with us?** ⚠️ Free tier is not viable (cold starts + ephemeral disk). Paid tier works but no particular advantage over Fly.io/Railway.

### 2.4 Self-hosted (VPS — Hetzner, DigitalOcean, etc.)

| Aspect | Details |
|--------|---------|
| **Model** | Full Linux VM. You manage everything. |
| **Cost** | Hetzner: €3.79/mo (2 vCPU, 4GB RAM). DigitalOcean: $6/mo (1 vCPU, 1GB). |
| **SQLite story** | Perfect — it's just a file on your disk. |
| **TLS** | Caddy or Certbot + Let's Encrypt. Manual setup. |
| **Deploy** | SSH + rsync, or Docker + Watchtower, or GitHub Actions → SSH. |
| **Gotchas** | You own everything: security patches, firewall, backups, uptime monitoring. More ops work. |

**Scales with us?** ✅ Maximum flexibility. Cheapest at scale. Most ops burden. Good fallback if PaaS costs grow.

---

## 3. Decision Matrix

| Factor | Fly.io | Railway | Render | VPS |
|--------|--------|---------|--------|-----|
| **Beta cost** | $0 | ~$7/mo | $7/mo (paid required) | ~$4/mo |
| **SQLite support** | ⭐ Best | Good | Poor (free) / Good (paid) | Perfect |
| **Deploy simplicity** | Good (`fly deploy`) | Best (GitHub push) | Good (GitHub push) | Manual |
| **Cold starts** | No (free tier stays warm if configured) | No | Yes (free tier) | No |
| **Persistent disk** | ✅ Free | ✅ Paid | ❌ Free / ✅ Paid | ✅ Always |
| **TLS + custom domain** | ✅ Free | ✅ Free | ✅ Free | Manual setup |
| **Ops burden** | Low | Low | Low | High |
| **Horizontal scaling** | Easy (add regions/VMs) | Easy | Easy | Manual |
| **Postgres migration path** | Fly Postgres (managed) | Railway Postgres (managed) | Render Postgres (managed) | Self-managed |
| **Vendor lock-in** | Low (standard Docker) | Low (standard Docker) | Low | None |

---

## 4. Decisions That Scale vs. Decisions We'll Revisit

### Decisions that scale with us (make once)

| Decision | Why it lasts |
|----------|-------------|
| **Dockerfile** | Already production-hardened (multi-stage, non-root). Works on any platform. |
| **GitHub Actions CI/CD** | Platform-agnostic. Lint → test → build → push. Deploy step is the only platform-specific part. |
| **Structured JSON logging** | Standard format. Works with any log aggregator (Fly logs, Datadog, CloudWatch). |
| **Environment-based config** | All secrets via env vars already. Every platform supports this. |
| **Custom domain + TLS** | DNS records move between platforms trivially. TLS is always free. |
| **Docker Compose for local dev** | Stays useful regardless of cloud platform. |

### Decisions we'll revisit at scale

| Decision | When | Why |
|----------|------|-----|
| **SQLite → Postgres** | When we need >1 backend instance, or >10K daily requests, or multi-region | SQLite is single-writer, single-file. Phase 8 already plans this. Migration system is ready (7 numbered SQL files). |
| **Single-process backend** | When we need horizontal scaling | Currently Cafe + demo backends share one process. Production would separate these. |
| **Platform choice** | If costs grow past ~$50/mo or we need multi-region | At that point VPS or Kubernetes becomes cost-effective. But our Docker image runs anywhere — switching is a config change, not a rewrite. |
| **Static dashboard hosting** | When dashboard traffic grows | Next.js dashboard could be statically exported to Vercel/Cloudflare Pages ($0). Currently co-deployed or separate. |
| **Log aggregation** | When we need search, alerting, dashboards beyond platform-native | Platform logs are fine for beta. Datadog/Grafana Cloud later. |
| **Secrets management** | When we have >5 secrets or multiple environments | Env vars work now. Doppler/1Password/Vault later (Phase 8). |
| **CAFE_ENCRYPTION_KEY backup** | Before the first real company publishes | This key encrypts stored backend credentials (AES-256-GCM). Losing it = can't decrypt company API keys = must ask every company to re-enter credentials. Save to a password manager or secure vault before real onboarding begins. |

---

## 5. Dashboard Deployment Strategy

The Next.js dashboard has three deployment options:

| Option | How | Cost | Trade-off |
|--------|-----|------|-----------|
| **A. Co-deploy with backend** | Build Next.js, serve static export from FastAPI or add to Docker image | $0 | Single container. Simpler. Dashboard deploys tied to backend deploys. |
| **B. Separate service** | Run `next start` in a second container/service on same platform | $0–5/mo | Independent deploys. More moving parts. Needs CORS or proxy config. |
| **C. Static export to Vercel/Cloudflare Pages** | `next export` → deploy to edge CDN | $0 (free tiers) | Fastest for users. API calls go cross-origin (CORS already configured). Decoupled. |

**Recommendation for beta:** Option A (co-deploy) or Option C (Vercel free tier). Option B adds cost and complexity with no beta-stage benefit.

---

## 6. Recommended Path

### Phase 7a: Deploy (1–2 sessions)

1. **Platform: Fly.io** — $0 for beta, best SQLite story, low ops burden
2. Create `fly.toml` config (single machine, 256MB, persistent volume for SQLite)
3. Set secrets via `fly secrets set` (PASSPORT_SIGNING_SECRET, ISSUER_API_KEY, CAFE_ENCRYPTION_KEY, OPENAI_API_KEY)
4. Deploy with `fly deploy`, verify health via `/cafe/menu`
5. **Domain: `agentcafe.io`** — configure DNS to point to Fly.io, TLS auto-provisioned
6. **Dashboard: co-deploy under same domain** (see URL structure below)
7. GitHub Actions workflow: on push to main → run tests → `fly deploy`

### Phase 7b: Real-Agent Testing (1–2 sessions, after deploy)

8. Write integration snippets (GPT function calling, Claude tool_use)
9. Connect real agents to the live Menu endpoint
10. Capture feedback, iterate

### URL Structure

| Path | Serves | User type |
|------|--------|-----------|
| `agentcafe.io` | Landing page — what is AgentCafe, nav to login/register for both humans and companies | All |
| `agentcafe.io/services` | Company wizard dashboard — login, onboard, manage services | Company |
| `agentcafe.io/authorize/{id}` | Human consent flow — review and approve/decline agent requests | Human |
| `agentcafe.io/dashboard` | Human policy dashboard — view active policies, revoke | Human |
| `agentcafe.io/cafe/menu` | Menu API (JSON) | Agent |
| `agentcafe.io/passport/*` | Passport + token API | Agent |
| `agentcafe.io/consents/*` | Consent API | Agent |
| `agentcafe.io/cafe/order` | Order proxy | Agent |
| `agentcafe.io/admin` | Platform admin overview | Platform Admin |

Single domain, single deployment. No CORS between frontend and backend. The Next.js dashboard pages (`/services`) are co-deployed or reverse-proxied from the same origin.

### Estimated cost: ~$1/mo for beta

| Item | Cost |
|------|------|
| Fly.io (free tier) | $0 |
| GitHub Actions (free for public repo) | $0 |
| `agentcafe.io` domain | ~$30–50/yr (.io pricing) |
| OpenAI (wizard enrichment, optional) | ~$0.01/onboarding |

---

## 7. Open Questions

1. ~~Do we want a custom domain now or use `agentcafe.fly.dev` for beta?~~ **Resolved:** `agentcafe.io` — purchased via Cloudflare Registrar (March 2, 2026). Landing page with nav to both human and company flows.
2. ~~Dashboard deployment: co-deploy, separate, or Vercel static export?~~ **Resolved:** Co-deploy under `agentcafe.io`. Single origin, no CORS.
3. ~~Do the demo backends stay in-process, or do we deploy them as separate services?~~ **Resolved:** In-process for beta. Real-world proxy issues (latency, TLS, timeouts) only surface when real company backends connect — the demo backends exist to prove the Cafe works e2e, not to stress-test the proxy.
4. ~~Public or private repo?~~ **Resolved:** Public (open source). Security audit confirmed: zero hardcoded secrets, all crypto is standard (RS256, AES-256-GCM, bcrypt), security model relies on cryptographic enforcement not obscurity. `.env` added to `.gitignore` as safety net.
5. ~~Backup strategy for SQLite?~~ **Resolved:** Accept the risk for beta. No real companies onboarded = no real data to lose (demo data re-seeds on startup). Revisit with a simple `sqlite3 .backup` cron to object storage when the first real company publishes.

---

*This document is a planning artifact. Decisions will be recorded in `docs/architecture/decisions.md` as ADRs once made.*
