"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError, apiFetch } from "@/lib/api";
import type { User, UsernameAvailability, UsernameClaimedResponse } from "@/lib/types";

// Mirrors the backend's rules (app/usernames.py) so obvious mistakes show
// instantly; the backend still has the final say.
const USERNAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function localProblem(username: string): string | null {
  if (username.length < 3 || username.length > 32) return "Username must be 3-32 characters long.";
  if (!USERNAME_PATTERN.test(username)) {
    return "Use lowercase letters, numbers and single hyphens, starting and ending with a letter or number.";
  }
  return null;
}

export default function OnboardingPage() {
  const router = useRouter();

  const [user, setUser] = useState<User | null>(null);
  const [username, setUsername] = useState("");
  // The last availability result. Only used while it matches what's typed.
  const [availability, setAvailability] = useState<UsernameAvailability | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [claimed, setClaimed] = useState<UsernameClaimedResponse | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    apiFetch<User>("/auth/me")
      .then((u) => {
        // Already onboarded: nothing to do here.
        if (u.username) router.replace("/dashboard");
        else setUser(u);
      })
      .catch(() => router.replace("/login"));
  }, [router]);

  const normalized = username.trim().toLowerCase();
  const problem = normalized ? localProblem(normalized) : null;

  // Check availability with the backend shortly after the user stops typing.
  useEffect(() => {
    if (!normalized || problem) return;

    const controller = new AbortController();
    const timer = setTimeout(() => {
      apiFetch<UsernameAvailability>(`/account/username-availability?username=${encodeURIComponent(normalized)}`, {
        signal: controller.signal,
      })
        .then(setAvailability)
        .catch(() => {
          if (controller.signal.aborted) return;
          setAvailability({
            username: normalized,
            available: false,
            reason: "Couldn't check availability. Try again in a moment.",
            forwarding_address: null,
          });
        });
    }, 300);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [normalized, problem]);

  const current = availability?.username === normalized ? availability : null;
  const checking = !!normalized && !problem && !current;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiFetch<UsernameClaimedResponse>("/account/username", {
        method: "POST",
        body: JSON.stringify({ username: normalized }),
      });
      setClaimed(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (!user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-neutral-950 text-neutral-100">
        <p className="text-sm text-neutral-400">Loading...</p>
      </main>
    );
  }

  // Step 2: show the forwarding address and the first API key, once.
  if (claimed) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-neutral-950 px-4 text-neutral-100">
        <div className="w-full max-w-md">
          <h1 className="text-xl font-semibold">You&apos;re all set</h1>

          <p className="mt-6 text-sm text-neutral-400">Your forwarding address</p>
          <p className="mt-1 rounded-md border border-neutral-800 bg-neutral-900 p-3 font-mono text-sm text-neutral-100">
            {claimed.user.forwarding_address}
          </p>
          <p className="mt-2 text-xs text-neutral-500">
            Have your users forward documents to this address and they&apos;ll be processed for your account.
          </p>

          <p className="mt-6 text-sm text-neutral-400">Your first API key</p>
          <div className="mt-1 rounded-md border border-emerald-900 bg-emerald-950/40 p-3">
            <p className="text-xs text-emerald-300">
              Copy this now &mdash; for your security, we won&apos;t show the full value again.
            </p>
            <div className="mt-2 flex items-center gap-2">
              <code className="flex-1 truncate text-sm text-emerald-100">{claimed.api_key.key}</code>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(claimed.api_key.key);
                  setCopied(true);
                }}
                className="shrink-0 rounded-md bg-emerald-100 px-3 py-1.5 text-xs font-medium text-emerald-950 hover:bg-white"
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </div>

          <button
            onClick={() => router.push("/dashboard")}
            className="mt-8 w-full rounded-md bg-neutral-100 px-3 py-2 text-sm font-medium text-neutral-900 hover:bg-white"
          >
            Continue to dashboard
          </button>
        </div>
      </main>
    );
  }

  // Step 1: choose a username.
  const canSubmit = !!current?.available && !submitting;
  const hint = problem
    ? { text: problem, tone: "text-red-400" }
    : checking
      ? { text: "Checking...", tone: "text-neutral-500" }
      : current
        ? current.available
          ? { text: `Available — your address will be ${current.forwarding_address}`, tone: "text-emerald-400" }
          : { text: current.reason ?? "Not available.", tone: "text-red-400" }
        : null;

  return (
    <main className="flex min-h-screen items-center justify-center bg-neutral-950 px-4 text-neutral-100">
      <form onSubmit={handleSubmit} className="w-full max-w-md">
        <h1 className="text-xl font-semibold">Choose your username</h1>
        <p className="mt-2 text-sm text-neutral-400">
          This becomes your forwarding address &mdash; the email your users forward documents to. It can&apos;t be
          changed later.
        </p>

        <label htmlFor="username" className="mt-6 block text-sm text-neutral-300">
          Username
        </label>
        <input
          id="username"
          autoFocus
          autoComplete="off"
          spellCheck={false}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="e.g. rolepay-agent"
          className="mt-2 w-full rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 font-mono text-sm outline-none focus:border-neutral-500"
        />
        <p className={`mt-2 min-h-5 text-sm ${hint?.tone ?? ""}`}>{hint?.text}</p>

        {error && <p className="mt-2 text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={!canSubmit}
          className="mt-4 w-full rounded-md bg-neutral-100 px-3 py-2 text-sm font-medium text-neutral-900 hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitting ? "Saving..." : "Continue"}
        </button>

        <p className="mt-4 text-xs text-neutral-500">Signed in as {user.email}</p>
      </form>
    </main>
  );
}
