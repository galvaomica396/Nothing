"""Fail-closed fixed-region analysis for public-document layouts."""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import math
import re
from typing import Iterable, Mapping

from approval_layout import APPROVAL_ROW_LABEL_VALUE_DISTANCE_MAX
from document_routing import COORDINATE_SPACE, PdfRect, SCHEMA_VERSION

REGION_STATES = frozenset({"confirmed", "review_required", "unconfirmed"})
INTERNAL_REVIEW_REGION_KINDS = ("approval", "header_meta", "labeled_staff")
OFFICIAL_DISPATCH_REGION_KINDS = (
    "recipient_reference", "sender_institution", "approval_staff", "dispatch_metadata", "footer_contact",
)
REGION_KINDS = frozenset(INTERNAL_REVIEW_REGION_KINDS + OFFICIAL_DISPATCH_REGION_KINDS)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONFIRMATION_VERSION = "layout-confirmation-v1"

# A region can be auto-applied only when every independent layout signal is
# present.  Partial evidence remains reviewable; weak evidence never enables a
# profile-specific masking rule.  The constants are pinned by regression tests.
REGION_SCORE_WEIGHTS = {
    "box_structure": 3,
    "label_match": 3,
    "label_value_distance": 2,
    "page_position": 2,
}
REGION_CONFIRMED_SCORE = sum(REGION_SCORE_WEIGHTS.values())
REGION_REVIEW_REQUIRED_SCORE = 5
EVIDENCE_LABEL_VALUE_DISTANCE_MAX = 96.0


