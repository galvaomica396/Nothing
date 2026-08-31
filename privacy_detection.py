from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Literal, Protocol, Sequence

from privacy_spans import (
    action_for_tag,
    clean_match_text as _clean_match_text,
    confidence_for_tag,
    label_for_tag,
    match_candidate_id,
    match_occurrence_id,
    match_metadata,
    match_offsets,
    match_source,
)
from privacy_spans import _find_offset  # re-exported for backwards compatibility


DetectionDecision = Literal["auto_mask", "review", "preserve"]
PUBLIC_NAME_TEST_POLICY_VERSION = "public-name-context-policy-v1"
PUBLIC_NAME_TEST_AUTO_MASK_THRESHOLD = 0.85
PUBLIC_NAME_TEST_REVIEW_THRESHOLD = 0.50


def score_public_body_name(
    *,
    authoritative_label: bool = False,
    approval_role: bool = False,
    punctuation_or_label_boundary: bool = False,
    distance_from_label: int | None = None,
    page_position_match: bool = False,
    region_state: str = "unconfirmed",
    auto_mask_threshold: float = PUBLIC_NAME_TEST_AUTO_MASK_THRESHOLD,
    review_threshold: float = PUBLIC_NAME_TEST_REVIEW_THRESHOLD,
) -> dict[str, object]:
    """Score a public-document name candidate from explicit, auditable context."""
    features = {
        "authoritative_label": authoritative_label,
        "approval_role": approval_role,
        "punctuation_or_label_boundary": punctuation_or_label_boundary,
        "distance_from_label": distance_from_label,
        "page_position_match": page_position_match,
        "confirmed_region": region_state in {"confirmed", "user_confirmed"},
        "user_confirmed_region": region_state == "user_confirmed",
    }
    if not 0 <= review_threshold <= auto_mask_threshold <= 1:
        raise ValueError("name thresholds must satisfy 0 <= review <= auto <= 1")
    score = 0.0
    reasons: list[str] = []
    if authoritative_label:
        score += .50
        reasons.append("authoritative_label")
    if approval_role:
        score += .60
        reasons.append("approval_role")
    if punctuation_or_label_boundary:
        score += .10
        reasons.append("label_boundary")
    if distance_from_label is not None and 0 <= distance_from_label <= 12:
        score += .10
        reasons.append("label_distance")
    if page_position_match:
        score += .05
        reasons.append("page_position")
    if features["confirmed_region"]:
        score += .10
        reasons.append("confirmed_region")
    if features["user_confirmed_region"]:
        score += .05
        reasons.append("user_confirmed_region")
    score = min(1.0, score)
    action: DetectionDecision = (
        "auto_mask" if score >= auto_mask_threshold else
        "review" if score >= review_threshold else "preserve"
    )
    return {"policy_version": PUBLIC_NAME_TEST_POLICY_VERSION, "score": round(score, 3),
            "action": action, "reason_codes": tuple(reasons), "features": features,
            "auto_mask_threshold": auto_mask_threshold, "review_threshold": review_threshold}


PUBLIC_CONTEXT_POLICY_VERSION = "public-region-institution-context-policy-v1"


def _public_context_action(
    score: float,
    *,
    auto_mask_threshold: float,
    review_threshold: float,
    automatic_allowed: bool,
) -> DetectionDecision:
    if not 0 <= review_threshold <= auto_mask_threshold <= 1:
        raise ValueError("public context thresholds must satisfy 0 <= review <= auto <= 1")
    # A low-confidence public-context hit remains visible as a review candidate.
    # Dropping it would turn a precision problem into a silent omission.
    if automatic_allowed and score >= auto_mask_threshold:
        return "auto_mask"
    return "review"


