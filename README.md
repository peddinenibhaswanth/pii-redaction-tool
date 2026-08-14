# PII Redaction Tool

Redacts personally identifiable information from a `.docx` file and
replaces it with realistic fake alternatives, preserving a consistent
mapping (e.g. every occurrence of "Rohan Dey" becomes the same fake name
throughout the document).

Tested end-to-end on a real, ~107,000-word IPO Red Herring Prospectus (the
uploaded `input/prospectus.docx`), not just short synthetic examples.

## Quick start

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm   # one-time model download

python src/main.py \
  --input input/prospectus.docx \
  --output output/redacted_prospectus.docx \
  --mapping output/redaction_mapping.json
```

Run tests: `pytest tests/ -v`
Run evaluation: `python evaluation/evaluate.py`

## Approach

**Hybrid: regex for deterministic PII types, spaCy NER for fuzzy ones.**

| Type | Method | Why |
|---|---|---|
| Email, Phone, SSN, Credit Card, IP Address, DOB | Regex (+ Luhn check for cards) | These have a fixed, learnable shape — regex is fast, deterministic, and easy to unit test. |
| Person | spaCy `en_core_web_sm` NER, filtered | Names have no fixed shape; NER generalizes far better than any name-pattern regex, but needs post-filtering (see below) in a legal/financial document. |
| Company | Regex on legal-entity suffix (`Ltd`, `Private Limited`, `LLP`, `Inc`, `Corp`, etc.) | Tried spaCy's `ORG` label first — on this document it flagged section headers and legal defined-terms ("DEFINITIONS", "OFFER", "Registered Office") as organizations far more often than real companies. Suffix-based regex is far more precise, at the cost of missing bare brand mentions with no legal suffix. |
| Address | spaCy `GPE`/`LOC`/`FAC` NER + a PIN-code/street-keyword regex, with a country/state stopword filter | Full mailing addresses are structurally similar to ordinary place-name mentions in running text ("our facility in Pune, Maharashtra"), so this is the hardest category; see tradeoffs below. |

**Pipeline** (`src/main.py`): read every paragraph in the document (body,
tables including nested tables, headers, footers) → run all detectors on
each paragraph's text → resolve overlapping spans by priority
(`redactor.py`) → substitute with a `Faker`-generated fake value, reusing
the same fake for every repeat of the same original value → write the
paragraph's text back into the docx.

**Formatting tradeoff:** when a paragraph is redacted, all of its runs are
merged into the first run. This keeps the substitution logic simple (no
need to re-split fake text across Word's often-arbitrary run boundaries)
at the cost of losing *mid-paragraph* formatting changes (e.g. if only the
phone number was bold, the whole paragraph now takes on the first run's
formatting). Paragraph-level formatting (headings, bullets, alignment) and
table structure are unaffected.

## Precision decisions (explicit, per the assignment's request)

- **Order/ticket/registration numbers are not PII** and are actively
  excluded — e.g. SEBI registration codes like `INM000013004` looked like
  phone numbers to an early version of the regex and were filtered out
  once found (see `evaluation_report.md` and the regression test in
  `tests/test_detectors.py`).
- **Bare country/state names are not addresses** ("the United States",
  "Maharashtra" on its own) — only multi-part strings that read like an
  actual mailing address are redacted as ADDRESS.
- **Bare brand names without a legal suffix are not companies**
  ("Reliance" alone won't be redacted, "Reliance Industries Limited"
  would be). This is a deliberate precision-over-recall tradeoff — see
  Known Limitations.
- **Dates of birth are only redacted when explicitly labeled** ("Date of
  Birth:", "DOB:") — a 100+ page prospectus contains thousands of dates
  (incorporation dates, resolution dates, fiscal year references); treating
  every date as a potential DOB would produce enormous numbers of false
  positives for essentially zero real DOB instances in this document type.

## Known limitations / false positives & negatives

- **Person names split across Word tab characters or glued to footnote
  markers** (`"Rohit Kushal Hegde*^&"`, `"Maithili\tRajesh Hegde"`)
  sometimes get partially tagged or missed by spaCy — a document-encoding
  edge case, not a logic bug.
- **Multi-line addresses with no NER-recognizable place entity** and a
  PIN code containing an internal space (`411 045` instead of `411045`)
  can be missed entirely.
- **Occasional NER mis-tags** on all-caps legal defined-terms as PERSON
  (rare; one instance in the 50-paragraph evaluation sample).
- Full numbers, methodology, and error analysis: see
  `evaluation/evaluation_report.md`.

## Extending to a new PII type

1. Add a `detect_x(text) -> list[Span]` function to `src/detectors.py`.
2. Add it to `DETECTOR_REGISTRY` at the bottom of that file.
3. Add its label to `REGEX_LABELS` in `main.py` (or wire it into the NER
   batch loop if it needs spaCy).
4. Add it to `PRIORITY` in `redactor.py` if it should win/lose against
   other types on overlapping spans, and to `_fake_for()` if it needs a
   specific kind of fake value.
5. Add a couple of table-driven tests to `tests/test_detectors.py` and a
   few gold examples to `evaluation/gold_annotations.json`.

## Project structure

```
pii-redaction-tool/
├── input/prospectus.docx              # source document
├── output/redacted_prospectus.docx    # generated by src/main.py
├── src/
│   ├── main.py         # CLI entry point / pipeline orchestration
│   ├── detectors.py    # one function per PII type + the registry
│   ├── redactor.py      # overlap resolution + fake-value substitution
│   └── docx_io.py       # docx read/write (body, tables, headers/footers)
├── evaluation/
│   ├── gold_annotations.json
│   ├── evaluate.py
│   └── evaluation_report.md
├── tests/test_detectors.py
├── requirements.txt
└── .gitignore
```
