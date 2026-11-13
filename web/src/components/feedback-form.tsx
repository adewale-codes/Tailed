"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useId, useRef, useState } from "react";
import { Spinner, TickIcon } from "./icons";
import { buttonPrimary } from "./state-panel";

type Vote = "up" | "down";

// The follow-up question changes with the answer. Values must match FOLLOW_UPS in the API.
const FOLLOW_UP: Record<Vote, { question: string; options: { value: string; label: string }[] }> = {
  up: {
    question: "What helped most?",
    options: [
      { value: "verdict", label: "The overall verdict" },
      { value: "evidence", label: "Seeing the problem on the page" },
      { value: "affordability", label: "The affordability figure" },
      { value: "next_steps", label: "The suggested next steps" },
    ],
  },
  down: {
    question: "What went wrong?",
    options: [
      { value: "wrong_result", label: "The result looks wrong" },
      { value: "hard_to_understand", label: "It was hard to understand" },
      { value: "missed_something", label: "It missed something I noticed" },
      { value: "needed_something_else", label: "I needed something else" },
    ],
  },
};

const EMAIL_RE = /^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$/;

export function FeedbackForm({ reportId }: { reportId: string }) {
  const [vote, setVote] = useState<Vote | null>(null);
  const [followUp, setFollowUp] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const thanksRef = useRef<HTMLDivElement>(null);
  const ids = { heading: useId(), followUp: useId(), comment: useId(), email: useId(), emailHint: useId() };
  const emailInvalid = email.trim() !== "" && !EMAIL_RE.test(email.trim());

  function choose(next: Vote) {
    if (next !== vote) setFollowUp(null); // the options change with the answer
    setVote(next);
    setError(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!vote || sending || emailInvalid) return;
    setSending(true);
    setError(null);
    try {
      const res = await fetch(`/api/checks/${reportId}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vote,
          follow_up: followUp,
          comment: comment.trim() || null,
          email: email.trim() || null,
        }),
      });
      if (res.status === 201) {
        setSent(true);
        requestAnimationFrame(() => thanksRef.current?.focus());
        return;
      }
      const data = await res.json().catch(() => null);
      setError(
        res.status === 410
          ? "This report has been deleted, so we can't attach feedback to it any more."
          : (data?.error?.message ?? "We couldn't send that. Please try again."),
      );
    } catch {
      setError("We couldn't send that. Check your connection and try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <section aria-labelledby={ids.heading} className="rounded-2xl border border-line bg-surface p-5 sm:p-7">
      <AnimatePresence mode="wait" initial={false}>
        {sent ? (
          <motion.div
            key="thanks"
            ref={thanksRef}
            tabIndex={-1}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className="flex gap-3"
            role="status"
          >
            <span className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-good-soft text-good">
              <TickIcon />
            </span>
            <div>
              <h2 id={ids.heading} className="text-2xl font-semibold text-ink">
                Thank you
              </h2>
              <p className="mt-1 text-ink">
                Your feedback goes straight to the people building Tallied.
                {email.trim() ? " We'll only email you about the launch." : ""}
              </p>
            </div>
          </motion.div>
        ) : (
          <motion.form key="form" onSubmit={submit} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
            <h2 id={ids.heading} className="text-2xl font-semibold text-ink">
              Was this report useful?
            </h2>
            <div className="mt-4 flex flex-wrap gap-3" role="group" aria-labelledby={ids.heading}>
              {(
                [
                  ["up", "Yes"],
                  ["down", "Not really"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={vote === value}
                  onClick={() => choose(value)}
                  className={`min-h-11 min-w-28 rounded-lg border px-5 py-2.5 font-medium transition-colors duration-150 ${
                    vote === value
                      ? "border-accent bg-accent text-surface"
                      : "border-line bg-surface text-ink hover:border-accent hover:text-accent"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            <AnimatePresence initial={false}>
              {vote ? (
                <motion.div
                  key="details"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.25 }}
                  className="overflow-hidden"
                >
                  <div className="space-y-6 pt-6">
                    <fieldset>
                      <legend id={ids.followUp} className="font-medium text-ink">
                        {FOLLOW_UP[vote].question} <span className="font-normal text-muted">(optional)</span>
                      </legend>
                      <div className="mt-3 grid gap-2 sm:grid-cols-2">
                        {FOLLOW_UP[vote].options.map((o) => (
                          <label
                            key={o.value}
                            className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 transition-colors duration-150 ${
                              followUp === o.value ? "border-accent bg-accent-soft" : "border-line hover:border-accent"
                            }`}
                          >
                            <input
                              type="radio"
                              name={`follow-up-${vote}`}
                              value={o.value}
                              checked={followUp === o.value}
                              onChange={() => setFollowUp(o.value)}
                              className="h-4 w-4 accent-accent"
                            />
                            <span className="text-ink">{o.label}</span>
                          </label>
                        ))}
                      </div>
                    </fieldset>

                    <div>
                      <label htmlFor={ids.comment} className="block font-medium text-ink">
                        Anything else? <span className="font-normal text-muted">(optional)</span>
                      </label>
                      <textarea
                        id={ids.comment}
                        rows={3}
                        maxLength={2000}
                        value={comment}
                        onChange={(e) => setComment(e.target.value)}
                        className="mt-2 w-full rounded-lg border border-line bg-surface px-3 py-2 text-ink focus-visible:border-accent"
                      />
                    </div>

                    <div>
                      <label htmlFor={ids.email} className="block font-medium text-ink">
                        Email for launch news <span className="font-normal text-muted">(optional)</span>
                      </label>
                      <p id={ids.emailHint} className="mt-1 text-sm text-muted">
                        Only used to tell you when Tallied launches. Stored separately from this report.
                      </p>
                      <input
                        id={ids.email}
                        type="email"
                        inputMode="email"
                        autoComplete="email"
                        maxLength={320}
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        aria-describedby={ids.emailHint}
                        aria-invalid={emailInvalid}
                        className="mt-2 h-12 w-full max-w-sm rounded-lg border border-line bg-surface px-3 text-ink focus-visible:border-accent aria-invalid:border-risk"
                      />
                      {emailInvalid ? (
                        <p className="mt-2 text-sm text-risk">That doesn&apos;t look like an email address.</p>
                      ) : null}
                    </div>

                    {error ? (
                      <p role="alert" className="rounded-lg border border-risk/30 bg-risk-soft p-3 text-ink">
                        {error}
                      </p>
                    ) : null}

                    <button type="submit" disabled={sending || emailInvalid} className={`${buttonPrimary} gap-2`}>
                      {sending ? <Spinner /> : null}
                      {sending ? "Sending…" : "Send feedback"}
                    </button>
                  </div>
                </motion.div>
              ) : null}
            </AnimatePresence>
          </motion.form>
        )}
      </AnimatePresence>
    </section>
  );
}
