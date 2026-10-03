"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import type { VerifyResponse } from "@/lib/types";

function VerifyInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [state, setState] = useState<"verifying" | "done" | "error">("verifying");
  const [createdApiKey, setCreatedApiKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // A magic link token is single-use, but React 19's Strict Mode
  // double-invokes effects in dev -- without this guard, the second
  // invocation would replay the same (now-consumed) token and stomp the
  // successful result with a 400. This ref makes the effect idempotent per
  // token regardless of how many times it's invoked.
  const verifiedTokenRef = useRef<string | null>(null);

  useEffect(() => {
    if (!token) {
      setError("This link is missing its token.");
      setState("error");
      return;
    }

    if (verifiedTokenRef.current === token) {
      return;
    }
    verifiedTokenRef.current = token;

    apiFetch<VerifyResponse>(`/auth/verify?token=${encodeURIComponent(token)}`)
      .then((res) => {
        if (res.created_api_key) {
          setCreatedApiKey(res.created_api_key);
          setState("done");
        } else {
          router.replace("/dashboard");
        }
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "This link is invalid or has expired.");
        setState("error");
      });
    // Only ever run once per token.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (state === "verifying") {
    return <p className="text-sm text-neutral-400">Verifying your sign-in link...</p>;
  }

  if (state === "error") {
    return (
      <div className="text-center">
        <p className="text-sm text-red-400">{error}</p>
        <a href="/login" className="mt-4 inline-block text-sm text-neutral-300 underline">
          Back to sign in
        </a>
      </div>
    );
  }

  return (
    <div className="w-full max-w-md text-center">
      <h1 className="text-xl font-semibold">Welcome to 3lay</h1>
      <p className="mt-2 text-sm text-neutral-400">
        Here&apos;s your first API key. Copy it now &mdash; for your security, we won&apos;t show the full value
        again.
      </p>

      <div className="mt-6 flex items-center gap-2 rounded-md border border-neutral-800 bg-neutral-900 p-3">
        <code className="flex-1 truncate text-left text-sm text-neutral-200">{createdApiKey}</code>
        <button
          onClick={() => {
            if (createdApiKey) {
              navigator.clipboard.writeText(createdApiKey);
              setCopied(true);
            }
          }}
          className="shrink-0 rounded-md bg-neutral-100 px-3 py-1.5 text-xs font-medium text-neutral-900 hover:bg-white"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>

      <button
        onClick={() => router.push("/dashboard")}
        className="mt-6 w-full rounded-md bg-neutral-100 px-3 py-2 text-sm font-medium text-neutral-900 hover:bg-white"
      >
        Continue to dashboard
      </button>
    </div>
  );
}

export default function VerifyPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-neutral-950 px-4 text-neutral-100">
      <Suspense fallback={<p className="text-sm text-neutral-400">Loading...</p>}>
        <VerifyInner />
      </Suspense>
    </main>
  );
}
