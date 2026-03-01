"use client";

import { useEffect, useState, useCallback } from "react";
import type { MenuService } from "@/lib/api";

const ADMIN_KEY_STORAGE = "agentcafe_admin_key";

interface AuditEntry {
  timestamp: string;
  service_id: string;
  action_id: string;
  outcome: string;
  response_code: number | null;
  latency_ms: number | null;
}

interface AdminStats {
  total_requests: number;
  recent_requests_24h: number;
  failed_requests: number;
  per_service: Record<string, { total: number; success: number; errors: number }>;
}

interface AdminData {
  services: MenuService[];
  stats: AdminStats;
  recent_audit: AuditEntry[];
}

export default function AdminPage() {
  const [adminKey, setAdminKey] = useState("");
  const [keyInput, setKeyInput] = useState("");
  const [data, setData] = useState<AdminData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expandedService, setExpandedService] = useState<string | null>(null);
  const [showAuditLog, setShowAuditLog] = useState(false);

  useEffect(() => {
    const stored = sessionStorage.getItem(ADMIN_KEY_STORAGE);
    if (stored) {
      setAdminKey(stored);
    }
  }, []);

  const loadData = useCallback(async (key: string) => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`/api/cafe/admin/overview?api_key=${encodeURIComponent(key)}`);
      if (res.status === 403) {
        sessionStorage.removeItem(ADMIN_KEY_STORAGE);
        setAdminKey("");
        setError("Invalid admin key.");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      setData(d);
      sessionStorage.setItem(ADMIN_KEY_STORAGE, key);
      setAdminKey(key);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load admin data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (adminKey) loadData(adminKey);
  }, [adminKey, loadData]);

  function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    if (keyInput.trim()) loadData(keyInput.trim());
  }

  function handleLogout() {
    sessionStorage.removeItem(ADMIN_KEY_STORAGE);
    setAdminKey("");
    setData(null);
    setKeyInput("");
  }

  // --- Login gate ---
  if (!adminKey || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-full max-w-sm space-y-6">
          <div className="text-center space-y-2">
            <h1 className="text-2xl font-bold">☕ AgentCafe Admin</h1>
            <p className="text-sm text-[var(--muted-foreground)]">Platform operator access only</p>
          </div>
          <form onSubmit={handleLogin} className="rounded-xl border bg-[var(--card)] p-6 space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1.5">Admin Key</label>
              <input
                type="password"
                value={keyInput}
                onChange={e => setKeyInput(e.target.value)}
                placeholder="Enter ISSUER_API_KEY"
                className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--ring)]"
                autoFocus
              />
            </div>
            {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
            {loading && <p className="text-sm text-[var(--muted-foreground)]">Verifying...</p>}
            <button type="submit" disabled={loading || !keyInput.trim()}
              className="w-full rounded-lg bg-[var(--primary)] px-4 py-2.5 text-sm font-medium text-[var(--primary-foreground)] hover:opacity-90 disabled:opacity-50 transition-opacity">
              Sign in
            </button>
          </form>
        </div>
      </div>
    );
  }

  // --- Admin dashboard ---
  const services = data.services;
  const stats = data.stats;

  function isQuarantined(svc: MenuService): boolean {
    return svc.actions.some(a => {
      const q = a.security_status?.quarantine_until;
      return q ? new Date(q) > new Date() : false;
    });
  }

  function isSuspended(svc: MenuService): boolean {
    return svc.actions.some(a => !!a.security_status?.suspended_at);
  }

  function quarantineDate(svc: MenuService): string | null {
    for (const a of svc.actions) {
      const q = a.security_status?.quarantine_until;
      if (q && new Date(q) > new Date()) return q;
    }
    return null;
  }

  const totalActions = services.reduce((sum, s) => sum + s.actions.length, 0);
  const quarantinedCount = services.filter(isQuarantined).length;
  const suspendedCount = services.filter(isSuspended).length;

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b bg-[var(--card)]">
        <div className="mx-auto max-w-6xl flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <span className="text-xl font-bold">☕ AgentCafe</span>
            <span className="text-sm text-[var(--muted-foreground)]">Platform Admin</span>
          </div>
          <div className="flex items-center gap-4">
            <button onClick={() => loadData(adminKey)} className="text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition-colors">
              Refresh
            </button>
            <button onClick={handleLogout} className="text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition-colors">
              Sign out
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-8 space-y-6">
        {/* Stats row */}
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
          <StatCard label="Services" value={services.length} />
          <StatCard label="Actions" value={totalActions} />
          <StatCard label="Total Requests" value={stats.total_requests} />
          <StatCard label="Last 24h" value={stats.recent_requests_24h} />
          <StatCard label="Quarantined" value={quarantinedCount} color={quarantinedCount > 0 ? "yellow" : undefined} />
          <StatCard label="Suspended" value={suspendedCount} color={suspendedCount > 0 ? "red" : undefined} />
        </div>

        {/* Services */}
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-[var(--muted-foreground)] uppercase tracking-wider">All Services</h2>
          {services.map(svc => {
            const quarantined = isQuarantined(svc);
            const suspended = isSuspended(svc);
            const qDate = quarantineDate(svc);
            const expanded = expandedService === svc.service_id;
            const svcStats = stats.per_service[svc.service_id];

            return (
              <div key={svc.service_id} className="rounded-xl border bg-[var(--card)] overflow-hidden">
                <button
                  onClick={() => setExpandedService(expanded ? null : svc.service_id)}
                  className="w-full text-left p-5 hover:bg-[var(--muted)]/30 transition-colors"
                >
                  <div className="flex items-center justify-between">
                    <div className="space-y-1.5">
                      <div className="flex items-center gap-3 flex-wrap">
                        <span className="font-semibold">{svc.name}</span>
                        <code className="text-xs bg-[var(--muted)] px-2 py-0.5 rounded">{svc.service_id}</code>
                        {quarantined && (
                          <span className="text-xs bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400 px-2 py-0.5 rounded font-medium">
                            Quarantine
                          </span>
                        )}
                        {suspended && (
                          <span className="text-xs bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 px-2 py-0.5 rounded font-medium">
                            Suspended
                          </span>
                        )}
                        {!quarantined && !suspended && (
                          <span className="text-xs bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 px-2 py-0.5 rounded font-medium">
                            Live
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-[var(--muted-foreground)] line-clamp-1">{svc.description}</p>
                    </div>
                    <div className="flex items-center gap-4 ml-4 flex-shrink-0 text-xs text-[var(--muted-foreground)]">
                      <span>{svc.actions.length} actions</span>
                      {svcStats && <span>{svcStats.total} reqs</span>}
                      <span>{expanded ? "▲" : "▼"}</span>
                    </div>
                  </div>
                </button>

                {expanded && (
                  <div className="border-t px-5 py-4 space-y-4">
                    {quarantined && qDate && (
                      <div className="rounded-lg border border-yellow-300 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-950/30 p-3 flex items-center gap-2 text-sm">
                        <span className="text-yellow-700 dark:text-yellow-400">
                          Quarantine active until <strong>{new Date(qDate).toLocaleDateString()}</strong> — all actions require Tier-2 Passport.
                        </span>
                      </div>
                    )}
                    {suspended && (
                      <div className="rounded-lg border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-3 text-sm text-red-700 dark:text-red-400">
                        Service suspended. All requests return 503.
                      </div>
                    )}

                    {/* Per-service stats */}
                    {svcStats && (
                      <div className="flex gap-6 text-sm">
                        <span>Total: <strong>{svcStats.total}</strong></span>
                        <span className="text-green-600 dark:text-green-400">Success: <strong>{svcStats.success}</strong></span>
                        <span className="text-red-600 dark:text-red-400">Errors: <strong>{svcStats.errors}</strong></span>
                      </div>
                    )}

                    {/* Actions table */}
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-xs text-[var(--muted-foreground)] border-b">
                            <th className="pb-2 pr-4">Action ID</th>
                            <th className="pb-2 pr-4">Description</th>
                            <th className="pb-2 pr-4">Auth</th>
                            <th className="pb-2 pr-4">Rate Limit</th>
                            <th className="pb-2">Security</th>
                          </tr>
                        </thead>
                        <tbody>
                          {svc.actions.map(action => {
                            const humanAuth = action.cost?.human_authorization_required;
                            const rateLimit = action.cost?.limits?.rate_limit;
                            const aq = action.security_status?.quarantine_until;
                            const as_ = action.security_status?.suspended_at;
                            const isAQ = aq ? new Date(aq) > new Date() : false;

                            return (
                              <tr key={action.action_id} className="border-b border-[var(--border)]/50">
                                <td className="py-2 pr-4"><code className="text-xs">{action.action_id}</code></td>
                                <td className="py-2 pr-4 text-[var(--muted-foreground)] text-xs max-w-xs truncate">{action.description}</td>
                                <td className="py-2 pr-4">
                                  {humanAuth ? (
                                    <span className="text-xs px-1.5 py-0.5 rounded bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400">Tier-2</span>
                                  ) : (
                                    <span className="text-xs text-[var(--muted-foreground)]">Tier-1</span>
                                  )}
                                </td>
                                <td className="py-2 pr-4 text-xs text-[var(--muted-foreground)]">{rateLimit || "—"}</td>
                                <td className="py-2">
                                  {as_ ? (
                                    <span className="text-xs px-1.5 py-0.5 rounded bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">Suspended</span>
                                  ) : isAQ ? (
                                    <span className="text-xs px-1.5 py-0.5 rounded bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">Quarantine</span>
                                  ) : (
                                    <span className="text-xs text-green-600 dark:text-green-400">OK</span>
                                  )}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Recent audit log */}
        <div className="space-y-3">
          <button onClick={() => setShowAuditLog(!showAuditLog)}
            className="text-sm font-semibold text-[var(--muted-foreground)] uppercase tracking-wider hover:text-[var(--foreground)] transition-colors">
            Recent Audit Log ({data.recent_audit.length}) {showAuditLog ? "▲" : "▼"}
          </button>
          {showAuditLog && (
            <div className="rounded-xl border bg-[var(--card)] overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-[var(--muted-foreground)] border-b bg-[var(--muted)]">
                      <th className="px-4 py-2">Timestamp</th>
                      <th className="px-4 py-2">Service</th>
                      <th className="px-4 py-2">Action</th>
                      <th className="px-4 py-2">Outcome</th>
                      <th className="px-4 py-2">Status</th>
                      <th className="px-4 py-2">Latency</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent_audit.map((entry, i) => (
                      <tr key={i} className="border-b border-[var(--border)]/50 hover:bg-[var(--muted)]/30">
                        <td className="px-4 py-1.5 font-mono text-[var(--muted-foreground)]">
                          {new Date(entry.timestamp).toLocaleString()}
                        </td>
                        <td className="px-4 py-1.5"><code>{entry.service_id}</code></td>
                        <td className="px-4 py-1.5"><code>{entry.action_id}</code></td>
                        <td className="px-4 py-1.5">
                          <span className={entry.outcome === "success" ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}>
                            {entry.outcome}
                          </span>
                        </td>
                        <td className="px-4 py-1.5">{entry.response_code ?? "—"}</td>
                        <td className="px-4 py-1.5">{entry.latency_ms != null ? `${entry.latency_ms}ms` : "—"}</td>
                      </tr>
                    ))}
                    {data.recent_audit.length === 0 && (
                      <tr><td colSpan={6} className="px-4 py-4 text-center text-[var(--muted-foreground)]">No audit entries yet.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color?: "yellow" | "red" }) {
  const colors = {
    yellow: "border-yellow-300 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-950/30",
    red: "border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/30",
  };
  const textColors = {
    yellow: "text-yellow-700 dark:text-yellow-400",
    red: "text-red-700 dark:text-red-400",
  };

  return (
    <div className={`rounded-xl border p-4 ${color ? colors[color] : "bg-[var(--card)]"}`}>
      <p className="text-xs text-[var(--muted-foreground)] font-medium uppercase tracking-wider">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${color ? textColors[color] : ""}`}>{value}</p>
    </div>
  );
}
