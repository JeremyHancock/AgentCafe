"use client";

import { useState } from "react";
import type { CandidateMenu, CandidateAction } from "@/lib/api";

interface ReviewStepProps {
  candidate: CandidateMenu;
  onSave: (edited: CandidateMenu, excluded: string[]) => void;
  onBack: () => void;
  loading: boolean;
}

function AiQualityHint({ confidence }: { confidence: Record<string, number> | undefined }) {
  if (!confidence) return null;
  const values = Object.values(confidence);
  if (values.length === 0) return null;
  const avg = values.reduce((a, b) => a + b, 0) / values.length;

  if (avg >= 0.75) return null; // High confidence — no noise needed
  if (avg >= 0.5) {
    return (
      <span className="text-xs text-yellow-600 dark:text-yellow-400" title="Some auto-generated fields may need editing">
        Review suggested
      </span>
    );
  }
  return (
    <span className="text-xs text-red-600 dark:text-red-400" title="Low confidence — please verify these fields">
      Needs review
    </span>
  );
}

export function ReviewStep({ candidate, onSave, onBack, loading }: ReviewStepProps) {
  const [serviceName, setServiceName] = useState(candidate.name);
  const [serviceId, setServiceId] = useState(candidate.service_id);
  const [category, setCategory] = useState(candidate.category);
  const [description, setDescription] = useState(candidate.description);
  const [tags, setTags] = useState(candidate.capability_tags.join(", "));
  const [actions, setActions] = useState<CandidateAction[]>(candidate.actions);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());

  function toggleExclude(actionId: string) {
    setExcluded(prev => {
      const next = new Set(prev);
      if (next.has(actionId)) next.delete(actionId);
      else next.add(actionId);
      return next;
    });
  }

  function updateAction(index: number, field: keyof CandidateAction, value: string | boolean) {
    setActions(prev => prev.map((a, i) => i === index ? { ...a, [field]: value } : a));
  }

  function handleSubmit() {
    const edited: CandidateMenu = {
      ...candidate,
      service_id: serviceId,
      name: serviceName,
      category,
      description,
      capability_tags: tags.split(",").map(t => t.trim()).filter(Boolean),
      actions,
    };
    onSave(edited, Array.from(excluded));
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border p-6 bg-[var(--card)] space-y-5">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-semibold">Review AI-generated Menu entry</h2>
          <AiQualityHint confidence={candidate.confidence} />
        </div>

        <p className="text-sm text-[var(--muted-foreground)]">
          Edit any field below. The AI generated these values from your spec — adjust anything that looks wrong.
        </p>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Service ID</label>
            <input value={serviceId} onChange={(e) => setServiceId(e.target.value)}
              className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm font-mono outline-none focus:ring-2 focus:ring-[var(--primary)]" />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Display name</label>
            <input value={serviceName} onChange={(e) => setServiceName(e.target.value)}
              className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]" />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Category</label>
            <input value={category} onChange={(e) => setCategory(e.target.value)}
              className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]" />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Tags <span className="text-[var(--muted-foreground)]">(comma-separated)</span></label>
            <input value={tags} onChange={(e) => setTags(e.target.value)}
              className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]" />
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium">Description</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2}
            className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)] resize-y" />
        </div>
      </div>

      {/* Actions */}
      <div className="space-y-4">
        <h3 className="text-lg font-semibold">Actions ({actions.length})</h3>
        {actions.map((action, idx) => (
          <div key={action.action_id} className={`rounded-xl border p-5 bg-[var(--card)] space-y-3 ${excluded.has(action.action_id) ? "opacity-40" : ""}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${action.is_write ? "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400" : "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"}`}>
                  {action.is_write ? "WRITE" : "READ"}
                </span>
                <code className="text-sm font-mono font-medium">{action.action_id}</code>
                {action.source_method && <span className="text-xs text-[var(--muted-foreground)]">{action.source_method} {action.source_path}</span>}
              </div>
              <div className="flex items-center gap-3">
                <AiQualityHint confidence={action.confidence} />
                <button onClick={() => toggleExclude(action.action_id)}
                  className={`text-xs px-3 py-1 rounded-lg border transition-colors ${excluded.has(action.action_id) ? "bg-[var(--primary)] text-white border-[var(--primary)]" : "hover:bg-[var(--muted)]"}`}>
                  {excluded.has(action.action_id) ? "Re-include" : "Exclude"}
                </button>
              </div>
            </div>

            {!excluded.has(action.action_id) && (
              <>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-[var(--muted-foreground)]">Description</label>
                  <input value={action.description} onChange={(e) => updateAction(idx, "description", e.target.value)}
                    className="w-full rounded-lg border bg-[var(--background)] px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]" />
                </div>
                {action.required_inputs.length > 0 && (
                  <div>
                    <label className="text-xs font-medium text-[var(--muted-foreground)]">Required inputs</label>
                    <div className="mt-1 flex flex-wrap gap-2">
                      {action.required_inputs.map((inp, i) => (
                        <span key={`${inp.name}-${i}`} className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-[var(--muted)] text-xs">
                          <code>{inp.name}</code>
                          <span className="text-[var(--muted-foreground)]">({inp.type})</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        ))}
      </div>

      {/* Navigation */}
      <div className="flex justify-between">
        <button onClick={onBack} className="rounded-lg border px-6 py-2.5 text-sm font-medium hover:bg-[var(--muted)] transition-colors">
          Back
        </button>
        <button onClick={handleSubmit} disabled={loading}
          className="rounded-lg bg-[var(--primary)] px-6 py-2.5 text-sm font-medium text-[var(--primary-foreground)] hover:opacity-90 disabled:opacity-50 transition-opacity">
          {loading ? "Saving..." : "Save & continue"}
        </button>
      </div>
    </div>
  );
}
