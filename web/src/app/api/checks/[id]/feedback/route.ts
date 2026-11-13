import { API_UNREACHABLE, apiUrl, errorResponse, isCheckId, relay } from "@/lib/api-server";

/** POST /api/checks/{id}/feedback: forwards the JSON body unchanged (the API validates it). */
export async function POST(request: Request, ctx: RouteContext<"/api/checks/[id]/feedback">): Promise<Response> {
  const { id } = await ctx.params;
  if (!isCheckId(id)) return errorResponse(404, "check_not_found", "We couldn't find this check.");
  const body = await request.text();
  if (body.length > 10_000) return errorResponse(400, "invalid_feedback", "That comment is too long.");
  try {
    return relay(
      await fetch(apiUrl(`/checks/${id}/feedback`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        cache: "no-store",
      }),
    );
  } catch {
    return API_UNREACHABLE();
  }
}
