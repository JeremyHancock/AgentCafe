"use client";

const TOKEN_KEY = "agentcafe_session";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function getCompanyName(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("agentcafe_company_name");
}

export function setCompanyName(name: string): void {
  localStorage.setItem("agentcafe_company_name", name);
}
