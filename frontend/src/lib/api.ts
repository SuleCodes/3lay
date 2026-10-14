const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as { detail?: string });
    const detail = typeof body.detail === "string" ? body.detail : undefined;
    throw new ApiError(res.status, detail ?? `Request failed with status ${res.status}`);
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
