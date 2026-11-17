# Rate Confirmation Extractor

Turns freight rate confirmations (text, PDF or scanned fax) into structured, validated load data. It flags anything a person needs to check, and detects when a broker silently revises a load.

Built from the carrier's side: a broker sends a rate con, and the carrier needs the stops, times, rate, references and fine print entered correctly before dispatching a driver and billing the load.

## Why this exists

Carriers receive rate cons in every layout imaginable: clean TMS printouts, emails, fax-quality scans, and PDFs with five pages of legal terms. Entering them by hand is slow, and the expensive mistakes are the quiet ones:

- A rate that doesn't add up, caught weeks later as a short-paid invoice.
- Two notes that contradict each other, such as a default "driver pays lumper" note next to a load note saying the customer pays.
- A fine for missed tracking buried on page four of the terms.
- A revised rate con with an added stop and a new rate, sent with no "REVISED" label and no note.

This project extracts the data with Claude, checks it with deterministic business rules, and routes anything uncertain to a person with the specific problem pointed out.

## How it works

```
document (.txt / .pdf / scan)
   │
   ▼
Claude extracts fields  ──►  Pydantic validates business rules  ──►  SQLite stores every version
   (transcribe only)          (math, stop order, appointments)        (append-only history)
                                         │                                     │
                                         ▼                                     ▼
                              NEEDS REVIEW with the            compare to previous version:
                              exact field that failed          duplicate / revision / no change
```

1. **Extract.** Claude reads the document (PDFs as text and page images, so scans work) and returns JSON matching the schema in `schema.py`.
2. **Validate.** Pydantic checks types and business rules: rate parts add up to the total, the first stop is a pickup, an appointment has a time.
3. **Flag.** Validation errors and contradictions Claude found send the load to review, along with the extraction so the reviewer only fixes what's wrong.
4. **Store.** Every rate con received is saved as a new version. Nothing is overwritten.
5. **Compare.** When a load number arrives again, the new version is compared against the last one, and the changes are summarized for the operator.

## Design decisions

**Extraction and policy are separate.** Claude transcribes exactly what the document says, including a wrong total. Business rules (what to do when the math is off, what counts as a revision) live in code. The model is never asked to "fix" a document, and the rules can change without touching the prompt.

**Ambiguity goes to a person, not a guess.** When the line items don't match the total, the system doesn't pick one. A mismatch is more likely a misread than a broker error, since broker TMSs calculate totals automatically, so a person checks it before money is involved.

**Domain judgment is written into rules, not left to the model.** Early runs showed Claude inferring things inconsistently from run to run, such as whether a tracking deduction implies tracking is required. Those calls are now explicit rules in the prompt, and they stopped varying.

**Warnings are not deductions.** A fine in the terms and conditions ("$200 if tracking isn't maintained") is a warning that applies only if an obligation is missed. A deduction already taken out of the pay is a negative line item. Each warning is tagged as load-specific or boilerplate, so the load-specific ones can be shown first.

**Plain code does what plain code can.** Once Claude has read a document, comparing versions is done without AI: file fingerprints catch exact duplicates, structured fields are compared directly, and wording changes are found by diffing the broker's original text line by line. Claude's paraphrase of a note varies between runs; the broker's text doesn't. Scans have no text to diff, so they fall back to comparing Claude's notes and say so.

**Every version is kept.** The database is append-only. In a billing dispute, the carrier can show exactly what the broker sent and when.

## Evaluation

`evaluate.py` scores extractions against hand-checked answer keys for 10 loads. Three loads are also tested as PDFs: a clean digital PDF, a 6-page PDF with terms buried in the fine print, and a fax-quality scan with no text layer. That's 13 documents and 502 scored fields.

