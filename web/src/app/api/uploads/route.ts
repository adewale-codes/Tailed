import { API_UNREACHABLE, apiFetch, errorResponse, relay } from "@/lib/api-server";

/**
 * POST /api/uploads[?sample=<id>]: opens a chunked upload session (refused early if the daily
 * limit is used). With a sample id, the sample checks' own daily limit applies instead.
 */
export async function POST(request: Request): Promise<Response> {
  const sample = new URL(request.url).searchParams.get("sample");
  if (sample !== null && !/^[a-z_]{1,32}$/.test(sample)) {
    return errorResponse(404, "sample_not_found", "We couldn't find that sample. Please choose one from the list.");
  }
  try {
    return relay(await apiFetch(request, sample ? `/uploads?sample=${sample}` : "/uploads", { method: "POST" }));
  } catch {
    return API_UNREACHABLE();
  }
}
