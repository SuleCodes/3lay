"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import type { VerifyResponse } from "@/lib/types";

function VerifyInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [state, setState] = useState<"verifying" | "error">("verifying");
  const [error, setError] = useState<string | null>(null);

  // A magic link token is single-use, but React 19's Strict Mode
  // double-invokes effects in dev -- without this guard, the second
  // invocation would replay the same (now-consumed) token and stomp the
  // successful result with a 400. This ref makes the effect idempotent per
  // token regardless of how many times it's invoked.
  const verifiedTokenRef = useRef<string | null>(null);

  useEffect(() => {
    if (!token || verifiedTokenRef.current === token) {
      return;
    }
    verifiedTokenRef.current = token;

    apiFetch<VerifyResponse>(`/auth/verify?token=${encodeURIComponent(token)}`)
      .then((res) => {
        // New users choose a username first; that step also creates their
        // first API key.
        router.replace(res.needs_username ? "/onboarding" : "/dashboard");
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "This link is invalid or has expired.");
        setState("error");
      });
    // Only ever run once per token.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (!token) {
    return (
      <div className="text-center">
        <p className="text-sm text-red-400">This link is missing its token.</p>
        <a href="/login" className="mt-4 inline-block text-sm text-neutral-300 underline">
          Back to sign in
        </a>
      </div>
    );
  }

  if (state === "verifying") {
    return <p className="text-sm text-neutral-400">Verifying your sign-in link...</p>;
  }

  return (
    <div className="text-center">
      <p className="text-sm text-red-400">{error}</p>
      <a href="/login" className="mt-4 inline-block text-sm text-neutral-300 underline">
        Back to sign in
      </a>
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
