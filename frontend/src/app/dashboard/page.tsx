"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError, apiFetch } from "@/lib/api";
import type {
  ApiKey,
  ApiKeyCreatedResponse,
  DeleteAccountResponse,
  RotatedSecret,
  User,
  Webhooks,
} from "@/lib/types";

const EVENT_LABELS: Record<string, string> = {
  "event.completed": "were processed",
  "event.failed": "couldn't be processed",
};

export default function DashboardPage() {
  const router = useRouter();

  const [user, setUser] = useState<User | null>(null);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);

  const [newKeyName, setNewKeyName] = useState("");
  const [creating, setCreating] = useState(false);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [addressCopied, setAddressCopied] = useState(false);

  const [webhooks, setWebhooks] = useState<Webhooks | null>(null);
  const [webhookError, setWebhookError] = useState<string | null>(null);
  const [confirmingRotate, setConfirmingRotate] = useState<string | null>(null);
  const [rotating, setRotating] = useState(false);
  // A new signing secret: shown once, until the page is left.
  const [newSecret, setNewSecret] = useState<string | null>(null);
  const [secretCopied, setSecretCopied] = useState(false);

  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteConfirmEmail, setDeleteConfirmEmail] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function loadKeys() {
    const data = await apiFetch<ApiKey[]>("/api-keys");
    setKeys(data);
  }

  async function loadWebhooks() {
    try {
      setWebhooks(await apiFetch<Webhooks>("/webhooks"));
    } catch (err) {
      setWebhookError(err instanceof ApiError ? err.message : "Couldn't load your webhooks.");
    }
  }

  async function handleRotate(webhookId: string) {
    setRotating(true);
    setWebhookError(null);
    try {
      const res = await apiFetch<RotatedSecret>(`/webhooks/${encodeURIComponent(webhookId)}/rotate-secret`, {
        method: "POST",
      });
      setNewSecret(res.secret);
      setSecretCopied(false);
      setConfirmingRotate(null);
      await loadWebhooks();
    } catch (err) {
      setWebhookError(err instanceof ApiError ? err.message : "Couldn't create a new secret.");
    } finally {
      setRotating(false);
    }
  }

  useEffect(() => {
    apiFetch<User>("/auth/me")
      .then(async (u) => {
        // No username yet: onboarding comes first (it also creates the
        // first API key; keys can't be created before it).
        if (!u.username) {
          router.replace("/onboarding");
          return;
        }
        setUser(u);
        await Promise.all([loadKeys(), loadWebhooks()]);
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

  async function handleDeleteAccount(e: FormEvent) {
    e.preventDefault();
    setDeleting(true);
    setDeleteError(null);
    try {
      await apiFetch<DeleteAccountResponse>("/account", {
        method: "DELETE",
        body: JSON.stringify({ confirm_email: deleteConfirmEmail }),
      });
      router.replace("/login");
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
      setDeleting(false);
    }
  }

  const deleteConfirmMatches =
    !!user && deleteConfirmEmail.trim().toLowerCase() === user.email.toLowerCase();

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

        <section className="mt-10 flex items-center justify-between gap-4 rounded-md border border-neutral-800 p-4">
          <div>
            <h2 className="text-sm font-medium text-neutral-300">Set up processing</h2>
            <p className="mt-1 text-sm text-neutral-500">
              Chat with Boardy, our onboarding assistant, to set up how your documents are read, checked and
              delivered.
            </p>
          </div>
          <button
            onClick={() => router.push("/setup")}
            className="shrink-0 rounded-md bg-neutral-100 px-3 py-1.5 text-sm font-medium text-neutral-900 hover:bg-white"
          >
            Open
          </button>
        </section>

        <section className="mt-10">
          <h2 className="text-sm font-medium text-neutral-300">Forwarding address</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Have your users forward documents here and they&apos;ll be processed for your account.
          </p>
          <div className="mt-3 flex items-center gap-2 rounded-md border border-neutral-900 p-3">
            <code className="flex-1 truncate text-sm text-neutral-200">{user?.forwarding_address}</code>
            <button
              onClick={() => {
                if (user?.forwarding_address) navigator.clipboard.writeText(user.forwarding_address);
                setAddressCopied(true);
              }}
              className="shrink-0 rounded-md border border-neutral-800 px-3 py-1.5 text-xs text-neutral-300 hover:border-neutral-600"
            >
              {addressCopied ? "Copied" : "Copy"}
            </button>
          </div>
        </section>

        <section className="mt-10">
          <div className="flex items-center justify-between gap-4">
            <h2 className="text-sm font-medium text-neutral-300">Webhooks</h2>
            <button onClick={() => router.push("/setup")} className="text-xs text-neutral-400 hover:text-neutral-200">
              Change with Boardy
            </button>
          </div>
          <p className="mt-1 text-sm text-neutral-500">
            Where your results are delivered. Each delivery is signed with the webhook&apos;s secret, so your server
            can check it came from 3lay.
            {webhooks?.active_version != null && ` From your live setup (version ${webhooks.active_version}).`}
          </p>

          {webhookError && <p className="mt-3 text-sm text-red-400">{webhookError}</p>}

          {newSecret && (
            <div className="mt-4 rounded-md border border-emerald-900 bg-emerald-950/40 p-3">
              <p className="text-xs text-emerald-300">
                Your new signing secret. Copy it now &mdash; you won&apos;t be able to see it again. Update your server
                with it: the previous secret keeps working until the time shown below.
              </p>
              <div className="mt-2 flex items-center gap-2">
                <code className="flex-1 truncate text-sm text-emerald-100">{newSecret}</code>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(newSecret);
                    setSecretCopied(true);
                  }}
                  className="shrink-0 rounded-md bg-emerald-100 px-3 py-1.5 text-xs font-medium text-emerald-950 hover:bg-white"
                >
                  {secretCopied ? "Copied" : "Copy"}
                </button>
              </div>
            </div>
          )}

          <div className="mt-4 divide-y divide-neutral-900 rounded-md border border-neutral-900">
            {webhooks && webhooks.webhooks.length === 0 && (
              <p className="p-4 text-sm text-neutral-500">No webhooks yet. Set up processing with Boardy to add one.</p>
            )}
            {webhooks?.webhooks.map((hook) => (
              <div key={hook.id} className="p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm text-neutral-200">{hook.url}</p>
                    <p className="mt-0.5 text-xs text-neutral-500">
                      Receives documents that {hook.events.map((e) => EVENT_LABELS[e] ?? e).join(" and ")}
                    </p>
                    <p className="mt-0.5 text-xs text-neutral-600">
                      {hook.secret_prefix ? (
                        <>
                          Secret <span className="font-mono">{hook.secret_prefix}&hellip;</span>
                          {hook.secret_rotated_at
                            ? ` · rotated ${new Date(hook.secret_rotated_at).toLocaleString()}`
                            : hook.secret_created_at
                              ? ` · created ${new Date(hook.secret_created_at).toLocaleDateString()}`
                              : ""}
                        </>
                      ) : (
                        "No signing secret yet"
                      )}
                    </p>
                    {hook.previous_secret_expires_at && (
                      <p className="mt-0.5 text-xs text-amber-400">
                        The previous secret also works until {new Date(hook.previous_secret_expires_at).toLocaleString()}.
                      </p>
                    )}
                  </div>
                  {confirmingRotate !== hook.id && (
                    <button
                      onClick={() => setConfirmingRotate(hook.id)}
                      className="shrink-0 rounded-md border border-neutral-800 px-3 py-1.5 text-xs text-neutral-400 hover:border-neutral-600"
                    >
                      {hook.secret_prefix ? "Rotate secret" : "Create secret"}
                    </button>
                  )}
                </div>
                {confirmingRotate === hook.id && (
                  <div className="mt-3 rounded-md border border-neutral-800 p-3">
                    <p className="text-xs text-neutral-400">
                      {hook.secret_prefix
                        ? "A new secret is created and shown once. The current one keeps working for 24 hours, so you can update your server without missing deliveries."
                        : "A secret is created and shown once."}
                    </p>
                    <div className="mt-2 flex gap-2">
                      <button
                        onClick={() => handleRotate(hook.id)}
                        disabled={rotating}
                        className="rounded-md bg-neutral-100 px-3 py-1.5 text-xs font-medium text-neutral-900 hover:bg-white disabled:opacity-50"
                      >
                        {rotating ? "Creating..." : "Create new secret"}
                      </button>
                      <button
                        onClick={() => setConfirmingRotate(null)}
                        className="rounded-md border border-neutral-800 px-3 py-1.5 text-xs text-neutral-400 hover:border-neutral-600"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>

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

        <section className="mt-16 rounded-md border border-red-950 p-4">
          <h2 className="text-sm font-medium text-red-400">Danger zone</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Permanently delete your account, your API keys and every email stored for
            {user?.forwarding_address ? (
              <>
                {" "}
                <span className="font-mono text-neutral-400">{user.forwarding_address}</span>
              </>
            ) : (
              " your forwarding address"
            )}
            . This can&apos;t be undone, and your username can&apos;t be used again.
          </p>

          {!confirmingDelete ? (
            <button
              onClick={() => setConfirmingDelete(true)}
              className="mt-4 rounded-md border border-red-900 px-3 py-1.5 text-sm text-red-400 hover:bg-red-950/50"
            >
              Delete account
            </button>
          ) : (
            <form onSubmit={handleDeleteAccount} className="mt-4">
              <label htmlFor="delete-confirm" className="text-sm text-neutral-300">
                Type <span className="font-mono text-neutral-100">{user?.email}</span> to confirm.
              </label>
              <input
                id="delete-confirm"
                type="email"
                autoComplete="off"
                value={deleteConfirmEmail}
                onChange={(e) => setDeleteConfirmEmail(e.target.value)}
                className="mt-2 w-full rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-red-800"
              />
              {deleteError && <p className="mt-2 text-sm text-red-400">{deleteError}</p>}
              <div className="mt-3 flex gap-2">
                <button
                  type="submit"
                  disabled={!deleteConfirmMatches || deleting}
                  className="rounded-md bg-red-700 px-4 py-2 text-sm font-medium text-white hover:bg-red-600 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {deleting ? "Deleting..." : "Permanently delete account"}
                </button>
                <button
                  type="button"
                  disabled={deleting}
                  onClick={() => {
                    setConfirmingDelete(false);
                    setDeleteConfirmEmail("");
                    setDeleteError(null);
                  }}
                  className="rounded-md border border-neutral-800 px-4 py-2 text-sm text-neutral-300 hover:border-neutral-600"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </section>
      </div>
    </main>
  );
}
