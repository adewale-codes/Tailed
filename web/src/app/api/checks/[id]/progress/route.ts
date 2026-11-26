import { API_UNREACHABLE, apiFetch, errorResponse, isCheckId, relay } from "@/lib/api-server";

export async function GET(request: Request, ctx: RouteContext<"/api/checks/[id]/progress">): Promise<Response> {
  const { id } = await ctx.params;
  if (!isCheckId(id)) return errorResponse(404, "check_not_found", "We couldn't find this check.");
  try {
    return relay(await apiFetch(request, `/checks/${id}/progress`));
  } catch {
    return API_UNREACHABLE();
  }
}
