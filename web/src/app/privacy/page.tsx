import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy | Tallied",
  description: "What Tallied collects, how long it keeps it, and who it shares it with.",
};

// Placeholders in [square brackets] must be filled in before launch.
const CONTACT = "[privacy@your-domain.co.uk]";
const CONTROLLER = "[Your name or company name]";

export default function PrivacyPage() {
  return (
    <article className="mx-auto max-w-2xl">
      <h1 className="text-3xl font-semibold tracking-tight text-ink sm:text-4xl">Privacy</h1>
      <p className="mt-3 text-lg text-muted">
        Tallied checks bank statements and payslips, which are some of the most private documents people have. This
        page explains, in plain English, what we collect, how long we keep it and who sees it.
      </p>
      <p className="mt-2 text-sm text-muted">Last updated: [date]</p>

      <Section title="The short version">
        <ul className="list-disc space-y-2 pl-5">
          <li>The documents you upload are deleted as soon as the check finishes.</li>
          <li>The report, and the page images it shows, are deleted 24 hours after the check.</li>
          <li>We don&apos;t use cookies, and we don&apos;t store your IP address.</li>
          <li>If you give us your email, we only use it to tell you when Tallied launches.</li>
        </ul>
      </Section>

      <Section title="Who we are">
        <p>
          Tallied is run by {CONTROLLER}, who is the data controller for the information on this page. ICO
          registration number: [number]. You can contact us at <Contact />.
        </p>
      </Section>

      <Section title="The documents you upload">
        <p>
          You confirm that the tenant has agreed to you checking their documents before you upload them. While a check
          runs, the files are kept in a temporary folder on our server, under random names. They are deleted the moment
          the check finishes, including when something goes wrong. If an upload is abandoned part-way through, what
          arrived is deleted within about an hour.
        </p>
        <p>
          We never use the documents for anything except the check you asked for, and we never use them to train
          software.
        </p>
      </Section>

      <Section title="The report">
        <p>
          The report contains what we found, including figures, dates and names taken from the documents, plus images of
          the pages so you can see the evidence. It also records the monthly rent you entered. Anyone with the report
          link can open it, so only share it with people who should see it.
        </p>
        <p>
          The report and the page images are deleted 24 hours after the check. After that we keep only the report&apos;s
          random id and the times it was created and deleted, for up to 30 days, so the link can say that the report
          has been deleted.
        </p>
      </Section>

      <Section title="Other companies involved">
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <span className="font-medium">Anthropic (Claude).</span> Photos, scans and documents with unusual layouts
            are sent to Anthropic&apos;s Claude service to be read. We also use Claude to word the report, and for that
            we send only the check results and figures, never names or the documents themselves.
          </li>
          <li>
            <span className="font-medium">Companies House.</span> We search the public register for the employer&apos;s
            name shown on the payslip.
          </li>
          <li>
            <span className="font-medium">Hetzner and Vercel.</span> Our server and database run on our own server
            at Hetzner, in the EU ([Hetzner location, for example: Falkenstein, Germany]). This website runs on
            Vercel: your documents pass through it on their way to our server, and the website doesn&apos;t keep
            them.
          </li>
        </ul>
      </Section>

      <Section title="Feedback and emails">
        <p>
          If you tell us whether a report was useful, we keep your answer and any comment to improve Tallied. It is
          linked to the overall result of the report (for example &quot;looks good&quot;), not to the documents, and it
          stays after the report is deleted.
        </p>
        <p>
          Leaving your email is optional. We use it only to tell you when Tallied launches, and we keep it separately
          from reports and feedback. To have it deleted at any time, email <Contact />.
        </p>
      </Section>

      <Section title="Limits and usage counts">
        <p>
          Each person can run 3 free checks a day. To enforce this we keep a scrambled version of your IP address (a
          one-way hash) for 2 days. We never store the IP address itself.
        </p>
        <p>
          We count how many checks are started, finished and viewed, and how often feedback is left. These counts use an
          anonymous code instead of anything that identifies you, involve no cookies, and are kept for about a year.
        </p>
      </Section>

      <Section title="Your rights">
        <p>
          Under UK data protection law you can ask to see the information we hold about you, ask us to correct or delete
          it, and object to how we use it. Email <Contact />. Because reports are deleted after 24 hours, we usually
          won&apos;t hold any documents or reports by the time you ask. If you&apos;re unhappy with how we&apos;ve handled
          your information, you can complain to the{" "}
          <a
            href="https://ico.org.uk/make-a-complaint/"
            className="text-accent underline underline-offset-4 hover:text-accent-hover"
            rel="noopener noreferrer"
          >
            Information Commissioner&apos;s Office
          </a>
          .
        </p>
      </Section>

      <div className="mt-10 border-t border-line pt-6">
        <Link href="/" className="font-medium text-accent underline underline-offset-4 hover:text-accent-hover">
          Back to Tallied
        </Link>
      </div>
    </article>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-8">
      <h2 className="text-2xl font-semibold text-ink">{title}</h2>
      <div className="mt-3 space-y-3 text-ink">{children}</div>
    </section>
  );
}

function Contact() {
  return <span className="font-medium">{CONTACT}</span>;
}
