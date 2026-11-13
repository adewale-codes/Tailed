/**
 * Remembers, in this browser tab's memory only, what the user just uploaded, so the live checks
 * screen can show their own filenames and a local preview. Nothing is written to storage, and it
 * is gone on reload (the screen then falls back to "File 1", "File 2"...).
 */

type Upload = { names: string[]; previewUrl: string | null };

const uploads = new Map<string, Upload>();

export function rememberUpload(id: string, files: File[]): void {
  const image = files.find((f) => f.type === "image/jpeg" || f.type === "image/png");
  uploads.set(id, {
    names: files.map((f) => f.name),
    previewUrl: image ? URL.createObjectURL(image) : null,
  });
}

export function recallUpload(id: string): Upload | undefined {
  return uploads.get(id);
}
