import { apiUrl, isCheckId } from "@/lib/api-server";

const ALLOWED = new Set(["report_viewed", "evidence_opened"]);

/**
 * POST /api/checks/{id}/events: cookie-free analytics. Always answers 204 so analytics can never
 * show an error to the user; unknown events are dropped here.
 */
export async function POST(request: Request, ctx: RouteContext<"/api/checks/[id]/events">): Promise<Response> {
  const { id } = await ctx.params;
  const body = (await request.json().catch(() => null)) as { name?: string } | null;
  if (isCheckId(id) && body?.name && ALLOWED.has(body.name)) {
    try {
      await fetch(apiUrl(`/checks/${id}/events`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: body.name }),
        cache: "no-store",
      });
    } catch {
      // ignored on purpose
    }
  }
  return new Response(null, { status: 204 });
}