def score_public_region_value(
    *,
    dictionary_match: bool = True,
    explicit_region_label: bool = False,
    hierarchical: bool = False,
    exact_boundary: bool = False,
    quoted_context: bool = False,
    auto_mask_threshold: float = PUBLIC_NAME_TEST_AUTO_MASK_THRESHOLD,
    review_threshold: float = PUBLIC_NAME_TEST_REVIEW_THRESHOLD,
) -> dict[str, object]:
    """Score a region-name candidate without turning dictionary hits into masks.

    Region names are required by the public-document policy, but a national
    place dictionary is intentionally insufficient evidence for automatic
    redaction.  The candidate is therefore always retained; only a complete
    labelled hierarchy can reach the automatic threshold.
    """
    if not 0 <= review_threshold <= auto_mask_threshold <= 1:
        raise ValueError("public context thresholds must satisfy 0 <= review <= auto <= 1")
    score = 0.0
    reasons: list[str] = []
    if dictionary_match:
        score += 0.40
        reasons.append("region_dictionary")
    if explicit_region_label:
        score += 0.25
        reasons.append("region_label")
    if hierarchical:
        score += 0.20
        reasons.append("hierarchical_region")
    if exact_boundary:
        score += 0.10
        reasons.append("exact_boundary")
    if quoted_context:
        reasons.append("quoted_context")
    score = min(1.0, score)
    return {
        "policy_version": PUBLIC_CONTEXT_POLICY_VERSION,
        "score": round(score, 3),
        "action": _public_context_action(
            score,
            auto_mask_threshold=auto_mask_threshold,
            review_threshold=review_threshold,
            automatic_allowed=not quoted_context,
        ),
        "reason_codes": tuple(reasons),
        "auto_mask_threshold": auto_mask_threshold,
        "review_threshold": review_threshold,
    }


def score_public_institution_value(
    *,
    strong_institution_pattern: bool = False,
    explicit_institution_label: bool = False,
    exact_boundary: bool = False,
    independent_context: bool = False,
    quoted_context: bool = False,
    auto_mask_threshold: float = PUBLIC_NAME_TEST_AUTO_MASK_THRESHOLD,
    review_threshold: float = PUBLIC_NAME_TEST_REVIEW_THRESHOLD,
) -> dict[str, object]:
    """Score an institution value while retaining weak hits for user review."""
    if not 0 <= review_threshold <= auto_mask_threshold <= 1:
        raise ValueError("public context thresholds must satisfy 0 <= review <= auto <= 1")
    score = 0.0
    reasons: list[str] = []
    if strong_institution_pattern:
        score += 0.45
        reasons.append("institution_pattern")
    if explicit_institution_label:
        score += 0.30
        reasons.append("institution_label")
    if exact_boundary:
        score += 0.10
        reasons.append("exact_boundary")
    if independent_context:
        score += 0.10
        reasons.append("independent_context")
    if quoted_context:
        reasons.append("quoted_context")
    score = min(1.0, score)
    return {
        "policy_version": PUBLIC_CONTEXT_POLICY_VERSION,
        "score": round(score, 3),
        "action": _public_context_action(
            score,
            auto_mask_threshold=auto_mask_threshold,
            review_threshold=review_threshold,
            automatic_allowed=not quoted_context,
        ),
        "reason_codes": tuple(reasons),
        "auto_mask_threshold": auto_mask_threshold,
        "review_threshold": review_threshold,
    }


def score_public_institution_address(
    *,
    address_pattern: bool = False,
    explicit_address_label: bool = False,
    footer_contact_context: bool = False,
    exact_boundary: bool = False,
    auto_mask_threshold: float = PUBLIC_NAME_TEST_AUTO_MASK_THRESHOLD,
    review_threshold: float = PUBLIC_NAME_TEST_REVIEW_THRESHOLD,
) -> dict[str, object]:
    """Score an institutional address as review-only without a policy override.

    Public and project-site addresses are not interchangeable with personal
    addresses.  Until an independent address policy distinguishes them, the
    detector must expose the candidate but leave the final decision to review.
    """
    if not 0 <= review_threshold <= auto_mask_threshold <= 1:
        raise ValueError("public context thresholds must satisfy 0 <= review <= auto <= 1")
    score = 0.0
    reasons: list[str] = []
    if address_pattern:
        score += 0.40
        reasons.append("address_pattern")
    if explicit_address_label:
        score += 0.25
        reasons.append("address_label")
    if footer_contact_context:
        score += 0.20
        reasons.append("footer_contact_context")
    if exact_boundary:
        score += 0.10
        reasons.append("exact_boundary")
    score = min(1.0, score)
    return {
        "policy_version": PUBLIC_CONTEXT_POLICY_VERSION,
        "score": round(score, 3),
        "action": "review",
        "reason_codes": tuple(reasons),
        "auto_mask_threshold": auto_mask_threshold,
        "review_threshold": review_threshold,
    }



class RedactionMatchLike(Protocol):
    tag: str
    text: str


