"use client";

import { useState } from "react";
import type { CandidateMenu } from "@/lib/api";

interface PolicyStepProps {
  candidate: CandidateMenu;
  onSave: (actions: Record<string, { scope: string; human_auth: boolean; rate_limit: string }>, backendUrl: string, backendAuth: string) => void;
  onBack: () => void;
  loading: boolean;
}

interface ActionPolicy {
  scope: string;
  human_auth: boolean;
  rate_limit: string;
}

export function PolicyStep({ candidate, onSave, onBack, loading }: PolicyStepProps) {
  const [backendUrl, setBackendUrl] = useState("");
  const [backendAuth, setBackendAuth] = useState("");
  const [policies, setPolicies] = useState<Record<string, ActionPolicy>>(() => {
    const init: Record<string, ActionPolicy> = {};
    for (const a of candidate.actions) {
      init[a.action_id] = {
        scope: a.suggested_scope || `${candidate.service_id}:${a.action_id}`,
        human_auth: a.suggested_human_auth,
        rate_limit: a.suggested_rate_limit || "60/minute",
      };
    }
    return init;
  });

  function updatePolicy(actionId: string, field: keyof ActionPolicy, value: string | boolean) {
    setPolicies(prev => ({ ...prev, [actionId]: { ...prev[actionId], [field]: value } }));
  }

  function handleSubmit() {
    if (!backendUrl.trim()) return;
    onSave(policies, backendUrl, backendAuth);
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border p-6 bg-[var(--card)] space-y-5">
        <div>
          <h2 className="text-xl font-semibold">Configure policies &amp; backend</h2>
          <p className="text-sm text-[var(--muted-foreground)] mt-1">
            Set access policies for each action and provide your backend URL where the Cafe will proxy requests.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Backend URL <span className="text-red-500">*</span></label>
            <input value={backendUrl} onChange={(e) => setBackendUrl(e.target.value)} type="url" required
              placeholder="https://api.yourcompany.com"
              className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]" />
            <p className="text-xs text-[var(--muted-foreground)]">The Cafe proxies agent requests to this URL</p>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Auth header <span className="text-[var(--muted-foreground)]">(optional, encrypted at rest)</span></label>
            <input value={backendAuth} onChange={(e) => setBackendAuth(e.target.value)} type="password"
              placeholder="Bearer sk-..."
              className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]" />
            <p className="text-xs text-[var(--muted-foreground)]">Sent as Authorization header to your backend</p>
          </div>
        </div>
      </div>

      {/* Per-action policies */}
      <div className="space-y-4">
        <h3 className="text-lg font-semibold">Action policies</h3>
        {candidate.actions.map((action) => {
          const policy = policies[action.action_id];
          if (!policy) return null;
          return (
            <div key={action.action_id} className="rounded-xl border p-5 bg-[var(--card)] space-y-4">
              <div className="flex items-center gap-3">
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${action.is_write ? "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400" : "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"}`}>
                  {action.is_write ? "WRITE" : "READ"}
                </span>
                <code className="text-sm font-mono font-medium">{action.action_id}</code>
                {action.suggested_risk_tier && (
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                    action.suggested_risk_tier === "high" || action.suggested_risk_tier === "critical"
                      ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                      : action.suggested_risk_tier === "medium"
                      ? "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400"
                      : "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                  }`}>
                    {action.suggested_risk_tier} risk
                  </span>
                )}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-[var(--muted-foreground)]">Scope</label>
                  <input value={policy.scope} onChange={(e) => updatePolicy(action.action_id, "scope", e.target.value)}
                    className="w-full rounded-lg border bg-[var(--background)] px-3 py-1.5 text-sm font-mono outline-none focus:ring-2 focus:ring-[var(--primary)]" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-[var(--muted-foreground)]">Rate limit</label>
                  <select value={policy.rate_limit} onChange={(e) => updatePolicy(action.action_id, "rate_limit", e.target.value)}
                    className="w-full rounded-lg border bg-[var(--background)] px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]">
                    <option value="60/minute">60/minute (default read)</option>
                    <option value="10/minute">10/minute (default write)</option>
                    <option value="5/minute">5/minute (restrictive)</option>
                    <option value="1/minute">1/minute (very restrictive)</option>
                    <option value="100/minute">100/minute (generous)</option>
                  </select>
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-[var(--muted-foreground)]">Human consent required</label>
                  <button
                    onClick={() => updatePolicy(action.action_id, "human_auth", !policy.human_auth)}
                    className={`w-full rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
                      policy.human_auth
                        ? "bg-orange-100 text-orange-700 border-orange-300 dark:bg-orange-900/30 dark:text-orange-400 dark:border-orange-800"
                        : "bg-[var(--background)] hover:bg-[var(--muted)]"
                    }`}
                  >
                    {policy.human_auth ? "Yes — Tier-2 required" : "No — Tier-1 sufficient"}
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Navigation */}
      <div className="flex justify-between">
        <button onClick={onBack} className="rounded-lg border px-6 py-2.5 text-sm font-medium hover:bg-[var(--muted)] transition-colors">
          Back
        </button>
        <button onClick={handleSubmit} disabled={loading || !backendUrl.trim()}
          className="rounded-lg bg-[var(--primary)] px-6 py-2.5 text-sm font-medium text-[var(--primary-foreground)] hover:opacity-90 disabled:opacity-50 transition-opacity">
          {loading ? "Saving..." : "Save & preview"}
        </button>
      </div>
    </div>
  );
}
