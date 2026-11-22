import { API_UNREACHABLE, apiUrl, proxyHeaders, relay } from "@/lib/api-server";

/** POST /api/uploads: opens a chunked upload session (refused early if the daily limit is used). */
export async function POST(request: Request): Promise<Response> {
  try {
    return relay(
      await fetch(apiUrl("/uploads"), { method: "POST", headers: proxyHeaders(request), cache: "no-store" }),
    );
  } catch {
    return API_UNREACHABLE();
  }
}
