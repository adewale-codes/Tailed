import type { ApiError } from "./types";

/**
 * The upload pipeline the browser uses for every check, real or sample: open an upload session,
 * send each file in pieces (Vercel refuses any request body over 4.5 MB), then start the check.
 */

const PIECE_BYTES = 3.5 * 1024 * 1024;
const FALLBACK_ERROR: ApiError = { code: "internal_error", message: "Something went wrong on our side. Please try again." };

export type Outcome = { ok: true; id: string } | { ok: false; status: number; error: ApiError };

async function failure(res: Response): Promise<Outcome> {
  const data = await res.json().catch(() => null);
  return { ok: false, status: res.status, error: data?.error ?? FALLBACK_ERROR };
}

/**
 * `sample`: the id of a sample (lib/samples.ts). The API then uses the sample's own daily limit,
 * needs no consent, and only accepts the check if the files are exactly that sample's documents.
 */
export async function uploadAndStart(
  files: File[],
  rent: string,
  onProgress: (pct: number) => void,
  { sample }: { sample?: string } = {},
): Promise<Outcome> {
  const session = await fetch(sample ? `/api/uploads?sample=${encodeURIComponent(sample)}` : "/api/uploads", {
    method: "POST",
  });
  if (session.status !== 201) return failure(session);
  const { upload_id: uploadId } = (await session.json()) as { upload_id: string };

  const total = files.reduce((n, f) => n + f.size, 0) || 1;
  let sent = 0;
  for (const [i, file] of files.entries()) {
    for (let offset = 0; offset < file.size; offset += PIECE_BYTES) {
      const piece = file.slice(offset, Math.min(offset + PIECE_BYTES, file.size));
      const form = new FormData();
      form.append("index", String(i + 1));
      form.append("offset", String(offset));
      form.append("name", file.name);
      form.append("chunk", piece, "piece");
      const res = await fetch(`/api/uploads/${uploadId}/chunks`, { method: "POST", body: form });
      if (!res.ok) {
        const out = await failure(res);
        if (out.ok) return out;
        return { ...out, error: { ...out.error, message: `${file.name}: ${out.error.message}` } };
      }
      sent += piece.size;
      onProgress(Math.min(99, Math.round((sent / total) * 100)));
    }
  }

  const start = new FormData();
  start.append("upload_id", uploadId);
  start.append("monthly_rent", rent);
  if (sample) start.append("sample", sample);
  else start.append("consent", "true");
  const res = await fetch("/api/checks", { method: "POST", body: start });
  const data = await res.json().catch(() => null);
  if (res.status === 202 && data?.id) {
    onProgress(100);
    return { ok: true, id: data.id as string };
  }
  return { ok: false, status: res.status, error: data?.error ?? FALLBACK_ERROR };
}