It reports:
- **Field accuracy**: share of scored fields that match.
- **Document accuracy**: share of documents with every field right. This matters more to a carrier than field accuracy, because one wrong field means a wrong load.
- **Silent errors**: documents that passed validation but contain a wrong field. These are the dangerous ones, because nobody looks at them.
- **Expected flags**: documents with planted problems (a math error, conflicting notes, a temperature that doesn't match the commodity) must be flagged for review.

**Latest result: 13/13 documents correct, 502/502 fields, 0 silent errors**, with all three planted problems flagged.

What the eval caught along the way:

| Finding | Cause | Fix |
|---|---|---|
| A flat rate was put in "linehaul" on one document but not on a similar one | The prompt never said how to treat a flat rate | Explicit rule; document accuracy went from 80% to 100% |
| A date with no year came back as next year | The year was resolved against the day the code ran | Dates now resolve against the day the rate con was received, so results don't depend on when the eval runs |
| Claude flagged "frozen" product at 34°F as a conflict | Claude was right; the test data was wrong | Corrected the test data, and kept one planted temperature mismatch as a test case |
| A conflict caught in one run was missed in the next | Run-to-run variation on a judgment call, confirmed by running the same document three times | Wrote the domain rule into the prompt instead of leaving it to inference |

Known limitation: one run per document. Judgment calls can vary between runs, so a stronger eval would run each document several times and report consistency.

## Revision detection

```
$ python3 process.py samples/rc6a_magnolia_original.txt samples/rc6b_magnolia_revised.txt samples/rc6b_magnolia_revised.txt

OK: MFG-33018 (Magnolia Freight Group), version 1

REVISION: MFG-33018 (Magnolia Freight Group), version 2
  • Rate total: $1,650.00 -> $1,950.00
  • Stop ADDED: delivery Carolina Home Goods Outlet, Greensboro, NC (2026-11-21 appointment 15:00)
  • Document lines changed:
    + DROP 2: Carolina Home Goods Outlet, 1820 Battleground Ave, Greensboro, NC 27408
    + 11/21/2026 APPT 1500 PO# CHG-55411
    - Rate: $1,650.00 flat
    + Rate: $1,950.00 flat

DUPLICATE: MFG-33018 (Magnolia Freight Group), version 3
```

The revised rate con has no "revised" label and no note. The system catches it because it remembers the first version. Stops are matched by type and location rather than stop number, so inserting a stop doesn't make every later stop look changed.

## Cost

Measured token usage for the same load in two formats:

| Format | Input tokens | Output tokens |
|---|---|---|
| Text | 4,656 | 1,117 |
| 6-page PDF | 26,760 | 776 |

The PDF costs about 5–6× more on input, mostly to read boilerplate. Most of the text version's input is the system prompt and schema, which are resent on every call. Two optimizations are planned but not yet built:

- **Routing**: send digital PDFs as extracted text and reserve page-image mode for real scans.
- **Prompt caching**: cache the fixed system prompt and schema across calls.

## Limitations and next steps

- Loads are matched on the broker's load number alone. Pairing it with the broker's MC number would rule out rare collisions between brokers.
- Test documents are synthetic. Real broker rate cons, with permission, would make the eval meaningfully harder.
- Structured outputs (API-enforced JSON schema) hit a grammar-size limit with this schema, so the schema is sent in the prompt and enforced by Pydantic afterward. Finding the exact part of the schema that exceeds the limit is an open task.
- Reefer temperature is checked by a prompt rule. Moving it to a numeric field checked in code would make it fully deterministic.
- No review screen yet. The data for one is stored: errors, changes, status and the original document text.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=your-key" > .env

python3 -m pytest -q              # schema, comparison and storage tests
python3 extract.py                # extract every sample into results/
python3 evaluate.py               # score results against answer keys
python3 process.py <files...>     # simulate arrivals: store, compare, flag
python3 process.py --queue        # show what needs a person
```

## Files

| File | Purpose |
|---|---|
| `schema.py` | Data model for a rate con, with business-rule validation |
| `extract.py` | Sends a document to Claude and validates the result |
| `evaluate.py` | Scores extractions against answer keys |
| `store.py` | SQLite storage with full version history and a review queue |
| `compare.py` | Duplicate detection, field comparison and document line diff |
| `process.py` | End-to-end inbox simulation |
| `samples/`, `answer_keys/` | Test documents and hand-checked correct answers |
| `test_*.py` | Tests |

Built with Claude (Anthropic) as a coding partner. The freight domain decisions, test scenarios and answer-key review are mine.
