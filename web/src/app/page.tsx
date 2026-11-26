import { FreshOnReturn } from "@/components/fresh-on-return";
import { SampleSection } from "@/components/sample-section";
import { UploadForm } from "@/components/upload-form";

export default function Home() {
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-3xl font-semibold leading-tight tracking-tight text-ink sm:text-4xl">
        Does the tenant&apos;s story add up?
      </h1>
      <p className="mt-3 max-w-xl text-lg text-muted">
        Upload their bank statements and payslips. We run seven checks and explain what we found in
        plain English. It&apos;s free, and you don&apos;t need an account.
      </p>
      {/* The form and the samples always start fresh, even when the page is shown again from the
          router's cache or the browser's back/forward cache. */}
      <FreshOnReturn>
        <div className="mt-8 rounded-2xl border border-line bg-surface p-5 sm:p-8">
          <UploadForm />
        </div>
        <SampleSection />
      </FreshOnReturn>
    </div>
  );
}
