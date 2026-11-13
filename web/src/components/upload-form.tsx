"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { useId, useRef, useState } from "react";
import type { ApiError } from "@/lib/types";
import { rememberUpload } from "@/lib/upload-store";
import { CloseIcon, DocumentIcon, Spinner, UploadIcon } from "./icons";
import { StatePanel, buttonPrimary, buttonSecondary } from "./state-panel";

const MAX_FILES = 12;
const MAX_FILE_MB = 10;
const ACCEPTED_TYPES = ["application/pdf", "image/jpeg", "image/png"];
const ACCEPTED_EXT = /\.(pdf|jpe?g|png)$/i;

type Picked = { id: string; file: File };

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function parseRent(raw: string): number | null {
  const cleaned = raw.replace(/[£,\s]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
  const value = Number(cleaned);
  return value > 0 && value <= 100000 ? value : null;
}

/** What still stops the user submitting, worded as the button label. Null when ready. */
export function missingStep(fileCount: number, consent: boolean, rentOk: boolean): string | null {
  if (fileCount === 0 && !consent) return "Add a document and tick the consent box";
  if (fileCount === 0) return "Add at least one document";
  if (!rentOk) return "Enter the monthly rent";
  if (!consent) return "Tick the consent box to continue";
  return null;
}

export function UploadForm() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const browseRef = useRef<HTMLButtonElement>(null);
  const rentId = useId();
  const rentHintId = useId();
  const rentErrorId = useId();
  const consentId = useId();
  const statusId = useId();
  const [rent, setRent] = useState("");
  const [rentTouched, setRentTouched] = useState(false);
  const [files, setFiles] = useState<Picked[]>([]);
  const [rejected, setRejected] = useState<string[]>([]);
  const [consent, setConsent] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [limitReached, setLimitReached] = useState<ApiError | null>(null);

  const rentOk = parseRent(rent) !== null;
  const showRentError = rentTouched && rent.trim() !== "" && !rentOk;
  const blocker = missingStep(files.length, consent, rentOk);
  const label = submitting
    ? "Uploading…"
    : (blocker ?? `Check ${files.length} document${files.length === 1 ? "" : "s"}`);

  function addFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    const problems: string[] = [];
    const next = [...files];
    for (const file of Array.from(list)) {
      if (!ACCEPTED_TYPES.includes(file.type) && !ACCEPTED_EXT.test(file.name)) {
        problems.push(`${file.name} isn't a PDF, JPG or PNG, so it wasn't added.`);
      } else if (file.size > MAX_FILE_MB * 1024 * 1024) {
        problems.push(`${file.name} is over ${MAX_FILE_MB} MB, so it wasn't added.`);
      } else if (file.size === 0) {
        problems.push(`${file.name} is empty, so it wasn't added.`);
      } else if (next.some((p) => p.file.name === file.name && p.file.size === file.size)) {
        continue; // already in the list
      } else if (next.length >= MAX_FILES) {
        problems.push(`You can add up to ${MAX_FILES} files, so ${file.name} wasn't added.`);
      } else {
        next.push({ id: crypto.randomUUID(), file });
      }
    }
    setFiles(next);
    setRejected(problems);
    setError(null);
  }

  function removeFile(id: string, index: number) {
    const remaining = files.filter((p) => p.id !== id);
    setFiles(remaining);
    setRejected([]);
    // Keep keyboard users in place: focus the next file's remove button (not the row that is
    // fading out), or the browse button when the list is empty.
    const target = remaining[Math.min(index, remaining.length - 1)];
    requestAnimationFrame(() => {
      const next = target
        ? document.querySelector<HTMLButtonElement>(`[data-remove-file="${target.id}"]`)
        : null;
      (next ?? browseRef.current)?.focus();
    });
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (blocker !== null || submitting) return;
    setSubmitting(true);
    setError(null);
    const body = new FormData();
    for (const { file } of files) body.append("files", file, file.name);
    body.append("monthly_rent", String(parseRent(rent)));
    body.append("consent", "true");
    try {
      const res = await fetch("/api/checks", { method: "POST", body });
      const data = await res.json().catch(() => null);
      if (res.status === 202 && data?.id) {
        rememberUpload(
          data.id,
          files.map((p) => p.file),
        );
        router.push(`/check/${data.id}`);
        return;
      }
      const apiError: ApiError = data?.error ?? {
        code: "internal_error",
        message: "Something went wrong on our side. Please try again.",
      };
      if (res.status === 429) setLimitReached(apiError);
      else setError(apiError);
    } catch {
      setError({
        code: "network",
        message: "We couldn't reach Tallied. Check your connection and try again.",
      });
    }
    setSubmitting(false);
  }

  if (limitReached) {
    return (
      <StatePanel
        tone="closer"
        title="You've reached today's limit"
        actions={
          <button type="button" className={buttonSecondary} onClick={() => setLimitReached(null)}>
            Back to the form
          </button>
        }
      >
        <p>{limitReached.message}</p>
        <p className="text-muted">
          Tallied is free, so each person can run 3 checks a day. The files you chose weren&apos;t
          uploaded or kept.
        </p>
      </StatePanel>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-8">
      <div>
        <label htmlFor={rentId} className="block font-medium text-ink">
          Monthly rent
        </label>
        <p id={rentHintId} className="mt-1 text-sm text-muted">
          We compare this with the tenant&apos;s take-home pay.
        </p>
        <div className="relative mt-2 max-w-60">
          <span
            className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted"
            aria-hidden="true"
          >
            £
          </span>
          <input
            id={rentId}
            name="monthly_rent"
            inputMode="decimal"
            autoComplete="off"
            placeholder="1,100"
            value={rent}
            onChange={(e) => setRent(e.target.value)}
            onBlur={() => setRentTouched(true)}
            aria-describedby={showRentError ? `${rentHintId} ${rentErrorId}` : rentHintId}
            aria-invalid={showRentError}
            className="h-12 w-full rounded-lg border border-line bg-surface pl-7 pr-3 text-lg text-ink placeholder:text-muted/60 focus-visible:border-accent aria-invalid:border-risk"
          />
        </div>
        {showRentError ? (
          <p id={rentErrorId} className="mt-2 text-sm text-risk">
            Enter the rent as a number, like 1100 or 1,100.50.
          </p>
        ) : null}
      </div>

      <fieldset>
        <legend className="font-medium text-ink">Bank statements and payslips</legend>
        <p className="mt-1 text-sm text-muted">
          PDFs straight from the bank or employer work best. Photos are fine too. Up to {MAX_FILES}{" "}
          files, {MAX_FILE_MB} MB each.
        </p>
        <div
          onDragEnter={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragOver={(e) => e.preventDefault()}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
          }}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            addFiles(e.dataTransfer.files);
          }}
          className={`mt-3 flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors duration-150 sm:py-10 ${
            dragging ? "border-accent bg-accent-soft" : "border-line bg-surface"
          }`}
        >
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft text-accent">
            <UploadIcon />
          </span>
          <p className="mt-3 text-ink">
            <span className="hidden sm:inline">Drag files here, or </span>
            <button
              ref={browseRef}
              type="button"
              onClick={() => inputRef.current?.click()}
              className="min-h-11 font-medium text-accent underline underline-offset-4 hover:text-accent-hover"
            >
              <span className="sm:hidden">Choose files</span>
              <span className="hidden sm:inline">browse your files</span>
            </button>
          </p>
          <p className="text-sm text-muted">PDF, JPG or PNG</p>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
            className="sr-only"
            tabIndex={-1}
            aria-label="Choose bank statements and payslips"
            onChange={(e) => {
              addFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        {rejected.length > 0 ? (
          <ul role="alert" className="mt-3 space-y-1 text-sm text-risk">
            {rejected.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        ) : null}

        {files.length > 0 ? (
          <ul
            aria-label={`${files.length} file${files.length === 1 ? "" : "s"} to check`}
            className="mt-4 divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface"
          >
            <AnimatePresence initial={false}>
              {files.map(({ id, file }, index) => (
                <motion.li
                  key={id}
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden"
                >
                  <div className="flex items-center gap-3 py-2 pl-4 pr-2">
                    <DocumentIcon className="h-5 w-5 shrink-0 text-muted" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-ink">{file.name}</p>
                      <p className="text-sm text-muted">{formatSize(file.size)}</p>
                    </div>
                    <button
                      type="button"
                      data-remove-file={id}
                      onClick={() => removeFile(id, index)}
                      aria-label={`Remove ${file.name}`}
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-muted transition-colors duration-150 hover:bg-risk-soft hover:text-risk"
                    >
                      <CloseIcon />
                    </button>
                  </div>
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        ) : null}
      </fieldset>

      <div className="flex gap-3 rounded-xl border border-line bg-surface p-4">
        <input
          id={consentId}
          type="checkbox"
          checked={consent}
          onChange={(e) => setConsent(e.target.checked)}
          className="mt-0.5 h-5 w-5 shrink-0 accent-accent"
        />
        <label htmlFor={consentId} className="text-ink">
          The tenant has agreed to me checking these documents.
          <span className="mt-1 block text-sm text-muted">
            We delete the documents as soon as the check finishes, and the report after 24 hours.
          </span>
        </label>
      </div>

      {error ? (
        <div role="alert" className="rounded-xl border border-risk/30 bg-risk-soft p-4 text-ink">
          {error.message}
        </div>
      ) : null}

      <div>
        <button
          type="submit"
          disabled={blocker !== null || submitting}
          aria-describedby={statusId}
          className={`${buttonPrimary} w-full gap-2 sm:w-auto`}
        >
          {submitting ? <Spinner /> : null}
          {label}
        </button>
        <p id={statusId} className="sr-only" aria-live="polite">
          {submitting
            ? "Uploading your documents."
            : blocker
              ? `Can't start yet. ${blocker}.`
              : "Ready to check."}
        </p>
      </div>
    </form>
  );
}
