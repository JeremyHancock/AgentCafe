"use client";

import type { PreviewResponse } from "@/lib/api";

interface PreviewStepProps {
  preview: PreviewResponse;
  onPublish: () => void;
  onBack: () => void;
  loading: boolean;
}

export function PreviewStep({ preview, onPublish, onBack, loading }: PreviewStepProps) {
  const menu = preview.final_menu_entry as Record<string, unknown>;
  const actions = (menu.actions || []) as Record<string, unknown>[];

  return (
    <div className="space-y-6">
      <div className="rounded-xl border p-6 bg-[var(--card)] space-y-5">
        <div>
          <h2 className="text-xl font-semibold">Preview your Menu entry</h2>
          <p className="text-sm text-[var(--muted-foreground)] mt-1">
            This is exactly what agents will see when they browse the AgentCafe Menu.
          </p>
        </div>

        {/* Service header */}
        <div className="rounded-lg border p-4 bg-[var(--muted)] space-y-2">
          <div className="flex items-center gap-3">
            <h3 className="text-lg font-bold">{menu.name as string}</h3>
            <code className="text-xs bg-[var(--background)] px-2 py-0.5 rounded">{menu.service_id as string}</code>
          </div>
          <p className="text-sm">{menu.description as string}</p>
          <div className="flex gap-2 mt-2">
            {((menu.capability_tags || []) as string[]).map((tag) => (
              <span key={tag} className="px-2 py-0.5 rounded-full bg-[var(--background)] text-xs">{tag}</span>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-[var(--muted-foreground)] uppercase tracking-wider">Actions ({actions.length})</h4>
          {actions.map((action) => {
            const cost = action.cost as Record<string, unknown> | undefined;
            const limits = cost?.limits as Record<string, unknown> | undefined;
            const humanAuth = cost?.human_authorization_required as boolean;
            return (
              <div key={action.action_id as string} className="rounded-lg border p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <code className="text-sm font-mono font-medium">{action.action_id as string}</code>
                    {humanAuth && (
                      <span className="px-2 py-0.5 rounded text-xs font-medium bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400">
                        Tier-2 required
                      </span>
                    )}
                  </div>
                  {limits?.rate_limit != null && (
                    <span className="text-xs text-[var(--muted-foreground)]">{String(limits.rate_limit)}</span>
                  )}
                </div>
                <p className="text-sm text-[var(--muted-foreground)]">{action.description as string}</p>

                {/* Inputs */}
                {(action.required_inputs as Record<string, unknown>[])?.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-1">
                    {(action.required_inputs as Record<string, unknown>[]).map((inp) => (
                      <span key={inp.name as string} className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-[var(--muted)] text-xs">
                        <code>{inp.name as string}</code>
                        {inp.type != null && <span className="text-[var(--muted-foreground)]">({String(inp.type)})</span>}
                      </span>
                    ))}
                  </div>
                )}

              </div>
            );
          })}
        </div>
      </div>

      {/* Raw JSON preview */}
      <details className="rounded-xl border bg-[var(--card)]">
        <summary className="px-6 py-4 cursor-pointer text-sm font-medium hover:bg-[var(--muted)] transition-colors rounded-xl">
          View raw JSON
        </summary>
        <pre className="px-6 pb-4 text-xs font-mono overflow-x-auto whitespace-pre-wrap text-[var(--muted-foreground)]">
          {JSON.stringify(preview, null, 2)}
        </pre>
      </details>

      {/* Quarantine notice */}
      <div className="rounded-lg border border-yellow-300 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-950/30 p-4 text-sm">
        <p className="font-medium text-yellow-800 dark:text-yellow-300">30-day quarantine period</p>
        <p className="text-yellow-700 dark:text-yellow-400 mt-1">
          New services start in quarantine mode. All actions will require human consent (Tier-2 Passport) for 30 days, regardless of your policy settings. This protects users while your service builds trust.
        </p>
      </div>

      {/* Navigation */}
      <div className="flex justify-between">
        <button onClick={onBack} className="rounded-lg border px-6 py-2.5 text-sm font-medium hover:bg-[var(--muted)] transition-colors">
          Back
        </button>
        <button onClick={onPublish} disabled={loading}
          className="rounded-lg bg-green-600 px-8 py-2.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50 transition-colors">
          {loading ? "Publishing..." : "Publish to AgentCafe"}
        </button>
      </div>
    </div>
  );
}
