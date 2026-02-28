"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, type Company } from "@/lib/api";
import { setToken, setCompanyName } from "@/lib/auth";

export default function RegisterPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [website, setWebsite] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await api<Company>("/companies", {
        method: "POST",
        body: { name, email, password, website },
      });
      setToken(data.session_token);
      setCompanyName(data.name);
      router.push("/onboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen p-4">
      <div className="w-full max-w-md space-y-8">
        <div className="text-center">
          <h1 className="text-3xl font-bold tracking-tight">☕ AgentCafe</h1>
          <p className="mt-2 text-[var(--muted-foreground)]">Register your company</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 rounded-xl border p-6 shadow-sm bg-[var(--card)]">
          <h2 className="text-xl font-semibold">Create account</h2>

          {error && (
            <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 p-3 text-sm text-red-700 dark:text-red-400">
              {error}
            </div>
          )}

          <div className="space-y-2">
            <label htmlFor="name" className="text-sm font-medium">Company name</label>
            <input id="name" type="text" required value={name} onChange={(e) => setName(e.target.value)}
              className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]"
              placeholder="Acme Corp" />
          </div>

          <div className="space-y-2">
            <label htmlFor="email" className="text-sm font-medium">Email</label>
            <input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]"
              placeholder="you@company.com" />
          </div>

          <div className="space-y-2">
            <label htmlFor="password" className="text-sm font-medium">Password</label>
            <input id="password" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]"
              placeholder="At least 8 characters" />
          </div>

          <div className="space-y-2">
            <label htmlFor="website" className="text-sm font-medium">Website <span className="text-[var(--muted-foreground)]">(optional)</span></label>
            <input id="website" type="url" value={website} onChange={(e) => setWebsite(e.target.value)}
              className="w-full rounded-lg border bg-[var(--background)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]"
              placeholder="https://company.com" />
          </div>

          <button type="submit" disabled={loading}
            className="w-full rounded-lg bg-[var(--primary)] px-4 py-2.5 text-sm font-medium text-[var(--primary-foreground)] hover:opacity-90 disabled:opacity-50 transition-opacity">
            {loading ? "Creating account..." : "Create account"}
          </button>

          <p className="text-center text-sm text-[var(--muted-foreground)]">
            Already have an account?{" "}
            <Link href="/login" className="text-[var(--primary)] hover:underline">Sign in</Link>
          </p>
        </form>
      </div>
    </div>
  );
}
