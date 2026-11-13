"use client";

import { MotionConfig, MotionGlobalConfig } from "framer-motion";
import { useEffect } from "react";
import { usePrefersReducedMotion } from "@/lib/use-prefers-reduced-motion";

const QUERY = "(prefers-reduced-motion: reduce)";

// Set before anything renders in the browser. framer-motion checks this global at the start of
// every animation, so it also covers elements created before React knows the setting.
if (typeof window !== "undefined") {
  MotionGlobalConfig.skipAnimations = window.matchMedia(QUERY).matches;
}

/** Quick, gentle defaults for every animation; none at all when the OS asks for reduced motion. */
export function MotionProvider({ children }: { children: React.ReactNode }) {
  const reduce = usePrefersReducedMotion();
  useEffect(() => {
    // Follow the OS setting live if the user changes it while the page is open.
    MotionGlobalConfig.skipAnimations = reduce;
  }, [reduce]);
  return (
    <MotionConfig
      reducedMotion="user"
      transition={reduce ? { duration: 0 } : { duration: 0.25, ease: [0.25, 0.1, 0.25, 1] }}
    >
      {children}
    </MotionConfig>
  );
}
