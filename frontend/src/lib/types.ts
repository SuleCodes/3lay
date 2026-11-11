export interface User {
  id: string;
  email: string;
  created_at: string;
  // Both null until the user has chosen a username during onboarding.
  username: string | null;
  forwarding_address: string | null;
}

export interface DeleteAccountResponse {
  message: string;
  deleted_stored_items: number;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface VerifyResponse {
  user: User;
  // True until the user has chosen a username (/onboarding), which is also
  // what creates their first API key.
  needs_username: boolean;
}

export interface UsernameAvailability {
  username: string;
  available: boolean;
  reason: string | null;
  forwarding_address: string | null;
}

export interface UsernameClaimedResponse {
  user: User;
  api_key: ApiKeyCreatedResponse;
}

export interface ApiKeyCreatedResponse {
  api_key: ApiKey;
  key: string;
}

// Boardy, the onboarding agent (/setup).

export interface BoardyMessage {
  role: "client" | "boardy";
  text: string;
  // What Boardy did before this reply, in plain words.
  steps: string[];
}

export interface BoardyConversation {
  id: string;
  created_at: string;
  // "failed": the last turn didn't finish, and can be retried.
  status: "ready" | "awaiting_approval" | "failed";
  messages: BoardyMessage[];
  approval: { summary: string } | null;
  missing: string[];
  approved: boolean;
  saved_version: number | null;
  // Changed since it was approved: not live until approved again.
  changes_pending: boolean;
  samples: { id: string; name: string }[];
  feedback: string | null;
  // The client's live configuration version, if any.
  active_version: number | null;
  // Approved here but not switched on yet (switching on failed): "Switch on" retries.
  activation_pending: boolean;
  activation_error: string | null;
  // Only in the response that switched the configuration on: show once.
  new_secrets: { destination_id: string; url: string; secret: string }[];
}

// Webhooks: the client's live destinations (dashboard).

export interface Webhook {
  id: string;
  url: string;
  events: string[];
  // The non-secret start of the current signing secret; null if it has none yet.
  secret_prefix: string | null;
  secret_created_at: string | null;
  secret_rotated_at: string | null;
  // After a rotation, the old secret keeps working until then.
  previous_secret_expires_at: string | null;
}

export interface Webhooks {
  active_version: number | null;
  webhooks: Webhook[];
}

export interface RotatedSecret {
  webhook: Webhook;
  secret: string;
}

export interface BoardyConversationSummary {
  id: string;
  created_at: string;
  updated_at: string;
}

// Events: the client's processed emails (/events, /events/[id]).

export type Verdict = "accepted" | "needs_review" | "rejected";

export interface EventSummary {
  event_id: string;
  run: number;
  received_at: string;
  finished_at: string | null;
  sender: string | null;
  status: "processing" | "completed" | "failed";
  error_code: string | null;
  documents: number;
  verdicts: Record<Verdict, number>;
  // "none": no webhook wanted this kind of event.
  delivery: "delivered" | "failed" | "none" | "pending";
  // The trace id: what to quote to 3lay.
  reference: string | null;
}

export interface EventPage {
  events: EventSummary[];
  next_cursor: string | null;
}

export interface EventDocument {
  name: string;
  content_type: string;
  location: string | null;
  forwarded_from: string | null;
  verdict: Verdict | null;
  reasons: string[];
  // The extracted fields; null when rejected or deleted after 90 days.
  data: Record<string, unknown> | null;
  error_code: string | null;
}

export interface TimelineStep {
  stage: "received" | "read" | "extracted" | "checked" | "delivered";
  label: string;
  at: string;
  duration_ms: number | null;
  outcome: "ok" | "failed" | "retried";
  detail: string | null;
}

export interface DeliveryAttempt {
  attempted_at: string;
  destination_id: string;
  url: string;
  attempt: number;
  status: "delivered" | "retry" | "gave_up";
  http_status: number | null;
  response_excerpt: string | null;
  error: string | null;
  duration_ms: number | null;
  trigger: "automatic" | "manual";
  webhook_id: string;
}

export interface EventDetail {
  event_id: string;
  run: number;
  received_at: string;
  finished_at: string | null;
  sender: string | null;
  status: EventSummary["status"];
  error_code: string | null;
  error_message: string | null;
  config_version: number | null;
  reference: string | null;
  data_deleted: boolean;
  documents: EventDocument[];
  timeline: TimelineStep[];
  deliveries: DeliveryAttempt[];
  can_resend: boolean;
  resend_unavailable_reason: string | null;
}
