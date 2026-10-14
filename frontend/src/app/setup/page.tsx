"use client";

import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError, apiFetch, apiUpload } from "@/lib/api";
import type { BoardyConversation, BoardyConversationSummary, User } from "@/lib/types";

// Setting up processing by chatting with Boardy, 3lay's onboarding agent.
// Each message runs a full Boardy turn on the API (several seconds when it
// uses its tools); the page waits for the whole reply.

const SAMPLE_TYPES = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
}

export default function SetupPage() {
  const router = useRouter();
  const bottom = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const [user, setUser] = useState<User | null>(null);
  const [conversation, setConversation] = useState<BoardyConversation | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null); // what's running, for the status line
  const [error, setError] = useState<string | null>(null);
  const [comments, setComments] = useState("");
  const [feedback, setFeedback] = useState("");
  const [feedbackSaved, setFeedbackSaved] = useState(false);
  // Webhook signing secrets from switching on: only in that one response, so kept until dismissed.
  const [secrets, setSecrets] = useState<BoardyConversation["new_secrets"]>([]);
  const [copiedSecret, setCopiedSecret] = useState<string | null>(null);

  // Sign-in and username first, then the latest conversation (or a new one).
  useEffect(() => {
    apiFetch<User>("/auth/me")
      .then(async (u) => {
        if (!u.username) {
          router.replace("/onboarding");
          return;
        }
        setUser(u);
        const existing = await apiFetch<BoardyConversationSummary[]>("/boardy/conversations");
        const latest = existing[0]
          ? await apiFetch<BoardyConversation>(`/boardy/conversations/${existing[0].id}`)
          : await apiFetch<BoardyConversation>("/boardy/conversations", { method: "POST" });
        show(latest);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else setError(errorText(err));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [conversation?.messages.length, busy]);

  function show(c: BoardyConversation) {
    setConversation(c);
    setFeedback(c.feedback ?? "");
    if (c.new_secrets.length > 0) setSecrets(c.new_secrets);
  }

  // Runs one request that changes the conversation, showing what's happening.
  async function act(label: string, request: () => Promise<BoardyConversation>) {
    setBusy(label);
    setError(null);
    try {
      show(await request());
      return true;
    } catch (err) {
      setError(errorText(err));
      // The conversation may have moved on (e.g. a turn failed part-way): show where it is now.
      if (conversation) {
        apiFetch<BoardyConversation>(`/boardy/conversations/${conversation.id}`).then(show).catch(() => {});
      }
      return false;
    } finally {
      setBusy(null);
    }
  }

  const base = conversation ? `/boardy/conversations/${conversation.id}` : "";

  async function handleSend(e?: FormEvent) {
    e?.preventDefault();
    const message = text.trim();
    if (!conversation || !message || busy) return;
    setText("");
    // Show the client's message straight away, while Boardy works.
    setConversation({
      ...conversation,
      messages: [...conversation.messages, { role: "client", text: message, steps: [] }],
    });
    const sent = await act("Boardy is working…", () =>
      apiFetch<BoardyConversation>(`${base}/messages`, { method: "POST", body: JSON.stringify({ text: message }) }),
    );
    if (!sent) setText(message);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  async function handleSwitchOn() {
    await act("Switching it on…", () => apiFetch<BoardyConversation>(`${base}/activate`, { method: "POST" }));
  }

  async function handleApproval(approved: boolean) {
    const answered = await act(approved ? "Switching your setup on…" : "Sending your changes…", () =>
      apiFetch<BoardyConversation>(`${base}/approval`, {
        method: "POST",
        body: JSON.stringify({ approved, comments }),
      }),
    );
    if (answered) setComments("");
  }

  async function handleUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // so the same file can be chosen again
    if (file) await act(`Uploading ${file.name}…`, () => apiUpload<BoardyConversation>(`${base}/samples`, file));
  }

  async function handleFeedback(e: FormEvent) {
    e.preventDefault();
    const saved = await act("Saving feedback…", () =>
      apiFetch<BoardyConversation>(`${base}/feedback`, { method: "PUT", body: JSON.stringify({ text: feedback }) }),
    );
    setFeedbackSaved(saved);
  }

  async function handleNewConversation() {
    setComments("");
    setFeedbackSaved(false);
    await act("Starting a new conversation…", () =>
      apiFetch<BoardyConversation>("/boardy/conversations", { method: "POST" }),
    );
  }

  if (!user || !conversation) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-neutral-950 text-neutral-100">
        <p className="text-sm text-neutral-400">{error ?? "Loading..."}</p>
      </main>
    );
  }

  const waitingForApproval = conversation.status === "awaiting_approval";
  const failed = conversation.status === "failed";
  const canType = !busy && !waitingForApproval && !failed;

  return (
    <main className="min-h-screen bg-neutral-950 px-4 py-10 text-neutral-100">
      <div className="mx-auto max-w-2xl">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold">Set up processing</h1>
            <p className="mt-1 text-sm text-neutral-400">
              Boardy, 3lay&apos;s onboarding assistant, sets up how documents sent to{" "}
              <span className="font-mono text-neutral-300">{user.forwarding_address}</span> are processed.
            </p>
          </div>
          <button
            onClick={() => router.push("/dashboard")}
            className="shrink-0 rounded-md border border-neutral-800 px-3 py-1.5 text-sm text-neutral-300 hover:border-neutral-600"
          >
            Dashboard
          </button>
        </div>

        {secrets.length > 0 && (
          <div className="mt-6 rounded-md border border-emerald-900 bg-emerald-950/40 p-4">
            <h2 className="text-sm font-medium text-emerald-200">Your webhook signing secret</h2>
            <p className="mt-1 text-xs text-emerald-300">
              Copy it now and keep it safe: for your security, we won&apos;t show it again. Your server uses it to
              check that results really come from 3lay.
            </p>
            {secrets.map((s) => (
              <div key={s.destination_id} className="mt-3">
                <p className="truncate text-xs text-neutral-400">{s.url}</p>
                <div className="mt-1 flex items-center gap-2">
                  <code className="flex-1 truncate text-sm text-emerald-100">{s.secret}</code>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(s.secret);
                      setCopiedSecret(s.destination_id);
                    }}
                    className="shrink-0 rounded-md bg-emerald-100 px-3 py-1.5 text-xs font-medium text-emerald-950 hover:bg-white"
                  >
                    {copiedSecret === s.destination_id ? "Copied" : "Copy"}
                  </button>
                </div>
              </div>
            ))}
            <button
              onClick={() => setSecrets([])}
              className="mt-4 text-xs text-emerald-300 underline hover:text-emerald-100"
            >
              I&apos;ve saved it
            </button>
          </div>
        )}

        {conversation.changes_pending && !waitingForApproval && (
          <p className="mt-6 rounded-md border border-amber-900 bg-amber-950/40 p-3 text-sm text-amber-200">
            You&apos;ve changed your setup since you approved it. The changes aren&apos;t live yet: ask Boardy to submit
            the updated setup, then approve it.
          </p>
        )}

        {conversation.activation_pending ? (
          <div className="mt-6 flex items-center justify-between gap-3 rounded-md border border-amber-900 bg-amber-950/40 p-3">
            <p className="text-sm text-amber-200">
              {conversation.activation_error ?? "You approved this setup, but it isn't switched on yet."}
            </p>
            <button
              onClick={handleSwitchOn}
              disabled={!!busy}
              className="shrink-0 rounded-md bg-amber-100 px-3 py-1.5 text-xs font-medium text-amber-950 hover:bg-white disabled:opacity-40"
            >
              Switch on
            </button>
          </div>
        ) : (
          conversation.approved &&
          conversation.saved_version !== null &&
          conversation.saved_version === conversation.active_version && (
            <p className="mt-6 rounded-md border border-emerald-900 bg-emerald-950/40 p-3 text-sm text-emerald-200">
              Your setup is live (version {conversation.saved_version}): documents sent to{" "}
              <span className="font-mono">{user.forwarding_address}</span> are processed with it.
            </p>
          )
        )}

        {/* The conversation */}
        <section className="mt-8 space-y-4">
          {conversation.messages.length === 0 && (
            <p className="rounded-md border border-neutral-900 p-4 text-sm text-neutral-400">
              Say hello and tell Boardy about your business: who sends you documents, what they are, and what you do
              with them. If you have an example document, add it as a sample and Boardy will read it.
            </p>
          )}
          {conversation.messages.map((m, i) =>
            m.role === "client" ? (
              <div key={i} className="flex justify-end">
                <p className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-neutral-100 px-3 py-2 text-sm text-neutral-900">
                  {m.text}
                </p>
              </div>
            ) : (
              <div key={i} className="max-w-[90%]">
                {m.steps.length > 0 && (
                  <ul className="mb-1 space-y-0.5 text-xs text-neutral-500">
                    {m.steps.map((step, j) => (
                      <li key={j}>· {step}</li>
                    ))}
                  </ul>
                )}
                <p className="whitespace-pre-wrap rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100">
                  {m.text}
                </p>
              </div>
            ),
          )}
          {busy && <p className="text-sm text-neutral-400">{busy}</p>}
          <div ref={bottom} />
        </section>

        {error && <p className="mt-4 text-sm text-red-400">{error}</p>}

        {failed && !busy && (
          <div className="mt-4 flex items-center justify-between gap-3 rounded-md border border-amber-900 bg-amber-950/40 p-3">
            <p className="text-sm text-amber-200">Boardy didn&apos;t finish its last reply.</p>
            <button
              onClick={() => act("Boardy is working…", () => apiFetch<BoardyConversation>(`${base}/retry`, { method: "POST" }))}
              className="shrink-0 rounded-md bg-amber-100 px-3 py-1.5 text-xs font-medium text-amber-950 hover:bg-white"
            >
              Try again
            </button>
          </div>
        )}

        {/* Boardy's approval question: the conversation waits for the answer */}
        {waitingForApproval && conversation.approval && (
          <div className="mt-6 rounded-md border border-sky-900 bg-sky-950/40 p-4">
            <h2 className="text-sm font-medium text-sky-200">Boardy asks for your approval</h2>
            <p className="mt-2 whitespace-pre-wrap text-sm text-neutral-200">{conversation.approval.summary}</p>
            <p className="mt-3 text-sm text-sky-200">
              Approving switches this setup on straight away: documents sent to{" "}
              <span className="font-mono">{user.forwarding_address}</span> from then on are processed with it
              {conversation.active_version !== null ? ", replacing your current setup" : ""}.
            </p>
            <textarea
              value={comments}
              onChange={(e) => setComments(e.target.value)}
              placeholder="Comments or changes (optional)"
              rows={2}
              className="mt-3 w-full rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-neutral-500"
            />
            <div className="mt-3 flex gap-2">
              <button
                onClick={() => handleApproval(true)}
                disabled={!!busy}
                className="rounded-md bg-sky-100 px-3 py-1.5 text-sm font-medium text-sky-950 hover:bg-white disabled:opacity-40"
              >
                Approve and switch on
              </button>
              <button
                onClick={() => handleApproval(false)}
                disabled={!!busy}
                className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm text-neutral-200 hover:border-neutral-500 disabled:opacity-40"
              >
                Request changes
              </button>
            </div>
          </div>
        )}

        {/* Writing to Boardy */}
        <form onSubmit={handleSend} className="mt-6">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={!canType}
            rows={3}
            placeholder={waitingForApproval ? "Answer Boardy's approval question above." : "Write to Boardy…"}
            className="w-full rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-neutral-500 disabled:opacity-50"
          />
          <div className="mt-2 flex items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                disabled={!!busy || conversation.samples.length >= 5}
                className="rounded-md border border-neutral-800 px-3 py-1.5 text-xs text-neutral-300 hover:border-neutral-600 disabled:opacity-40"
              >
                Add sample
              </button>
              <input ref={fileInput} type="file" accept={SAMPLE_TYPES} onChange={handleUpload} className="hidden" />
              {conversation.samples.map((s) => (
                <span key={s.id} className="rounded-md bg-neutral-900 px-2 py-1 text-xs text-neutral-400">
                  {s.name}
                </span>
              ))}
            </div>
            <button
              type="submit"
              disabled={!canType || !text.trim()}
              className="shrink-0 rounded-md bg-neutral-100 px-4 py-1.5 text-sm font-medium text-neutral-900 hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              Send
            </button>
          </div>
          <p className="mt-2 text-xs text-neutral-500">
            Samples: PDF, JPEG or PNG, up to 5. Boardy reads them when you next send a message. Use invented or
            anonymised documents where you can.
          </p>
        </form>

        {conversation.missing.length > 0 && conversation.messages.length > 0 && (
          <section className="mt-8">
            <h2 className="text-sm font-medium text-neutral-300">Still needed</h2>
            <ul className="mt-2 space-y-1 text-sm text-neutral-400">
              {conversation.missing.map((item) => (
                <li key={item}>· {item}</li>
              ))}
            </ul>
          </section>
        )}

        {/* Feedback on Boardy, for 3lay */}
        <form onSubmit={handleFeedback} className="mt-10 rounded-md border border-neutral-900 p-4">
          <h2 className="text-sm font-medium text-neutral-300">Feedback for 3lay</h2>
          <p className="mt-1 text-sm text-neutral-500">
            How was this? Anything confusing, wrong or missing? It helps us improve Boardy.
          </p>
          <textarea
            value={feedback}
            onChange={(e) => {
              setFeedback(e.target.value);
              setFeedbackSaved(false);
            }}
            rows={3}
            className="mt-3 w-full rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-neutral-500"
          />
          <div className="mt-2 flex items-center gap-3">
            <button
              type="submit"
              disabled={!!busy}
              className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm text-neutral-200 hover:border-neutral-500 disabled:opacity-40"
            >
              Save feedback
            </button>
            {feedbackSaved && <span className="text-xs text-emerald-400">Saved. Thank you!</span>}
          </div>
        </form>

        <div className="mt-10 border-t border-neutral-900 pt-4">
          <button
            onClick={handleNewConversation}
            disabled={!!busy}
            className="text-xs text-neutral-500 hover:text-neutral-300 disabled:opacity-40"
          >
            Start a new conversation
          </button>
        </div>
      </div>
    </main>
  );
}
