/**
 * Sample checks: made-up documents anyone can run to see Tallied work. The PDFs are the synthetic
 * test fixtures (fictional people, a fictional employer and the fictional Northgate Bank), served
 * as static files from public/samples/<id>/. The API knows each sample by the same id and only
 * accepts exactly these files (api/app/samples.py; api/tests/test_samples.py keeps them in step).
 */

export type SampleDoc = { file: string; label: string };

export type Sample = {
  id: "genuine" | "edited_balance" | "multiple_problems";
  title: string;
  summary: string;
  outcome: { label: string; tone: "good" | "closer" | "risk" };
  rent: number;
  docs: SampleDoc[];
};

const DOCS: SampleDoc[] = [
  { file: "statement_2026-08.pdf", label: "Bank statement, August 2026" },
  { file: "statement_2026-09.pdf", label: "Bank statement, September 2026" },
  { file: "statement_2026-10.pdf", label: "Bank statement, October 2026" },
  { file: "payslip_2026-09.pdf", label: "Payslip, September 2026" },
  { file: "payslip_2026-10.pdf", label: "Payslip, October 2026" },
];

export const SAMPLES: Sample[] = [
  {
    id: "genuine",
    title: "A genuine tenant",
    summary: "Jordan Hayes, paid monthly by Brightwater Logistics. Three months of statements and two payslips that all agree.",
    outcome: { label: "Looks good", tone: "good" },
    rent: 1100,
    docs: DOCS,
  },
  {
    id: "edited_balance",
    title: "An edited bank statement",
    summary: "The same tenant, but one balance on the October statement has been changed by £900.",
    outcome: { label: "Needs a closer look", tone: "closer" },
    rent: 1100,
    docs: DOCS,
  },
  {
    id: "multiple_problems",
    title: "Several problems at once",
    summary: "An edited statement, and payslips showing far too little tax and National Insurance.",
    outcome: { label: "High risk", tone: "risk" },
    rent: 1100,
    docs: DOCS,
  },
];

export function sampleUrl(sample: Sample, doc: SampleDoc): string {
  return `/samples/${sample.id}/${doc.file}`;
}

/** Downloads a sample's documents from this site, ready to upload like any other files. */
export async function sampleFiles(sample: Sample): Promise<File[]> {
  return Promise.all(
    sample.docs.map(async (doc) => {
      const res = await fetch(sampleUrl(sample, doc));
      if (!res.ok) throw new Error("sample_unavailable");
      return new File([await res.blob()], doc.file, { type: "application/pdf" });
    }),
  );
}
