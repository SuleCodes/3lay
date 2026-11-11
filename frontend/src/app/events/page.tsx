"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, apiFetch } from "@/lib/api";
import { DELIVERY_LABEL, VERDICT_LABEL, VERDICT_TONE, when } from "@/lib/format";
import type { EventPage, EventSummary, User } from "@/lib/types";

// The client's events (one per email processed), newest first. Filters narrow
// it to what needs attention; "Load more" fetches older ones (cursor paging).

type Filter = "all" | "needs_review" | "delivery_failed" | "failed";

const FILTERS: { key: Filter; label: string; query: string }[] = [
  { key: "all", label: "All", query: "" },
  { key: "needs_review", label: "Needs review", query: "needs_review=true" },
  { key: "delivery_failed", label: "Delivery failed", query: "delivery_failed=true" },
  { key: "failed", label: "Failed", query: "status=failed" },
];

export default function EventsPage() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (selected: Filter, after: string | null) => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams(FILTERS.find((f) => f.key === selected)!.query);
    if (after) params.set("cursor", after);
    try {
      const page = await apiFetch<EventPage>(`/events?${params}`);
      setEvents((current) => (after ? [...current, ...page.events] : page.events));
      setCursor(page.next_cursor);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't load your events.");
    } finally {
      setLoading(false);
    }
  }, []);

  // Signed in (with a username)? Then the first page. Later pages and filter
  // changes load from their buttons.
  useEffect(() => {
    apiFetch<User>("/auth/me")
      .then((u) => {
        if (!u.username) {
          router.replace("/onboarding");
          return;
        }
        setUser(u);
        load("all", null);
      })
      .catch(() => router.replace("/login"));
  }, [router, load]);

  function choose(selected: Filter) {
    setFilter(selected);
    load(selected, null);
  }

  if (!user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-neutral-950 text-neutral-100">
        <p className="text-sm text-neutral-400">Loading...</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-neutral-950 px-4 py-10 text-neutral-100">
      <div className="mx-auto max-w-4xl">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold">Events</h1>
            <p className="mt-1 text-sm text-neutral-400">
              Every email sent to <span className="font-mono text-neutral-300">{user.forwarding_address}</span>, and
              what happened to it.
            </p>
          </div>
          <Link
            href="/dashboard"
            className="shrink-0 rounded-md border border-neutral-800 px-3 py-1.5 text-sm text-neutral-300 hover:border-neutral-600"
          >
            Dashboard
          </Link>
        </div>

        <div className="mt-6 flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => choose(f.key)}
              className={`rounded-md border px-3 py-1.5 text-xs ${
                filter === f.key
                  ? "border-neutral-300 bg-neutral-100 text-neutral-900"
                  : "border-neutral-800 text-neutral-400 hover:border-neutral-600"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {error && <p className="mt-4 text-sm text-red-400">{error}</p>}

        <div className="mt-4 divide-y divide-neutral-900 rounded-md border border-neutral-900">
          {!loading && events.length === 0 && !error && (
            <p className="p-4 text-sm text-neutral-500">
              {filter === "all"
                ? "No events yet. They appear here as soon as an email arrives at your address."
                : "Nothing here: no events match this filter."}
            </p>
          )}
          {events.map((e) => (
            <Link
              key={`${e.event_id}-${e.run}`}
              href={`/events/${encodeURIComponent(e.event_id)}`}
              className="flex flex-wrap items-center justify-between gap-3 p-4 hover:bg-neutral-900/60"
            >
              <div className="min-w-0">
                <p className="text-sm text-neutral-200">{when(e.received_at)}</p>
                <p className="mt-0.5 truncate text-xs text-neutral-500">
                  {e.sender ?? "Unknown sender"} · {e.documents} document{e.documents === 1 ? "" : "s"}
                  {e.run > 1 && ` · run ${e.run}`}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {e.status === "failed" ? (
                  <span className="rounded border border-red-900 bg-red-950 px-2 py-0.5 text-xs text-red-300">
                    Failed{e.error_code ? `: ${e.error_code}` : ""}
                  </span>
                ) : (
                  (Object.keys(VERDICT_LABEL) as (keyof EventSummary["verdicts"])[])
                    .filter((v) => e.verdicts[v] > 0)
                    .map((v) => (
                      <span key={v} className={`rounded border px-2 py-0.5 text-xs ${VERDICT_TONE[v]}`}>
                        {e.verdicts[v]} {VERDICT_LABEL[v].toLowerCase()}
                      </span>
                    ))
                )}
                <span className={`text-xs ${DELIVERY_LABEL[e.delivery].tone}`}>{DELIVERY_LABEL[e.delivery].text}</span>
              </div>
            </Link>
          ))}
        </div>

        {loading && <p className="mt-4 text-sm text-neutral-500">Loading...</p>}
        {!loading && cursor && (
          <button
            onClick={() => load(filter, cursor)}
            className="mt-4 rounded-md border border-neutral-800 px-3 py-1.5 text-sm text-neutral-300 hover:border-neutral-600"
          >
            Load more
          </button>
        )}
      </div>
    </main>
  );
}
