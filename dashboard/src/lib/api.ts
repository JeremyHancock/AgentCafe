const API_BASE = "/api/wizard";

interface ApiOptions {
  method?: string;
  body?: unknown;
  token?: string;
}

export async function api<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (opts.token) {
    headers["Authorization"] = `Bearer ${opts.token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method: opts.method || "GET",
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: { message: res.statusText } }));
    throw new Error(err?.detail?.message || err?.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

export async function uploadSpec(file: File, token: string) {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_BASE}/specs/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: { message: res.statusText } }));
    throw new Error(err?.detail?.message || err?.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

// --- Types ---

export interface Company {
  company_id: string;
  name: string;
  session_token: string;
}

export interface CandidateAction {
  action_id: string;
  description: string;
  required_inputs: { name: string; description: string; example?: unknown; type: string }[];
  example_response: Record<string, unknown>;
  suggested_scope: string;
  suggested_human_auth: boolean;
  suggested_rate_limit: string;
  suggested_risk_tier: string;
  suggested_human_identifier_field: string;
  is_write: boolean;
  confidence: Record<string, number>;
  source_path: string;
  source_method: string;
}

export interface CandidateMenu {
  service_id: string;
  name: string;
  category: string;
  capability_tags: string[];
  description: string;
  actions: CandidateAction[];
  confidence: Record<string, number>;
}

export interface ParsedSpec {
  title: string;
  version: string;
  description: string;
  operations: { path: string; method: string; operation_id: string; summary: string; is_write: boolean }[];
}

export interface SpecParseResponse {
  draft_id: string;
  parsed_spec: ParsedSpec;
  candidate_menu: CandidateMenu;
}

export interface PreviewResponse {
  final_menu_entry: Record<string, unknown>;
  proxy_configs: Record<string, unknown>[];
}
