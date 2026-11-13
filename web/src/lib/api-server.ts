import "server-only";

/**
 * Server-side helpers for talking to the Tallied API. The API URL and the proxy secret live in
 * server-only env vars (API_URL, WEB_PROXY_SECRET), so the browser never sees either.
 */

const ID_RE = /^[A-Za-z0-9_-]{40,64}$/;

export function apiUrl(path: string): string {
  const base = process.env.API_URL ?? "http://localhost:8000";
  return `${base.replace(/\/$/, "")}${path}`;
}

export function isCheckId(id: string): boolean {
  return ID_RE.test(id);
}

/**
 * The visitor's IP, only when a host we trust set it. Next.js passes a client's own
 * X-Forwarded-For straight through, so reading it blindly would let anyone dodge the rate limit.
 * - On Vercel, x-real-ip is set by Vercel's edge.
 * - Behind another proxy that overwrites X-Forwarded-For, set TRUST_FORWARDED_FOR=true.
 * Otherwise null: the API then counts against the connecting address (this server).
 */
export function visitorIp(request: Request): string | null {
  if (process.env.VERCEL) {
    const real = request.headers.get("x-real-ip")?.trim();
    if (real) return real;
  }
  if (process.env.TRUST_FORWARDED_FOR === "true") {
    const first = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
    if (first) return first;
  }
  return null;
}

/** Headers that let the API rate-limit per visitor rather than per web server. */
export function proxyHeaders(request: Request): Record<string, string> {
  const secret = process.env.WEB_PROXY_SECRET;
  const ip = visitorIp(request);
  if (!secret || !ip) return {};
  return { "X-Tallied-Proxy-Secret": secret, "X-Tallied-Client-IP": ip };
}

export function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status, headers: { "Cache-Control": "no-store" } });
}

export const API_UNREACHABLE = () =>
  errorResponse(502, "api_unreachable", "We couldn't reach the checking service. Please try again in a moment.");

/** Pass an API response through unchanged (status, JSON or image body), never cached. */
export async function relay(res: Response): Promise<Response> {
  const headers = new Headers({ "Cache-Control": "no-store" });
  const type = res.headers.get("content-type");
  if (type) headers.set("Content-Type", type);
  return new Response(res.body, { status: res.status, headers });
}
