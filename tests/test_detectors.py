"""
test_detectors.py
------------------
Table-driven unit tests for every detector in src/detectors.py, plus a
couple of integration tests for redactor.py's overlap resolution and
consistent-fake-mapping behaviour.

Run with:  pytest tests/ -v
(from the project root, with src/ on PYTHONPATH -- see conftest via sys.path
 hack below, kept dependency-free on purpose.)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import detectors  # noqa: E402
from redactor import Redactor  # noqa: E402


def found_texts(spans):
    return sorted(s.text for s in spans)


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

def test_email_basic():
    text = "Contact us at cs.connect@kshinternational.com for queries."
    assert found_texts(detectors.detect_email(text)) == ["cs.connect@kshinternational.com"]


def test_email_multiple():
    text = "Reach a@b.com or c.d+tag@sub.example.co.in"
    assert found_texts(detectors.detect_email(text)) == ["a@b.com", "c.d+tag@sub.example.co.in"]


def test_email_no_false_positive_on_plain_text():
    assert detectors.detect_email("This has no email address in it at all.") == []


# ---------------------------------------------------------------------------
# PHONE
# ---------------------------------------------------------------------------

def test_phone_with_country_code():
    text = "Telephone: +91 22 6807 7100"
    assert len(detectors.detect_phone(text)) == 1


def test_phone_local_format():
    text = "Call 022-68052182 for support."
    assert len(detectors.detect_phone(text)) == 1


def test_phone_rejects_registration_numbers():
    """SEBI-style registration codes like INM000013004 must not be
    mistaken for phone numbers (regression test for a real false positive
    found in evaluation)."""
    text = "SEBI registration no.: INM000013004"
    assert detectors.detect_phone(text) == []


# ---------------------------------------------------------------------------
# SSN
# ---------------------------------------------------------------------------

def test_ssn_basic():
    text = "SSN on file: 123-45-6789."
    assert found_texts(detectors.detect_ssn(text)) == ["123-45-6789"]


def test_ssn_no_false_positive_on_similar_number():
    # a 9-digit run without the 3-2-4 dash grouping should not match
    assert detectors.detect_ssn("Reference number 123456789") == []


# ---------------------------------------------------------------------------
# CREDIT CARD
# ---------------------------------------------------------------------------

def test_credit_card_valid_luhn():
    # well-known test Visa number, passes Luhn check
    text = "Card number 4532015112830366 on file."
    assert len(detectors.detect_credit_card(text)) == 1


def test_credit_card_invalid_luhn_rejected():
    text = "Card number 1234567890123456 on file."
    assert detectors.detect_credit_card(text) == []


# ---------------------------------------------------------------------------
# IP ADDRESS
# ---------------------------------------------------------------------------

def test_ip_address_basic():
    text = "Server accessible at 192.168.1.10 internally."
    assert found_texts(detectors.detect_ip(text)) == ["192.168.1.10"]


def test_ip_address_rejects_out_of_range():
    # 999.999.999.999 is not a valid IP and should not match
    assert detectors.detect_ip("Value was 999.999.999.999 in the log.") == []


# ---------------------------------------------------------------------------
# DOB
# ---------------------------------------------------------------------------

def test_dob_with_label():
    text = "Date of Birth: 14-08-1990"
    spans = detectors.detect_dob(text)
    assert found_texts(spans) == ["14-08-1990"]


def test_dob_requires_label_by_design():
    """A bare date with no DOB-style label is intentionally NOT flagged --
    documented precision/recall tradeoff (see README)."""
    assert detectors.detect_dob("The meeting was held on 14-08-1990.") == []


# ---------------------------------------------------------------------------
# PERSON
# ---------------------------------------------------------------------------

def test_person_basic():
    text = "The report was signed by Kushal Subbayya Hegde on behalf of the board."
    assert "Kushal Subbayya Hegde" in found_texts(detectors.detect_person(text))


def test_person_rejects_single_word():
    assert detectors.detect_person("Reliance is a large company.") == []


def test_person_rejects_institutional_terms():
    """Regression test: 'Supa Facility' and similar capitalized legal/
    facility terms must not be tagged as person names."""
    text = "The Supa Facility phase II is under construction."
    assert "Supa Facility" not in found_texts(detectors.detect_person(text))


# ---------------------------------------------------------------------------
# COMPANY
# ---------------------------------------------------------------------------

def test_company_private_limited_suffix():
    text = "KSH International Private Limited is the issuer."
    assert found_texts(detectors.detect_company(text)) == ["KSH International Private Limited"]


def test_company_llp_suffix():
    text = "Audited by Kirtane & Pandit LLP, Chartered Accountants."
    assert found_texts(detectors.detect_company(text)) == ["Kirtane & Pandit LLP"]


def test_company_no_suffix_not_matched():
    """Documented tradeoff: bare brand mentions without a legal suffix are
    not caught by the regex-based company detector."""
    assert detectors.detect_company("Reliance announced its results today.") == []


# ---------------------------------------------------------------------------
# ADDRESS
# ---------------------------------------------------------------------------

def test_address_with_pin_code():
    text = "Registered at 163, 5th Floor, Backbay Reclamation, Mumbai - 400020, India."
    assert len(detectors.detect_address(text)) >= 1


def test_address_rejects_bare_country_name():
    assert detectors.detect_address("Our operations span the United States and India.") == []


# ---------------------------------------------------------------------------
# Redactor: overlap resolution + consistent fake mapping
# ---------------------------------------------------------------------------

def test_redactor_consistent_mapping_for_repeated_name():
    text = "Rohan Dey called. Rohan Dey will follow up tomorrow."
    r = Redactor(seed=1)
    spans = [
        detectors.Span(0, 9, "PERSON", "Rohan Dey"),
        detectors.Span(17, 26, "PERSON", "Rohan Dey"),
    ]
    new_text, applied = r.apply(text, spans)
    fakes = {fake for (_, orig, fake) in applied if orig == "Rohan Dey"}
    assert len(fakes) == 1  # same original always maps to the same fake


def test_redactor_no_overlap_in_output():
    text = "Email john@example.com or call +91 9876543210."
    r = Redactor(seed=1)
    spans = detectors.detect_email(text) + detectors.detect_phone(text)
    new_text, applied = r.apply(text, spans)
    assert "john@example.com" not in new_text
    assert "9876543210" not in new_text


def test_redactor_rejects_fake_that_exists_in_source():
    """A synthetic replacement must never reuse a real value from the source."""
    text = "Rohan Dey is a real person in this document."
    r = Redactor(seed=1, source_text=text)
    # Force Faker to propose a source value first, then a safe value.
    candidates = iter(["Rohan Dey", "Michael Anderson"])
    r.faker.name = lambda: next(candidates)
    fake = r._fake_for("PERSON", "Some Other Person")
    assert fake == "Michael Anderson"
    assert fake not in text


def test_redactor_mapping_has_one_fake_per_unique_value():
    text = "Rohan Dey called. Rohan Dey called again."
    r = Redactor(seed=1, source_text=text)
    spans = [
        detectors.Span(0, 9, "PERSON", "Rohan Dey"),
        detectors.Span(17, 26, "PERSON", "Rohan Dey"),
    ]
    new_text, applied = r.apply(text, spans)
    mappings = {(label, original): fake for label, original, fake in applied}
    assert len(mappings) == 1
    assert new_text.count(next(iter(mappings.values()))) == 2


def test_address_accepts_spaced_indian_pin():
    text = "Corporate Office: 201, Tower 2, Montreal Business Centre, Off Pallod Farms, Baner, Pune – 411 045, Maharashtra, India;"
    spans = detectors.detect_address(text)
    assert any("411 045" in s.text for s in spans)


def test_person_handles_tab_between_name_tokens():
    text = "Rajesh\tKushal Hegde"
    spans = detectors.detect_person(text)
    assert any(s.text == "Rajesh Kushal Hegde" for s in spans)
