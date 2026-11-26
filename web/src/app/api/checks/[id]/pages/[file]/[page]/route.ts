import { API_UNREACHABLE, apiFetch, errorResponse, isCheckId, relay } from "@/lib/api-server";

/** GET /api/checks/{id}/pages/{file}/{page}: the image (WebP) of a page with a finding. */
export async function GET(
  request: Request,
  ctx: RouteContext<"/api/checks/[id]/pages/[file]/[page]">,
): Promise<Response> {
  const { id, file, page } = await ctx.params;
  if (!isCheckId(id) || !/^\d{1,3}$/.test(file) || !/^\d{1,3}$/.test(page)) {
    return errorResponse(404, "page_not_found", "We couldn't find that page.");
  }
  try {
    return relay(await apiFetch(request, `/checks/${id}/pages/${file}/${page}`));
  } catch {
    return API_UNREACHABLE();
  }
}
