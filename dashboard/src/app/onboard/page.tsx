"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { getToken, getCompanyName, clearToken } from "@/lib/auth";
import { api, uploadSpec, type SpecParseResponse, type CandidateMenu, type CandidateAction, type PreviewResponse } from "@/lib/api";
import { SpecInputStep } from "@/components/spec-input";
import { ReviewStep } from "@/components/review-step";
import { PolicyStep } from "@/components/policy-step";
import { PreviewStep } from "@/components/preview-step";

const STEPS = ["Spec Input", "Review", "Policy", "Preview & Publish"];

export default function OnboardPage() {
  const router = useRouter();
  const [token, setTokenState] = useState<string | null>(null);
  const [companyName, setCompanyNameState] = useState<string>("");
  const [step, setStep] = useState(0);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<CandidateMenu | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [published, setPublished] = useState(false);

  useEffect(() => {
    const t = getToken();
    if (!t) { router.replace("/login"); return; }
    setTokenState(t);
    setCompanyNameState(getCompanyName() || "Company");
  }, [router]);

  const handleLogout = useCallback(() => {
    clearToken();
    router.replace("/login");
  }, [router]);

  // Step 1: Parse spec (paste, upload, or URL)
  async function handleSpecPaste(rawSpec: string) {
    if (!token) return;
    setError("");
    setLoading(true);
    try {
      const data = await api<SpecParseResponse>("/specs/parse", { method: "POST", body: { raw_spec: rawSpec }, token });
      setDraftId(data.draft_id);
      setCandidate(data.candidate_menu);
      setStep(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Parse failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleSpecUpload(file: File) {
    if (!token) return;
    setError("");
    setLoading(true);
    try {
      const data = await uploadSpec(file, token);
      setDraftId(data.draft_id);
      setCandidate(data.candidate_menu);
      setStep(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleSpecFetch(url: string) {
    if (!token) return;
    setError("");
    setLoading(true);
    try {
      const data = await api<SpecParseResponse>("/specs/fetch", { method: "POST", body: { url }, token });
      setDraftId(data.draft_id);
      setCandidate(data.candidate_menu);
      setStep(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Fetch failed");
    } finally {
      setLoading(false);
    }
  }

  // Step 2: Save review
  async function handleReviewSave(edited: CandidateMenu, excluded: string[]) {
    if (!token || !draftId) return;
    setError("");
    setLoading(true);
    try {
      await api(`/drafts/${draftId}/review`, {
        method: "PUT",
        body: {
          service_id: edited.service_id,
          name: edited.name,
          category: edited.category,
          capability_tags: edited.capability_tags,
          description: edited.description,
          actions: edited.actions,
          excluded_actions: excluded,
        },
        token,
      });
      setCandidate(edited);
      setStep(2);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Review save failed");
    } finally {
      setLoading(false);
    }
  }

  // Step 3: Save policy + generate preview
  async function handlePolicySave(actions: Record<string, { scope: string; human_auth: boolean; rate_limit: string }>, backendUrl: string, backendAuth: string) {
    if (!token || !draftId) return;
    setError("");
    setLoading(true);
    try {
      await api(`/drafts/${draftId}/policy`, {
        method: "PUT",
        body: { actions, backend_url: backendUrl, backend_auth_header: backendAuth },
        token,
      });
      const prev = await api<PreviewResponse>(`/drafts/${draftId}/preview`, { token });
      setPreview(prev);
      setStep(3);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Policy save failed");
    } finally {
      setLoading(false);
    }
  }

  // Step 4: Publish
  async function handlePublish() {
    if (!token || !draftId) return;
    setError("");
    setLoading(true);
    try {
      await api(`/drafts/${draftId}/publish`, { method: "POST", token });
      setPublished(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Publish failed");
    } finally {
      setLoading(false);
    }
  }

  if (!token) return null;

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b bg-[var(--card)]">
        <div className="mx-auto max-w-5xl flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <span className="text-xl font-bold">☕ AgentCafe</span>
            <span className="text-sm text-[var(--muted-foreground)]">Company Dashboard</span>
          </div>
          <div className="flex items-center gap-4">
            <span className="text-sm text-[var(--muted-foreground)]">{companyName}</span>
            <button onClick={handleLogout} className="text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition-colors">
              Sign out
            </button>
          </div>
        </div>
      </header>

      {/* Step indicator */}
      <div className="mx-auto max-w-5xl px-6 py-6">
        <div className="flex items-center gap-2 mb-8">
          {STEPS.map((label, i) => (
            <div key={label} className="flex items-center gap-2">
              <div className={`flex items-center justify-center w-8 h-8 rounded-full text-sm font-medium transition-colors ${
                i < step ? "bg-[var(--success)] text-white" :
                i === step ? "bg-[var(--primary)] text-white" :
                "bg-[var(--muted)] text-[var(--muted-foreground)]"
              }`}>
                {i < step ? "✓" : i + 1}
              </div>
              <span className={`text-sm ${i === step ? "font-medium" : "text-[var(--muted-foreground)]"}`}>{label}</span>
              {i < STEPS.length - 1 && <div className="w-8 h-px bg-[var(--border)]" />}
            </div>
          ))}
        </div>

        {/* Error banner */}
        {error && (
          <div className="mb-6 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 p-4 text-sm text-red-700 dark:text-red-400">
            {error}
            <button onClick={() => setError("")} className="ml-2 underline">dismiss</button>
          </div>
        )}

        {/* Published success */}
        {published ? (
          <div className="rounded-xl border p-8 bg-[var(--card)] text-center space-y-4">
            <div className="text-5xl">🎉</div>
            <h2 className="text-2xl font-bold">Published!</h2>
            <p className="text-[var(--muted-foreground)]">
              Your service is now live on the AgentCafe Menu. Agents can discover and use it immediately.
            </p>
            <p className="text-sm text-[var(--warning)]">
              Note: New services start in quarantine mode (30 days). All actions require human consent during this period.
            </p>
            <button onClick={() => { setStep(0); setDraftId(null); setCandidate(null); setPreview(null); setPublished(false); }}
              className="rounded-lg bg-[var(--primary)] px-6 py-2.5 text-sm font-medium text-[var(--primary-foreground)] hover:opacity-90 transition-opacity">
              Onboard another service
            </button>
          </div>
        ) : (
          <>
            {step === 0 && <SpecInputStep onPaste={handleSpecPaste} onUpload={handleSpecUpload} onFetch={handleSpecFetch} loading={loading} />}
            {step === 1 && candidate && <ReviewStep candidate={candidate} onSave={handleReviewSave} onBack={() => setStep(0)} loading={loading} />}
            {step === 2 && candidate && <PolicyStep candidate={candidate} onSave={handlePolicySave} onBack={() => setStep(1)} loading={loading} />}
            {step === 3 && preview && <PreviewStep preview={preview} onPublish={handlePublish} onBack={() => setStep(2)} loading={loading} />}
          </>
        )}
      </div>
    </div>
  );
}
