// Small display helpers shared by the events pages and the dashboard.

export function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function duration(ms: number | null): string {
  if (ms === null) return "";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  return seconds < 60 ? `${seconds.toFixed(1)} s` : `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

export const VERDICT_LABEL: Record<string, string> = {
  accepted: "Accepted",
  needs_review: "Needs review",
  rejected: "Rejected",
};

// Tailwind classes for a small coloured badge.
export const VERDICT_TONE: Record<string, string> = {
  accepted: "bg-emerald-950 text-emerald-300 border-emerald-900",
  needs_review: "bg-amber-950 text-amber-300 border-amber-900",
  rejected: "bg-red-950 text-red-300 border-red-900",
};

export const DELIVERY_LABEL: Record<string, { text: string; tone: string }> = {
  delivered: { text: "Delivered", tone: "text-emerald-400" },
  failed: { text: "Delivery failed", tone: "text-red-400" },
  none: { text: "No webhook", tone: "text-neutral-500" },
  pending: { text: "Processing", tone: "text-neutral-400" },
};
