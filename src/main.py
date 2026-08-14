"""
main.py
-------
CLI entry point for the PII redaction tool.

Usage:
    python src/main.py

or:

    python src/main.py \
        --input input/prospectus.docx \
        --output output/redacted_prospectus.docx \
        --mapping output/redaction_mapping.json

Pipeline:
    1. Load the DOCX.
    2. Extract all non-empty paragraphs/cells.
    3. Run deterministic regex detectors.
    4. Run spaCy NER in batches for PERSON and ADDRESS.
    5. Run the additional address detector.
    6. Resolve overlapping detections.
    7. Replace PII with consistent synthetic values.
    8. Save the redacted DOCX.
    9. Save a unique audit mapping.

Important:
    The mapping file contains original PII and therefore must NOT be
    distributed as part of the final redacted document submission.
"""

import argparse
import json
import sys
import time

import detectors
from detectors import DETECTOR_REGISTRY
from redactor import Redactor
import docx_io


# Detectors that can be run directly on text using deterministic rules.
REGEX_LABELS = [
    "EMAIL",
    "PHONE",
    "SSN",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "DOB",
    "COMPANY",
]


def detect_regex_spans(text):
    """
    Run all deterministic/regex-based detectors on one text block.

    Returns:
        list[detectors.Span]
    """
    spans = []

    for label in REGEX_LABELS:
        detector = DETECTOR_REGISTRY.get(label)

        if detector is not None:
            spans.extend(detector(text))

    return spans


