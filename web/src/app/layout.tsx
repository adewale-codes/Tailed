import type { Metadata, Viewport } from "next";
import { Inter, Source_Serif_4 } from "next/font/google";
import Link from "next/link";
import { MotionProvider } from "@/components/motion-provider";
import "./globals.css";

const inter = Inter({ variable: "--font-sans", subsets: ["latin"] });
const sourceSerif = Source_Serif_4({ variable: "--font-serif", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Tallied",
  description: "Check whether a tenant's bank statements and payslips add up.",
};

export const viewport: Viewport = { themeColor: "#FAF7F2" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en-GB" className={`${inter.variable} ${sourceSerif.variable} h-full`}>
      <body className="flex min-h-full flex-col">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:text-ink"
        >
          Skip to content
        </a>
        <header className="border-b border-line bg-canvas">
          <div className="mx-auto flex h-16 w-full max-w-3xl items-center justify-between px-4 sm:px-6">
            <Link href="/" className="font-serif text-2xl font-semibold tracking-tight text-ink">
              Tallied
            </Link>
            <span className="text-sm text-muted">Free tenant document check</span>
          </div>
        </header>
        <MotionProvider>
          <main id="main" className="mx-auto w-full max-w-3xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
            {children}
          </main>
        </MotionProvider>
      </body>
    </html>
  );
}
