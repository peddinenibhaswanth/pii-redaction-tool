"""
detectors.py
------------
PII detectors for the redaction pipeline.

The detectors are intentionally conservative for a financial prospectus:
false positives are costly because ordinary financial terms, years, codes,
and company/legal phrases occur frequently in the document.
"""

import re
from dataclasses import dataclass

import spacy


_NLP = None


def _get_nlp():
    """Lazy-load the spaCy model once."""
    global _NLP
    if _NLP is None:
        _NLP = spacy.load("en_core_web_sm")
    return _NLP


@dataclass
class Span:
    start: int
    end: int
    label: str
    text: str


# ---------------------------------------------------------------------------
# Regex-based detectors
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)


# Phone numbers in this prospectus appear mainly as Indian/international
# contact numbers. We deliberately require a phone-like separator/prefix or
# an explicit phone label/context so dates, years and financial codes are not
# mistaken for phone numbers.
PHONE_STRICT_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:"
    # International format with separated or compact 10-digit subscriber.
    r"\+\s*\d{1,3}[\s.-]?"
    r"(?:\(?\d{2,5}\)?[\s.-]?)?"
    r"(?:\d{3,4}[\s.-]\d{3,4}|\d{10})"

    r"|"

    # Indian/local STD format such as 022-68052182.
    r"\d{3}[-.]\d{8}"

    r"|"

    # Other separated local/international formats.
    r"\(?\d{2,5}\)?[\s.-]\d{3,4}[\s.-]\d{3,4}"

    r"|"

    # Indian mobile format with a space.
    r"\d{5}[\s.-]\d{5}"
    r")"
    r"(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# Explicit phone-labelled values may be compact (for example 02268052182).
PHONE_LABEL_RE = re.compile(
    r"(?:Telephone|Phone|Tel\.?|Mobile|Contact(?:\s+No\.?)?)\s*[:\-]?\s*"
    r"(\+?\s*[\d() .-]{8,18}(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?)",
    re.IGNORECASE,
)

SSN_RE = re.compile(
    r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"
)

CREDIT_CARD_RE = re.compile(
    r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"
)

IP_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
)

DOB_RE = re.compile(
    r"(?:Date of Birth|DOB|D\.O\.B\.?)\s*[:\-]?\s*"
    r"(\d{1,2}[-/. ](?:\d{1,2}|[A-Za-z]+)[-/. ]\d{2,4})",
    re.IGNORECASE,
)


def detect_email(text):
    return [
        Span(m.start(), m.end(), "EMAIL", m.group())
        for m in EMAIL_RE.finditer(text)
    ]


