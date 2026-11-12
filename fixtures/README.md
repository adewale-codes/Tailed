# Synthetic test documents

Everything here is fictional: Northgate Bank, Brightwater Logistics Ltd, the people and the account
numbers. NI numbers use the `QQ` prefix, which HMRC never issues. Every page carries a footer saying it
is a synthetic test document. **Never add real people's statements or payslips to this folder.**

Regenerate (deterministic, overwrites only the six case folders):

```bash
cd api
uv run python ../fixtures/generate.py
```

## Cases

Each folder has 3 statements (Aug to Oct 2026), 2 payslips (Sep and Oct 2026) and an `expected.json`.

| Folder | What's wrong | Checks that should flag |
|---|---|---|
| `genuine/` | Nothing. £2,650.00 net salary on the 27th, balances add up | none |
| `edited_balance/` | First balance on 12 Oct is £900 too high, carried to every later line and the summary closing balance. That line uses Vera instead of Helvetica | `balance_arithmetic`, `document_integrity` |
| `salary_mismatch/` | Statements show £2,650.00; payslips say net £3,100.64 (gross £4,190, tax £628.50, NI £251.36, pension £209.50) | `income_match` |
| `bad_payslip/` | Gross £4,600 but tax £310 and NI £95, far too low (expected about £792.67 and £259.50). Totals add up and match the statements | `payslip_maths` |
| `name_mismatch/` | Statements: Jordan A Hayes. Payslips: Daniel K Okafor | `name_match` |
| `edited_metadata/` | Content is genuine, but the October statement's PDF metadata says Producer `iLovePDF`, modified 9 Nov 2026 | `document_integrity` |

## expected.json

- `expected_verdict`: `pass` or `flag`
- `expected_checks`: one entry per check ID: `pass`, `flag` or `not_asserted`
- `expected_flags`: the specific findings a correct implementation should report
- `truth`: the ground-truth figures (opening/closing balances, totals, salary credits, payslip fields)

Check IDs: `balance_arithmetic`, `document_integrity`, `income_match`, `payslip_maths`, `name_match`,
`employer_check`. `employer_check` is always `not_asserted` because the employer is fictional and
won't be on Companies House. That check needs mocked API responses in its own tests.

Tax model used for `payslip_maths` (2026/27, England, 1257L, NI category A, monthly): personal allowance
£1,047.50, basic band £3,141.67 at 20%, then 40%; NI 8% between £1,048 and £4,189, 2% above. Employee
pension is 5% under relief at source, so it does not reduce taxable pay.

## Photo fixture (AI path)

`photo_statement/` holds a simulated phone photo of page 1 of `genuine/statement_2026-08.pdf`
(perspective, shadow, uneven light, blur, noise, JPEG) and an `expected.json` with the 32
transactions on that page. Regenerate with:

```bash
cd api
uv run python ../fixtures/make_photo.py
```

The live test that sends it to Claude runs only when `ANTHROPIC_API_KEY` is set:
`uv run pytest -m live_ai -s`.
