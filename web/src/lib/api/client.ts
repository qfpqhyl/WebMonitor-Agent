"use client";

export class ApiError extends Error {
  constructor(public code: string, public status: number, message: string, public details: Record<string, unknown> = {}) {
    super(message); this.name = "ApiError";
  }
}
let csrf: { token: string; expires: number } | undefined;
let pendingCsrf: Promise<string> | undefined;
async function csrfToken(): Promise<string> {
  if (csrf && csrf.expires > Date.now() + 10_000) return csrf.token;
  if (!pendingCsrf) pendingCsrf = fetch("/api/v1/auth/csrf", { credentials: "same-origin", cache: "no-store" })
    .then(async response => {
      if (!response.ok) throw new ApiError("csrf_unavailable", response.status, "无法建立安全请求，请重试。");
      const body = await response.json();
      csrf = { token: body.csrf_token, expires: body.expires_at * 1000 };
      return csrf.token;
    }).finally(() => { pendingCsrf = undefined; });
  return pendingCsrf;
}
export function clearSessionClient() {
  csrf = undefined;
  localStorage.removeItem("wm_conversation");
  window.dispatchEvent(new Event("wm:session-ended"));
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (!path.startsWith("/") || path.startsWith("//")) throw new Error("API path must be relative");
  const headers = new Headers(options.headers);
  const method = (options.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRF-Token", await csrfToken());
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  let response: Response;
  try { response = await fetch(`/api/v1${path}`, { ...options, method, headers, credentials: "same-origin", cache: "no-store" }); }
  catch { throw new ApiError("network_unavailable", 0, "连接失败，请检查服务后重试。"); }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && !["/auth/login", "/auth/register", "/auth/me"].includes(path)) {
      clearSessionClient();
      if (window.location.pathname.startsWith("/app")) window.location.assign(`/login?returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    }
    throw new ApiError(body.error?.code ?? "request_failed", response.status,
      response.status === 403 ? "权限不足或安全验证失效，请刷新后重试。" : body.error?.message ?? "请求失败", body.error?.details ?? {});
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export function safeReturnTo(value: string | null | undefined): string {
  if (!value || /[\\\u0000-\u0020]/.test(value)) return "/app";
  if (value === "/app" || value.startsWith("/app/") || value.startsWith("/app?")) {
    try {
      const url = new URL(value, window.location.origin);
      if (url.origin === window.location.origin && (url.pathname === "/app" || url.pathname.startsWith("/app/"))) return url.pathname + url.search + url.hash;
    } catch { /* Reject invalid navigation. */ }
  }
  return "/app";
}
