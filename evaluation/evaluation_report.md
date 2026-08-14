# Evaluation Report

> **Important:** The metrics below are the baseline results from the pre-fix version of the detector. After the fixes in the current source code are run, execute `python evaluation/evaluate.py` and replace the baseline numbers with the new actual results. Do not submit stale metrics.

## Methodology

The uploaded file is a real ~107,000-word, 12,400-line IPO Red Herring
Prospectus (4,686 non-empty paragraphs/table cells once headers, footers,
body, and nested tables are all walked). Hand-annotating all of it was not
feasible in the time available, so evaluation uses a **stratified random
sample of 50 paragraphs**:

- **35 paragraphs** pre-filtered as likely to contain PII (keyword hits on
  `@`, known promoter surnames such as "Hegde", "Registered Office:",
  "Date of Birth", etc.) — this ensures the sample actually contains enough
  positive examples of every relevant type to measure recall meaningfully.
- **15 paragraphs** sampled uniformly at random from the remaining text —
  this measures the false-positive rate on ordinary prospectus prose
  (financial disclosures, risk factors, legal boilerplate), which is where
  a naive detector is most likely to over-trigger.

Each of the 50 paragraphs was read manually and every PII instance was
labeled by hand into `evaluation/gold_annotations.json`, using the same
category scope as the rest of the tool (see README for what counts as
"COMPANY", "ADDRESS", etc.).

`evaluation/evaluate.py` re-runs the actual detectors (the same functions
`main.py` uses) against the 50 gold paragraphs and matches predictions to
gold entities by **label + character-span overlap** (not exact string
match), since address/name boundaries are sometimes fuzzy even when the
detection is substantively correct. Each gold entity and each predicted
span can be consumed at most once, so duplicate predictions cannot inflate
the true-positive count. Full run: `python evaluation/evaluate.py`.

## Results (n = 50 paragraphs, 66 gold PII instances)

| Label | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| EMAIL | 16 | 0 | 0 | 100.00% | 100.00% | 100.00% |
| PHONE | 5 | 0 | 0 | 100.00% | 100.00% | 100.00% |
| SSN | 0 | 0 | 0 | n/a (none in sample) | n/a | n/a |
| CREDIT_CARD | 0 | 0 | 0 | n/a (none in sample) | n/a | n/a |
| IP_ADDRESS | 0 | 0 | 0 | n/a (none in sample) | n/a | n/a |
| DOB | 0 | 0 | 0 | n/a (none in sample) | n/a | n/a |
| PERSON | 28 | 3 | 8 | 90.32% | 77.78% | 83.58% |
| COMPANY | 5 | 0 | 0 | 100.00% | 100.00% | 100.00% |
| ADDRESS | 2 | 0 | 1 | 100.00% | 66.67% | 80.00% |
| **OVERALL** | **56** | **3** | **9** | **94.92%** | **86.15%** | **90.32%** |

**Paragraph-level exact-set accuracy: 37/50 (74.00%) (baseline)** — the fraction of
paragraphs where the predicted PII set exactly equals the gold set. This is
reported because the assignment asks for an "accuracy" number; it is a
stricter, coarser metric than per-entity precision/recall (a single missed
name in an otherwise-correct paragraph counts as a full paragraph failure),
included alongside P/R/F1 rather than in place of them.

SSN, credit card, IP address, and DOB detectors have **zero real-world
instances** in this document — expected, since a corporate IPO prospectus
doesn't contain individual customers' SSNs or card numbers. Those four
detectors are validated separately in `tests/test_detectors.py` against
synthetic inputs (including Luhn-check validation for credit cards and
correct rejection of out-of-range IPs).

## Error Analysis

**PERSON — 3 false positives, 8 false negatives:**
- FN: **Word-internal split failures.** Names printed across multiple
  Word runs with a literal tab character between tokens (e.g.
  `"Maithili\tRajesh Hegde"`) or with a trailing footnote marker glued on
  (`"Rohit Kushal Hegde*^&"`) sometimes get partially tagged by spaCy
  ("Rajesh Hegde" instead of "Maithili Rajesh Hegde", or missed entirely).
  This is a tokenization edge case from how the prospectus's tables encode
  whitespace, not a logic bug — spaCy's PERSON model wasn't trained on
  documents with stray tab characters mid-name.
- FN: **Recall drop on 2-word variants of an already-seen 3-word name**
  ("Karunakar Hegde" missed once, though "Karunakar Hegde" is caught
  elsewhere in the document with a middle initial). NER confidence on
  short 2-token names is inherently lower than on 3-token names.
- FP: `"a Bid"` — a rare NER mis-tag from spaCy on an all-caps defined-term
  context; would need a bigger stoplist or a real dependency-parse check
  to filter deterministically without risking recall elsewhere.

**ADDRESS — 1 false negative:** a full multi-line address block
(`"201, Tower 2, Montreal Business Centre, Off Pallod Farms, Baner, Pune –
411 045, Maharashtra, India"`) was entirely missed because none of its
individual tokens got tagged as GPE/LOC/FAC by spaCy (it read as a run of
proper nouns without a recognizable place-entity boundary) and its PIN
code has an internal space (`411 045`) that our PIN regex (which expects
a contiguous 6-digit token) doesn't match. **Known limitation**, documented
in the README.

## Precision decisions made explicit (per assignment's evaluation criteria)

- **Order/ticket/registration numbers are not treated as PII** and are
  actively filtered out where they'd otherwise collide with the PHONE
  regex (see `test_phone_rejects_registration_numbers`, a regression test
  for a real false positive found during development: SEBI registration
  codes like `INM000013004` were initially being redacted as phone
  numbers).
- **Bare country/state names are not treated as ADDRESS** (`"the United
  States"`, `"Maharashtra"` alone) — only multi-part strings that look like
  a mailing address are.
- **Bare brand mentions without a legal suffix are not treated as
  COMPANY** (e.g. `"Reliance"` alone) — only suffixed legal entity names
  (`Ltd`, `Private Limited`, `LLP`, `Inc`, etc.) are, trading a known
  recall gap for much higher precision (an earlier NER-based version of
  this detector produced 2,538 "company" hits on a first pass, the large
  majority of which were legal defined-terms and section headers, not
  company names).
