import { API_UNREACHABLE, apiUrl, errorResponse, proxyHeaders, relay } from "@/lib/api-server";

// 12 files x 10 MB plus multipart overhead. The API enforces the real limits; this just stops
// obviously oversized bodies before they're streamed on.
const MAX_BODY_BYTES = 12 * 10 * 1024 * 1024 + 1024 * 1024;

/** POST /api/checks: streams the multipart upload straight through to the API. */
export async function POST(request: Request): Promise<Response> {
  const type = request.headers.get("content-type") ?? "";
  if (!type.startsWith("multipart/form-data") || !request.body) {
    return errorResponse(400, "invalid_request", "Please choose at least one document to upload.");
  }
  const length = Number(request.headers.get("content-length") ?? "0");
  if (length > MAX_BODY_BYTES) {
    return errorResponse(413, "upload_too_large", "These files are too large together. Please upload fewer or smaller files.");
  }
  try {
    const res = await fetch(apiUrl("/checks"), {
      method: "POST",
      headers: { "Content-Type": type, ...(length ? { "Content-Length": String(length) } : {}), ...proxyHeaders(request) },
      body: request.body,
      // Required by Node's fetch to stream a request body.
      duplex: "half",
      cache: "no-store",
    } as RequestInit & { duplex: "half" });
    return relay(res);
  } catch {
    return API_UNREACHABLE();
  }
}
