from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Protocol, Sequence


class RedactionMatchLike(Protocol):
    tag: str
    text: str


TAG_LABELS: dict[str, str] = {
    "RRN": "rrn",
    "FOREIGN_REG": "foreign_id",
    "PHONE": "phone",
    "EMAIL": "email",
    "BUSINESS_REG_NO": "business_number",
    "CARD": "card_number",
    "PASSPORT": "passport_number",
    "ACCOUNT": "bank_account",
    "NAME": "person_name",
    "LEGAL_PARTY": "person_name",
    "ADDRESS": "address",
    "PLACE": "region_name",
    "WEAK_PLACE": "region_name",
    "REGION": "region_name",
    "ADDR_DETAIL": "apartment_unit",
    "LOT_NO": "lot_address",
    "CASE_NUMBER": "case_number",
    "DOC_META": "document_number",
    "KEYWORD": "custom_keyword",
}

TAG_SOURCES: dict[str, str] = {
    "RRN": "regex_rrn",
    "FOREIGN_REG": "regex_foreign_id",
    "PHONE": "regex_phone",
    "EMAIL": "regex_email",
    "BUSINESS_REG_NO": "regex_business_number",
    "CARD": "regex_card_number",
    "PASSPORT": "regex_passport_number",
    "ACCOUNT": "regex_account_candidate",
    "NAME": "regex_name_context",
    "LEGAL_PARTY": "regex_legal_party",
    "ADDRESS": "regex_address_context",
    "PLACE": "regex_region",
    "WEAK_PLACE": "dictionary_weak_place",
    "REGION": "custom_region",
    "ADDR_DETAIL": "regex_address_detail",
    "LOT_NO": "regex_lot_address",
    "CASE_NUMBER": "regex_case_number",
    "DOC_META": "regex_document_meta",
    "KEYWORD": "manual_keyword",
}

REVIEW_TAGS = {
    "ACCOUNT",
    "ADDRESS",
    "NAME",
    "LEGAL_PARTY",
    "WEAK_PLACE",
    "CASE_NUMBER",
    "DOC_META",
}

SOURCE_PRIORITY = {
    "manual_keyword": 0,
    "regex_rrn": 1,
    "regex_foreign_id": 1,
    "regex_business_number": 1,
    "regex_phone": 2,
    "regex_email": 2,
    "regex_card_number": 2,
    "regex_passport_number": 2,
    "regex_region": 3,
    "dictionary_weak_place": 3,
    "optional_ai_detector": 4,
}


@dataclass(frozen=True, slots=True)
class DetectionSpan:
    id: str
    label: str
    start: int
    end: int
    length: int
    source: str
    confidence: float
    action: str
    page: int | None = None
    raw_text_stored: bool = False
    evidence: tuple[str, ...] = ()

    def with_source(self, source: str) -> DetectionSpan:
        return replace(self, source=source)

    def to_report_dict(self) -> dict[str, str | int | float | bool | None]:
        return {
            "id": self.id,
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "length": self.length,
            "source": self.source,
            "confidence": self.confidence,
            "action": self.action,
            "page": self.page,
            "bbox": None,
            "raw_text_stored": self.raw_text_stored,
        }


class PrivacyDetector(Protocol):
    name: str

    def detect(self, text: str, context: dict[str, str] | None = None) -> list[DetectionSpan]: ...


class OptionalAIPrivacyDetector:
    name = "optional_ai_detector"

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def detect(self, text: str, context: dict[str, str] | None = None) -> list[DetectionSpan]:
        if not self.enabled:
            return []
        raise RuntimeError("Optional AI detector is disabled unless explicitly implemented and configured.")


def action_for_tag(tag: str) -> str:
    return "review" if tag in REVIEW_TAGS else "mask"


def label_for_tag(tag: str) -> str:
    return TAG_LABELS.get(tag, tag.lower())


def source_for_tag(tag: str) -> str:
    return TAG_SOURCES.get(tag, "regex")


def tag_for_label(label: str) -> str:
    for tag, candidate in TAG_LABELS.items():
        if candidate == label:
            return tag
    return "KEYWORD"


def confidence_for_tag(tag: str) -> float:
    return 0.72 if action_for_tag(tag) == "review" else 0.99


