import { currentRole, currentUser } from "./role";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type J = any;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

const headers = () => ({ "X-Demo-Role": currentRole(), "X-Demo-User": currentUser() });

async function req<T = J>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: { ...headers(), ...(body !== undefined && !form ? { "Content-Type": "application/json" } : {}) },
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch { /* not json */ }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  get: <T = J>(p: string) => req<T>("GET", p),
  post: <T = J>(p: string, b?: unknown) => req<T>("POST", p, b ?? {}),
  put: <T = J>(p: string, b?: unknown) => req<T>("PUT", p, b ?? {}),
  patch: <T = J>(p: string, b?: unknown) => req<T>("PATCH", p, b ?? {}),
  upload: <T = J>(p: string, form: FormData) => req<T>("POST", p, undefined, form),
};
