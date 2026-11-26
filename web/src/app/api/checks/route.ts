import { API_UNREACHABLE, apiFetch, errorResponse, relay } from "@/lib/api-server";

// 12 files x 10 MB plus multipart overhead. The API enforces the real limits; this just stops
// obviously oversized bodies before they're streamed on.
const MAX_BODY_BYTES = 12 * 10 * 1024 * 1024 + 1024 * 1024;

/**
 * POST /api/checks: starts a check. The web app sends a small form with the upload session id;
 * a direct multipart upload with the files also works (under Vercel's 4.5 MB request cap).
 */
export async function POST(request: Request): Promise<Response> {
  const type = request.headers.get("content-type") ?? "";
  const isForm = type.startsWith("multipart/form-data") || type.startsWith("application/x-www-form-urlencoded");
  if (!isForm || !request.body) {
    return errorResponse(400, "invalid_request", "Please choose at least one document to upload.");
  }
  const length = Number(request.headers.get("content-length") ?? "0");
  if (length > MAX_BODY_BYTES) {
    return errorResponse(413, "upload_too_large", "These files are too large together. Please upload fewer or smaller files.");
  }
  try {
    const res = await apiFetch(request, "/checks", {
      method: "POST",
      headers: { "Content-Type": type, ...(length ? { "Content-Length": String(length) } : {}) },
      body: request.body,
      // Required by Node's fetch to stream a request body.
      duplex: "half",
    });
    return relay(res);
  } catch {
    return API_UNREACHABLE();
  }
}
