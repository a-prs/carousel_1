// api.ts — thin fetch wrapper. Token in localStorage (NOT sessionStorage — this
// is a PWA; standalone close wipes sessionStorage). Slides are fetched as authed
// blobs so <img> and Web Share work without relying on the cookie over http dev.

const API = "/carousel/api";
const TOKEN_KEY = "carousel_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const t = getToken();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : extra;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function handle(res: Response) {
  if (res.status === 401) {
    clearToken();
    throw new ApiError(401, "unauthorized");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return res;
}

export async function login(username: string, password: string) {
  const res = await fetch(`${API}/auth`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  await handle(res);
  const data = await res.json();
  setToken(data.token);
  return data;
}

export async function me() {
  const res = await handle(await fetch(`${API}/me`, { headers: authHeaders() }));
  return res.json();
}

export async function uploadCover(file: File): Promise<{ upload_id: string }> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await handle(
    await fetch(`${API}/upload-cover`, { method: "POST", headers: authHeaders(), body: fd })
  );
  return res.json();
}

export async function generate(text: string, coverUploadId?: string) {
  const res = await handle(
    await fetch(`${API}/generate`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ text, cover_upload_id: coverUploadId }),
    })
  );
  return res.json() as Promise<{ job_id: string }>;
}

export type JobStatus = {
  status: "queued" | "running" | "done" | "error";
  slide_count: number;
  cover_title: string | null;
  error: string | null;
  expired: boolean;
  slides: { index: number; url: string }[];
};

export async function jobStatus(jobId: string): Promise<JobStatus> {
  const res = await handle(await fetch(`${API}/jobs/${jobId}`, { headers: authHeaders() }));
  return res.json();
}

// Fetch a slide as an authed Blob (works locally over http; also feeds Web Share).
export async function slideBlob(url: string): Promise<Blob> {
  const res = await handle(await fetch(url, { headers: authHeaders() }));
  return res.blob();
}

// Fetch the whole deck as a zip Blob ("скачать всё zip", Phase 9). Zip is a
// desktop-first fallback (plan §9: "zip на мобиле неудобен") — per-slide Web
// Share stays the primary mobile save path.
export async function zipBlob(jobId: string): Promise<Blob> {
  const res = await handle(await fetch(`${API}/jobs/${jobId}/zip`, { headers: authHeaders() }));
  return res.blob();
}

export async function history() {
  const res = await handle(await fetch(`${API}/history`, { headers: authHeaders() }));
  return res.json();
}
