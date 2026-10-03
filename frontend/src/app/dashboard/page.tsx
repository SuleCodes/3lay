"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";
import type { ApiKey, ApiKeyCreatedResponse, User } from "@/lib/types";

export default function DashboardPage() {
  const router = useRouter();

  const [user, setUser] = useState<User | null>(null);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);

  const [newKeyName, setNewKeyName] = useState("");
  const [creating, setCreating] = useState(false);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function loadKeys() {
    const data = await apiFetch<ApiKey[]>("/api-keys");
    setKeys(data);
  }

  useEffect(() => {
    apiFetch<User>("/auth/me")
      .then(async (u) => {
        setUser(u);
        await loadKeys();
      })
      .catch(() => router.replace("/login"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleCreateKey(e: FormEvent) {
    e.preventDefault();
    setCreating(true);
    try {
      const res = await apiFetch<ApiKeyCreatedResponse>("/api-keys", {
        method: "POST",
        body: JSON.stringify({ name: newKeyName || "New key" }),
      });
      setRevealedKey(res.key);
      setNewKeyName("");
      await loadKeys();
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(id: string) {
    await apiFetch(`/api-keys/${id}/revoke`, { method: "POST" });
    await loadKeys();
  }

  async function handleLogout() {
    await apiFetch("/auth/logout", { method: "POST" });
    router.replace("/login");
  }

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-neutral-950 text-neutral-100">
        <p className="text-sm text-neutral-400">Loading...</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-neutral-950 px-4 py-10 text-neutral-100">
      <div className="mx-auto max-w-2xl">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold">3lay</h1>
            <p className="mt-1 text-sm text-neutral-400">{user?.email}</p>
          </div>
          <button
            onClick={handleLogout}
            className="rounded-md border border-neutral-800 px-3 py-1.5 text-sm text-neutral-300 hover:border-neutral-600"
          >
            Log out
          </button>
        </div>

        <section className="mt-10">
          <h2 className="text-sm font-medium text-neutral-300">API keys</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Use one of these to authenticate requests from your own backend.
          </p>

          {revealedKey && (
            <div className="mt-4 rounded-md border border-emerald-900 bg-emerald-950/40 p-3">
              <p className="text-xs text-emerald-300">
                Copy this now &mdash; you won&apos;t be able to see the full value again.
              </p>
              <div className="mt-2 flex items-center gap-2">
                <code className="flex-1 truncate text-sm text-emerald-100">{revealedKey}</code>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(revealedKey);
                    setCopied(true);
                  }}
                  className="shrink-0 rounded-md bg-emerald-100 px-3 py-1.5 text-xs font-medium text-emerald-950 hover:bg-white"
                >
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
            </div>
          )}

          <div className="mt-4 divide-y divide-neutral-900 rounded-md border border-neutral-900">
            {keys.length === 0 && <p className="p-4 text-sm text-neutral-500">No API keys yet.</p>}
            {keys.map((key) => (
              <div key={key.id} className="flex items-center justify-between p-4">
                <div>
                  <p className="text-sm text-neutral-200">{key.name}</p>
                  <p className="mt-0.5 font-mono text-xs text-neutral-500">{key.prefix}&hellip;</p>
                  <p className="mt-0.5 text-xs text-neutral-600">
                    Created {new Date(key.created_at).toLocaleDateString()}
                    {key.revoked_at && " · Revoked"}
                  </p>
                </div>
                {!key.revoked_at && (
                  <button
                    onClick={() => handleRevoke(key.id)}
                    className="rounded-md border border-neutral-800 px-3 py-1.5 text-xs text-neutral-400 hover:border-red-800 hover:text-red-400"
                  >
                    Revoke
                  </button>
                )}
              </div>
            ))}
          </div>

          <form onSubmit={handleCreateKey} className="mt-4 flex gap-2">
            <input
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              placeholder="Key name (e.g. production)"
              className="flex-1 rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-neutral-500"
            />
            <button
              type="submit"
              disabled={creating}
              className="rounded-md bg-neutral-100 px-4 py-2 text-sm font-medium text-neutral-900 hover:bg-white disabled:opacity-50"
            >
              {creating ? "Creating..." : "New key"}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}
