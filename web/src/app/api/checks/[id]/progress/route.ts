import { API_UNREACHABLE, apiUrl, errorResponse, isCheckId, relay } from "@/lib/api-server";

export async function GET(_request: Request, ctx: RouteContext<"/api/checks/[id]/progress">): Promise<Response> {
  const { id } = await ctx.params;
  if (!isCheckId(id)) return errorResponse(404, "check_not_found", "We couldn't find this check.");
  try {
    return relay(await fetch(apiUrl(`/checks/${id}/progress`), { cache: "no-store" }));
  } catch {
    return API_UNREACHABLE();
  }
}