def _hash(payload: dict[str, object]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
_IDENTITY_COORDINATE_SCALE = 1_000_000


def _canonical_rects(rect_list: tuple[PdfRect, ...]) -> tuple[tuple[int, int, int, int], ...]:
    return tuple(sorted(
        tuple(
            int(math.floor(value * _IDENTITY_COORDINATE_SCALE + 0.5))
            for value in rect.normalized()
        )
        for rect in rect_list
    ))


def _canonical_rect_list(rect_list: tuple[PdfRect, ...]) -> tuple[PdfRect, ...]:
    return tuple(
        PdfRect(*(value / _IDENTITY_COORDINATE_SCALE for value in rect))
        for rect in _canonical_rects(rect_list)
    )
def _has_negative_coordinates(rect_list: tuple[PdfRect, ...]) -> bool:
    return any(
        coordinate < 0
        for rect in rect_list
        for coordinate in rect.normalized()
    )




@dataclass(frozen=True, slots=True)
class RegionEvidence:
    """Evidence for a page-local candidate region; labels are represented only as codes."""
    kind: str
    page_index: int
    rect_list: tuple[PdfRect, ...] = ()
    box_structure_match: bool = False
    label_match: bool = False
    structural_match: bool = False
    label_value_distance: float | None = None
    approval_row_pattern: bool = False
    page_position_match: bool = False
    ocr_confidence: float | None = None
    confidence_source: str = "ocr"
    user_confirmed: bool | None = None
    user_confirmation: Mapping[str, object] | None = None
    schema_version: str = SCHEMA_VERSION
    coordinate_space: str = COORDINATE_SPACE

    def __post_init__(self) -> None:
        if self.kind not in REGION_KINDS:
            raise ValueError("unsupported region kind")
        if isinstance(self.page_index, bool) or not isinstance(self.page_index, int) or self.page_index < 0:
            raise ValueError("page_index is 0-based")
        if self.coordinate_space != COORDINATE_SPACE or self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported evidence contract")
        if not isinstance(self.rect_list, tuple) or any(not isinstance(rect, PdfRect) for rect in self.rect_list):
            raise ValueError("rect_list must contain PdfRect values")
        if _has_negative_coordinates(self.rect_list):
            raise ValueError("rectangle coordinates are out-of-page")

        for value, name in ((self.box_structure_match, "box_structure_match"),
                            (self.label_match, "label_match"), (self.structural_match, "structural_match"),
                            (self.approval_row_pattern, "approval_row_pattern"),
                            (self.page_position_match, "page_position_match")):
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be boolean")
        if self.label_value_distance is not None and (
            isinstance(self.label_value_distance, bool)
            or not isinstance(self.label_value_distance, (int, float))
            or not math.isfinite(self.label_value_distance)
            or self.label_value_distance < 0
        ):
            raise ValueError("label_value_distance must be a finite non-negative number")
        if self.approval_row_pattern and self.kind not in {"approval", "approval_staff"}:
            raise ValueError("approval_row_pattern is limited to approval evidence")
        if self.user_confirmed is not None and not isinstance(self.user_confirmed, bool):
            raise ValueError("user_confirmed must be boolean when supplied")
        if self.ocr_confidence is not None and (
            isinstance(self.ocr_confidence, bool) or not isinstance(self.ocr_confidence, (int, float))
            or not math.isfinite(self.ocr_confidence) or not 0 <= self.ocr_confidence <= 1
        ):
            raise ValueError("ocr_confidence must be in [0, 1]")
        if self.confidence_source not in {"ocr", "text_layer"}:
            raise ValueError("unsupported confidence source")
        if self.confidence_source == "text_layer" and self.ocr_confidence != 1.0:
            raise ValueError("text-layer confidence must be satisfied")
        if self.user_confirmed is True and not self.rect_list:
            raise ValueError("user confirmation requires page-local geometry")
        if self.user_confirmation is not None and not isinstance(self.user_confirmation, Mapping):
            raise ValueError("user_confirmation must be a content-bound mapping")
        if self.user_confirmation is not None and self.user_confirmed is not None:
            confirmed = self.user_confirmation.get("confirmed")
            if isinstance(confirmed, bool) and confirmed is not self.user_confirmed:
                raise ValueError("contradictory user confirmation representations")
            raise ValueError("ambiguous user confirmation representations")

    def safe_dict(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "kind": self.kind, "page_index": self.page_index,
                "coordinate_space": self.coordinate_space, "rect_count": len(self.rect_list),
                "box_structure_match": self.box_structure_match,
                "label_match": self.label_match, "structural_match": self.structural_match,
                "label_value_distance_present": self.label_value_distance is not None,
                "approval_row_pattern": self.approval_row_pattern,
                "page_position_match": self.page_position_match,
                "ocr_confidence_present": self.ocr_confidence is not None,
                "confidence_source": self.confidence_source,
                "user_confirmed": self.user_confirmed is True}


@dataclass(frozen=True, slots=True)
class LayoutRegion:
    region_id: str
    analysis_revision: int
    kind: str
    page_index: int
    rect_list: tuple[PdfRect, ...]
    state: str
    confirmation_source: str | None
    reason_codes: tuple[str, ...]
    coordinate_space: str = COORDINATE_SPACE

    def __post_init__(self) -> None:
        if not isinstance(self.region_id, str) or not self.region_id:
            raise ValueError("invalid region id")
        if isinstance(self.analysis_revision, bool) or not isinstance(self.analysis_revision, int) or self.analysis_revision < 1:
            raise ValueError("analysis_revision must be a positive integer")
        if self.kind not in REGION_KINDS:
            raise ValueError("unsupported region kind")
        if isinstance(self.page_index, bool) or not isinstance(self.page_index, int) or self.page_index < 0:
            raise ValueError("page_index is 0-based")
        if not isinstance(self.rect_list, tuple) or any(not isinstance(rect, PdfRect) for rect in self.rect_list):
            raise ValueError("rect_list must contain PdfRect values")
        if _has_negative_coordinates(self.rect_list):
            raise ValueError("rectangle coordinates are out-of-page")
        if self.state not in REGION_STATES:
            raise ValueError("invalid region state")
        if not self.rect_list and (self.state != "review_required" or self.confirmation_source is not None):
            raise ValueError("geometry-free regions must be review-required and unconfirmed")
        if self.coordinate_space != COORDINATE_SPACE:
            raise ValueError("unsupported coordinate space")
        if self.confirmation_source not in {None, "automatic", "user"}:
            raise ValueError("invalid confirmation source")

    @property
    def user_confirmed(self) -> bool:
        return self.confirmation_source == "user"

    def safe_dict(self) -> dict[str, object]:
        return {"region_id": self.region_id, "analysis_revision": self.analysis_revision,
                "kind": self.kind, "page_index": self.page_index,
                "coordinate_space": self.coordinate_space, "rect_count": len(self.rect_list),
                "state": self.state, "confirmation_source": self.confirmation_source,
                "reason_codes": list(self.reason_codes)}


def confirmation_content_hash(kind: str, page_index: int, rect_list: tuple[PdfRect, ...]) -> str:
    return _hash({
        "kind": kind,
        "page_index": page_index,
        "rect_list": _canonical_rects(rect_list),
    })

def _user_confirmation_is_bound(
    evidence: RegionEvidence, *, document_hash: str, analysis_revision: int,
) -> bool:
    confirmation = evidence.user_confirmation
    return (
        isinstance(confirmation, Mapping)
        and confirmation.get("version") == _CONFIRMATION_VERSION
        and confirmation.get("document_hash") == document_hash
        and confirmation.get("analysis_revision") == analysis_revision
        and confirmation.get("content_hash") == confirmation_content_hash(
            evidence.kind, evidence.page_index, evidence.rect_list,
        )
        and confirmation.get("confirmed") is True
    )


def _geometry_reason(evidence: RegionEvidence) -> str | None:
    if not evidence.rect_list:
        return "page_local_geometry_missing"
    if not all(isinstance(rect, PdfRect) for rect in evidence.rect_list):
        return "page_local_geometry_invalid"
    return None


def _state(
    evidence: RegionEvidence,
    confidence_threshold: float,
    *,
    document_hash: str,
    analysis_revision: int,
) -> tuple[str, str | None, tuple[str, ...]]:
    geometry_reason = _geometry_reason(evidence)
    if geometry_reason is not None:
        return "review_required", None, (geometry_reason,)
    if _user_confirmation_is_bound(
        evidence, document_hash=document_hash, analysis_revision=analysis_revision,
    ):
        return "confirmed", "user", ("user_geometry_confirmed",)
    if evidence.user_confirmed is True or evidence.user_confirmation is not None:
        return "review_required", None, ("review_evidence_required",)
    missing: list[str] = []
    if evidence.ocr_confidence is None:
        missing.append("ocr_confidence_missing")
    elif evidence.ocr_confidence < confidence_threshold:
        missing.append("ocr_confidence_uncertain")
    if not evidence.box_structure_match:
        missing.append("box_structure_missing")
    if not evidence.label_match:
        missing.append("label_evidence_missing")
    if not evidence.structural_match:
        missing.append("layout_structure_missing")
    distance_cap = (
        APPROVAL_ROW_LABEL_VALUE_DISTANCE_MAX
        if evidence.approval_row_pattern
        else EVIDENCE_LABEL_VALUE_DISTANCE_MAX
    )
    distance_matches = (
        evidence.label_value_distance is not None
        and evidence.label_value_distance <= distance_cap
    )
    if evidence.label_value_distance is None:
        missing.append("label_value_distance_missing")
    elif not distance_matches:
        missing.append("label_value_distance_out_of_range")
    if not evidence.page_position_match:
        missing.append("page_position_evidence_missing")
    score = (
        (REGION_SCORE_WEIGHTS["box_structure"] if evidence.box_structure_match else 0)
        + (REGION_SCORE_WEIGHTS["label_match"] if evidence.label_match else 0)
        + (REGION_SCORE_WEIGHTS["label_value_distance"] if distance_matches else 0)
        + (REGION_SCORE_WEIGHTS["page_position"] if evidence.page_position_match else 0)
    )
    # The score covers layout signals; ``not missing`` additionally enforces
    # trustworthy value geometry and OCR confidence before auto-confirmation.
    if score == REGION_CONFIRMED_SCORE and not missing:
        return "confirmed", "automatic", ("compound_layout_text_evidence",)
    if score < REGION_REVIEW_REQUIRED_SCORE:
        return "unconfirmed", None, tuple(missing)
    return "review_required", None, tuple(missing)


def _approval_evidence_score(evidence: RegionEvidence) -> int:
    distance_cap = (
        APPROVAL_ROW_LABEL_VALUE_DISTANCE_MAX
        if evidence.approval_row_pattern
        else EVIDENCE_LABEL_VALUE_DISTANCE_MAX
    )
    return (
        (REGION_SCORE_WEIGHTS["box_structure"] if evidence.box_structure_match else 0)
        + (REGION_SCORE_WEIGHTS["label_match"] if evidence.label_match else 0)
        + (REGION_SCORE_WEIGHTS["label_value_distance"]
           if evidence.label_value_distance is not None and evidence.label_value_distance <= distance_cap else 0)
        + (REGION_SCORE_WEIGHTS["page_position"] if evidence.page_position_match else 0)
    )


def _rect_lists_overlap(left: tuple[PdfRect, ...], right: tuple[PdfRect, ...]) -> bool:
    return any(
        max(left_rect.x0, right_rect.x0) < min(left_rect.x1, right_rect.x1)
        and max(left_rect.y0, right_rect.y0) < min(left_rect.y1, right_rect.y1)
        for left_rect in left
        for right_rect in right
    )


def _dedupe_overlapping_approval_evidence(candidates: list[RegionEvidence]) -> list[RegionEvidence]:
    deduped: list[RegionEvidence] = []
    handled: set[int] = set()
    for index, candidate in enumerate(candidates):
        if index in handled:
            continue
        if candidate.kind not in {"approval", "approval_staff"} or not candidate.rect_list:
            deduped.append(candidate)
            continue
        group = {index}
        expanded = True
        while expanded:
            expanded = False
            for other_index, other in enumerate(candidates):
                if other_index in group or other.kind != candidate.kind or other.page_index != candidate.page_index:
                    continue
                if other.rect_list and any(
                    _rect_lists_overlap(other.rect_list, candidates[group_index].rect_list)
                    for group_index in group
                ):
                    group.add(other_index)
                    expanded = True
        handled.update(group)
        grouped = [candidates[group_index] for group_index in sorted(group)]
        if len({_canonical_rects(item.rect_list) for item in grouped}) == 1:
            deduped.extend(grouped)
            continue
        highest = max(
            grouped,
            key=_approval_evidence_score,
        )
        rects = tuple(rect for item in grouped for rect in item.rect_list)
        deduped.append(replace(
            highest,
            rect_list=(PdfRect(
                min(rect.x0 for rect in rects),
                min(rect.y0 for rect in rects),
                max(rect.x1 for rect in rects),
                max(rect.y1 for rect in rects),
            ),),
        ))
    return deduped


def _detect(evidence: Iterable[RegionEvidence], required_kinds: tuple[str, ...], *, document_hash: str,
            analysis_revision: int, confidence_threshold: float) -> tuple[LayoutRegion, ...]:
    if not isinstance(document_hash, str) or _SHA256_RE.fullmatch(document_hash) is None:
        raise ValueError("document_hash must be a lowercase SHA-256")
    if isinstance(analysis_revision, bool) or not isinstance(analysis_revision, int) or analysis_revision < 1:
        raise ValueError("analysis_revision must be a positive integer")
    if (isinstance(confidence_threshold, bool) or not isinstance(confidence_threshold, (int, float))
            or not math.isfinite(confidence_threshold) or not 0 < confidence_threshold <= 1):
        raise ValueError("confidence_threshold must be a finite number in (0, 1]")
    candidates = sorted(
        (item for item in evidence if item.kind in required_kinds),
        key=lambda item: (item.page_index, required_kinds.index(item.kind)),
    )
    candidates = _dedupe_overlapping_approval_evidence(candidates)
    regions_by_key: dict[tuple[str, int, tuple[tuple[int, int, int, int], ...]], LayoutRegion] = {}
    for item in candidates:
        state, source, reasons = _state(
            item, confidence_threshold,
            document_hash=document_hash, analysis_revision=analysis_revision,
        )
        geometry_valid = _geometry_reason(item) is None
        rect_list = _canonical_rect_list(item.rect_list) if geometry_valid else ()
        canonical_rects = _canonical_rects(rect_list)
        semantic_key = (item.kind, item.page_index, canonical_rects)
        region_id = "region_" + _hash({"document_hash": document_hash, "analysis_revision": analysis_revision,
                                        "kind": item.kind, "page_index": item.page_index, "rect_list": canonical_rects})[:24]
        region = LayoutRegion(region_id, analysis_revision, item.kind, item.page_index, rect_list, state, source, reasons)
        existing = regions_by_key.get(semantic_key)
        if existing is not None:
            if (existing.state, existing.confirmation_source, existing.reason_codes) != (region.state, region.confirmation_source, region.reason_codes):
                state = min(
                    (existing.state, region.state),
                    key=("unconfirmed", "review_required", "confirmed").index,
                )
                confirmation_source = (
                    "user"
                    if state == "confirmed" and "user" in {existing.confirmation_source, region.confirmation_source}
                    else "automatic"
                    if state == "confirmed"
                    else None
                )
                regions_by_key[semantic_key] = LayoutRegion(
                    existing.region_id,
                    existing.analysis_revision,
                    existing.kind,
                    existing.page_index,
                    existing.rect_list,
                    state,
                    confirmation_source,
                    tuple(sorted({*existing.reason_codes, *region.reason_codes, "conflicting_region_evidence"})),
                )
            continue
        regions_by_key[semantic_key] = region
    return tuple(regions_by_key.values())


def detect_internal_review_regions(evidence: Iterable[RegionEvidence], *, document_hash: str,
                                   analysis_revision: int = 1, confidence_threshold: float = 0.85) -> tuple[LayoutRegion, ...]:
    return _detect(evidence, INTERNAL_REVIEW_REGION_KINDS, document_hash=document_hash,
                   analysis_revision=analysis_revision, confidence_threshold=confidence_threshold)


def detect_official_dispatch_regions(evidence: Iterable[RegionEvidence], *, document_hash: str,
                                     analysis_revision: int = 1, confidence_threshold: float = 0.85) -> tuple[LayoutRegion, ...]:
    return _detect(evidence, OFFICIAL_DISPATCH_REGION_KINDS, document_hash=document_hash,
                   analysis_revision=analysis_revision, confidence_threshold=confidence_threshold)
