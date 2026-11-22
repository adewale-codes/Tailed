import { checkDatabase } from "./helpers";

/** Fail fast, with a clear message, if the web app, the API or the local Postgres isn't running. */
export default async function globalSetup(): Promise<void> {
  const web = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";
  const api = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";
  const problems: string[] = [];

  const reach = async (label: string, url: string) => {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(10_000) });
      if (!res.ok) problems.push(`${label} answered HTTP ${res.status} at ${url}`);
    } catch {
      problems.push(`${label} isn't reachable at ${url}`);
    }
  };
  await reach("The web app", web);
  await reach("The API", `${api}/health`);
  try {
    checkDatabase();
  } catch {
    problems.push("The local Postgres isn't reachable (or migrations haven't run): uv run python scripts/e2e_db.py check");
  }

  if (problems.length) {
    throw new Error(
      `End-to-end tests need the web app, the API and Postgres running (see README, "End-to-end tests"):\n  - ${problems.join("\n  - ")}`,
    );
  }
}
