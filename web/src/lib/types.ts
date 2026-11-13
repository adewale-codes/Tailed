export type CheckStatus = "pass" | "flag" | "not_checked";

export type ApiError = { code: string; message: string };

export type ProgressFile =
  | { key: string; status: "pending" | "read" }
  | { key: string; status: "unreadable"; code: string; message: string };

export type Progress = {
  id: string;
  status: "queued" | "reading" | "checking" | "writing" | "done" | "failed" | string;
  stage: string;
  documents: { read: number; total: number };
  files: ProgressFile[];
  preview: { file: string; page: number } | null;
  checks: { id: string; number: number; label: string; done: boolean; status: CheckStatus | null }[];
  error?: ApiError;
};

/** Reader error codes: the check failed because the files themselves couldn't be read. */
export const UNREADABLE_CODES = new Set([
  "empty_file",
  "file_too_large",
  "unsupported_file_type",
  "password_protected",
  "unreadable_file",
  "too_many_pages",
  "ai_unavailable",
  "ai_truncated",
  "ai_invalid_response",
  "ai_refused",
  "ai_daily_limit",
  "no_usable_documents",
]);
