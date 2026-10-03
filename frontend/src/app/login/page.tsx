"use client";

import { useState, type FormEvent } from "react";
import { apiFetch } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setStatus("sending");
    setError(null);

    try {
      await apiFetch("/auth/request-link", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      setStatus("sent");
    } catch {
      setError("Something went wrong sending the link. Try again in a moment.");
      setStatus("error");
    }
  }

  if (status === "sent") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-neutral-950 px-4 text-neutral-100">
        <div className="w-full max-w-sm text-center">
          <h1 className="text-xl font-semibold">Check your email</h1>
          <p className="mt-2 text-sm text-neutral-400">
            We sent a sign-in link to <span className="text-neutral-200">{email}</span>. It expires in 15 minutes.
          </p>
          <p className="mt-6 text-xs text-neutral-500">
            Running the backend locally without email configured? The link is printed to the backend console instead.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-neutral-950 px-4 text-neutral-100">
      <form onSubmit={handleSubmit} className="w-full max-w-sm">
        <h1 className="text-xl font-semibold">Sign in to 3lay</h1>
        <p className="mt-2 text-sm text-neutral-400">
          No password needed &mdash; enter your email and we&apos;ll send you a sign-in link. New here? This creates
          your account too.
        </p>

        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          className="mt-6 w-full rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-neutral-500"
        />

        {error && <p className="mt-2 text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={status === "sending"}
          className="mt-4 w-full rounded-md bg-neutral-100 px-3 py-2 text-sm font-medium text-neutral-900 transition hover:bg-white disabled:opacity-50"
        >
          {status === "sending" ? "Sending..." : "Send sign-in link"}
        </button>
      </form>
    </main>
  );
}
