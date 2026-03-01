"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { getToken, getCompanyName, clearToken } from "@/lib/auth";
import { api, type ServiceDashboard, type AuditLogEntry, type ServiceLogsResponse, type ServiceStatusResponse } from "@/lib/api";

interface ServiceWithLogs extends ServiceDashboard {
  logs?: AuditLogEntry[];
  logsTotal?: number;
}

export default function ServicesPage() {
  const router = useRouter();
  const [token, setTokenState] = useState<string | null>(null);
  const [companyName, setCompanyNameState] = useState("");
  const [services, setServices] = useState<ServiceWithLogs[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedService, setExpandedService] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState("");
  const [confirmUnpublish, setConfirmUnpublish] = useState<string | null>(null);

  useEffect(() => {
    const t = getToken();
    if (!t) { router.replace("/login"); return; }
    setTokenState(t);
    setCompanyNameState(getCompanyName() || "Company");
  }, [router]);

  const loadServices = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const data = await api<{ services: ServiceDashboard[] }>("/services", { token });
      setServices(data.services || []);
    } catch (err) {
      // If no /services list endpoint, try loading individually from menu
      setError(err instanceof Error ? err.message : "Failed to load services");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { loadServices(); }, [loadServices]);

  async function loadLogs(serviceId: string) {
    if (!token) return;
    try {
      const data = await api<ServiceLogsResponse>(`/services/${serviceId}/logs`, { token });
      setServices(prev => prev.map(s =>
        s.service_id === serviceId ? { ...s, logs: data.entries, logsTotal: data.total_entries } : s
      ));
    } catch { /* ignore */ }
  }

  async function handleAction(serviceId: string, action: "pause" | "resume" | "unpublish") {
    if (!token) return;
    if (action === "unpublish") {
      setConfirmUnpublish(serviceId);
      return;
    }
    await executeAction(serviceId, action);
  }

  async function executeAction(serviceId: string, action: "pause" | "resume" | "unpublish") {
    if (!token) return;
    setActionLoading(`${serviceId}-${action}`);
    try {
      const res = await api<ServiceStatusResponse>(`/services/${serviceId}/${action}`, { method: "PUT", token });
      setServices(prev => prev.map(s =>
        s.service_id === serviceId ? { ...s, status: res.status } : s
      ));
    } catch (err) {
      setError(err instanceof Error ? err.message : `${action} failed`);
    } finally {
      setActionLoading("");
      setConfirmUnpublish(null);
    }
  }

  function toggleExpand(serviceId: string) {
    if (expandedService === serviceId) {
      setExpandedService(null);
    } else {
      setExpandedService(serviceId);
      const svc = services.find(s => s.service_id === serviceId);
      if (!svc?.logs) loadLogs(serviceId);
    }
  }

  const handleLogout = useCallback(() => {
    clearToken();
    router.replace("/login");
  }, [router]);

  if (!token) return null;

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b bg-[var(--card)]">
        <div className="mx-auto max-w-5xl flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <a href="/onboard" className="text-xl font-bold hover:opacity-80 transition-opacity">☕ AgentCafe</a>
            <span className="text-sm text-[var(--muted-foreground)]">My Services</span>
          </div>
          <div className="flex items-center gap-4">
            <a href="/onboard" className="text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition-colors">+ New service</a>
            <span className="text-sm text-[var(--muted-foreground)]">{companyName}</span>
            <button onClick={handleLogout} className="text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition-colors">
              Sign out
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-6 py-8 space-y-6">
        {error && (
          <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 p-4 text-sm text-red-700 dark:text-red-400">
            {error}
            <button onClick={() => setError("")} className="ml-2 underline">dismiss</button>
          </div>
        )}

        {loading ? (
          <div className="text-center py-12 text-[var(--muted-foreground)]">Loading services...</div>
        ) : services.length === 0 ? (
          <div className="text-center py-12 space-y-4">
            <p className="text-[var(--muted-foreground)]">No services published yet.</p>
            <a href="/onboard" className="inline-block rounded-lg bg-[var(--primary)] px-6 py-2.5 text-sm font-medium text-[var(--primary-foreground)] hover:opacity-90 transition-opacity">
              Onboard your first service
            </a>
          </div>
        ) : (
          <div className="space-y-4">
            {services.map(svc => (
              <div key={svc.service_id} className="rounded-xl border bg-[var(--card)] overflow-hidden">
                {/* Service row */}
                <div className="p-5 flex items-center justify-between">
                  <div className="space-y-1.5 flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      <span className="font-semibold truncate">{svc.name}</span>
                      <code className="text-xs bg-[var(--muted)] px-2 py-0.5 rounded flex-shrink-0">{svc.service_id}</code>
                      <StatusBadge status={svc.status} />
                    </div>
                    <div className="flex items-center gap-4 text-xs text-[var(--muted-foreground)]">
                      <span>{svc.actions_count} actions</span>
                      <span>{svc.total_requests} total requests</span>
                      <span>{svc.recent_requests} in last 24h</span>
                      <span>Published {new Date(svc.published_at).toLocaleDateString()}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 ml-4 flex-shrink-0">
                    {svc.status === "live" && (
                      <button onClick={() => handleAction(svc.service_id, "pause")}
                        disabled={actionLoading === `${svc.service_id}-pause`}
                        className="rounded-lg border px-3 py-1.5 text-xs font-medium hover:bg-yellow-50 dark:hover:bg-yellow-950/30 text-yellow-700 dark:text-yellow-400 border-yellow-300 dark:border-yellow-800 disabled:opacity-50 transition-colors">
                        {actionLoading === `${svc.service_id}-pause` ? "..." : "Pause"}
                      </button>
                    )}
                    {svc.status === "paused" && (
                      <button onClick={() => handleAction(svc.service_id, "resume")}
                        disabled={actionLoading === `${svc.service_id}-resume`}
                        className="rounded-lg border px-3 py-1.5 text-xs font-medium hover:bg-green-50 dark:hover:bg-green-950/30 text-green-700 dark:text-green-400 border-green-300 dark:border-green-800 disabled:opacity-50 transition-colors">
                        {actionLoading === `${svc.service_id}-resume` ? "..." : "Resume"}
                      </button>
                    )}
                    {svc.status !== "unpublished" && (
                      <button onClick={() => handleAction(svc.service_id, "unpublish")}
                        disabled={actionLoading === `${svc.service_id}-unpublish`}
                        className="rounded-lg border px-3 py-1.5 text-xs font-medium hover:bg-red-50 dark:hover:bg-red-950/30 text-red-700 dark:text-red-400 border-red-300 dark:border-red-800 disabled:opacity-50 transition-colors">
                        {actionLoading === `${svc.service_id}-unpublish` ? "..." : "Unpublish"}
                      </button>
                    )}
                    <button onClick={() => toggleExpand(svc.service_id)}
                      className="rounded-lg border px-3 py-1.5 text-xs font-medium hover:bg-[var(--muted)] transition-colors">
                      {expandedService === svc.service_id ? "Hide logs" : "View logs"}
                    </button>
                  </div>
                </div>

                {/* Expanded logs */}
                {expandedService === svc.service_id && (
                  <div className="border-t px-5 py-4 bg-[var(--muted)]/30">
                    {!svc.logs ? (
                      <p className="text-sm text-[var(--muted-foreground)]">Loading logs...</p>
                    ) : svc.logs.length === 0 ? (
                      <p className="text-sm text-[var(--muted-foreground)]">No requests yet.</p>
                    ) : (
                      <div className="space-y-2">
                        <p className="text-xs text-[var(--muted-foreground)] font-medium">{svc.logsTotal} total entries (showing last {svc.logs.length})</p>
                        <div className="overflow-x-auto">
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="text-left text-[var(--muted-foreground)] border-b">
                                <th className="pb-2 pr-4">Timestamp</th>
                                <th className="pb-2 pr-4">Action</th>
                                <th className="pb-2 pr-4">Outcome</th>
                                <th className="pb-2 pr-4">Status</th>
                                <th className="pb-2">Latency</th>
                              </tr>
                            </thead>
                            <tbody>
                              {svc.logs.map((log, i) => (
                                <tr key={i} className="border-b border-[var(--border)]/50">
                                  <td className="py-1.5 pr-4 font-mono text-[var(--muted-foreground)]">
                                    {new Date(log.timestamp).toLocaleString()}
                                  </td>
                                  <td className="py-1.5 pr-4"><code>{log.action_id}</code></td>
                                  <td className="py-1.5 pr-4">
                                    <span className={log.outcome === "success" ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}>
                                      {log.outcome}
                                    </span>
                                  </td>
                                  <td className="py-1.5 pr-4">{log.response_code}</td>
                                  <td className="py-1.5">{log.latency_ms != null ? `${log.latency_ms}ms` : "—"}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Unpublish confirmation dialog */}
      {confirmUnpublish && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="rounded-xl border bg-[var(--card)] p-6 max-w-md w-full mx-4 space-y-4 shadow-xl">
            <h3 className="text-lg font-semibold">Unpublish service?</h3>
            <div className="rounded-lg border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-3 text-sm text-red-700 dark:text-red-400 space-y-1">
              <p className="font-medium">This action is permanent.</p>
              <p>The service will be removed from the AgentCafe Menu. Agents will no longer be able to discover or use it. Existing tokens for this service will stop working.</p>
            </div>
            <p className="text-sm text-[var(--muted-foreground)]">
              Service: <code className="bg-[var(--muted)] px-1.5 py-0.5 rounded">{confirmUnpublish}</code>
            </p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setConfirmUnpublish(null)}
                className="rounded-lg border px-4 py-2 text-sm font-medium hover:bg-[var(--muted)] transition-colors">
                Cancel
              </button>
              <button onClick={() => executeAction(confirmUnpublish, "unpublish")}
                disabled={actionLoading === `${confirmUnpublish}-unpublish`}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50 transition-colors">
                {actionLoading === `${confirmUnpublish}-unpublish` ? "Unpublishing..." : "Yes, unpublish permanently"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    live: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
    paused: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
    unpublished: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[status] || styles.unpublished}`}>
      {status}
    </span>
  );
}