@dataclass(frozen=True, slots=True)
class DetectionCandidate:
    id: str
    tag: str
    label: str
    start: int
    end: int
    length: int
    recognizer_name: str
    score: float
    decision: DetectionDecision
    reason: str
    raw_text_stored: bool
    _match: RedactionMatchLike
    occurrence_id: str = ""
    analysis_revision: int | None = None
    page: int | None = None
    rect_list: tuple[tuple[float, float, float, float], ...] = ()
    action: str = "mask"
    confidence: float | None = None
    evidence: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    coordinate_space: str = "text_offsets"
    metadata: dict[str, object] = field(default_factory=dict)

    def to_redaction_match(self) -> RedactionMatchLike:
        return self._match

    def to_safe_report_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "tag": self.tag,
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "length": self.length,
            "recognizer_name": self.recognizer_name,
            "score": self.score,
            "decision": self.decision,
            "reason": self.reason,
            "raw_text_stored": self.raw_text_stored,
            "page": self.page,
            "occurrence_id": self.occurrence_id,
            "analysis_revision": self.analysis_revision,
            "rect_count": len(self.rect_list),
            "action": self.action,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "provenance": list(self.provenance),
            "coordinate_space": self.coordinate_space,
            "metadata": self.metadata,
        }


def detection_candidates_from_matches(
    text: str,
    matches: Sequence[RedactionMatchLike],
) -> list[DetectionCandidate]:
    cursors: dict[str, int] = {}
    candidates: list[DetectionCandidate] = []
    for match in matches:
        value = _clean_match_text(match.text)
        start, end, authoritative = match_offsets(text, match, cursors.get(value, 0))
        if start < 0:
            continue
        if not authoritative:
            cursors[value] = end
        length = end - start
        candidates.append(
            DetectionCandidate(
                id=match_candidate_id(match, start, end),
                tag=_report_tag(match.tag),
                label=label_for_tag(match.tag),
                start=start,
                end=end,
                length=length,
                recognizer_name=match_source(match),
                score=confidence_for_tag(match.tag),
                decision=_decision_for_match(match),
                reason=_reason_for_match(match),
                raw_text_stored=False,
                _match=match,
                occurrence_id=match_occurrence_id(match, start, end),
                analysis_revision=_analysis_revision(match),
                page=_page(match),
                rect_list=_rect_list(match),
                action=_action_for_match(match),
                confidence=_confidence(match),
                evidence=_safe_codes(getattr(match, "evidence", ())),
                provenance=_safe_codes(getattr(match, "provenance", ())),
                coordinate_space=_coordinate_space(match),
                metadata=match_metadata(match),
            )
        )
    return candidates


def safe_detection_candidate_reports(
    text: str,
    matches: Sequence[RedactionMatchLike],
) -> list[dict[str, str | int | float | bool | None]]:
    return [candidate.to_safe_report_dict() for candidate in detection_candidates_from_matches(text, matches)]


def _report_tag(tag: str) -> str:
    return "PLACE" if tag == "WEAK_PLACE" else tag


def _action_for_match(match: RedactionMatchLike) -> str:
    action = getattr(match, "action", action_for_tag(match.tag))
    return action if action in {"mask", "review", "exclude"} else "review"

def _decision_for_match(match: RedactionMatchLike) -> DetectionDecision:
    action = _action_for_match(match)
    if action == "mask":
        return "auto_mask"
    if action == "review":
        return "review"
    return "preserve"

def _reason_for_match(match: RedactionMatchLike) -> str:
    action = _action_for_match(match)
    return {
        "mask": "recognizer_auto_mask",
        "review": "recognizer_review_required",
        "exclude": "recognizer_preserved",
    }[action]
def _analysis_revision(match: RedactionMatchLike) -> int | None:
    value = getattr(match, "analysis_revision", None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _page(match: RedactionMatchLike) -> int | None:
    value = getattr(match, "page", getattr(match, "page_index", None))
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

def _rect_list(match: RedactionMatchLike) -> tuple[tuple[float, float, float, float], ...]:
    value = getattr(match, "rects", ())
    rects: list[tuple[float, float, float, float]] = []
    if not isinstance(value, (tuple, list)):
        return ()
    for rect in value:
        if not isinstance(rect, (tuple, list)) or len(rect) != 4:
            continue
        try:
            normalized = tuple(float(part) for part in rect)
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(part) for part in normalized) and normalized[2] > normalized[0] and normalized[3] > normalized[1]:
            rects.append(normalized)
    return tuple(rects)

def _confidence(match: RedactionMatchLike) -> float | None:
    value = getattr(match, "confidence", confidence_for_tag(match.tag))
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None

def _coordinate_space(match: RedactionMatchLike) -> str:
    value = getattr(match, "coordinate_space", "text_offsets")
    return value if value in {"text_offsets", "pdf_points_top_left"} else "text_offsets"


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
