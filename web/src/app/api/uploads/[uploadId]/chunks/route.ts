import { API_UNREACHABLE, apiFetch, errorResponse, isCheckId, relay } from "@/lib/api-server";

// Each piece is at most 3.5 MB plus form overhead, under Vercel's 4.5 MB request cap.
const MAX_BODY_BYTES = 4 * 1024 * 1024 + 64 * 1024;

/** POST /api/uploads/{id}/chunks: streams one piece of one file to the API. */
export async function POST(
  request: Request,
  ctx: RouteContext<"/api/uploads/[uploadId]/chunks">,
): Promise<Response> {
  const { uploadId } = await ctx.params;
  if (!isCheckId(uploadId)) {
    return errorResponse(404, "upload_not_found", "This upload has expired. Please choose your files again.");
  }
  const type = request.headers.get("content-type") ?? "";
  const length = Number(request.headers.get("content-length") ?? "0");
  if (!type.startsWith("multipart/form-data") || !request.body) {
    return errorResponse(400, "invalid_request", "Something went wrong with the upload. Please try again.");
  }
  if (length > MAX_BODY_BYTES) {
    return errorResponse(413, "chunk_too_large", "That piece of the upload was too large.");
  }
  try {
    const res = await apiFetch(request, `/uploads/${uploadId}/chunks`, {
      method: "POST",
      headers: { "Content-Type": type, ...(length ? { "Content-Length": String(length) } : {}) },
      body: request.body,
      duplex: "half",
    });
    return relay(res);
  } catch {
    return API_UNREACHABLE();
  }
}
