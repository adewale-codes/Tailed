import { API_UNREACHABLE, apiUrl, errorResponse, isCheckId, relay } from "@/lib/api-server";

/** GET /api/checks/{id}/pages/{file}/{page}: a rendered page image (PNG). */
export async function GET(
  _request: Request,
  ctx: RouteContext<"/api/checks/[id]/pages/[file]/[page]">,
): Promise<Response> {
  const { id, file, page } = await ctx.params;
  if (!isCheckId(id) || !/^\d{1,3}$/.test(file) || !/^\d{1,3}$/.test(page)) {
    return errorResponse(404, "page_not_found", "We couldn't find that page.");
  }
  try {
    return relay(await fetch(apiUrl(`/checks/${id}/pages/${file}/${page}.png`), { cache: "no-store" }));
  } catch {
    return API_UNREACHABLE();
  }
}
