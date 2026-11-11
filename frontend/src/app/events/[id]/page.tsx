"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ApiError, apiFetch } from "@/lib/api";
import { duration, VERDICT_LABEL, VERDICT_TONE, when } from "@/lib/format";
import type { EventDetail, User } from "@/lib/types";

// One event: what arrived, what was read from each document, a simple
// timeline, and every attempt to deliver it to the client's webhook, with
// Resend. This is the page behind every webhook's dashboard_url
// (app.3lay.live/events/<event_id>).

const OUTCOME_TONE: Record<string, string> = {
  ok: "bg-emerald-500",
  retried: "bg-amber-500",
  failed: "bg-red-500",
};

export default function EventPage() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const [event, setEvent] = useState<EventDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resending, setResending] = useState(false);
  const [resendMessage, setResendMessage] = useState<{ text: string; ok: boolean } | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<User>("/auth/me")
      .then(async (u) => {
        if (!u.username) {
          router.replace("/onboarding");
          return;
        }
        setEvent(await apiFetch<EventDetail>(`/events/${encodeURIComponent(id)}`));
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else setError(err instanceof ApiError && err.status === 404 ? "Event not found." : errorText(err));
      });
  }, [id, router]);

  function copy(key: string, text: string) {
    navigator.clipboard.writeText(text);
    setCopied(key);
  }

  async function handleResend() {
    if (!event) return;
    const before = event.deliveries.length;
    setResending(true);
    setResendMessage(null);
    try {
      const updated = await apiFetch<EventDetail>(`/events/${encodeURIComponent(id)}/resend`, { method: "POST" });
      setEvent(updated);
      const sent = updated.deliveries.slice(before);
      const failed = sent.filter((d) => d.status !== "delivered");
      setResendMessage(
        failed.length === 0
          ? { text: `Delivered (${sent.map((d) => d.http_status).join(", ")}).`, ok: true }
          : {
              text: `Not delivered: ${failed.map((d) => (d.http_status ? `HTTP ${d.http_status}` : d.error)).join("; ")}.`,
              ok: false,
            },
      );
    } catch (err) {
      setResendMessage({ text: errorText(err), ok: false });
    } finally {
      setResending(false);
    }
  }

  if (error || !event) {
    return (
      <main className="min-h-screen bg-neutral-950 px-4 py-10 text-neutral-100">
        <div className="mx-auto max-w-4xl">
          <BackLink />
          <p className="mt-6 text-sm text-neutral-400">{error ?? "Loading..."}</p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-neutral-950 px-4 py-10 text-neutral-100">
      <div className="mx-auto max-w-4xl">
        <BackLink />

        {/* Summary */}
        <div className="mt-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold">{when(event.received_at)}</h1>
            <p className="mt-1 text-sm text-neutral-400">
              From {event.sender ?? "an unknown sender"} · {event.documents.length} document
              {event.documents.length === 1 ? "" : "s"}
              {event.config_version !== null && ` · setup version ${event.config_version}`}
              {event.run > 1 && ` · run ${event.run}`}
            </p>
          </div>
          <StatusBadge status={event.status} />
        </div>

        {event.reference && (
          <div className="mt-4 flex items-center gap-2 text-xs text-neutral-500">
            <span>Reference</span>
            <code className="text-neutral-300">{event.reference}</code>
            <button onClick={() => copy("reference", event.reference!)} className="text-neutral-400 hover:text-neutral-200">
              {copied === "reference" ? "Copied" : "Copy"}
            </button>
            <span className="hidden sm:inline">· quote it if you contact 3lay about this event</span>
          </div>
        )}

        {event.status === "failed" && (
          <p className="mt-4 rounded-md border border-red-900 bg-red-950/40 p-3 text-sm text-red-200">
            {event.error_message || "This email couldn't be processed."}
            {event.error_code && <span className="ml-2 font-mono text-xs text-red-300">({event.error_code})</span>}
          </p>
        )}

        {event.data_deleted && (
          <p className="mt-4 rounded-md border border-neutral-800 p-3 text-sm text-neutral-400">
            This event is older than 90 days: the data read from its documents has been deleted.
          </p>
        )}

        {/* Timeline */}
        <section className="mt-8">
          <h2 className="text-sm font-medium text-neutral-300">Timeline</h2>
          <ol className="mt-3 space-y-3">
            {event.timeline.map((step) => (
              <li key={step.stage} className="flex gap-3">
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${OUTCOME_TONE[step.outcome]}`} />
                <div className="text-sm">
                  <p className="text-neutral-200">
                    {step.label}
                    {step.duration_ms !== null && (
                      <span className="ml-2 text-xs text-neutral-500">{duration(step.duration_ms)}</span>
                    )}
                  </p>
                  {step.detail && <p className="text-xs text-neutral-500">{step.detail}</p>}
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* Documents */}
        <section className="mt-8">
          <h2 className="text-sm font-medium text-neutral-300">Documents</h2>
          {event.documents.length === 0 && (
            <p className="mt-2 text-sm text-neutral-500">No documents were found in this email.</p>
          )}
          <div className="mt-3 space-y-3">
            {event.documents.map((doc, i) => (
              <div key={i} className="rounded-md border border-neutral-900 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm text-neutral-200">{doc.name}</p>
                    {doc.forwarded_from && (
                      <p className="text-xs text-neutral-500">Forwarded from {doc.forwarded_from}</p>
                    )}
                  </div>
                  {doc.verdict && (
                    <span className={`rounded border px-2 py-0.5 text-xs ${VERDICT_TONE[doc.verdict]}`}>
                      {VERDICT_LABEL[doc.verdict]}
                    </span>
                  )}
                </div>
                {doc.reasons.length > 0 && (
                  <ul className="mt-2 space-y-0.5 text-xs text-amber-300">
                    {doc.reasons.map((r, j) => (
                      <li key={j}>· {r}</li>
                    ))}
                  </ul>
                )}
                {doc.data && (
                  <div className="mt-3">
                    <div className="flex items-center justify-between">
                      <p className="text-xs text-neutral-500">Extracted data</p>
                      <button
                        onClick={() => copy(`doc-${i}`, JSON.stringify(doc.data, null, 2))}
                        className="text-xs text-neutral-400 hover:text-neutral-200"
                      >
                        {copied === `doc-${i}` ? "Copied" : "Copy JSON"}
                      </button>
                    </div>
                    <pre className="mt-1 max-h-80 overflow-auto rounded bg-neutral-900 p-3 text-xs text-neutral-300">
                      {JSON.stringify(doc.data, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* Deliveries */}
        <section className="mt-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-medium text-neutral-300">Webhook deliveries</h2>
            <div className="flex items-center gap-3">
              {!event.can_resend && event.resend_unavailable_reason && (
                <span className="text-xs text-neutral-500">{event.resend_unavailable_reason}</span>
              )}
              <button
                onClick={handleResend}
                disabled={!event.can_resend || resending}
                className="rounded-md bg-neutral-100 px-3 py-1.5 text-xs font-medium text-neutral-900 hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
              >
                {resending ? "Sending..." : "Resend"}
              </button>
            </div>
          </div>
          {resendMessage && (
            <p className={`mt-2 text-sm ${resendMessage.ok ? "text-emerald-400" : "text-red-400"}`}>{resendMessage.text}</p>
          )}
          <p className="mt-1 text-xs text-neutral-500">
            Resend sends the same message again (same webhook-id) to your live setup&apos;s webhook, once.
          </p>
          {event.deliveries.length === 0 ? (
            <p className="mt-3 text-sm text-neutral-500">Nothing was sent: no webhook in your setup receives this kind of event.</p>
          ) : (
            <div className="mt-3 overflow-x-auto rounded-md border border-neutral-900">
              <table className="w-full text-left text-xs">
                <thead className="text-neutral-500">
                  <tr className="border-b border-neutral-900">
                    <th className="p-2 font-normal">When</th>
                    <th className="p-2 font-normal">Attempt</th>
                    <th className="p-2 font-normal">Result</th>
                    <th className="p-2 font-normal">Response</th>
                    <th className="p-2 font-normal">URL</th>
                  </tr>
                </thead>
                <tbody>
                  {event.deliveries.map((d, i) => (
                    <tr key={i} className="border-b border-neutral-900 align-top last:border-0">
                      <td className="whitespace-nowrap p-2 text-neutral-300">{when(d.attempted_at)}</td>
                      <td className="whitespace-nowrap p-2 text-neutral-400">
                        #{d.attempt}
                        {d.trigger === "manual" && <span className="ml-1 text-neutral-500">(resend)</span>}
                      </td>
                      <td className="whitespace-nowrap p-2">
                        <span className={d.status === "delivered" ? "text-emerald-400" : "text-red-400"}>
                          {d.http_status ?? "—"} {d.status === "delivered" ? "delivered" : d.status === "retry" ? "retried" : "failed"}
                        </span>
                        {d.duration_ms !== null && <span className="ml-1 text-neutral-500">{duration(d.duration_ms)}</span>}
                      </td>
                      <td className="max-w-56 p-2 text-neutral-400">
                        <span className="line-clamp-2 break-all">{d.error ?? d.response_excerpt ?? ""}</span>
                      </td>
                      <td className="max-w-56 truncate p-2 font-mono text-neutral-500">{d.url}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function BackLink() {
  return (
    <Link href="/events" className="text-sm text-neutral-400 hover:text-neutral-200">
      ← All events
    </Link>
  );
}

function StatusBadge({ status }: { status: EventDetail["status"] }) {
  const tone =
    status === "completed"
      ? "border-emerald-900 bg-emerald-950 text-emerald-300"
      : status === "failed"
        ? "border-red-900 bg-red-950 text-red-300"
        : "border-neutral-800 bg-neutral-900 text-neutral-300";
  const label = status === "completed" ? "Completed" : status === "failed" ? "Failed" : "Processing";
  return <span className={`rounded border px-2 py-1 text-xs ${tone}`}>{label}</span>;
}

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
}