def _looks_like_date_or_year(raw):
    """Reject common dates/years that a generic digit matcher could confuse with phones."""
    value = raw.strip()

    if re.fullmatch(r"\d{4}", value):
        return True

    if re.fullmatch(r"\d{4}[-/]\d{4}", value):
        return True

    if re.fullmatch(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", value):
        return True

    if re.fullmatch(r"\d{8}", value):
        return True

    return False


def _phone_span_is_plausible(raw, text, start, end):
    """Additional precision checks for phone candidates."""
    if _looks_like_date_or_year(raw):
        return False

    digits = re.sub(r"\D", "", raw)

    if not (10 <= len(digits) <= 13):
        # Compact local numbers are allowed only when explicitly labelled.
        context = text[max(0, start - 30):min(len(text), end + 30)]
        if not re.search(
            r"\b(?:telephone|phone|tel\.?|mobile|contact)\b",
            context,
            re.IGNORECASE,
        ):
            return False

    # Never classify a value with an obvious decimal/financial structure.
    if re.search(r"\d+\.\d+\.\d+", raw):
        # Dotted phone numbers are legitimate, so retain them when 10+ digits.
        if len(digits) < 10:
            return False

    return True


def detect_phone(text):
    spans = []

    # Explicitly labelled phone fields.
    for m in PHONE_LABEL_RE.finditer(text):
        raw = m.group(1).strip()
        start, end = m.start(1), m.end(1)

        if _phone_span_is_plausible(raw, text, start, end):
            spans.append(
                Span(start, end, "PHONE", raw)
            )

    # General phone-like formats.
    for m in PHONE_STRICT_RE.finditer(text):
        raw = m.group()
        if _phone_span_is_plausible(raw, text, m.start(), m.end()):
            spans.append(
                Span(m.start(), m.end(), "PHONE", raw)
            )

    # Deduplicate identical spans.
    unique = {}
    for span in spans:
        unique[(span.start, span.end, span.label)] = span

    return list(unique.values())


def detect_ssn(text):
    return [
        Span(m.start(), m.end(), "SSN", m.group())
        for m in SSN_RE.finditer(text)
    ]


def _luhn_valid(digits: str) -> bool:
    total = 0
    alternate = False

    for digit in reversed(digits):
        n = int(digit)

        if alternate:
            n *= 2
            if n > 9:
                n -= 9

        total += n
        alternate = not alternate

    return total % 10 == 0


def detect_credit_card(text):
    spans = []

    for m in CREDIT_CARD_RE.finditer(text):
        digits = re.sub(r"[ -]", "", m.group())

        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            spans.append(
                Span(m.start(), m.end(), "CREDIT_CARD", m.group())
            )

    return spans


def detect_ip(text):
    return [
        Span(m.start(), m.end(), "IP_ADDRESS", m.group())
        for m in IP_RE.finditer(text)
    ]


def detect_dob(text):
    return [
        Span(m.start(1), m.end(1), "DOB", m.group(1))
        for m in DOB_RE.finditer(text)
    ]


# ---------------------------------------------------------------------------
# PERSON
# ---------------------------------------------------------------------------

_NON_PERSON_INDICATOR_WORDS = {
    "facility", "facilities", "park", "industrial", "rate", "reference",
    "shareholder", "shareholders", "personnel", "standard", "selling",
    "taluka", "time", "company", "companies", "board", "report", "reports",
    "statements", "measures", "terms", "government", "committee",
    "department", "office", "court", "tribunal", "exchange", "scheme",
    "fund", "funds", "trust", "bank", "limited", "private", "corporation",
    "act", "regulations", "prospectus", "definitions", "offer", "key",
    "managerial", "capital", "employed", "data", "financial", "non-gaap",
    "amount", "bid", "bids", "complex", "kurla", "bandra", "mumbai", "pune",
    "india", "air", "conditioning", "price", "kilometers", "bidders",
    "volt", "amperes", "photo", "voltaic", "branch", "lane", "agents",
    "defaulter", "website", "showroom", "hospital", "apartment", "chambers",
    "electricals", "transfer", "cagr", "margin", "account", "schedule",
    "iso", "listing", "bhavan", "newspaper", "circulated", "widely",
    "operational", "acknowledgement", "acknowledgment", "slip",
    "form", "forms", "period", "closing", "opening", "registrar",
}

_NON_PERSON_EXACT_PHRASES = {
    "acknowledgement slip",
    "acknowledgment slip",
    "air conditioning",
    "cap price",
    "cherag gyara website",
    "circuit kilometers",
    "deccan gymkhana",
    "floor price",
    "gram jyoti",
    "individual bidders",
    "kisan urja suraksha",
    "kubera chambers opp",
    "kushal electricals",
    "mega volt-amperes",
    "parents branch",
    "photo voltaic",
    "promoter trusts",
    "pushpakamal apartment",
    "raj esh branch",
    "road lane",
    "sancheti hospital shivajinagar",
    "sangeeta branch",
    "secondary transfer of",
    "share transfer agents",
    "sharmila joshi website",
    "shivaji nagar",
    "soni website",
    "tanishq showroom",
    "tara chambers",
    "wilful defaulter",
    "a registered broker",
    "iso 14001:2015",
    "iso 45001:2018",
    "iso 9001:2015",
    "pat cagr",
    "pat margin",
    "nro account",
    "schedule xiii",
    "widely circulated marathi daily newspaper",
    "bidder’s dp id",
    "bidder's dp id",
    "c. operational",
    "gopal bo",
    "listing sebi bhavan",
    "corrigenda thereto",
}


def _clean_person_text(text):
    return re.sub(r"[\s\*\^&/,;:]+$", "", text).strip()


def _is_person_ent(ent):
    if ent.label_ != "PERSON":
        return False

    raw = ent.text.strip()
    normalized = _clean_person_text(raw).casefold()

    if not normalized:
        return False

    if "/" in raw:
        return False

    tokens = normalized.split()

    if len(tokens) < 2 or len(tokens) > 5:
        return False

    if tokens[0] in {"a", "an", "the"}:
        return False

    if normalized in _NON_PERSON_EXACT_PHRASES:
        return False

    for token in tokens:
        cleaned = token.strip(".,;:()[]{}\"'")

        if cleaned in _NON_PERSON_INDICATOR_WORDS:
            return False

        # Tokens containing digits, except a trailing annotation marker that
        # was removed above, are very unlikely to be ordinary human names.
        if any(ch.isdigit() for ch in cleaned):
            return False

    if re.search(
        r"\b(?:website|branch|hospital|showroom|apartment|chambers|"
        r"electricals|bidders|defaulter|agents|cagr|margin|account|"
        r"schedule|newspaper|iso|listing|bhavan)\b",
        normalized,
    ):
        return False

    # Names should have alphabetic content in every token. This still allows
    # genuine ALL-CAPS names such as KUSHAL SUBBAYYA HEGDE.
    if not all(any(ch.isalpha() for ch in token) for token in tokens):
        return False

    return True


def detect_person(text):
    nlp = _get_nlp()
    normalized_text = text.replace("\t", " ")

    return [
        Span(
            ent.start_char,
            ent.end_char,
            "PERSON",
            ent.text,
        )
        for ent in nlp(normalized_text).ents
        if _is_person_ent(ent)
    ]


# ---------------------------------------------------------------------------
# ADDRESS
# ---------------------------------------------------------------------------

_REGION_STOPWORDS = {
    "india",
    "united states",
    "united states of america",
    "united kingdom",
    "republic of india",
    "maharashtra",
    "madhya pradesh",
    "european union",
    "lok sabha",
    "rajya sabha",
}

_ADDRESS_WORDS = re.compile(
    r"\b("
    r"road|rd\.?|street|st\.?|floor|building|bldg\.?|nagar|marg|"
    r"complex|wing|society|lane|village|district|taluka|plot|tower|"
    r"block|phase|sector|park|office|address|reclamation|industrial|"
    r"estate|chowk|colony|apartment|residency|bungalow|near|opposite|"
    r"opp\.?|centre|center|house"
    r")\b",
    re.IGNORECASE,
)

_COMMON_LOCATION_HINTS = re.compile(
    r"\b("
    r"pune|mumbai|bhandarkar|pashan|baner|vikhroli|churchgate|"
    r"prabhadevi|erandawane|shivajinagar|shivaji\s+nagar|"
    r"bhosari|akurdi|bkc|bandra|lower\s+parel|bhopal|maharashtra|"
    r"madhya\s+pradesh|ahilyanagar|ahmednagar|panvel|raigad|khed|"
    r"parner|taloja|chakan|pallod|senapati\s+bapat|deccan\s+gymkhana"
    r")\b",
    re.IGNORECASE,
)

_NON_ADDRESS_EXACT_PHRASES = {
    "the acknowledgement slip",
    "the acknowledgment slip",
    "the bid /offer period",
    "the bid cum application form",
    "the bid/offer closing date",
    "the bid/offer opening date",
    "the bid/offer period",
    "the cap price",
    "the offer documents",
    "the registrar of companies",
    "the supa facility",
}

PIN_RE = re.compile(r"\b\d{3}[ -]?\d{3}\b")


def _is_address_ent(ent):
    if ent.label_ not in ("GPE", "LOC", "FAC"):
        return False

    normalized = ent.text.strip().casefold()
    normalized = re.sub(r"^(the|a|an)\s+[“\"']?", "", normalized).strip(
        " “\"'"
    )

    if normalized in _REGION_STOPWORDS:
        return False

    if len(normalized.split()) < 2:
        return False

    if _ADDRESS_WORDS.search(normalized):
        return True

    if re.search(r"\d", normalized):
        return True

    return False


def _address_candidate_from_pin(text, pin_match):
    pin_start = pin_match.start()
    pin_end = pin_match.end()

    segment_start = max(
        text.rfind("\n", 0, pin_start),
        text.rfind(";", 0, pin_start),
        text.rfind("|", 0, pin_start),
    ) + 1

    if pin_start - segment_start > 180:
        segment_start = pin_start - 180

    candidate_start = segment_start
    candidate_end = pin_end

    after = text[pin_end:]
    contact = re.search(
        r"\s+(?:Telephone|Phone|Tel\.?|Email|E-mail|Website|Contact Person)\s*:",
        after,
        re.IGNORECASE,
    )

    if contact:
        candidate_end = pin_end + contact.start()

    candidate = text[candidate_start:candidate_end].strip()

    label = re.match(
        r"^(?:(?:the\s+)?(?:Registered|Corporate|Correspondence|Residential|"
        r"Principal|Head)\s+(?:Office|Address)(?:\s+of\s+(?:our|the)\s+company)?"
        r"(?:\s+located)?\s+at\s*[:\-]?\s*"
        r"|our\s+manufacturing\s+facility\s+located\s+at\s*)",
        candidate,
        re.IGNORECASE,
    )

    if label:
        candidate_start += label.end()
        candidate = text[candidate_start:candidate_end].strip()

    normalized = re.sub(r"\s+", " ", candidate).strip().casefold()

    if normalized in _NON_ADDRESS_EXACT_PHRASES:
        return None

    if re.match(
        r"^the\s+(?:bid|offer|cap price|registrar|supa facility)",
        normalized,
    ):
        return None

    if not _ADDRESS_WORDS.search(candidate):
        return None

    before_pin = candidate[: max(0, len(candidate) - len(pin_match.group()))]

    numeric_before_pin = re.search(r"\d", before_pin)
    address_word_count = len(_ADDRESS_WORDS.findall(before_pin))
    has_location_hint = bool(_COMMON_LOCATION_HINTS.search(before_pin))

    if not numeric_before_pin and not (
        address_word_count >= 2
        or (address_word_count >= 1 and has_location_hint)
    ):
        return None

    return Span(
        candidate_start,
        candidate_end,
        "ADDRESS",
        text[candidate_start:candidate_end].strip(),
    )


def detect_address(text):
    spans = []

    for match in PIN_RE.finditer(text):
        span = _address_candidate_from_pin(text, match)
        if span is not None:
            spans.append(span)

    labelled = re.compile(
        r"(?:Registered|Corporate|Correspondence|Residential|Principal|Head)"
        r"\s+(?:Office|Address)\s*[:\-]\s*"
        r"([^\n;]{15,220})",
        re.IGNORECASE,
    )

    for match in labelled.finditer(text):
        value = match.group(1).strip()

        if _ADDRESS_WORDS.search(value) or re.search(r"\d", value):
            spans.append(
                Span(
                    match.start(1),
                    match.end(1),
                    "ADDRESS",
                    value,
                )
            )

    try:
        nlp = _get_nlp()
        doc = nlp(text.replace("\t", " "))

        for ent in doc.ents:
            if _is_address_ent(ent):
                spans.append(
                    Span(
                        ent.start_char,
                        ent.end_char,
                        "ADDRESS",
                        ent.text,
                    )
                )
    except OSError:
        pass

    unique = {}
    for span in spans:
        unique[(span.start, span.end, span.label)] = span

    return list(unique.values())


# ---------------------------------------------------------------------------
# COMPANY
# ---------------------------------------------------------------------------

# Match one legal entity at a time. A company name must contain a legal/entity
# suffix; generic phrases such as "Private Limited" alone are rejected later.
COMPANY_RE = re.compile(
    r"\b"
    r"(?:[A-Z][A-Za-z0-9&.'’/-]*|of|and|the)"
    r"(?:\s+(?:[A-Z][A-Za-z0-9&.'’/-]*|of|and|the|&)){0,10}"
    r"\s*,?\s+"
    r"(?:Private\s+Limited|Pvt\.?\s*Ltd\.?|Limited|Ltd\.?|LLP|"
    r"Inc\.?|Corporation|Corp\.?|Industries|Holdings|Associates|Co\.)"
    r"\b",
)

# Contextual words that should not be part of the company entity itself.
_COMPANY_PREFIX_RE = re.compile(
    r"^(?:"
    r"company\s+|"
    r"formerly\s+|"
    r"statutory\s+auditors,\s*|"
    r"offer\s+escrow\s+collection\s+bank\s+|"
    r"the\s+|"
    r"and\s+"
    r")+",
    re.IGNORECASE,
)

_COMPANY_SUFFIX_ONLY = {
    "private limited",
    "limited",
    "ltd",
    "llp",
    "corporation",
    "corp",
    "industries",
    "holdings",
    "associates",
    "co.",
}


def detect_company(text):
    spans = []

    for match in COMPANY_RE.finditer(text):
        raw_value = match.group()
        prefix_match = _COMPANY_PREFIX_RE.match(raw_value)

        if prefix_match:
            start = match.start() + prefix_match.end()
            value = raw_value[prefix_match.end():].strip()
        else:
            start = match.start()
            value = raw_value.strip()

        normalized = value.casefold().strip(" ,;:")

        if normalized in _COMPANY_SUFFIX_ONLY:
            continue

        if normalized in {"company", "formerly"}:
            continue

        if len(value.split()) > 12:
            continue

        # A useful company entity should contain at least one lexical token
        # before the legal suffix.
        body = re.sub(
            r"\b(?:private\s+limited|pvt\.?\s*Ltd\.?|limited|ltd\.?|llp|"
            r"inc\.?|corporation|corp\.?|industries|holdings|associates|co\.)\b",
            "",
            value,
            flags=re.IGNORECASE,
        ).strip(" ,;:")

        if len(body.split()) < 1:
            continue

        spans.append(
            Span(
                start,
                start + len(value),
                "COMPANY",
                value,
            )
        )

    # Deduplicate.
    unique = {}
    for span in spans:
        unique[(span.start, span.end, span.label)] = span

    return list(unique.values())


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

DETECTOR_REGISTRY = {
    "EMAIL": detect_email,
    "PHONE": detect_phone,
    "SSN": detect_ssn,
    "CREDIT_CARD": detect_credit_card,
    "IP_ADDRESS": detect_ip,
    "DOB": detect_dob,
    "PERSON": detect_person,
    "COMPANY": detect_company,
    "ADDRESS": detect_address,
}