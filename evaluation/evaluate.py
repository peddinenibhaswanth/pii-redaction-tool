"""
evaluate.py
-----------
Scores the detection pipeline against evaluation/gold_annotations.json and
writes predictions.json + a metrics summary.

Matching rule: a predicted span counts as a match for a gold entity if they
share the same label and their character ranges overlap by at least one
character. We use overlap rather than exact-string match because address
and (occasionally) company/person boundaries are inherently fuzzy (e.g. our
detector might tag "163, 5th Floor, H.T.Parekh Marg ... Mumbai" while gold
also includes the trailing PIN code) -- exact match would under-count
genuinely correct detections. Each gold entity and each predicted span is
consumed at most once (greedy one-to-one matching) so duplicate/overlapping
predictions can't inflate the true-positive count.

Usage:
    python evaluation/evaluate.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import detectors  # noqa: E402
from detectors import DETECTOR_REGISTRY  # noqa: E402

ALL_LABELS = ["EMAIL", "PHONE", "SSN", "CREDIT_CARD", "IP_ADDRESS", "DOB",
              "PERSON", "COMPANY", "ADDRESS"]


def predict(text):
    spans = []
    for label in ["EMAIL", "PHONE", "SSN", "CREDIT_CARD", "IP_ADDRESS", "DOB", "COMPANY"]:
        spans.extend(DETECTOR_REGISTRY[label](text))
    spans.extend(detectors.detect_person(text))
    spans.extend(detectors.detect_address(text))
    return spans


def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _char_labels(text, spans):
    """Binary character-level PII labels used for supplementary accuracy."""
    labels = [False] * len(text)
    for s in spans:
        start = max(0, s.start)
        end = min(len(text), s.end)
        for i in range(start, end):
            labels[i] = True
    return labels


def score(gold_items):
    counts = {label: {"tp": 0, "fp": 0, "fn": 0} for label in ALL_LABELS}
    predictions_dump = []
    exact_paragraph_matches = 0
    char_tp = char_tn = char_fp = char_fn = 0

    for item in gold_items:
        text = item["text"]
        gold_entities = item["entities"]
        pred_spans = predict(text)
        predictions_dump.append({
            "id": item["id"], "text": text,
            "predictions": [{"label": s.label, "text": s.text} for s in pred_spans],
        })

        # Resolve each gold entity to a character span within this paragraph
        # (first unclaimed occurrence of its text).
        claimed_gold_ranges = []
        gold_spans = []
        for ent in gold_entities:
            start = text.find(ent["text"])
            while start != -1 and any(s == start for s, _ in claimed_gold_ranges):
                start = text.find(ent["text"], start + 1)
            end = start + len(ent["text"]) if start != -1 else -1
            gold_spans.append({"label": ent["label"], "start": start, "end": end,
                                "text": ent["text"], "matched": False})
            if start != -1:
                claimed_gold_ranges.append((start, end))

        # Supplementary binary character-level accuracy. Unlike the entity
        # metrics, this asks whether each character is correctly classified
        # as PII vs non-PII. It is less informative than precision/recall but
        # gives the assignment's requested "accuracy" a standard definition.
        gold_labels = _char_labels(text, [
            type("GoldSpan", (), {"start": g["start"], "end": g["end"]})
            for g in gold_spans if g["start"] >= 0
        ])
        pred_labels = _char_labels(text, pred_spans)
        char_tp += sum(g and p for g, p in zip(gold_labels, pred_labels))
        char_tn += sum((not g) and (not p) for g, p in zip(gold_labels, pred_labels))
        char_fp += sum((not g) and p for g, p in zip(gold_labels, pred_labels))
        char_fn += sum(g and (not p) for g, p in zip(gold_labels, pred_labels))

        pred_used = [False] * len(pred_spans)

        for g in gold_spans:
            if g["start"] == -1:
                continue  # annotation text not found verbatim; shouldn't happen
            for i, p in enumerate(pred_spans):
                if pred_used[i] or p.label != g["label"]:
                    continue
                if overlaps(p.start, p.end, g["start"], g["end"]):
                    pred_used[i] = True
                    g["matched"] = True
                    counts[g["label"]]["tp"] += 1
                    break
            if not g["matched"]:
                counts[g["label"]]["fn"] += 1

        for i, p in enumerate(pred_spans):
            if not pred_used[i]:
                counts.setdefault(p.label, {"tp": 0, "fp": 0, "fn": 0})
                counts[p.label]["fp"] += 1

        # paragraph-level exact-set accuracy (see README for why this is a
        # secondary/coarser metric, not the primary one)
        gold_set = {(g["label"], g["text"]) for g in gold_spans}
        pred_set = {(p.label, p.text) for p in pred_spans}
        if gold_set == pred_set:
            exact_paragraph_matches += 1

    return counts, predictions_dump, exact_paragraph_matches, (char_tp, char_tn, char_fp, char_fn)


def main():
    eval_dir = os.path.dirname(__file__)
    with open(os.path.join(eval_dir, "gold_annotations.json"), encoding="utf-8") as f:
        gold = json.load(f)
    gold_items = gold["items"]

    counts, predictions_dump, exact_matches, char_counts = score(gold_items)

    with open(os.path.join(eval_dir, "predictions.json"), "w", encoding="utf-8") as f:
        json.dump(predictions_dump, f, indent=2, ensure_ascii=False)

    total_tp = total_fp = total_fn = 0
    print(f"{'LABEL':<14}{'TP':>5}{'FP':>5}{'FN':>5}{'Precision':>12}{'Recall':>10}{'F1':>8}")
    for label in ALL_LABELS:
        c = counts.get(label, {"tp": 0, "fp": 0, "fn": 0})
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        total_tp += tp
        total_fp += fp
        total_fn += fn
        precision = tp / (tp + fp) if (tp + fp) else float("nan")
        recall = tp / (tp + fn) if (tp + fn) else float("nan")
        if precision == precision and recall == recall and (precision + recall):
            f1 = 2 * precision * recall / (precision + recall)
            print(f"{label:<14}{tp:>5}{fp:>5}{fn:>5}{precision:>12.2%}{recall:>10.2%}{f1:>8.2%}")
        else:
            print(f"{label:<14}{tp:>5}{fp:>5}{fn:>5}{'n/a':>12}{'n/a':>10}{'n/a':>8}")

    overall_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else float("nan")
    overall_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else float("nan")
    overall_f1 = (2 * overall_p * overall_r / (overall_p + overall_r)
                  if (overall_p + overall_r) else float("nan"))
    print("-" * 58)
    print(f"{'OVERALL':<14}{total_tp:>5}{total_fp:>5}{total_fn:>5}{overall_p:>12.2%}{overall_r:>10.2%}{overall_f1:>8.2%}")
    print(f"\nParagraph-level exact-set accuracy: {exact_matches}/{len(gold_items)} "
          f"({exact_matches/len(gold_items):.2%})")

    char_tp, char_tn, char_fp, char_fn = char_counts
    char_total = char_tp + char_tn + char_fp + char_fn
    char_accuracy = (char_tp + char_tn) / char_total if char_total else float("nan")
    print(f"Character-level binary accuracy: {char_accuracy:.2%}")


if __name__ == "__main__":
    main()
