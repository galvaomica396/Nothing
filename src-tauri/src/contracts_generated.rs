use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AnalysisOccurrenceV1 {
    pub analysis_revision: u64,
    pub category: String,
    pub expected_text_hash: String,
    pub occurrence_id: String,
    pub page: u64,
    pub policy: String,
    pub proposed_action: OccurrenceAction,
    pub provenance: String,
    pub rects: Vec<PdfPointsTopLeftRect>,
    pub region_id: Option<String>,
    pub segment_id: String,
    pub source: String,
    pub state: OccurrenceState,
    pub tag: String,
    pub value_hash: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AnalysisRegionKind {
    Approval,
    HeaderMeta,
    LabeledStaff,
    RecipientReference,
    SenderInstitution,
    ApprovalStaff,
    DispatchMetadata,
    FooterContact,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AnalysisRegionState {
    Confirmed,
    ReviewRequired,
    Unconfirmed,
    UserConfirmed,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AnalysisRegionV1 {
    pub analysis_revision: u64,
    pub confirmation_source: Option<String>,
    pub kind: AnalysisRegionKind,
    pub page: u64,
    pub reason_codes: Vec<String>,
    pub rects: Vec<PdfPointsTopLeftRect>,
    pub region_id: String,
    pub segment_id: String,
    pub source: String,
    pub state: AnalysisRegionState,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AnalysisSegmentKind {
    InternalReview,
    OfficialDispatch,
    Attachment,
    Unknown,
    Legal,
    Mixed,
    Common,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AnalysisSegmentState {
    Confirmed,
    ReviewRequired,
    UserConfirmed,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AnalysisSegmentV1 {
    pub analysis_revision: u64,
    pub common_only: bool,
    pub kind: AnalysisSegmentKind,
    pub page_end: u64,
    pub page_start: u64,
    pub segment_id: String,
    pub source: String,
    pub state: AnalysisSegmentState,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ApprovalCoverage {
    pub protected_neighbor_count: u64,
    pub schema_version: u64,
    pub signer_count: u64,
    pub state: CoverageState,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BoundarySegmentKind {
    InternalReview,
    OfficialDispatch,
    Attachment,
    Legal,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CoverageState {
    Present,
    Absent,
    Indeterminate,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct FinalizeSaveConfirmation {
    pub status: String,
    pub unresolved_reviews: Vec<FinalizeSaveWarning>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct FinalizeSaveWarning {
    pub category: String,
    pub kind: String,
    pub page_end: u64,
    pub page_start: u64,
    pub reason_codes: Vec<String>,
    pub target_id: Option<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ManualActionV1 {
    pub action_id: String,
    pub analysis_revision: u64,
    pub expected_text_hash: Option<String>,
    pub linked_occurrence_id: Option<String>,
    pub mode: String,
    pub page: u64,
    pub protected_neighbor_refs: Vec<PdfPointsTopLeftRect>,
    pub rects: Vec<PdfPointsTopLeftRect>,
    pub restore_authorization_hash: Option<Option<String>>,
    pub source_kind: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OccurrenceAction {
    Mask,
    Exclude,
    Review,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OccurrenceState {
    Confirmed,
    ReviewRequired,
    UserConfirmed,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PdfPointsTopLeftRect {
    pub x0: f64,
    pub x1: f64,
    pub y0: f64,
    pub y1: f64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PublicReviewProfile {
    InternalReview,
    OfficialDispatch,
    Mixed,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RequiredRegionCoverage {
    pub blocking: bool,
    pub kinds: Vec<RequiredRegionCoverageKind>,
    pub profile: PublicReviewProfile,
    pub schema_version: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RequiredRegionCoverageKind {
    pub kind: AnalysisRegionKind,
    pub state: CoverageState,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RestoreAuthorizationSummary {
    pub action_id_hash: String,
    pub authorization_event: String,
    pub target_occurrence_id_hash: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ReviewItemV1 {
    pub analysis_revision: u64,
    pub common_only: bool,
    pub kind: ReviewKind,
    pub page_end: u64,
    pub page_start: u64,
    pub provenance: String,
    pub reason_codes: Vec<String>,
    pub requires_acknowledgment: bool,
    pub review_id: String,
    pub status: ReviewStatus,
    pub target_id: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewKind {
    Name,
    Institution,
    Acknowledge,
    Boundary,
    Ocr,
    RegionGeometry,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewStatus {
    Pending,
    Resolved,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ThresholdArtifactV1 {
    pub auto_mask_threshold: f64,
    pub content_hash: String,
    pub review_threshold: f64,
    pub version: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AnalysisManifestV1 {
    pub analysis_revision: u64,
    pub approval_coverage: ApprovalCoverage,
    pub coordinate_space: String,
    pub manifest_hash: String,
    pub manifest_version: u64,
    pub manual_actions: Vec<ManualActionV1>,
    pub occurrences: Vec<AnalysisOccurrenceV1>,
    pub options_hash: String,
    pub options_version: String,
    pub original_document_hash: String,
    pub policy_version: String,
    pub profile: PublicReviewProfile,
    pub regions: Vec<AnalysisRegionV1>,
    pub required_region_coverage: RequiredRegionCoverage,
    pub review_items: Vec<ReviewItemV1>,
    pub run_id: String,
    pub segments: Vec<AnalysisSegmentV1>,
    pub threshold_artifact: ThresholdArtifactV1,
    pub threshold_hash: String,
    pub threshold_version: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct FinalizeMaskingRunResult {
    pub analysis_revision: u64,
    pub applied_mask_count: u64,
    pub effective_mask_count: u64,
    pub final_hash: String,
    pub final_hash_attested: bool,
    pub final_path: String,
    pub manifest_hash: String,
    pub manual_mask_count: u64,
    pub occurrence_count: u64,
    pub restore_authorization: RestoreAuthorizationSummary,
    pub restore_count: u64,
    pub run_id: String,
    pub save_confirmation: FinalizeSaveConfirmation,
    pub status: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ResolveMaskingReviewRequest {
    pub analysis_revision: u64,
    pub manifest_hash: String,
    pub resolution: ReviewResolution,
    pub review_id: String,
    pub run_id: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ReviewResolution {
    Name {
        action: String,
    },
    Institution {
        action: String,
    },
    Acknowledge {
        acknowledged: bool,
    },
    Boundary {
        #[serde(rename = "pageEnd")]
        page_end: u64,
        #[serde(rename = "pageStart")]
        page_start: u64,
        #[serde(rename = "segmentKind")]
        segment_kind: BoundarySegmentKind,
    },
    Ocr {
        accepted: bool,
    },
    RegionGeometry {
        rects: Vec<PdfPointsTopLeftRect>,
    },
}
