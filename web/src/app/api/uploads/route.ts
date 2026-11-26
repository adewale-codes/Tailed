import { API_UNREACHABLE, apiFetch, relay } from "@/lib/api-server";

/** POST /api/uploads: opens a chunked upload session (refused early if the daily limit is used). */
export async function POST(request: Request): Promise<Response> {
  try {
    return relay(await apiFetch(request, "/uploads", { method: "POST" }));
  } catch {
    return API_UNREACHABLE();
  }
}
