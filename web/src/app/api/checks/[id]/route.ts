import { API_UNREACHABLE, apiUrl, errorResponse, isCheckId, relay } from "@/lib/api-server";

/** GET /api/checks/{id}: the finished report (202 while processing, 410 once deleted). */
export async function GET(_request: Request, ctx: RouteContext<"/api/checks/[id]">): Promise<Response> {
  const { id } = await ctx.params;
  if (!isCheckId(id)) return errorResponse(404, "check_not_found", "We couldn't find this check.");
  try {
    return relay(await fetch(apiUrl(`/checks/${id}`), { cache: "no-store" }));
  } catch {
    return API_UNREACHABLE();
  }
}
