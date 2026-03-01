"use client";

import { useState, useRef } from "react";

interface SpecInputStepProps {
  onPaste: (raw: string) => void;
  onUpload: (file: File) => void;
  onFetch: (url: string) => void;
  loading: boolean;
  rawSpec: string;
  onRawSpecChange: (value: string) => void;
}

type InputMode = "paste" | "upload" | "url";

export function SpecInputStep({ onPaste, onUpload, onFetch, loading, rawSpec, onRawSpecChange }: SpecInputStepProps) {
  const [mode, setMode] = useState<InputMode>("paste");
  const [url, setUrl] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  function handleSubmit() {
    if (mode === "paste" && rawSpec.trim()) {
      onPaste(rawSpec);
    } else if (mode === "url" && url.trim()) {
      onFetch(url);
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) onUpload(file);
  }

  return (
    <div className="rounded-xl border p-6 bg-[var(--card)] space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Upload your OpenAPI spec</h2>
        <p className="text-sm text-[var(--muted-foreground)] mt-1">
          Paste JSON/YAML, upload a file, or provide a URL. We&apos;ll parse it and generate an agent-friendly Menu entry.
        </p>
      </div>

      {/* Mode tabs */}
      <div className="flex gap-1 p-1 rounded-lg bg-[var(--muted)] w-fit">
        {(["paste", "upload", "url"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
              mode === m ? "bg-[var(--card)] shadow-sm" : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            }`}
          >
            {m === "paste" ? "Paste" : m === "upload" ? "Upload file" : "Fetch URL"}
          </button>
        ))}
      </div>

      {/* Paste mode */}
      {mode === "paste" && (
        <div className="space-y-3">
          <textarea
            value={rawSpec}
            onChange={(e) => onRawSpecChange(e.target.value)}
            placeholder='{"openapi": "3.1.0", "info": {"title": "My API", ...}}'
            rows={14}
            className="w-full rounded-lg border bg-[var(--background)] px-4 py-3 text-sm font-mono outline-none focus:ring-2 focus:ring-[var(--primary)] resize-y"
          />
          <button
            onClick={handleSubmit}
            disabled={loading || !rawSpec.trim()}
            className="rounded-lg bg-[var(--primary)] px-6 py-2.5 text-sm font-medium text-[var(--primary-foreground)] hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {loading ? "Parsing..." : "Parse spec"}
          </button>
        </div>
      )}

      {/* Upload mode */}
      {mode === "upload" && (
        <div className="space-y-3">
          <div
            onClick={() => fileRef.current?.click()}
            className="border-2 border-dashed rounded-lg p-12 text-center cursor-pointer hover:border-[var(--primary)] transition-colors"
          >
            <p className="text-sm font-medium">Click to select a file</p>
            <p className="text-xs text-[var(--muted-foreground)] mt-1">JSON or YAML, up to 2 MB</p>
          </div>
          <input ref={fileRef} type="file" accept=".json,.yaml,.yml" className="hidden" onChange={handleFileChange} />
          {loading && <p className="text-sm text-[var(--muted-foreground)]">Uploading and parsing...</p>}
        </div>
      )}

      {/* URL mode */}
      {mode === "url" && (
        <div className="space-y-3">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://api.example.com/openapi.json"
            className="w-full rounded-lg border bg-[var(--background)] px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]"
          />
          <button
            onClick={handleSubmit}
            disabled={loading || !url.trim()}
            className="rounded-lg bg-[var(--primary)] px-6 py-2.5 text-sm font-medium text-[var(--primary-foreground)] hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {loading ? "Fetching..." : "Fetch & parse"}
          </button>
        </div>
      )}
    </div>
  );
}
