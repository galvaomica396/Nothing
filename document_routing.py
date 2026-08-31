"""Deterministic, revision-scoped logical-document routing.

This module deliberately retains page evidence only in memory.  Its public projections
contain reason codes and hashes, never extracted document text.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from typing import Final, Iterable, Mapping, Sequence

SCHEMA_VERSION = "page-evidence-v1"
COORDINATE_SPACE = "pdf_points_top_left"
CANONICAL_PROFILES = frozenset({"internal_review", "official_dispatch", "mixed", "legal"})
SUPPORTED_SEGMENT_KINDS = frozenset({"internal_review", "official_dispatch", "attachment", "unknown", "legal"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_SIGNAL_CODES = frozenset({
    "internal_review", "internal", "approval", "review",
    "official_dispatch", "dispatch", "recipient", "reference", "sender",
    "attachment", "appendix", "page_number", "footer",
    "common_document_header",
    "page_number_sequence", "repeated_header_footer", "no_start_signal",
    "prereview",
})
_SAFE_BOUNDARY_REASON_CODES = frozenset({
    "ambiguous_boundary",
    "common_document_header_ambiguous",
    "unrecognized_start_signals",
    "conflicting_start_signals",
    "continuation_evidence_missing",
    "different_running_title",
    "profile_authority_missing",
})
_MAX_SAFE_CODES = 16
CONTINUATION_PAGE_NUMBER_SEQUENCE: Final = "page_number_sequence"
CONTINUATION_REPEATED_HEADER_FOOTER: Final = "repeated_header_footer"
CONTINUATION_NO_START_SIGNAL: Final = "no_start_signal"
COMMON_DOCUMENT_HEADER: Final = "common_document_header"
_PROFILE_SEGMENT_KINDS: Final = frozenset({"internal_review", "official_dispatch"})
_RUNNING_TITLE_MIN_MATCH_CHARACTERS: Final = 4
_RUNNING_TITLE_MIN_MATCH_RATIO: Final = 0.8
_GENERIC_RUNNING_TITLES: Final = frozenset({
    "검토보고", "검토보고서", "보고", "보고서", "붙임", "붙임자료", "첨부", "첨부자료", "목차", "본문",
})


def _safe_codes(codes: Iterable[object], allowed: frozenset[str]) -> list[str]:
    safe: list[str] = []
    for code in codes:
        if isinstance(code, str) and code in allowed and code not in safe:
            safe.append(code)
            if len(safe) == _MAX_SAFE_CODES:
                break
    return safe


def _bounded_count(values: Iterable[object]) -> int:
    return min(sum(1 for _ in values), _MAX_SAFE_CODES)


def normalize_profile(profile: str | None) -> str:
    """Normalize the one supported legacy profile without changing legal routing."""
    if profile is None:
        return "mixed"
    value = profile.strip().lower()
    if value == "official":
        return "mixed"
    if value not in CANONICAL_PROFILES:
        raise ValueError("unsupported routing profile")
    return value


def _digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PdfRect:
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        coordinates = (self.x0, self.y0, self.x1, self.y1)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in coordinates):
            raise ValueError("rectangle coordinates must be finite numbers")
        if any(value < 0 for value in coordinates):
            raise ValueError("rectangle coordinates are out-of-page")
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("rectangles must be non-empty")

    def normalized(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True, slots=True)
class PageEvidence:
    """Versioned page-local evidence used by routing and layout analysis.

    ``start_signals`` and ``continuity_signals`` are reason codes, not source text.
    The confidence remains ``None`` when OCR did not provide it.
    """
    page_index: int
    start_signals: frozenset[str] = frozenset()
    continuity_signals: frozenset[str] = frozenset()
    ocr_confidence: float | None = None
    page_rects: tuple[PdfRect, ...] = ()
    boundary_confidence: float | None = None
    confidence_source: str = "ocr"
    schema_version: str = SCHEMA_VERSION
    coordinate_space: str = COORDINATE_SPACE
    routing_titles: tuple[str, ...] = ()
    routing_title_kind: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.page_index, bool) or not isinstance(self.page_index, int) or self.page_index < 0:
            raise ValueError("page_index is 0-based")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported page evidence schema")
        if self.coordinate_space != COORDINATE_SPACE:
            raise ValueError("unsupported coordinate space")
        if not isinstance(self.page_rects, tuple) or any(not isinstance(rect, PdfRect) for rect in self.page_rects):
            raise ValueError("page_rects must contain PdfRect values")
        for confidence, name in ((self.ocr_confidence, "ocr_confidence"), (self.boundary_confidence, "boundary_confidence")):
            if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise ValueError(f"{name} must be in [0, 1]")
        if self.confidence_source not in {"ocr", "text_layer"}:
            raise ValueError("unsupported confidence source")
        if self.confidence_source == "text_layer" and (
            self.ocr_confidence != 1.0 or self.boundary_confidence not in {None, 1.0}
        ):
            raise ValueError("text-layer confidence must be satisfied")
        if not isinstance(self.routing_titles, tuple) or any(
            not isinstance(title, str) or not title for title in self.routing_titles
        ):
            raise ValueError("routing_titles must contain normalized title strings")
        if self.routing_title_kind is not None and self.routing_title_kind not in _PROFILE_SEGMENT_KINDS:
            raise ValueError("routing_title_kind must be a public document kind")

    def safe_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "page_index": self.page_index,
            "coordinate_space": self.coordinate_space,
            "start_signal_codes": sorted(_safe_codes(self.start_signals, _SAFE_SIGNAL_CODES)),
            "continuity_signal_codes": sorted(_safe_codes(self.continuity_signals, _SAFE_SIGNAL_CODES)),
            "start_signal_count": _bounded_count(self.start_signals),
            "continuity_signal_count": _bounded_count(self.continuity_signals),
            "has_page_local_geometry": bool(self.page_rects),
            "ocr_confidence_present": self.ocr_confidence is not None,
            "confidence_source": self.confidence_source,
        }


@dataclass(frozen=True, slots=True)
class BoundaryEvidence:
    page_index: int
    reason_codes: tuple[str, ...]
    confidence: float | None
    ambiguous: bool
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.page_index, bool) or not isinstance(self.page_index, int) or self.page_index < 0:
            raise ValueError("page_index is 0-based")

    def safe_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "page_index": self.page_index,
            "reason_codes": _safe_codes(self.reason_codes, _SAFE_BOUNDARY_REASON_CODES),
            "reason_code_count": _bounded_count(self.reason_codes),
            "ambiguous": self.ambiguous,
            "confidence_present": self.confidence is not None,
        }


@dataclass(frozen=True, slots=True)
class LogicalDocumentSegment:
    segment_id: str
    analysis_revision: int
    kind: str
    state: str
    page_start: int
    page_end: int
    common_only: bool
    boundary_evidence: tuple[BoundaryEvidence, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.analysis_revision, bool) or not isinstance(self.analysis_revision, int) or self.analysis_revision < 1:
            raise ValueError("analysis_revision must be a positive integer")
        if isinstance(self.page_start, bool) or isinstance(self.page_end, bool) or not isinstance(self.page_start, int) or not isinstance(self.page_end, int) or self.page_start < 0 or self.page_end < self.page_start:
            raise ValueError("invalid inclusive page range")
        if self.kind not in SUPPORTED_SEGMENT_KINDS:
            raise ValueError("invalid segment kind")
        if self.state not in {"confirmed", "review_required", "user_confirmed"}:
            raise ValueError("invalid segment state")

    @property
    def page_range(self) -> tuple[int, int]:
        return (self.page_start, self.page_end)

    def safe_dict(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "analysis_revision": self.analysis_revision,
            "kind": self.kind,
            "state": self.state,
            "page_range": [self.page_start, self.page_end],
            "common_only": self.common_only,
            "boundary_reason_counts": min(len(self.boundary_evidence), _MAX_SAFE_CODES),
        }


@dataclass(frozen=True, slots=True)
class ReviewItem:
    review_id: str
    analysis_revision: int
    kind: str
    page_start: int
    page_end: int
    requires_acknowledgment: bool
    reason_codes: tuple[str, ...]
    common_only: bool = True

    def safe_dict(self) -> dict[str, object]:
        return {
            "review_id": self.review_id,
            "analysis_revision": self.analysis_revision,
            "kind": self.kind,
            "page_range": [self.page_start, self.page_end],
            "requires_acknowledgment": self.requires_acknowledgment,
            "reason_codes": _safe_codes(self.reason_codes, _SAFE_BOUNDARY_REASON_CODES),
            "reason_code_count": _bounded_count(self.reason_codes),
            "common_only": self.common_only,
        }


@dataclass(frozen=True, slots=True)
class RoutingResult:
    profile: str
    analysis_revision: int
    segments: tuple[LogicalDocumentSegment, ...]
    review_items: tuple[ReviewItem, ...]
    document_hash: str

    def __post_init__(self) -> None:
        if isinstance(self.analysis_revision, bool) or not isinstance(self.analysis_revision, int) or self.analysis_revision < 1:
            raise ValueError("analysis_revision must be a positive integer")
        if not isinstance(self.document_hash, str) or _SHA256_RE.fullmatch(self.document_hash) is None:
            raise ValueError("document_hash must be a lowercase SHA-256")
        previous = -1
        for segment in self.segments:
            if segment.analysis_revision != self.analysis_revision:
                raise ValueError("segment revision must match routing result")
            if segment.page_start != previous + 1:
                raise ValueError("segments must be a contiguous partition")
            previous = segment.page_end
        if any(item.analysis_revision != self.analysis_revision for item in self.review_items):
            raise ValueError("review revision must match routing result")

    def safe_dict(self) -> dict[str, object]:
        return {"schema_version": SCHEMA_VERSION, "profile": self.profile,
                "analysis_revision": self.analysis_revision, "document_hash": self.document_hash,
                "segment_count": len(self.segments), "review_count": len(self.review_items),
                "segments": [segment.safe_dict() for segment in self.segments],
                "reviews": [item.safe_dict() for item in self.review_items]}


def _signal_kinds(signals: Iterable[str]) -> frozenset[str]:
    values = {signal.lower() for signal in signals}
    if "prereview" in values:
        # The dedicated prereview compound signal is stronger than shared
        # dispatch/footer vocabulary that can coexist on the same page.
        return frozenset({"internal_review"})
    kinds: set[str] = set()
    if values & {"internal_review", "internal", "approval", "review", "prereview"}:
        kinds.add("internal_review")
    if values & {"official_dispatch", "dispatch", "recipient", "reference", "sender"}:
        kinds.add("official_dispatch")
    if values & {"attachment", "appendix"}:
        kinds.add("attachment")
    return frozenset(kinds)


def _has_legacy_continuation_evidence(page: PageEvidence) -> bool:
    positive_signals = {
        CONTINUATION_PAGE_NUMBER_SEQUENCE,
        CONTINUATION_REPEATED_HEADER_FOOTER,
    }
    return (
        not page.start_signals
        and CONTINUATION_NO_START_SIGNAL in page.continuity_signals
        and bool(positive_signals & page.continuity_signals)
    )


def _titles_match(left: Sequence[str], right: Sequence[str]) -> bool:
    for left_title in left:
        for right_title in right:
            normalized_left = re.sub(r"[\W_]+", "", left_title).casefold()
            normalized_right = re.sub(r"[\W_]+", "", right_title).casefold()
            if (
                len(normalized_left) >= _RUNNING_TITLE_MIN_MATCH_CHARACTERS
                and normalized_left == normalized_right
                and normalized_left not in _GENERIC_RUNNING_TITLES
            ):
                return True
            shorter, longer = sorted((left_title, right_title), key=len)
            normalized_shorter = re.sub(r"[\W_]+", "", shorter).casefold()
            normalized_longer = re.sub(r"[\W_]+", "", longer).casefold()
            if (
                len(normalized_shorter) < _RUNNING_TITLE_MIN_MATCH_CHARACTERS
                or normalized_shorter in _GENERIC_RUNNING_TITLES
                or len(normalized_shorter) / len(normalized_longer) < _RUNNING_TITLE_MIN_MATCH_RATIO
            ):
                continue
            if normalized_shorter in normalized_longer:
                return True
            shorter_tokens = tuple(re.findall(r"\w+", shorter.casefold()))
            longer_tokens = tuple(re.findall(r"\w+", longer.casefold()))
            if any(
                longer_tokens[index:index + len(shorter_tokens)] == shorter_tokens
                for index in range(len(longer_tokens) - len(shorter_tokens) + 1)
            ):
                return True
    return False


def _has_running_title_evidence(page: PageEvidence, active_titles: Sequence[str]) -> bool:
    return not page.start_signals and _titles_match(active_titles, page.routing_titles)


def _has_continuation_evidence(page: PageEvidence, active_titles: Sequence[str]) -> bool:
    if page.routing_titles:
        return _has_running_title_evidence(page, active_titles)
    return _has_legacy_continuation_evidence(page)


def _has_bridge_positive_evidence(page: PageEvidence, active_titles: Sequence[str]) -> bool:
    return (
        not page.start_signals
        and (
            (
                CONTINUATION_NO_START_SIGNAL in page.continuity_signals
                and CONTINUATION_PAGE_NUMBER_SEQUENCE in page.continuity_signals
            )
            or _has_running_title_evidence(page, active_titles)
        )
    )


def _can_bridge_single_gap(
    ordered: Sequence[PageEvidence],
    position: int,
    active_titles: Sequence[str],
    previous_page_is_anchored: bool,
) -> bool:
    if not previous_page_is_anchored or position + 1 >= len(ordered):
        return False
    page = ordered[position]
    if page.start_signals or page.routing_titles or _has_legacy_continuation_evidence(page):
        return False
    for later_page in ordered[position + 1:]:
        if later_page.start_signals:
            return False
        if _has_bridge_positive_evidence(later_page, active_titles):
            return True
        if later_page.routing_titles or _has_legacy_continuation_evidence(later_page):
            return False
    return False




def _segment_id(document_hash: str, revision: int, kind: str, start: int, end: int, common_only: bool) -> str:
    return "seg_" + _digest({"document_hash": document_hash, "analysis_revision": revision,
                               "kind": kind, "page_start": start, "page_end": end,
                               "common_only": common_only})[:24]


def _review_id(document_hash: str, revision: int, start: int, end: int, reasons: Sequence[str]) -> str:
    return "review_" + _digest({"document_hash": document_hash, "analysis_revision": revision,
                                 "kind": "boundary", "page_start": start, "page_end": end,
                                 "reason_codes": sorted(reasons)})[:24]


def _has_profile_authority(
    authority: Mapping[str, object] | None, *, document_hash: str, analysis_revision: int, profile: str,
) -> bool:
    if not isinstance(authority, Mapping) or set(authority) != {
        "document_sha256", "analysis_revision", "profile", "decision_code",
    }:
        return False
    return (
        isinstance(authority.get("document_sha256"), str)
        and type(authority.get("analysis_revision")) is int
        and isinstance(authority.get("profile"), str)
        and isinstance(authority.get("decision_code"), str)
        and authority["document_sha256"] == document_hash
        and authority["analysis_revision"] == analysis_revision
        and authority["profile"] == profile
        and authority["decision_code"] == "profile_confirmed"
    )


def route_logical_documents(profile: str | None, pages: Iterable[PageEvidence], *, document_hash: str,
                            analysis_revision: int = 1,
                            profile_authority: Mapping[str, object] | None = None) -> RoutingResult:
    """Route ordered page evidence into a complete, non-overlapping segment partition."""
    profile = normalize_profile(profile)
    ordered = tuple(sorted(pages, key=lambda page: page.page_index))
    if any(page.page_index != index for index, page in enumerate(ordered)):
        raise ValueError("pages must be consecutive and 0-based")
    if not isinstance(document_hash, str) or _SHA256_RE.fullmatch(document_hash) is None:
        raise ValueError("document_hash must be a lowercase SHA-256")
    if isinstance(analysis_revision, bool) or not isinstance(analysis_revision, int) or analysis_revision < 1:
        raise ValueError("analysis_revision must be a positive integer")
    if not ordered:
        return RoutingResult(profile, analysis_revision, (), (), document_hash)
    if profile != "mixed":
        kind = profile
        is_public_profile = profile in {"internal_review", "official_dispatch"}
        confirmed = not is_public_profile or _has_profile_authority(
            profile_authority,
            document_hash=document_hash,
            analysis_revision=analysis_revision,
            profile=profile,
        )
        state = "confirmed" if confirmed else "review_required"
        segment = LogicalDocumentSegment(
            _segment_id(document_hash, analysis_revision, kind, 0, len(ordered) - 1, not confirmed),
            analysis_revision, kind, state, 0, len(ordered) - 1, not confirmed,
        )
        reviews = () if confirmed else (
            ReviewItem(
                _review_id(document_hash, analysis_revision, 0, len(ordered) - 1, ("profile_authority_missing",)),
                analysis_revision, "boundary", 0, len(ordered) - 1, True,
                ("profile_authority_missing",),
            ),
        )
        return RoutingResult(profile, analysis_revision, (segment,), reviews, document_hash)

    starts: list[tuple[int, str, bool, tuple[str, ...], float | None]] = []
    first_kind = "unknown"
    active_kind = "unknown"
    active_confirmed = False
    active_titles: tuple[str, ...] = ()
    previous_page_is_anchored = False
    for position, page in enumerate(ordered):
        signal_codes = tuple(sorted(page.start_signals))
        candidate_kinds = _signal_kinds(page.start_signals)
        if candidate_kinds:
            if len(candidate_kinds) > 1:
                candidate = "unknown"
                ambiguous = True
                reasons = ("conflicting_start_signals",) + signal_codes
            else:
                candidate = next(iter(candidate_kinds))
                ambiguous = False
                reasons = signal_codes
        elif COMMON_DOCUMENT_HEADER in page.start_signals:
            candidate = "unknown"
            ambiguous = True
            reasons = ("common_document_header_ambiguous",)
        elif signal_codes:
            candidate = "unknown"
            ambiguous = True
            reasons = ("unrecognized_start_signals",) + signal_codes
        else:
            candidate = None
            ambiguous = False
            reasons = signal_codes

        if page.page_index == 0:
            first_kind = candidate or "unknown"
            first_uncertain = (
                candidate is None or ambiguous or bool(page.continuity_signals)
                or page.boundary_confidence is None or page.boundary_confidence < 0.8
            )
            if first_uncertain:
                starts.append((0, first_kind, True, reasons or ("unrecognized_start_signals",), page.boundary_confidence))
            active_kind = first_kind
            active_confirmed = not first_uncertain
            active_titles = page.routing_titles
            previous_page_is_anchored = active_confirmed
            continue
        if candidate is None:
            if active_confirmed and active_kind in _PROFILE_SEGMENT_KINDS:
                if _has_continuation_evidence(page, active_titles):
                    previous_page_is_anchored = True
                    continue
                if page.routing_titles:
                    confidence = page.ocr_confidence
                    uncertain = (
                        page.routing_title_kind is None
                        or confidence is None
                        or confidence < 0.8
                    )
                    starts.append((
                        page.page_index,
                        page.routing_title_kind or "unknown",
                        uncertain,
                        ("different_running_title",),
                        confidence,
                    ))
                    active_kind = page.routing_title_kind or "unknown"
                    active_confirmed = not uncertain
                    active_titles = page.routing_titles
                    previous_page_is_anchored = active_confirmed
                    continue
                if _can_bridge_single_gap(
                    ordered,
                    position,
                    active_titles,
                    previous_page_is_anchored,
                ):
                    previous_page_is_anchored = True
                    continue
            if not active_confirmed and active_kind in _PROFILE_SEGMENT_KINDS:
                continue
            if active_kind == "unknown":
                continue
            starts.append((
                page.page_index,
                "unknown",
                True,
                ("continuation_evidence_missing",),
                page.boundary_confidence,
            ))
            active_kind = "unknown"
            active_confirmed = False
            active_titles = ()
            previous_page_is_anchored = False
            continue
        # Every grounded document-start signal is a boundary, including repeated kinds.
        confidence = page.boundary_confidence
        uncertain = ambiguous or bool(page.continuity_signals) or confidence is None or confidence < 0.8
        starts.append((
            page.page_index,
            candidate,
            uncertain,
            reasons,
            confidence,
        ))
        active_kind = candidate
        active_confirmed = not uncertain
        active_titles = page.routing_titles
        previous_page_is_anchored = active_confirmed

    # Segment boundaries are only start signals. Unrecognized or conflicting starts are common-only.
    boundaries = [0] + [entry[0] for entry in starts if entry[0] > 0] + [len(ordered)]
    starts_by_page = {entry[0]: entry for entry in starts}
    segments: list[LogicalDocumentSegment] = []
    reviews: list[ReviewItem] = []
    for position, start in enumerate(boundaries[:-1]):
        end = boundaries[position + 1] - 1
        boundary = starts_by_page.get(start)
        kind = first_kind if start == 0 else boundary[1]
        common_only = boundary is not None and boundary[2]
        evidence = () if boundary is None else (BoundaryEvidence(start, boundary[3], boundary[4], boundary[2]),)
        state = "review_required" if common_only else "confirmed"
        segment = LogicalDocumentSegment(_segment_id(document_hash, analysis_revision, kind, start, end, common_only),
                                         analysis_revision, kind, state, start, end, common_only, evidence)
        segments.append(segment)
        if common_only:
            reasons = ("ambiguous_boundary",) + boundary[3]
            reviews.append(ReviewItem(_review_id(document_hash, analysis_revision, start, end, reasons), analysis_revision,
                                      "boundary", start, end, True, reasons))
    return RoutingResult(profile, analysis_revision, tuple(segments), tuple(reviews), document_hash)


@dataclass(frozen=True, slots=True)
class BoundaryCorrection:
    page_start: int
    page_end: int
    kind: str

    def __post_init__(self) -> None:
        if isinstance(self.page_start, bool) or isinstance(self.page_end, bool) or not isinstance(self.page_start, int) or not isinstance(self.page_end, int) or self.page_start < 0 or self.page_end < self.page_start:
            raise ValueError("invalid inclusive page range")
        if self.kind not in SUPPORTED_SEGMENT_KINDS - {"unknown"}:
            raise ValueError("unsupported correction segment kind")


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """A resolved review decision with the semantic fingerprint required for carry."""

    review_id: str
    page_start: int
    page_end: int
    semantic_fingerprint: str
    policy_version: str
    action: str
    analysis_revision: int

    def __post_init__(self) -> None:
        if self.page_start < 0 or self.page_end < self.page_start:
            raise ValueError("invalid inclusive page range")
        if not all(isinstance(value, str) and value for value in (self.review_id, self.semantic_fingerprint, self.policy_version, self.action)):
            raise ValueError("invalid review decision identity")


def carry_semantically_identical_decisions(
    prior: Iterable[ReviewDecision],
    current: Iterable[ReviewDecision],
    affected_page_range: tuple[int, int],
) -> tuple[ReviewDecision, ...]:
    """Return only unchanged, outside-range decisions, never boundary acknowledgments."""
    affected_start, affected_end = affected_page_range
    if affected_start < 0 or affected_end < affected_start:
        raise ValueError("invalid affected page range")

    def key(item: ReviewDecision) -> tuple[str, str, str, str, int, int]:
        return (item.review_id, item.semantic_fingerprint, item.policy_version, item.action, item.page_start, item.page_end)

    prior_by_key: dict[tuple[str, str, str, str, int, int], ReviewDecision] = {}
    current_by_key: dict[tuple[str, str, str, str, int, int], ReviewDecision] = {}
    for collection, target in ((prior, prior_by_key), (current, current_by_key)):
        for item in collection:
            if item.action == "boundary_acknowledgment":
                continue
            item_key = key(item)
            if item_key in target:
                raise ValueError("duplicate review decision carry key")
            target[item_key] = item
    carried: list[ReviewDecision] = []
    for item_key, decision in prior_by_key.items():
        outside = decision.page_end < affected_start or decision.page_start > affected_end
        replacement = current_by_key.get(item_key)
        if outside and replacement is not None and (
            replacement.page_end < affected_start or replacement.page_start > affected_end
        ):
            carried.append(replacement)
    return tuple(carried)


@dataclass(frozen=True, slots=True)
class RevisionUpdate:
    analysis_revision: int
    affected_page_range: tuple[int, int]
    carried_review_ids: tuple[str, ...]
    routing_result: RoutingResult


def _correction_fingerprint(correction: BoundaryCorrection) -> str:
    return _digest({
        "page_start": correction.page_start,
        "page_end": correction.page_end,
        "kind": correction.kind,
    })
def _evidence_for_range(
    evidence: tuple[BoundaryEvidence, ...], start: int, end: int,
) -> tuple[BoundaryEvidence, ...]:
    return tuple(item for item in evidence if start <= item.page_index <= end)




def _has_correction_authority(
    authority: Mapping[str, object] | None, result: RoutingResult, correction: BoundaryCorrection,
) -> bool:
    if not isinstance(authority, Mapping) or set(authority) != {
        "document_sha256", "prior_analysis_revision", "profile", "decision_code", "correction_sha256",
    }:
        return False
    return (
        isinstance(authority.get("document_sha256"), str)
        and type(authority.get("prior_analysis_revision")) is int
        and isinstance(authority.get("profile"), str)
        and isinstance(authority.get("decision_code"), str)
        and isinstance(authority.get("correction_sha256"), str)
        and authority["document_sha256"] == result.document_hash
        and authority["prior_analysis_revision"] == result.analysis_revision
        and authority["profile"] == result.profile
        and authority["decision_code"] == "boundary_correction_confirmed"
        and authority["correction_sha256"] == _correction_fingerprint(correction)
    )


def apply_boundary_correction(
    result: RoutingResult,
    correction: BoundaryCorrection,
    *,
    correction_authority: Mapping[str, object] | None,
) -> RevisionUpdate:
    """Rebuild a correction only when it is bound to the current decision identity."""
    if not _has_correction_authority(correction_authority, result, correction):
        raise ValueError("correction authority is invalid or stale")
    last_page = max((segment.page_end for segment in result.segments), default=-1)
    if correction.page_end > last_page:
        raise ValueError("correction exceeds routed pages")
    new_revision = result.analysis_revision + 1
    left_segments: list[LogicalDocumentSegment] = []
    right_segments: list[LogicalDocumentSegment] = []
    for segment in result.segments:
        fragment_common_only = segment.common_only
        fragment_state = segment.state
        if segment.page_start < correction.page_start:
            end = min(segment.page_end, correction.page_start - 1)
            left_segments.append(LogicalDocumentSegment(
                _segment_id(result.document_hash, new_revision, segment.kind, segment.page_start, end, fragment_common_only),
                new_revision, segment.kind, fragment_state, segment.page_start, end, fragment_common_only,
                _evidence_for_range(segment.boundary_evidence, segment.page_start, end),
            ))
        if segment.page_end > correction.page_end:
            start = max(segment.page_start, correction.page_end + 1)
            right_segments.append(LogicalDocumentSegment(
                _segment_id(result.document_hash, new_revision, segment.kind, start, segment.page_end, fragment_common_only),
                new_revision, segment.kind, fragment_state, start, segment.page_end, fragment_common_only,
                _evidence_for_range(segment.boundary_evidence, start, segment.page_end),
            ))
    corrected = LogicalDocumentSegment(
        _segment_id(result.document_hash, new_revision, correction.kind, correction.page_start, correction.page_end, False),
        new_revision, correction.kind, "user_confirmed", correction.page_start, correction.page_end, False,
    )

    reviews: list[ReviewItem] = []
    for review in result.review_items:
        if review.page_end < correction.page_start or review.page_start > correction.page_end:
            fragments = ((review.page_start, review.page_end),)
        else:
            fragments = tuple(
                (start, end)
                for start, end in (
                    (review.page_start, correction.page_start - 1),
                    (correction.page_end + 1, review.page_end),
                )
                if start <= end
            )
        for start, end in fragments:
            reasons = review.reason_codes
            reviews.append(ReviewItem(
                _review_id(result.document_hash, new_revision, start, end, reasons),
                new_revision, review.kind, start, end,
                review.requires_acknowledgment, reasons, review.common_only,
            ))
    rebuilt = RoutingResult(result.profile, new_revision, tuple(left_segments + [corrected] + right_segments), tuple(reviews), result.document_hash)
    return RevisionUpdate(new_revision, (correction.page_start, correction.page_end), (), rebuilt)


def acknowledgment_is_current(item: ReviewItem, analysis_revision: int) -> bool:
    return item.requires_acknowledgment and item.analysis_revision == analysis_revision