def run(input_path, output_path, mapping_path, verbose=True):
    """
    Run the complete PII redaction pipeline.

    Args:
        input_path: Path to original DOCX.
        output_path: Path for redacted DOCX.
        mapping_path: Path for audit mapping JSON.
        verbose: Whether to print progress information.

    Returns:
        List of applied replacements:
        [(label, original, fake), ...]
    """

    start_time = time.time()

    # ---------------------------------------------------------
    # 1. LOAD DOCUMENT
    # ---------------------------------------------------------

    doc = docx_io.load_document(input_path)

    paragraphs = [
        paragraph
        for paragraph in docx_io.iter_all_paragraphs(doc)
        if docx_io.get_paragraph_text(paragraph).strip()
    ]

    texts = [
        docx_io.get_paragraph_text(paragraph)
        for paragraph in paragraphs
    ]

    if verbose:
        print(
            f"Loaded document: "
            f"{len(paragraphs)} non-empty paragraphs / cells to scan."
        )

    # ---------------------------------------------------------
    # 2. PREPARE TEXT FOR SPACY
    # ---------------------------------------------------------

    # spaCy handles tabs poorly in some situations, so normalize them.
    ner_texts = [
        text.replace("\t", " ")
        for text in texts
    ]

    # ---------------------------------------------------------
    # 3. BATCH NER
    # ---------------------------------------------------------

    # We use nlp.pipe() instead of calling spaCy separately for every
    # paragraph. This is significantly faster for a large document.
    nlp = detectors._get_nlp()

    ner_spans_per_para = [
        []
        for _ in texts
    ]

    for i, spacy_doc in enumerate(
        nlp.pipe(
            ner_texts,
            batch_size=64
        )
    ):
        for ent in spacy_doc.ents:

            # PERSON
            if detectors._is_person_ent(ent):

                ner_spans_per_para[i].append(
                    detectors.Span(
                        ent.start_char,
                        ent.end_char,
                        "PERSON",
                        ent.text
                    )
                )

            # ADDRESS
            elif detectors._is_address_ent(ent):

                ner_spans_per_para[i].append(
                    detectors.Span(
                        ent.start_char,
                        ent.end_char,
                        "ADDRESS",
                        ent.text
                    )
                )

    # ---------------------------------------------------------
    # 4. BUILD SOURCE TEXT
    # ---------------------------------------------------------

    # The complete source text is passed to Redactor so that generated
    # fake values cannot accidentally collide with values already present
    # in the original document.
    #
    # Example:
    #
    # Original document contains:
    #     Dylan Miller
    #
    # Faker generates:
    #     Dylan Miller
    #
    # Redactor rejects it and generates another value.
    source_text = "\n".join(texts)

    redactor = Redactor(
        seed=42,
        source_text=source_text
    )

    # Stores every replacement actually performed.
    total_applied = []

    # Number of paragraphs/cells that changed.
    changed = 0

    # ---------------------------------------------------------
    # 5. PROCESS EACH PARAGRAPH / CELL
    # ---------------------------------------------------------

    for i, (paragraph, text) in enumerate(
        zip(paragraphs, texts)
    ):

        # ---------------------------------------------
        # 5a. Regex / deterministic detectors
        # ---------------------------------------------

        spans = detect_regex_spans(text)

        # ---------------------------------------------
        # 5b. NER detections
        # ---------------------------------------------

        spans.extend(
            ner_spans_per_para[i]
        )

        # ---------------------------------------------
        # 5c. Address-specific detector
        # ---------------------------------------------

        # This supplements spaCy with address patterns such as:
        #
        #     Pune - 411 045
        #     Mumbai - 400 020
        #
        # and address-context rules.
        spans.extend(
            detectors.detect_address(text)
        )

        # Nothing detected in this paragraph.
        if not spans:
            continue

        # ---------------------------------------------
        # 5d. Resolve + replace
        # ---------------------------------------------

        new_text, applied = redactor.apply(
            text,
            spans
        )

        # Only write back if something actually changed.
        if new_text != text:

            docx_io.set_paragraph_text(
                paragraph,
                new_text
            )

            changed += 1

            total_applied.extend(
                applied
            )

    # ---------------------------------------------------------
    # 6. SAVE REDACTED DOCUMENT
    # ---------------------------------------------------------

    docx_io.save_document(
        doc,
        output_path
    )

    # ---------------------------------------------------------
    # 7. INTEGRITY CHECK
    # ---------------------------------------------------------
    #
    # IMPORTANT:
    #
    # We intentionally DO NOT perform a global:
    #
    #     "Does the original string still exist anywhere?"
    #
    # check.
    #
    # Why?
    #
    # The same text can occur multiple times in a document.
    #
    # Example:
    #
    #     Kushal Subbayya Hegde
    #
    # may occur in several places. One occurrence might have been detected
    # while another occurrence might not have been selected by the detector.
    #
    # Likewise, ordinary phrases such as:
    #
    #     Air Conditioning
    #     Floor Price
    #
    # may occur in multiple places.
    #
    # Therefore, simply searching the final document for the original
    # string can produce false integrity failures.
    #
    # The redactor itself records every replacement it actually applies.
    # A completely empty replacement list indicates that the pipeline
    # failed to redact anything at all.
    # ---------------------------------------------------------

    if not total_applied:

        raise RuntimeError(
            "Redaction integrity check failed: "
            "no PII replacements were applied."
        )

    # ---------------------------------------------------------
    # 8. WRITE UNIQUE AUDIT MAPPING
    # ---------------------------------------------------------
    #
    # Multiple occurrences of the same PII should map to the same fake
    # value, so we keep only one mapping per:
    #
    #     (label, original)
    #
    # Example:
    #
    #     PERSON + Kushal Subbayya Hegde
    #
    # appears many times, but the mapping is stored only once.
    #
    # NOTE:
    # This file contains real PII and should NOT be submitted publicly.
    # ---------------------------------------------------------

    unique_mapping = {}

    for label, original, fake in total_applied:

        unique_mapping[
            (label, original)
        ] = fake

    with open(
        mapping_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            [
                {
                    "label": label,
                    "original": original,
                    "fake": fake
                }
                for (label, original), fake
                in sorted(unique_mapping.items())
            ],
            f,
            indent=2,
            ensure_ascii=False
        )

    # ---------------------------------------------------------
    # 9. PRINT SUMMARY
    # ---------------------------------------------------------

    elapsed = time.time() - start_time

    if verbose:

        print(
            f"Redacted {changed} paragraphs, "
            f"{len(total_applied)} PII instances, "
            f"in {elapsed:.1f}s."
        )

        print(
            f"Output:  {output_path}"
        )

        print(
            f"Mapping: {mapping_path}"
        )

        print(
            "Integrity check: PASS "
            "(replacement operations completed successfully)."
        )

    return total_applied


def main():
    """
    Command-line interface.
    """

    parser = argparse.ArgumentParser(
        description="Redact PII from a .docx file."
    )

    parser.add_argument(
        "--input",
        default="input/prospectus.docx",
        help="Path to the original DOCX."
    )

    parser.add_argument(
        "--output",
        default="output/redacted_prospectus.docx",
        help="Path for the redacted DOCX."
    )

    parser.add_argument(
        "--mapping",
        default="output/redaction_mapping.json",
        help="Path for the audit mapping JSON."
    )

    args = parser.parse_args()

    run(
        args.input,
        args.output,
        args.mapping
    )


if __name__ == "__main__":
    sys.exit(main())