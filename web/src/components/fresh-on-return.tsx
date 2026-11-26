"use client";

import { Fragment, useEffect, useLayoutEffect, useState } from "react";

/**
 * Remounts its children, so they start from their first state, whenever the page is left and
 * shown again. The upload page uses it so it never shows a run, files, rent, consent, errors or a
 * limit message from before.
 *
 * Two things otherwise bring old state back:
 * - With cacheComponents, Next.js doesn't unmount a page you navigate away from. It hides it with
 *   React's <Activity> and shows the same instance, state and all, when you return (Link, router
 *   push, or the back button). Hiding runs layout-effect cleanups, so the cleanup below starts a
 *   new generation before the page can be seen again. (Next.js's guide "Preserving UI state".)
 * - The browser's back/forward cache restores a whole frozen page. `pageshow` with `persisted`
 *   says so.
 */
export function FreshOnReturn({ children }: { children: React.ReactNode }) {
  const [generation, setGeneration] = useState(0);

  useLayoutEffect(() => () => setGeneration((g) => g + 1), []);

  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) setGeneration((g) => g + 1);
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);

  return <Fragment key={generation}>{children}</Fragment>;
}
