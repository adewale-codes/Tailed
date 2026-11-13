import type { Config } from "tailwindcss";

// Tallied design tokens. Use these names in classes (bg-canvas, text-ink, border-line...),
// never one-off hex values. Loaded from src/app/globals.css with @config.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#FAF7F2", // page background
        surface: "#FFFFFF", // cards, inputs
        line: "#E7E1D6", // borders and dividers
        ink: "#1B2A41", // body text and headings
        muted: "#5B6472", // secondary text
        accent: {
          DEFAULT: "#0F6E6E",
          hover: "#0B5454",
          soft: "#E3F0EE",
        },
        good: { DEFAULT: "#2F6B45", soft: "#E6F1EA" },
        closer: { DEFAULT: "#8A5207", soft: "#FBEFD9" },
        risk: { DEFAULT: "#9E3424", soft: "#F7E3DE" },
      },
      fontFamily: {
        serif: ["var(--font-serif)", "Georgia", "serif"],
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
      },
    },
  },
};

export default config;
