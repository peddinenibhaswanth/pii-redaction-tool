"""
redactor.py
-----------
Resolves detector overlaps, creates synthetic replacements, and applies them.

Important safety property:
    A generated fake value is never allowed to be equal to, or occur inside,
    the source document. This prevents a synthetic replacement from exposing
    another real value that already exists in the prospectus.
"""

from faker import Faker

from detectors import Span


PRIORITY = [
    "EMAIL",
    "SSN",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "DOB",
    "PHONE",
    "PERSON",
    "COMPANY",
    "ADDRESS",
]


class Redactor:
    def __init__(self, seed=42, source_text=""):
        self.faker = Faker()
        self.faker.seed_instance(seed)

        self._mapping = {}
        self._used_fakes = set()

        # Keep source values in a normalized form for collision checking.
        self._source_lower = source_text.casefold()

    def _fake_is_safe(self, fake):
        """
        Return True only when the generated replacement is not already present
        in the source document and has not already been used as another fake.
        """
        value = str(fake).strip()

        if not value:
            return False

        value_lower = value.casefold()

        # Do not allow the fake to reproduce any source text.
        if value_lower in self._source_lower:
            return False

        # Do not reuse one fake for two different source values unless the
        # source mapping itself is identical.
        if value_lower in {
            x.casefold()
            for x in self._used_fakes
        }:
            return False

        return True

    def resolve_overlaps(self, spans):
        """
        Return non-overlapping spans.

        At the same starting position, higher-priority types win.
        For different starting positions, the earliest span wins. This keeps
        the behavior deterministic and prevents nested replacements.
        """
        def rank(span):
            try:
                return PRIORITY.index(span.label)
            except ValueError:
                return len(PRIORITY)

        ordered = sorted(
            spans,
            key=lambda s: (
                s.start,
                rank(s),
                -(s.end - s.start),
            ),
        )

        kept = []
        i = 0

        while i < len(ordered):
            current = ordered[i]

            same_start = [current]
            j = i + 1

            while (
                j < len(ordered)
                and ordered[j].start == current.start
            ):
                same_start.append(ordered[j])
                j += 1

            best = min(
                same_start,
                key=lambda s: (
                    rank(s),
                    -(s.end - s.start),
                ),
            )

            if best.end > best.start and not any(
                best.start < k.end and k.start < best.end
                for k in kept
            ):
                kept.append(best)

            i = j

        return kept

    def _fake_for(self, label, original_text):
        key = (label, original_text)

        if key in self._mapping:
            return self._mapping[key]

        for _ in range(500):
            if label == "PERSON":
                fake = self.faker.name()

            elif label == "EMAIL":
                fake = self.faker.email()

            elif label == "PHONE":
                fake = self.faker.phone_number()

            elif label == "COMPANY":
                fake = self.faker.company()

            elif label == "ADDRESS":
                fake = self.faker.address().replace("\n", ", ")

            elif label == "SSN":
                fake = self.faker.ssn()

            elif label == "CREDIT_CARD":
                fake = self.faker.credit_card_number()

            elif label == "DOB":
                fake = self.faker.date_of_birth().strftime("%d-%m-%Y")

            elif label == "IP_ADDRESS":
                fake = self.faker.ipv4()

            else:
                fake = "[REDACTED]"

            if self._fake_is_safe(fake):
                break

        else:
            # Deterministic fallback. It is still checked against the source.
            fake = f"[REDACTED_{label}]"

            while not self._fake_is_safe(fake):
                fake += "X"

        self._used_fakes.add(fake)
        self._mapping[key] = fake

        return fake

    def apply(self, text, spans):
        """Replace each resolved span and return the new text plus audit data."""
        spans = self.resolve_overlaps(spans)

        out = []
        applied = []
        cursor = 0

        for span in spans:
            out.append(text[cursor:span.start])

            fake = self._fake_for(
                span.label,
                span.text,
            )

            out.append(fake)

            applied.append(
                (
                    span.label,
                    span.text,
                    fake,
                )
            )

            cursor = span.end

        out.append(text[cursor:])

        return "".join(out), applied

    @property
    def mapping(self):
        """Full original -> fake mapping for audit/debug purposes."""
        return {
            f"{label}:{original}": fake
            for (label, original), fake in self._mapping.items()
        }