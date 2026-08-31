from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field, replace
from typing import Mapping, Protocol, Sequence


class RedactionMatchLike(Protocol):
    tag: str
    text: str


DETECTION_SPAN_SCHEMA_VERSION = 2


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
    "INSTITUTION_VALUE": "institution_value",
    "DEPARTMENT_VALUE": "department_value",
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
    "INSTITUTION_VALUE": "fixed_region_institution",
    "DEPARTMENT_VALUE": "fixed_region_department",
}

REVIEW_TAGS = {
    "ADDRESS",
    "NAME",
    "LEGAL_PARTY",
    "PLACE",
    "WEAK_PLACE",
    "CASE_NUMBER",
    "DOC_META",
    "INSTITUTION_VALUE",
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
    confidence: float | None
    action: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    rects: tuple[tuple[float, float, float, float], ...] = ()
    evidence: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    raw_text_stored: bool = False
    occurrence_id: str = ""
    analysis_revision: int | None = None
    coordinate_space: str = "text_offsets"
    metadata: dict[str, object] = field(default_factory=dict)

    def with_source(self, source: str) -> DetectionSpan:
        return replace(self, source=source)

    def to_report_dict(self) -> dict[str, object]:
        return {
            "schema_version": DETECTION_SPAN_SCHEMA_VERSION,
            "id": self.id,
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "length": self.length,
            "source": self.source,
            "confidence": self.confidence,
            "action": self.action,
            "page": self.page,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "rects": [list(rect) for rect in self.rects],
            "evidence": list(dict.fromkeys(self.evidence)),
            "provenance": list(self.provenance),
            "sources": list(dict.fromkeys((self.source, *self.sources))),
            "raw_text_stored": self.raw_text_stored,
            "occurrence_id": self.occurrence_id,
            "analysis_revision": self.analysis_revision,
            "coordinate_space": self.coordinate_space,
            "metadata": self.metadata,
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
    for match in matches:
        value = clean_match_text(match.text)
        start, end, authoritative = match_offsets(text, match, cursors.get(value, 0))
        if start < 0:
            spans.append(
                DetectionSpan(
                    id=match_candidate_id(match, start, end),
                    occurrence_id=match_occurrence_id(match, start, end),
                    label=label_for_tag(match.tag),
                    start=-1,
                    end=-1,
                    length=0,
                    source=match_source(match),
                    confidence=_optional_confidence(match, confidence_for_tag(match.tag)),
                    action=_string_attribute(match, "action", action_for_tag(match.tag)),
                    page=_optional_page(match),
                    bbox=_optional_rect(getattr(match, "bbox", None)),
                    rects=_optional_rects(getattr(match, "rects", ())),
                    evidence=_safe_codes(getattr(match, "evidence", ())),
                    provenance=_safe_codes(getattr(match, "provenance", ())),
                    sources=_string_sequence(getattr(match, "sources", ())),
                    analysis_revision=_optional_revision(match),
                    coordinate_space=_string_attribute(match, "coordinate_space", "text_offsets"),
                    metadata=match_metadata(match),
                )
            )
            continue
        if not authoritative:
            cursors[value] = end
        length = end - start
        spans.append(
            DetectionSpan(
                id=match_candidate_id(match, start, end),
                occurrence_id=match_occurrence_id(match, start, end),
                label=label_for_tag(match.tag),
                start=start,
                end=end,
                length=length,
                source=match_source(match),
                confidence=_optional_confidence(match, confidence_for_tag(match.tag)),
                action=_string_attribute(match, "action", action_for_tag(match.tag)),
                page=_optional_page(match),
                bbox=_optional_rect(getattr(match, "bbox", None)),
                rects=_optional_rects(getattr(match, "rects", ())),
                evidence=_safe_codes(getattr(match, "evidence", ())),
                provenance=_safe_codes(getattr(match, "provenance", ())),
                sources=_string_sequence(getattr(match, "sources", ())),
                analysis_revision=_optional_revision(match),
                coordinate_space=_string_attribute(match, "coordinate_space", "text_offsets"),
                metadata=match_metadata(match),
            )
        )
    return spans


def merge_detection_spans(spans: Sequence[DetectionSpan]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for span in sorted(
        spans,
        key=lambda item: (
            item.analysis_revision if item.analysis_revision is not None else -1,
            item.page if item.page is not None else -1,
            item.coordinate_space,
            item.start,
            item.end,
            _source_rank(item.source),
        ),
    ):
        if merged and _overlaps(merged[-1], span):
            _merge_into(merged[-1], span)
            continue
        item = span.to_report_dict()
        item["sources"] = list(dict.fromkeys((span.source, *span.sources)))
        merged.append(item)
    for item in merged:
        sources = item["sources"]
        if isinstance(sources, list) and sources:
            item["source"] = sources[0]
        item["id"] = _canonical_span_id(item)
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
    if isinstance(start, int) and isinstance(end, int):
        if start == end == -1:
            legacy_start_offset, legacy_end_offset = locate_value(text, value, legacy_start)
            return legacy_start_offset, legacy_end_offset, False
        if (
            0 <= start < end <= len(text)
            and clean_match_text(text[start:end]) == value
        ):
            return start, end, True
        return -1, -1, True
    legacy_start_offset, legacy_end_offset = locate_value(text, value, legacy_start)
    return legacy_start_offset, legacy_end_offset, False


def canonical_json_sha256(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_metadata(value: object, *, _depth: int = 0) -> object:
    """Return the bounded, recursively safe metadata representation used for identity and reports."""
    if _depth > 8:
        return None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        items = ((key, item) for key, item in value.items() if isinstance(key, str))
        return {
            key: canonical_metadata(item, _depth=_depth + 1)
            for key, item in sorted(items)
        }
    if isinstance(value, (list, tuple)):
        return [canonical_metadata(item, _depth=_depth + 1) for item in value]
    return None


def match_metadata(match: RedactionMatchLike) -> dict[str, object]:
    value = canonical_metadata(getattr(match, "metadata", {}))
    return value if isinstance(value, dict) else {}


_LOCAL_OCCURRENCE_RE = re.compile(r"occ_[0-9a-f]{24}")
def _local_occurrence_id_for(
    *,
    tag: str,
    source: str,
    text: str,
    start: int,
    end: int,
    action: str,
    metadata: Mapping[str, object] | None = None,
) -> str:
    normalized_metadata = canonical_metadata(metadata if metadata is not None else {})
    normalized_text = clean_match_text(text) if isinstance(text, str) else ""
    if (
        not isinstance(tag, str)
        or not tag
        or not isinstance(source, str)
        or not source
        or not isinstance(text, str)
        or not normalized_text
        or not (
            (start == -1 and end == -1)
            or (type(start) is int and type(end) is int and 0 <= start < end)
        )
        or action not in {"mask", "review", "exclude"}
        or not isinstance(normalized_metadata, dict)
    ):
        raise ValueError("OCCURRENCE_IDENTITY_INVALID")
    return "occ_" + canonical_json_sha256({
        "namespace": "untrusted-local-occurrence-v1",
        "tag": tag,
        "source": source,
        "text_sha256": hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
        "start": start,
        "end": end,
        "action": action,
        "metadata": normalized_metadata,
    })[:24]


def occurrence_id_for(
    *,
    document_sha256: str,
    run_id: str,
    tag: str,
    source: str,
    start: int,
    end: int,
    page: int,
    analysis_revision: int,
    action: str = "mask",
    metadata: Mapping[str, object] | None = None,
) -> str:
    if (
        not isinstance(document_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", document_sha256.lower()) is None
        or not isinstance(run_id, str)
        or not run_id
        or not isinstance(tag, str)
        or not tag
        or not isinstance(source, str)
        or not source
        or type(start) is not int
        or type(end) is not int
        or start < 0
        or end <= start
        or type(page) is not int
        or page < 0
        or type(analysis_revision) is not int
        or analysis_revision < 1
        or action not in {"mask", "review", "exclude"}
    ):
        raise ValueError("OCCURRENCE_IDENTITY_INVALID")
    normalized_metadata = canonical_metadata(metadata if metadata is not None else {})
    if not isinstance(normalized_metadata, dict):
        raise ValueError("OCCURRENCE_IDENTITY_INVALID")
    payload: dict[str, object] = {
        "document_sha256": document_sha256.lower(),
        "run_id": run_id,
        "tag": tag,
        "source": source,
        "start": start,
        "end": end,
        "page": page,
        "analysis_revision": analysis_revision,
        "action": action,
        "metadata": normalized_metadata,
    }
    return f"occ_{canonical_json_sha256(payload)[:24]}"


def match_occurrence_id(match: RedactionMatchLike, start: int, end: int) -> str:
    supplied = getattr(match, "occurrence_id", "")
    raw_document_sha256 = getattr(match, "document_sha256", None)
    raw_run_id = getattr(match, "run_id", None)
    document_present = raw_document_sha256 is not None and raw_document_sha256 != ""
    run_present = raw_run_id is not None and raw_run_id != ""

    if not document_present and not run_present:
        if isinstance(supplied, str) and _LOCAL_OCCURRENCE_RE.fullmatch(supplied):
            return supplied
        return _local_occurrence_id_for(
            tag=match.tag,
            source=match_source(match),
            text=match.text,
            start=start,
            end=end,
            action=_string_attribute(match, "action", action_for_tag(match.tag)),
            metadata=match_metadata(match),
        )

    if document_present != run_present:
        raise ValueError("OCCURRENCE_IDENTITY_INVALID")
    if (
        not isinstance(raw_document_sha256, str)
        or not isinstance(raw_run_id, str)
        or not isinstance(supplied, str)
        or _LOCAL_OCCURRENCE_RE.fullmatch(supplied) is None
        or type(start) is not int
        or type(end) is not int
        or start < 0
        or end <= start
    ):
        raise ValueError("OCCURRENCE_IDENTITY_INVALID")
    page = _optional_page(match)
    revision = _optional_revision(match)
    if page is None or revision is None:
        raise ValueError("OCCURRENCE_IDENTITY_INVALID")
    trusted_id = occurrence_id_for(
        document_sha256=raw_document_sha256,
        run_id=raw_run_id,
        tag=match.tag,
        source=match_source(match),
        start=start,
        end=end,
        page=page,
        analysis_revision=revision,
        action=_string_attribute(match, "action", action_for_tag(match.tag)),
        metadata=match_metadata(match),
    )
    if supplied != trusted_id:
        raise ValueError("OCCURRENCE_IDENTITY_INVALID")
    return trusted_id


def _string_attribute(match: RedactionMatchLike, name: str, default: str) -> str:
    value = getattr(match, name, default)
    return value if isinstance(value, str) and value else default


def _optional_confidence(match: RedactionMatchLike, default: float) -> float | None:
    value = getattr(match, "confidence", default)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return float(value)
    return None


def _optional_page(match: RedactionMatchLike) -> int | None:
    value = getattr(match, "page", getattr(match, "page_index", None))
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
def _optional_revision(match: RedactionMatchLike) -> int | None:
    value = getattr(match, "analysis_revision", None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None



def _optional_rect(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    try:
        rect = tuple(float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(part) for part in rect) or rect[2] <= rect[0] or rect[3] <= rect[1]:
        return None
    return rect  # type: ignore[return-value]


def _optional_rects(value: object) -> tuple[tuple[float, float, float, float], ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    return tuple(rect for candidate in value if (rect := _optional_rect(candidate)) is not None)


def _safe_codes(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    allowed_singletons = {"pattern", "obfuscated", "bank_context", "date_valid", "checksum_invalid", "paddleocr", "origin", "position", "brand_hint"}
    return tuple(
        item for item in value
        if isinstance(item, str)
        and (
            item in allowed_singletons
            or re.fullmatch(r"(?:ocr|pdf|trusted|regex|optional|fixed|layout|pattern|keyword|origin|pos|checksum|date)[:_][a-z_:-]{1,88}", item)
        )
    )


def match_source(match: RedactionMatchLike) -> str:
    source = getattr(match, "source", "")
    return source if isinstance(source, str) and source else source_for_tag(match.tag)


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


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
    if (
        item.get("analysis_revision") != span.analysis_revision
        or item.get("page") != span.page
        or item.get("coordinate_space") != span.coordinate_space
        or item.get("action") != span.action
    ):
        return False
    existing_occurrence = item.get("occurrence_id")
    if (
        isinstance(existing_occurrence, str)
        and re.fullmatch(r"occ_[0-9a-f]{24}", existing_occurrence)
        and existing_occurrence != span.occurrence_id
    ):
        return False
    if canonical_metadata(item.get("metadata", {})) != canonical_metadata(span.metadata):
        return False
    existing_rects = item.get("rects")
    span_rects = [list(rect) for rect in span.rects]
    if isinstance(existing_rects, list) and existing_rects and span_rects and existing_rects != span_rects:
        return False
    start = int(item["start"])
    end = int(item["end"])
    if start < 0 or end < 0 or span.start < 0 or span.end < 0:
        return start == span.start and end == span.end and item["label"] == span.label
    return item["label"] == span.label and start < span.end and span.start < end


def _merge_into(item: dict[str, object], span: DetectionSpan) -> None:
    item["start"] = min(int(item["start"]), span.start)
    item["end"] = max(int(item["end"]), span.end)
    item["length"] = int(item["end"]) - int(item["start"])
    confidence = [
        value for value in (item["confidence"], span.confidence)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
    ]
    item["confidence"] = max(confidence) if confidence else None
    if item["action"] == "mask" or span.action == "mask":
        item["action"] = "mask"
    sources = item["sources"]
    if isinstance(sources, list):
        for source in (span.source, *span.sources):
            if source not in sources:
                sources.append(source)
    for field, values in (
        ("rects", [list(rect) for rect in span.rects]),
        ("evidence", list(span.evidence)),
        ("provenance", list(span.provenance)),
    ):
        existing = item.get(field)
        if isinstance(existing, list):
            for value in values:
                if value not in existing:
                    existing.append(value)
    geometry = [
        rect for rect in (
            *(item["rects"] if isinstance(item.get("rects"), list) else ()),
            item.get("bbox"),
            list(span.bbox) if span.bbox is not None else None,
        )
        if isinstance(rect, list) and _optional_rect(rect) is not None
    ]
    if geometry:
        item["bbox"] = [
            min(rect[0] for rect in geometry),
            min(rect[1] for rect in geometry),
            max(rect[2] for rect in geometry),
            max(rect[3] for rect in geometry),
        ]
    else:
        item["bbox"] = None
    if not (
        isinstance(item.get("occurrence_id"), str)
        and re.fullmatch(r"occ_[0-9a-f]{24}", item["occurrence_id"])
    ):
        item["occurrence_id"] = span.occurrence_id
    if item["page"] is None:
        item["page"] = span.page

def candidate_id_for(
    *,
    label: str,
    start: int,
    end: int,
    page: int | None,
    action: str,
    sources: Sequence[str],
    analysis_revision: int | None,
    coordinate_space: str,
    metadata: Mapping[str, object] | None = None,
) -> str:
    normalized_metadata = canonical_metadata(metadata if metadata is not None else {})
    return "candidate_" + canonical_json_sha256({
        "label": label,
        "start": start,
        "end": end,
        "page": page,
        "action": action,
        "sources": sorted(dict.fromkeys(sources)),
        "analysis_revision": analysis_revision,
        "coordinate_space": coordinate_space,
        "metadata": normalized_metadata,
    })[:24]


def match_candidate_id(match: RedactionMatchLike, start: int, end: int) -> str:
    source = match_source(match)
    return candidate_id_for(
        label=label_for_tag(match.tag),
        start=start,
        end=end,
        page=_optional_page(match),
        action=_string_attribute(match, "action", action_for_tag(match.tag)),
        sources=(source, *_string_sequence(getattr(match, "sources", ()))),
        analysis_revision=_optional_revision(match),
        coordinate_space=_string_attribute(match, "coordinate_space", "text_offsets"),
        metadata=match_metadata(match),
    )


def _canonical_span_id(item: dict[str, object]) -> str:
    sources = item.get("sources", ())
    return candidate_id_for(
        label=str(item["label"]),
        start=int(item["start"]),
        end=int(item["end"]),
        page=item["page"] if isinstance(item["page"], int) else None,
        action=str(item["action"]),
        sources=tuple(value for value in sources if isinstance(value, str)),
        analysis_revision=(
            item["analysis_revision"]
            if isinstance(item["analysis_revision"], int)
            and not isinstance(item["analysis_revision"], bool)
            else None
        ),
        coordinate_space=str(item["coordinate_space"]),
        metadata=item.get("metadata") if isinstance(item.get("metadata"), Mapping) else None,
    )
