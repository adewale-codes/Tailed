"use client";

import { motion } from "framer-motion";
import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type ApiStatus = "checking" | "ok" | "unreachable";

export default function Home() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((res) => setApiStatus(res.ok ? "ok" : "unreachable"))
      .catch(() => setApiStatus("unreachable"));
  }, []);

  return (
    <main className="flex flex-1 items-center justify-center bg-slate-50 px-4">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="max-w-lg text-center"
      >
        <h1 className="text-4xl font-semibold tracking-tight text-slate-900">Tallied</h1>
        <p className="mt-4 text-lg text-slate-600">
          Upload a tenant&apos;s bank statements and payslips and see, in plain English, whether
          the story adds up.
        </p>
        <p className="mt-8 text-sm text-slate-500">
          API status:{" "}
          <span
            className={
              apiStatus === "ok"
                ? "text-emerald-600"
                : apiStatus === "unreachable"
                  ? "text-rose-600"
                  : "text-slate-500"
            }
          >
            {apiStatus}
          </span>
        </p>
      </motion.div>
    </main>
  );
}