def detection_spans_from_matches(text: str, matches: Sequence[RedactionMatchLike]) -> list[DetectionSpan]:
    cursors: dict[str, int] = {}
    spans: list[DetectionSpan] = []
    for index, match in enumerate(matches, 1):
        value = clean_match_text(match.text)
        start, end, authoritative = match_offsets(text, match, cursors.get(value, 0))
        if start >= 0:
            if not authoritative:
                cursors[value] = end
            length = end - start
        else:
            end = -1
            length = 0
        spans.append(
            DetectionSpan(
                id=match_occurrence_id(match, f"span_{index:06d}"),
                label=label_for_tag(match.tag),
                start=start,
                end=end,
                length=length,
                source=match_source(match),
                confidence=confidence_for_tag(match.tag),
                action=action_for_tag(match.tag),
            )
        )
    return spans


def merge_detection_spans(spans: Sequence[DetectionSpan]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for span in sorted(spans, key=lambda item: (item.start, item.end, _source_rank(item.source))):
        if merged and _overlaps(merged[-1], span):
            _merge_into(merged[-1], span)
            continue
        item = span.to_report_dict()
        item["sources"] = [span.source]
        merged.append(item)
    for item in merged:
        sources = item["sources"]
        if isinstance(sources, list) and sources:
            item["source"] = sources[0]
    return merged


def clean_match_text(value: str) -> str:
    return " ".join(value.split()).strip(" ,;:/")


def match_offsets(
    text: str,
    match: RedactionMatchLike,
    legacy_start: int = 0,
) -> tuple[int, int, bool]:
    start = getattr(match, "start", -1)
    end = getattr(match, "end", -1)
    value = clean_match_text(match.text)
    if (
        isinstance(start, int)
        and isinstance(end, int)
        and 0 <= start < end <= len(text)
        and clean_match_text(text[start:end]) == value
    ):
        return start, end, True
    legacy_start_offset, legacy_end_offset = locate_value(text, value, legacy_start)
    return legacy_start_offset, legacy_end_offset, False


def match_occurrence_id(match: RedactionMatchLike, fallback: str) -> str:
    occurrence_id = getattr(match, "occurrence_id", "")
    return occurrence_id if isinstance(occurrence_id, str) and occurrence_id else fallback


def match_source(match: RedactionMatchLike) -> str:
    source = getattr(match, "source", "")
    return source if isinstance(source, str) and source else source_for_tag(match.tag)


def locate_value(text: str, value: str, start: int = 0) -> tuple[int, int]:
    """Locate ``value`` inside ``text`` returning ``(start, end)`` offsets.

    Falls back to a whitespace-flexible regex search so that values whose
    interior whitespace was collapsed (e.g. a newline turned into a single
    space, or 자간 공백 removed) can still be located. When the value cannot be
    found at all both offsets are ``-1`` so callers keep length ``0``.
    """
    if not value:
        return (-1, -1)
    offset = text.find(value, start) if start > 0 else text.find(value)
    if offset < 0 and start > 0:
        offset = text.find(value)
    if offset >= 0:
        return (offset, offset + len(value))
    match = _flexible_search(text, value, start)
    if match is not None:
        return (match.start(), match.end())
    return (-1, -1)


def _flexible_search(text: str, value: str, start: int) -> re.Match[str] | None:
    chars = [re.escape(ch) for ch in value if not ch.isspace()]
    if not chars:
        return None
    pattern = re.compile(r"\s*".join(chars))
    if start > 0:
        match = pattern.search(text, start)
        if match is not None:
            return match
    return pattern.search(text)


def _find_offset(text: str, value: str, start: int) -> int:
    return locate_value(text, value, start)[0]


def _source_rank(source: str) -> int:
    return SOURCE_PRIORITY.get(source, 99)


def _overlaps(item: dict[str, object], span: DetectionSpan) -> bool:
    start = int(item["start"])
    end = int(item["end"])
    if start < 0 or end < 0 or span.start < 0 or span.end < 0:
        return start == span.start and end == span.end and item["label"] == span.label
    return item["label"] == span.label and start < span.end and span.start < end


def _merge_into(item: dict[str, object], span: DetectionSpan) -> None:
    item["start"] = min(int(item["start"]), span.start)
    item["end"] = max(int(item["end"]), span.end)
    item["length"] = int(item["end"]) - int(item["start"])
    item["confidence"] = max(float(item["confidence"]), span.confidence)
    # Strong action wins: keep/promote to "mask" whenever either side masks;
    # never demote an existing mask down to review.
    if item["action"] == "mask" or span.action == "mask":
        item["action"] = "mask"
    sources = item["sources"]
    if isinstance(sources, list) and span.source not in sources:
        sources.append(span.source)
