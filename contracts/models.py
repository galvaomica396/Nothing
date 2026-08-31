from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


def _camel_case(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel_case,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


NonNegativeInt: TypeAlias = Annotated[int, Field(ge=0)]
NonEmptyText: TypeAlias = Annotated[str, Field(min_length=1)]
Hash: TypeAlias = Annotated[str, Field(pattern=r"^[a-fA-F0-9]{64}$")]
MaskingId: TypeAlias = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
OccurrenceId: TypeAlias = Annotated[str, Field(pattern=r"^occ_[a-f0-9]{24}$")]

class CoverageState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    INDETERMINATE = "indeterminate"


class PublicReviewProfile(StrEnum):
    INTERNAL_REVIEW = "internal_review"
    OFFICIAL_DISPATCH = "official_dispatch"
    MIXED = "mixed"


class AnalysisSegmentKind(StrEnum):
    INTERNAL_REVIEW = "internal_review"
    OFFICIAL_DISPATCH = "official_dispatch"
    ATTACHMENT = "attachment"
    UNKNOWN = "unknown"
    LEGAL = "legal"
    MIXED = "mixed"
    COMMON = "common"


class AnalysisSegmentState(StrEnum):
    CONFIRMED = "confirmed"
    REVIEW_REQUIRED = "review_required"
    USER_CONFIRMED = "user_confirmed"


class BoundarySegmentKind(StrEnum):
    INTERNAL_REVIEW = "internal_review"
    OFFICIAL_DISPATCH = "official_dispatch"
    ATTACHMENT = "attachment"
    LEGAL = "legal"


class AnalysisRegionKind(StrEnum):
    APPROVAL = "approval"
    HEADER_META = "header_meta"
    LABELED_STAFF = "labeled_staff"
    RECIPIENT_REFERENCE = "recipient_reference"
    SENDER_INSTITUTION = "sender_institution"
    APPROVAL_STAFF = "approval_staff"
    DISPATCH_METADATA = "dispatch_metadata"
    FOOTER_CONTACT = "footer_contact"


class AnalysisRegionState(StrEnum):
    CONFIRMED = "confirmed"
    REVIEW_REQUIRED = "review_required"
    UNCONFIRMED = "unconfirmed"
    USER_CONFIRMED = "user_confirmed"


class OccurrenceAction(StrEnum):
    MASK = "mask"
    EXCLUDE = "exclude"
    REVIEW = "review"


class OccurrenceState(StrEnum):
    CONFIRMED = "confirmed"
    REVIEW_REQUIRED = "review_required"
    USER_CONFIRMED = "user_confirmed"


class ReviewKind(StrEnum):
    NAME = "name"
    INSTITUTION = "institution"
    ACKNOWLEDGE = "acknowledge"
    BOUNDARY = "boundary"
    OCR = "ocr"
    REGION_GEOMETRY = "region_geometry"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class PdfPointsTopLeftRect(ContractModel):
    x0: float
    y0: float
    x1: float
    y1: float


class ThresholdArtifactV1(ContractModel):
    version: NonEmptyText
    content_hash: Hash
    auto_mask_threshold: Annotated[float, Field(ge=0, le=1)]
    review_threshold: Annotated[float, Field(ge=0, le=1)]


class ApprovalCoverage(ContractModel):
    schema_version: Literal[1]
    state: CoverageState
    signer_count: NonNegativeInt
    protected_neighbor_count: NonNegativeInt


class RequiredRegionCoverageKind(ContractModel):
    kind: AnalysisRegionKind
    state: CoverageState


class RequiredRegionCoverage(ContractModel):
    schema_version: Literal[1]
    profile: PublicReviewProfile
    kinds: tuple[RequiredRegionCoverageKind, ...]
    blocking: bool


class AnalysisSegmentV1(ContractModel):
    segment_id: MaskingId
    analysis_revision: NonNegativeInt
    page_start: NonNegativeInt
    page_end: NonNegativeInt
    kind: AnalysisSegmentKind
    state: AnalysisSegmentState
    common_only: bool
    source: NonEmptyText


class AnalysisRegionV1(ContractModel):
    region_id: MaskingId
    segment_id: MaskingId
    analysis_revision: NonNegativeInt
    page: NonNegativeInt
    rects: tuple[PdfPointsTopLeftRect, ...]
    kind: AnalysisRegionKind
    state: AnalysisRegionState
    confirmation_source: Literal["automatic", "user"] | None
    reason_codes: tuple[NonEmptyText, ...]
    source: NonEmptyText


class AnalysisOccurrenceV1(ContractModel):
    occurrence_id: OccurrenceId
    segment_id: MaskingId
    region_id: MaskingId | None
    analysis_revision: NonNegativeInt
    page: NonNegativeInt
    rects: tuple[PdfPointsTopLeftRect, ...]
    tag: NonEmptyText
    category: NonEmptyText
    value_hash: Hash
    expected_text_hash: Hash
    source: NonEmptyText
    policy: NonEmptyText
    proposed_action: OccurrenceAction
    state: OccurrenceState
    provenance: NonEmptyText


class ReviewItemV1(ContractModel):
    review_id: MaskingId
    analysis_revision: NonNegativeInt
    kind: ReviewKind
    target_id: MaskingId
    page_start: NonNegativeInt
    page_end: NonNegativeInt
    status: ReviewStatus
    reason_codes: tuple[NonEmptyText, ...]
    requires_acknowledgment: bool
    common_only: bool
    provenance: NonEmptyText


class ManualActionV1(ContractModel):
    action_id: MaskingId
    analysis_revision: NonNegativeInt
    page: NonNegativeInt
    rects: tuple[PdfPointsTopLeftRect, ...]
    protected_neighbor_refs: tuple[PdfPointsTopLeftRect, ...]
    mode: Literal["mask", "restore"]
    source_kind: Literal["text_pdf", "scan"]
    linked_occurrence_id: OccurrenceId | None
    expected_text_hash: Hash | None
    restore_authorization_hash: Hash | None = None


class FinalizeSaveWarning(ContractModel):
    kind: NonEmptyText
    target_id: MaskingId | None
    category: NonEmptyText
    page_start: NonNegativeInt
    page_end: NonNegativeInt
    reason_codes: tuple[NonEmptyText, ...]


class FinalizeSaveConfirmation(ContractModel):
    status: Literal["not_required", "user_confirmed"]
    unresolved_reviews: tuple[FinalizeSaveWarning, ...]


class RestoreAuthorizationSummary(ContractModel):
    action_id_hash: Hash
    target_occurrence_id_hash: Hash
    authorization_event: NonEmptyText


class AnalysisManifestV1(ContractModel):
    manifest_version: Literal[1]
    run_id: MaskingId
    original_document_hash: Hash
    analysis_revision: NonNegativeInt
    manifest_hash: Hash
    profile: PublicReviewProfile
    policy_version: NonEmptyText
    options_version: NonEmptyText
    options_hash: Hash
    threshold_version: NonEmptyText
    threshold_hash: Hash
    threshold_artifact: ThresholdArtifactV1
    coordinate_space: Literal["pdf_points_top_left"]
    approval_coverage: ApprovalCoverage
    required_region_coverage: RequiredRegionCoverage
    segments: tuple[AnalysisSegmentV1, ...]
    regions: tuple[AnalysisRegionV1, ...]
    occurrences: tuple[AnalysisOccurrenceV1, ...]
    review_items: tuple[ReviewItemV1, ...]
    manual_actions: tuple[ManualActionV1, ...]


class FinalizeMaskingRunResult(ContractModel):
    run_id: MaskingId
    analysis_revision: NonNegativeInt
    manifest_hash: Hash
    final_path: NonEmptyText
    final_hash: Hash
    final_hash_attested: Literal[True]
    occurrence_count: NonNegativeInt
    applied_mask_count: NonNegativeInt
    manual_mask_count: NonNegativeInt
    restore_count: NonNegativeInt
    effective_mask_count: NonNegativeInt
    restore_authorization: RestoreAuthorizationSummary
    save_confirmation: FinalizeSaveConfirmation
    status: Literal["promoted"]


class NameReviewResolution(ContractModel):
    kind: Literal["name"]
    action: Literal["mask", "exclude"]


class InstitutionReviewResolution(ContractModel):
    kind: Literal["institution"]
    action: Literal["mask", "exclude"]


class AcknowledgeReviewResolution(ContractModel):
    kind: Literal["acknowledge"]
    acknowledged: Literal[True]


class BoundaryReviewResolution(ContractModel):
    kind: Literal["boundary"]
    page_start: NonNegativeInt
    page_end: NonNegativeInt
    segment_kind: BoundarySegmentKind


class OcrReviewResolution(ContractModel):
    kind: Literal["ocr"]
    accepted: bool


class RegionGeometryReviewResolution(ContractModel):
    kind: Literal["region_geometry"]
    rects: tuple[PdfPointsTopLeftRect, ...]


ReviewResolution: TypeAlias = Annotated[
    NameReviewResolution
    | InstitutionReviewResolution
    | AcknowledgeReviewResolution
    | BoundaryReviewResolution
    | OcrReviewResolution
    | RegionGeometryReviewResolution,
    Field(discriminator="kind"),
]


class ResolveMaskingReviewRequest(ContractModel):
    run_id: MaskingId
    analysis_revision: NonNegativeInt
    manifest_hash: Hash
    review_id: MaskingId
    resolution: ReviewResolution


CONTRACT_MODELS = (
    AnalysisManifestV1,
    FinalizeMaskingRunResult,
    ResolveMaskingReviewRequest,
)


def contract_schemas() -> dict[str, dict[str, object]]:
    schemas = {
        model.__name__: model.model_json_schema(by_alias=True)
        for model in CONTRACT_MODELS
    }
    schemas["ReviewResolution"] = TypeAdapter(ReviewResolution).json_schema(by_alias=True)
    return dict(sorted(schemas.items()))
