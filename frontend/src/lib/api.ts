const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  // The request's correlation id (X-Correlation-Id): its trace in Datadog.
  correlationId: string | null;

  constructor(status: number, message: string, correlationId: string | null = null) {
    super(message);
    this.status = status;
    this.correlationId = correlationId;
  }
}

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as { detail?: string });
    const detail = typeof body.detail === "string" ? body.detail : undefined;
    const correlationId = res.headers.get("X-Correlation-Id");
    let message = detail ?? `Request failed with status ${res.status}`;
    // A problem on 3lay's side: give the client a reference to quote, which leads to the trace.
    if (res.status >= 500 && correlationId) message += ` (reference: ${correlationId})`;
    throw new ApiError(res.status, message, correlationId);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return (await res.json()) as T;
}

/**
 * Thin wrapper around fetch that talks to the 3lay API. Every call includes
 * credentials so the httpOnly session cookie set by /auth/verify rides along
 * automatically -- the frontend never handles the session token directly.
 */
export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
  });
  return parse<T>(res);
}

/**
 * Uploads a file as multipart form data (field "file"). No Content-Type
 * header: the browser sets it, with the boundary between the parts.
 */
export async function apiUpload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}${path}`, { method: "POST", body: form, credentials: "include" });
  return parse<T>(res);
}
