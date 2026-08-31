use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

pub const MANIFEST_VERSION: u32 = 1;
pub const POLICY_VERSION: &str = "masking-policy-v1";
pub const THRESHOLD_VERSION: &str = "thresholds-v2";
pub const OPTIONS_VERSION: &str = "options-v2";
const MAX_TRUSTED_ENTITIES: usize = 10_000;
const MAX_ACTIVE_SESSIONS: usize = 64;
const MAX_COMPLETED_TOMBSTONES: usize = 64;
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ThresholdArtifactV1 {
    pub version: String,
    pub content_hash: String,
    pub auto_mask_threshold: f64,
    pub review_threshold: f64,
}
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AnalysisManifestV1 {
    pub manifest_version: u32,
    pub run_id: String,
    pub original_document_hash: String,
    pub analysis_revision: u64,
    pub manifest_hash: String,
    pub profile: String,
    pub policy_version: String,
    pub options_version: String,
    pub options_hash: String,
    pub threshold_version: String,
    pub threshold_hash: String,
    pub threshold_artifact: ThresholdArtifactV1,
    pub coordinate_space: String,
    pub approval_coverage: ApprovalCoverage,
    pub required_region_coverage: RequiredRegionCoverage,
    pub segments: Vec<AnalysisSegment>,
    pub regions: Vec<AnalysisRegion>,
    pub occurrences: Vec<AnalysisOccurrence>,
    pub review_items: Vec<ReviewItem>,
    pub manual_actions: Vec<ManualAction>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CoverageState {
    Present,
    Absent,
    Indeterminate,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ApprovalCoverage {
    pub schema_version: u32,
    pub state: CoverageState,
    pub signer_count: usize,
    pub protected_neighbor_count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RequiredRegionCoverageKind {
    pub kind: String,
    pub state: CoverageState,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RequiredRegionCoverage {
    pub schema_version: u32,
    pub profile: String,
    pub kinds: Vec<RequiredRegionCoverageKind>,
    pub blocking: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AnalysisSegment {
    pub segment_id: String,
    pub analysis_revision: u64,
    pub page_start: u32,
    pub page_end: u32,
    pub kind: String,
    pub state: String,
    pub common_only: bool,
    pub source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AnalysisRegion {
    pub region_id: String,
    pub segment_id: String,
    pub analysis_revision: u64,
    pub page: u32,
    pub rects: Vec<Rect>,
    pub kind: String,
    pub state: String,
    pub confirmation_source: Option<String>,
    pub reason_codes: Vec<String>,
    pub source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AnalysisOccurrence {
    pub occurrence_id: String,
    pub segment_id: String,
    pub region_id: Option<String>,
    pub analysis_revision: u64,
    pub page: u32,
    pub rects: Vec<Rect>,
    pub tag: String,
    pub category: String,
    pub value_hash: String,
    pub expected_text_hash: String,
    pub source: String,
    pub policy: String,
    pub proposed_action: String,
    pub state: String,
    pub provenance: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Rect {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ReviewKind {
    Name,
    Institution,
    Acknowledge,
    Boundary,
    Ocr,
    RegionGeometry,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewItem {
    pub review_id: String,
    pub analysis_revision: u64,
    pub kind: ReviewKind,
    pub target_id: Option<String>,
    pub page_start: u32,
    pub page_end: u32,
    pub status: String,
    pub reason_codes: Vec<String>,
    pub requires_acknowledgment: bool,
    pub common_only: bool,
    pub provenance: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ManualAction {
    pub action_id: String,
    pub analysis_revision: u64,
    pub page: u32,
    pub rects: Vec<Rect>,
    pub mode: String,
    pub source_kind: String,
    pub linked_occurrence_id: Option<String>,
    pub expected_text_hash: Option<String>,
    pub protected_neighbor_refs: Vec<Rect>,
    #[serde(default)]
    pub restore_authorization_hash: Option<String>,
}
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(deny_unknown_fields)]
pub struct ManualActionV1Request {
    pub run_id: String,
    pub analysis_revision: u64,
    pub manifest_hash: String,
    pub page: u32,
    pub rects: Vec<Rect>,
    pub mode: String,
    pub source_kind: String,
    pub linked_occurrence_id: Option<String>,
    pub target_region_id: Option<String>,
    pub expected_text_hash: Option<String>,
    pub protected_neighbor_refs: Vec<Rect>,
    pub restore_capability: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(deny_unknown_fields)]
pub struct RestoreCapabilityRequest {
    pub run_id: String,
    pub analysis_revision: u64,
    pub manifest_hash: String,
    pub occurrence_id: String,
    pub rects: Vec<Rect>,
    pub expected_text_hash: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RestoreCapabilityResponse {
    pub capability: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(deny_unknown_fields)]
pub struct AnalyzeMaskingRunRequest {
    pub input_file: String,
    pub profile: String,
    #[serde(default)]
    pub options: serde_json::Value,
}
#[derive(Debug, Clone, Serialize)]
struct ProfileAuthority {
    document_sha256: String,
    analysis_revision: u64,
    profile: String,
    decision_code: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PublicMaskingOptions {
    rrn: bool,
    phone: bool,
    business_reg: bool,
    name: bool,
    address: bool,
    place: bool,
    legal_party: bool,
    company: bool,
    court: bool,
    case_title: bool,
    case_number: bool,
    law_firm: bool,
    attorney: bool,
    approval_line: bool,
    region_context: bool,
    doc_meta: bool,
    email: bool,
    pdf_redaction: bool,
    custom_keywords: String,
    extract_engine: String,
    profile: String,
    output_artifacts: String,
    display_mode: String,
    deidentification_policy: String,
    region_scope: String,
    custom_regions: String,
    return_text_preview: bool,
    auto_mask_threshold: f64,
    review_threshold: f64,
    #[serde(default, skip_deserializing, skip_serializing_if = "Option::is_none")]
    profile_authority: Option<ProfileAuthority>,
}

pub fn canonical_public_options(
    options: serde_json::Value,
    requested_profile: &str,
) -> Result<serde_json::Value, String> {
    let mut options: PublicMaskingOptions =
        serde_json::from_value(options).map_err(|_| safe_error("OPTIONS_INVALID"))?;
    let profile = canonical_profile(&options.profile)?;
    if profile != canonical_profile(requested_profile)? {
        return Err(safe_error("OPTIONS_INVALID"));
    }
    validate_public_options(&options)?;
    options.profile = profile.to_string();
    serde_json::to_value(options).map_err(|_| safe_error("OPTIONS_INVALID"))
}

pub fn with_server_profile_authority(
    options: serde_json::Value,
    document_sha256: &str,
    analysis_revision: u64,
) -> Result<serde_json::Value, String> {
    let mut options: PublicMaskingOptions =
        serde_json::from_value(options).map_err(|_| safe_error("OPTIONS_INVALID"))?;
    let profile = canonical_profile(&options.profile)?;
    if analysis_revision == 0 {
        return Err(safe_error("OPTIONS_INVALID"));
    }
    match profile {
        "internal_review" | "official_dispatch" => {
            options.profile_authority = Some(ProfileAuthority {
                document_sha256: document_sha256.to_string(),
                analysis_revision,
                profile: profile.to_string(),
                decision_code: "profile_confirmed".to_string(),
            });
        }
        "mixed" | "legal" => {}
        _ => return Err(safe_error("OPTIONS_INVALID")),
    }
    serde_json::to_value(options).map_err(|_| safe_error("OPTIONS_INVALID"))
}
const MAX_OPTION_TEXT_BYTES: usize = 16 * 1024;

fn validate_public_options(options: &PublicMaskingOptions) -> Result<(), String> {
    if !matches!(
        options.output_artifacts.as_str(),
        "pdf_safe_report" | "pdf_masked_txt_safe_report"
    ) || !matches!(
        options.deidentification_policy.as_str(),
        "token" | "partial" | "pseudonym"
    ) || !matches!(
        options.display_mode.as_str(),
        "black" | "label_en" | "label_ko" | "pseudonym"
    ) || !matches!(
        options.extract_engine.as_str(),
        "auto" | "marker" | "paddle" | "pymupdf" | "pypdf"
    ) || !matches!(
        options.region_scope.as_str(),
        "national" | "seoul" | "custom"
    ) || (options.region_scope == "custom") != !options.custom_regions.trim().is_empty()
        || options.custom_keywords.as_bytes().len() > MAX_OPTION_TEXT_BYTES
        || options.custom_regions.as_bytes().len() > MAX_OPTION_TEXT_BYTES
        || options.return_text_preview
        || !options.auto_mask_threshold.is_finite()
        || !options.review_threshold.is_finite()
        || !(0.0..=1.0).contains(&options.auto_mask_threshold)
        || !(0.0..=1.0).contains(&options.review_threshold)
        || options.auto_mask_threshold < options.review_threshold
    {
        return Err(safe_error("OPTIONS_INVALID"));
    }
    Ok(())
}

fn threshold_artifact(
    auto_mask_threshold: f64,
    review_threshold: f64,
) -> Result<ThresholdArtifactV1, String> {
    let material = serde_json::json!({
        "auto_threshold": auto_mask_threshold,
        "policy_version": POLICY_VERSION,
        "review_threshold": review_threshold,
    });
    let content_hash = canonical_json_hash(&material)?;
    Ok(ThresholdArtifactV1 {
        version: THRESHOLD_VERSION.to_string(),
        content_hash,
        auto_mask_threshold,
        review_threshold,
    })
}

fn threshold_artifact_hash(
    auto_mask_threshold: f64,
    review_threshold: f64,
) -> Result<String, String> {
    Ok(threshold_artifact(auto_mask_threshold, review_threshold)?.content_hash)
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(deny_unknown_fields)]
pub struct FinalizeMaskingRunRequest {
    pub run_id: String,
    pub analysis_revision: u64,
    pub manifest_hash: String,
    pub destination: String,
    pub save_token: String,
    pub warnings_confirmed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FinalizeSaveWarning {
    pub kind: String,
    pub target_id: Option<String>,
    pub category: String,
    pub page_start: u32,
    pub page_end: u32,
    pub reason_codes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FinalizeSaveConfirmation {
    pub status: String,
    pub unresolved_reviews: Vec<FinalizeSaveWarning>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FinalizeMaskingRunResult {
    pub run_id: String,
    pub analysis_revision: u64,
    pub manifest_hash: String,
    pub final_path: String,
    pub final_hash: String,
    pub final_hash_attested: bool,
    pub occurrence_count: usize,
    pub applied_mask_count: usize,
    pub manual_mask_count: usize,
    pub restore_count: usize,
    pub effective_mask_count: usize,
    pub restore_authorization: RestoreAuthorizationSummary,
    pub save_confirmation: FinalizeSaveConfirmation,
    pub status: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RestoreAuthorizationSummary {
    pub action_id_hash: String,
    pub target_occurrence_id_hash: String,
    pub authorization_event: String,
}
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(deny_unknown_fields)]
pub struct ResolveMaskingReviewRequest {
    pub run_id: String,
    pub analysis_revision: u64,
    pub manifest_hash: String,
    pub review_id: String,
    pub resolution: ReviewResolution,
}

#[derive(Debug, Clone, Deserialize)]
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
        #[serde(rename = "pageStart")]
        page_start: u32,
        #[serde(rename = "pageEnd")]
        page_end: u32,
        #[serde(rename = "segmentKind")]
        segment_kind: String,
    },
    Ocr {
        accepted: bool,
    },
    RegionGeometry {
        rects: Vec<Rect>,
    },
}

impl ReviewResolution {
    fn kind(&self) -> ReviewKind {
        match self {
            Self::Name { .. } => ReviewKind::Name,
            Self::Institution { .. } => ReviewKind::Institution,
            Self::Acknowledge { .. } => ReviewKind::Acknowledge,
            Self::Boundary { .. } => ReviewKind::Boundary,
            Self::Ocr { .. } => ReviewKind::Ocr,
            Self::RegionGeometry { .. } => ReviewKind::RegionGeometry,
        }
    }

    fn creates_revision(&self) -> bool {
        matches!(self, Self::Boundary { .. } | Self::RegionGeometry { .. })
    }
}
#[derive(Debug, Clone)]
pub struct ReanalysisContext {
    pub original: std::path::PathBuf,
    pub options: serde_json::Value,
    pub profile: String,
    pub original_document_hash: String,
}

pub struct MaskingRunSessions {
    state: Mutex<SessionState>,
    manifest_hmac_key: [u8; 32],
}

impl Default for MaskingRunSessions {
    fn default() -> Self {
        let mut manifest_hmac_key = [0_u8; 32];
        getrandom::getrandom(&mut manifest_hmac_key)
            .expect("operating system randomness is required for masking sessions");
        Self {
            state: Mutex::new(SessionState::default()),
            manifest_hmac_key,
        }
    }
}

#[derive(Default)]
struct SessionState {
    sessions: HashMap<String, SessionRecord>,
    restore_capabilities: HashMap<String, RestoreCapabilityRecord>,
    completed_tombstones: VecDeque<(String, &'static str)>,
    next_run: u64,
}

#[derive(Clone)]
struct RestoreCapabilityRecord {
    run_id: String,
    original_document_hash: String,
    issued_revision: u64,
    issued_manifest_hash: String,
    occurrence_id: String,
    target_value_hash: String,
    page: u32,
    rects: Vec<Rect>,
    expected_text_hash: String,
    authorization_event: String,
    claimed: bool,
}

struct SessionRecord {
    manifest: AnalysisManifestV1,
    original: Option<std::path::PathBuf>,
    options: Option<serde_json::Value>,
    lifecycle: SessionLifecycle,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum FinalizeDisposition {
    RetryReady,
    CleanupRequired,
    Consumed,
    PublishedIndeterminate,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum SessionLifecycle {
    Ready,
    Finalizing,
    CleanupRequired,
    Completed,
}

impl MaskingRunSessions {
    pub fn create(
        &self,
        original_bytes: &[u8],
        profile: &str,
        options: serde_json::Value,
    ) -> Result<AnalysisManifestV1, String> {
        let options = canonical_public_options(options, profile)?;
        let profile = canonical_profile(profile)?;
        let original_document_hash = sha256_hex(original_bytes);
        let sequence = {
            let mut state = self
                .state
                .lock()
                .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
            state.next_run += 1;
            state.next_run
        };
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|value| value.as_nanos())
            .unwrap_or(0);
        let run_id = sha256_hex(format!("{original_document_hash}:{sequence}:{nonce}").as_bytes());
        let mut manifest = AnalysisManifestV1 {
            manifest_version: MANIFEST_VERSION,
            run_id: format!("run_{}", &run_id[..32]),
            original_document_hash,
            analysis_revision: 1,
            manifest_hash: String::new(),
            profile: profile.to_string(),
            policy_version: POLICY_VERSION.to_string(),
            options_version: OPTIONS_VERSION.to_string(),
            options_hash: canonical_json_hash(&options)?,
            threshold_version: THRESHOLD_VERSION.to_string(),
            threshold_hash: threshold_artifact_hash(
                options["auto_mask_threshold"]
                    .as_f64()
                    .ok_or_else(|| safe_error("OPTIONS_INVALID"))?,
                options["review_threshold"]
                    .as_f64()
                    .ok_or_else(|| safe_error("OPTIONS_INVALID"))?,
            )?,
            threshold_artifact: threshold_artifact(
                options["auto_mask_threshold"]
                    .as_f64()
                    .ok_or_else(|| safe_error("OPTIONS_INVALID"))?,
                options["review_threshold"]
                    .as_f64()
                    .ok_or_else(|| safe_error("OPTIONS_INVALID"))?,
            )?,
            coordinate_space: "pdf_points_top_left".to_string(),
            approval_coverage: ApprovalCoverage {
                schema_version: 1,
                state: CoverageState::Indeterminate,
                signer_count: 0,
                protected_neighbor_count: 0,
            },
            required_region_coverage: RequiredRegionCoverage {
                schema_version: 1,
                profile: profile.to_string(),
                kinds: required_region_kinds(profile)
                    .iter()
                    .map(|kind| RequiredRegionCoverageKind {
                        kind: (*kind).to_string(),
                        state: CoverageState::Indeterminate,
                    })
                    .collect(),
                blocking: true,
            },
            segments: Vec::new(),
            regions: Vec::new(),
            occurrences: Vec::new(),
            review_items: Vec::new(),
            manual_actions: Vec::new(),
        };
        refresh_manifest_hash_with_key(&mut manifest, &self.manifest_hmac_key)?;
        Ok(manifest)
    }
    pub fn create_from_trusted(
        &self,
        original_bytes: &[u8],
        profile: &str,
        options: serde_json::Value,
        trusted: &serde_json::Value,
    ) -> Result<AnalysisManifestV1, String> {
        let page_count =
            pdf_page_count(original_bytes).ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?;
        if !trusted_object_fields(
            trusted,
            &[
                "schema_version",
                "original_document_hash",
                "profile",
                "coordinate_space",
                "analysis_revision",
                "segments",
                "regions",
                "occurrences",
                "manual_actions",
                "review_items",
                "policy_version",
                "options_version",
                "options_hash",
                "threshold_version",
                "threshold_hash",
                "threshold_artifact",
                "approval_coverage",
                "required_region_coverage",
            ],
        ) {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        let source_hash = sha256_hex(original_bytes);
        if trusted
            .get("original_document_hash")
            .and_then(serde_json::Value::as_str)
            != Some(source_hash.as_str())
            || trusted
                .get("schema_version")
                .and_then(serde_json::Value::as_u64)
                != Some(MANIFEST_VERSION as u64)
            || trusted.get("profile").and_then(serde_json::Value::as_str)
                != Some(canonical_profile(profile)?)
            || trusted
                .get("coordinate_space")
                .and_then(serde_json::Value::as_str)
                != Some("pdf_points_top_left")
            || trusted.get("outputs").is_some()
            || trusted.get("extracted_text").is_some()
            || contains_raw_field(trusted)
        {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        let mut manifest = self.create(original_bytes, profile, options)?;
        if trusted
            .get("policy_version")
            .and_then(serde_json::Value::as_str)
            != Some(manifest.policy_version.as_str())
            || trusted
                .get("options_version")
                .and_then(serde_json::Value::as_str)
                != Some(manifest.options_version.as_str())
            || trusted
                .get("options_hash")
                .and_then(serde_json::Value::as_str)
                != Some(manifest.options_hash.as_str())
        {
            return Err(safe_error("TRUSTED_MANIFEST_AUTHORITY_MISMATCH"));
        }
        let segments = trusted_array(trusted, "segments")?;
        let regions = trusted_array(trusted, "regions")?;
        let occurrences = trusted_array(trusted, "occurrences")?;
        let manual_actions = trusted_array(trusted, "manual_actions")?;
        let reviews = trusted_array(trusted, "review_items")?;
        if [segments, regions, occurrences, manual_actions, reviews]
            .iter()
            .any(|items| items.len() > MAX_TRUSTED_ENTITIES)
        {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        manifest.analysis_revision = trusted
            .get("analysis_revision")
            .and_then(serde_json::Value::as_u64)
            .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?;
        if manifest.analysis_revision == 0 {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        let threshold = trusted
            .get("threshold_artifact")
            .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?;
        if !trusted_object_fields(
            threshold,
            &[
                "version",
                "content_hash",
                "auto_mask_threshold",
                "review_threshold",
            ],
        ) {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        let artifact = ThresholdArtifactV1 {
            version: trusted_code(threshold, "version")?,
            content_hash: trusted_hash(threshold, "content_hash")?,
            auto_mask_threshold: threshold
                .get("auto_mask_threshold")
                .and_then(serde_json::Value::as_f64)
                .filter(|value| value.is_finite() && (0.0..=1.0).contains(value))
                .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
            review_threshold: threshold
                .get("review_threshold")
                .and_then(serde_json::Value::as_f64)
                .filter(|value| value.is_finite() && (0.0..=1.0).contains(value))
                .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
        };
        if artifact.auto_mask_threshold < artifact.review_threshold
            || artifact
                != threshold_artifact(artifact.auto_mask_threshold, artifact.review_threshold)?
            || trusted
                .get("threshold_version")
                .and_then(serde_json::Value::as_str)
                != Some(artifact.version.as_str())
            || trusted
                .get("threshold_hash")
                .and_then(serde_json::Value::as_str)
                != Some(artifact.content_hash.as_str())
        {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        manifest.threshold_version = artifact.version.clone();
        manifest.threshold_hash = artifact.content_hash.clone();
        manifest.threshold_artifact = artifact;
        manifest.approval_coverage = trusted_approval_coverage(trusted)?;
        manifest.required_region_coverage = trusted_required_region_coverage(trusted)?;
        if !valid_coverage(&manifest) {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        manifest.segments = segments
            .iter()
            .map(|v| {
                Ok(AnalysisSegment {
                    segment_id: trusted_id(v, "segment_id")?,
                    analysis_revision: trusted_u64(v, "analysis_revision")?,
                    page_start: trusted_page(v, "page_start")?,
                    page_end: trusted_page(v, "page_end")?,
                    kind: trusted_code(v, "kind")?,
                    state: trusted_state(
                        v,
                        "state",
                        &["confirmed", "review_required", "user_confirmed"],
                    )?,
                    common_only: v
                        .get("common_only")
                        .and_then(serde_json::Value::as_bool)
                        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
                    source: trusted_code(v, "source")?,
                })
            })
            .collect::<Result<_, String>>()?;
        if segments.iter().any(|value| {
            !trusted_object_fields(
                value,
                &[
                    "segment_id",
                    "analysis_revision",
                    "page_start",
                    "page_end",
                    "kind",
                    "state",
                    "common_only",
                    "source",
                ],
            )
        }) {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        validate_segments(&manifest.segments, manifest.analysis_revision, page_count)?;
        if manifest.segments.iter().any(|segment| {
            segment.segment_id
                != segment_fingerprint(
                    &manifest.original_document_hash,
                    manifest.analysis_revision,
                    segment,
                )
        }) {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        manifest.regions = regions
            .iter()
            .map(|v| {
                Ok(AnalysisRegion {
                    region_id: trusted_id(v, "region_id")?,
                    segment_id: trusted_id(v, "segment_id")?,
                    analysis_revision: trusted_u64(v, "analysis_revision")?,
                    page: trusted_page(v, "page")?,
                    rects: trusted_rects(v)?,
                    kind: trusted_code(v, "kind")?,
                    state: trusted_state(
                        v,
                        "state",
                        &[
                            "confirmed",
                            "review_required",
                            "unconfirmed",
                            "user_confirmed",
                        ],
                    )?,
                    confirmation_source: trusted_optional_code(v, "confirmation_source")?,
                    reason_codes: trusted_codes(v, "reason_codes")?,
                    source: trusted_code(v, "source")?,
                })
            })
            .collect::<Result<_, String>>()?;
        if regions.iter().any(|value| {
            !trusted_object_fields(
                value,
                &[
                    "region_id",
                    "segment_id",
                    "analysis_revision",
                    "page",
                    "rects",
                    "kind",
                    "state",
                    "confirmation_source",
                    "reason_codes",
                    "source",
                ],
            )
        }) {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        for region in &manifest.regions {
            if region.analysis_revision != manifest.analysis_revision
                || !manifest.segments.iter().any(|segment| {
                    segment.segment_id == region.segment_id
                        && region.page >= segment.page_start
                        && region.page <= segment.page_end
                })
            {
                return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
            }
        }
        if manifest.regions.iter().any(|region| {
            region.region_id
                != region_fingerprint(
                    &manifest.original_document_hash,
                    manifest.analysis_revision,
                    region,
                )
        }) {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        manifest.occurrences = occurrences
            .iter()
            .map(|v| {
                let trusted_occurrence_id = trusted_id(v, "occurrence_id")?;
                let segment_id = trusted_id(v, "segment_id")?;
                let region_id = trusted_optional_id(v, "region_id")?;
                let analysis_revision = trusted_u64(v, "analysis_revision")?;
                let page = trusted_page(v, "page")?;
                let rects = trusted_rects(v)?;
                let tag = trusted_code(v, "tag")?;
                let category = trusted_code(v, "category")?;
                let value_hash = trusted_hash(v, "value_hash")?;
                let expected_text_hash = trusted_hash(v, "expected_text_hash")?;
                let source = trusted_code(v, "source")?;
                let policy = trusted_code(v, "policy")?;
                let proposed_action =
                    trusted_state(v, "proposed_action", &["mask", "exclude", "review"])?;
                let occurrence_id = occurrence_fingerprint_bound(
                    &manifest.original_document_hash,
                    analysis_revision,
                    &segment_id,
                    region_id.as_deref(),
                    page,
                    &rects,
                    &tag,
                    &category,
                    &value_hash,
                    &source,
                    &policy,
                    &proposed_action,
                );
                if trusted_occurrence_id != occurrence_id {
                    return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
                }
                Ok(AnalysisOccurrence {
                    occurrence_id,
                    segment_id,
                    region_id,
                    analysis_revision,
                    page,
                    rects,
                    tag,
                    category,
                    value_hash,
                    expected_text_hash,
                    source,
                    policy,
                    proposed_action,
                    state: trusted_state(
                        v,
                        "state",
                        &["confirmed", "review_required", "user_confirmed"],
                    )?,
                    provenance: trusted_code(v, "provenance")?,
                })
            })
            .collect::<Result<_, String>>()?;
        if occurrences.iter().any(|value| {
            !trusted_object_fields(
                value,
                &[
                    "occurrence_id",
                    "segment_id",
                    "region_id",
                    "analysis_revision",
                    "page",
                    "rects",
                    "tag",
                    "category",
                    "value_hash",
                    "expected_text_hash",
                    "source",
                    "policy",
                    "proposed_action",
                    "state",
                    "provenance",
                ],
            )
        }) {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        for occurrence in &manifest.occurrences {
            if occurrence.analysis_revision != manifest.analysis_revision
                || !manifest.segments.iter().any(|s| {
                    s.segment_id == occurrence.segment_id
                        && occurrence.page >= s.page_start
                        && occurrence.page <= s.page_end
                })
                || occurrence.region_id.as_ref().is_some_and(|id| {
                    !manifest.regions.iter().any(|r| {
                        &r.region_id == id
                            && r.segment_id == occurrence.segment_id
                            && r.page == occurrence.page
                    })
                })
            {
                return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
            }
        }
        manifest.review_items = reviews
            .iter()
            .map(|v| {
                Ok(ReviewItem {
                    review_id: trusted_id(v, "review_id")?,
                    analysis_revision: trusted_u64(v, "analysis_revision")?,
                    kind: serde_json::from_value(
                        v.get("kind")
                            .cloned()
                            .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
                    )
                    .map_err(|_| safe_error("TRUSTED_MANIFEST_INVALID"))?,
                    target_id: trusted_optional_id(v, "target_id")?,
                    page_start: trusted_page(v, "page_start")?,
                    page_end: trusted_page(v, "page_end")?,
                    status: trusted_state(v, "status", &["pending", "resolved"])?,
                    reason_codes: trusted_codes(v, "reason_codes")?,
                    requires_acknowledgment: v
                        .get("requires_acknowledgment")
                        .and_then(serde_json::Value::as_bool)
                        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
                    common_only: v
                        .get("common_only")
                        .and_then(serde_json::Value::as_bool)
                        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
                    provenance: trusted_code(v, "provenance")?,
                })
            })
            .collect::<Result<_, String>>()?;
        if reviews.iter().any(|value| {
            !trusted_object_fields(
                value,
                &[
                    "review_id",
                    "analysis_revision",
                    "kind",
                    "target_id",
                    "page_start",
                    "page_end",
                    "status",
                    "reason_codes",
                    "requires_acknowledgment",
                    "common_only",
                    "provenance",
                ],
            )
        }) {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        manifest.manual_actions = manual_actions
            .iter()
            .map(|v| {
                Ok(ManualAction {
                    action_id: trusted_id(v, "action_id")?,
                    analysis_revision: trusted_u64(v, "analysis_revision")?,
                    page: trusted_page(v, "page")?,
                    rects: trusted_rects(v)?,
                    mode: trusted_state(v, "mode", &["mask", "restore"])?,
                    source_kind: trusted_state(v, "source_kind", &["text_pdf", "scan"])?,
                    linked_occurrence_id: trusted_optional_id(v, "linked_occurrence_id")?,
                    expected_text_hash: match v.get("expected_text_hash") {
                        Some(serde_json::Value::Null) | None => None,
                        Some(_) => Some(trusted_hash(v, "expected_text_hash")?),
                    },
                    protected_neighbor_refs: trusted_rects_field(v, "protected_neighbor_refs")?,
                    restore_authorization_hash: match v.get("restore_authorization_hash") {
                        Some(serde_json::Value::Null) | None => None,
                        Some(_) => Some(trusted_hash(v, "restore_authorization_hash")?),
                    },
                })
            })
            .collect::<Result<_, String>>()?;
        if manual_actions.iter().any(|value| {
            !trusted_object_fields(
                value,
                &[
                    "action_id",
                    "analysis_revision",
                    "page",
                    "rects",
                    "mode",
                    "source_kind",
                    "linked_occurrence_id",
                    "expected_text_hash",
                    "protected_neighbor_refs",
                    "restore_authorization_hash",
                ],
            )
        }) {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        if manifest.manual_actions.iter().any(|action| {
            action.analysis_revision != manifest.analysis_revision
                || !manual_action_has_trusted_evidence(&manifest, action)
                || action.action_id
                    != manual_action_fingerprint(
                        &manifest.run_id,
                        &manifest.original_document_hash,
                        action,
                    )
        }) {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        if manifest.review_items.iter().any(|r| {
            r.analysis_revision != manifest.analysis_revision
                || r.page_end < r.page_start
                || !valid_review_target(&manifest, r)
        }) || !blocking_items_have_pending_reviews(&manifest)
        {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        if !unique_ids(
            manifest
                .segments
                .iter()
                .map(|item| item.segment_id.as_str()),
        ) || !unique_ids(manifest.regions.iter().map(|item| item.region_id.as_str()))
            || !unique_ids(
                manifest
                    .occurrences
                    .iter()
                    .map(|item| item.occurrence_id.as_str()),
            )
            || !unique_ids(
                manifest
                    .review_items
                    .iter()
                    .map(|item| item.review_id.as_str()),
            )
            || !unique_ids(
                manifest
                    .review_items
                    .iter()
                    .filter_map(|item| item.target_id.as_deref()),
            )
            || !unique_ids(
                manifest
                    .manual_actions
                    .iter()
                    .map(|item| item.action_id.as_str()),
            )
        {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        refresh_manifest_hash_with_key(&mut manifest, &self.manifest_hmac_key)?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        reclaim_completed_sessions(&mut state);
        if state.sessions.len() >= MAX_ACTIVE_SESSIONS {
            return Err(safe_error("SESSION_LIMIT_REACHED"));
        }
        state.sessions.insert(
            manifest.run_id.clone(),
            SessionRecord {
                manifest: manifest.clone(),
                original: None,
                options: None,
                lifecycle: SessionLifecycle::Ready,
            },
        );
        Ok(manifest)
    }
    pub fn bind_private_context(
        &self,
        run_id: &str,
        original: std::path::PathBuf,
        options: serde_json::Value,
    ) -> Result<(), String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        if !state.sessions.contains_key(run_id) {
            return Err(replay_or_unknown(&state, run_id));
        }
        let record = state.sessions.get_mut(run_id).expect("checked above");
        if record.lifecycle != SessionLifecycle::Ready {
            return Err(safe_error(match record.lifecycle {
                SessionLifecycle::Completed => "RUN_CONSUMED",
                SessionLifecycle::CleanupRequired => "RUN_CLEANUP_REQUIRED",
                SessionLifecycle::Finalizing => "RUN_FINALIZING",
                SessionLifecycle::Ready => unreachable!(),
            }));
        }
        let options = canonical_public_options(options, &record.manifest.profile)?;
        record.original = Some(original);
        record.options = Some(options);
        Ok(())
    }

    pub fn reanalysis_context(
        &self,
        request: &ResolveMaskingReviewRequest,
    ) -> Result<ReanalysisContext, String> {
        let state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        let record = state
            .sessions
            .get(&request.run_id)
            .ok_or_else(|| replay_or_unknown(&state, &request.run_id))?;
        if record.lifecycle != SessionLifecycle::Ready
            || record.manifest.analysis_revision != request.analysis_revision
            || record.manifest.manifest_hash != request.manifest_hash
        {
            return Err(safe_error("STALE_ANALYSIS"));
        }
        let review = record
            .manifest
            .review_items
            .iter()
            .find(|item| item.review_id == request.review_id && item.status == "pending")
            .ok_or_else(|| safe_error("UNKNOWN_REVIEW"))?;
        if review.kind != request.resolution.kind() {
            return Err(safe_error("REVIEW_KIND_MISMATCH"));
        }
        match &request.resolution {
            ReviewResolution::Boundary {
                page_start,
                page_end,
                segment_kind,
            } if page_start <= page_end && is_safe_segment_kind(segment_kind) => {
                let target = review
                    .target_id
                    .as_deref()
                    .ok_or_else(|| safe_error("REVIEW_TARGET_MISMATCH"))?;
                let segment = record
                    .manifest
                    .segments
                    .iter()
                    .find(|item| item.segment_id == target)
                    .ok_or_else(|| safe_error("REVIEW_TARGET_MISMATCH"))?;
                if *page_start < segment.page_start || *page_end > segment.page_end {
                    return Err(safe_error("BOUNDARY_CORRECTION_OUT_OF_RANGE"));
                }
            }
            ReviewResolution::Ocr { accepted: true } => {}
            _ => return Err(safe_error("REANALYSIS_REQUIRED")),
        }
        Ok(ReanalysisContext {
            original: record
                .original
                .clone()
                .ok_or_else(|| safe_error("REANALYSIS_UNAVAILABLE"))?,
            options: record
                .options
                .clone()
                .ok_or_else(|| safe_error("REANALYSIS_UNAVAILABLE"))?,
            profile: record.manifest.profile.clone(),
            original_document_hash: record.manifest.original_document_hash.clone(),
        })
    }

    pub fn replace_from_trusted_reanalysis(
        &self,
        request: ResolveMaskingReviewRequest,
        original_bytes: &[u8],
        trusted: &serde_json::Value,
    ) -> Result<AnalysisManifestV1, String> {
        let context = self.reanalysis_context(&request)?;
        let temporary = MaskingRunSessions::default();
        let mut candidate = temporary.create_from_trusted(
            original_bytes,
            &context.profile,
            context.options,
            trusted,
        )?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        if !state.sessions.contains_key(&request.run_id) {
            return Err(replay_or_unknown(&state, &request.run_id));
        }
        let record = state
            .sessions
            .get_mut(&request.run_id)
            .expect("checked above");
        if record.lifecycle != SessionLifecycle::Ready
            || record.manifest.analysis_revision != request.analysis_revision
            || record.manifest.manifest_hash != request.manifest_hash
        {
            return Err(safe_error("STALE_ANALYSIS"));
        }
        let prior = &record.manifest;
        let resolved_review = prior
            .review_items
            .iter()
            .find(|item| item.review_id == request.review_id && item.status == "pending")
            .cloned()
            .ok_or_else(|| safe_error("STALE_ANALYSIS"))?;
        if candidate.analysis_revision
            != prior
                .analysis_revision
                .checked_add(1)
                .ok_or_else(|| safe_error("REVISION_OVERFLOW"))?
            || candidate.original_document_hash != prior.original_document_hash
            || candidate.profile != prior.profile
            || candidate.policy_version != prior.policy_version
            || candidate.options_version != prior.options_version
            || candidate.options_hash != prior.options_hash
            || candidate.threshold_version != prior.threshold_version
            || candidate.threshold_hash != prior.threshold_hash
            || candidate.threshold_artifact != prior.threshold_artifact
            || candidate.coordinate_space != prior.coordinate_space
        {
            return Err(safe_error("REANALYSIS_MANIFEST_INVALID"));
        }
        let (affected_start, affected_end, resolved_target_id) = match &request.resolution {
            ReviewResolution::Boundary {
                page_start,
                page_end,
                segment_kind,
            } => {
                let corrected = candidate
                    .segments
                    .iter()
                    .find(|segment| {
                        segment.page_start == *page_start
                            && segment.page_end == *page_end
                            && segment.kind == *segment_kind
                            && segment.state == "user_confirmed"
                    })
                    .ok_or_else(|| safe_error("REANALYSIS_EVIDENCE_REQUIRED"))?;
                (*page_start, *page_end, corrected.segment_id.clone())
            }
            ReviewResolution::Ocr { accepted: true } => {
                if candidate.review_items.iter().any(|item| {
                    item.kind == ReviewKind::Ocr
                        && item.status == "pending"
                        && !(item.page_end < resolved_review.page_start
                            || item.page_start > resolved_review.page_end)
                }) || !candidate.occurrences.iter().any(|item| {
                    item.page >= resolved_review.page_start
                        && item.page <= resolved_review.page_end
                        && !item.rects.is_empty()
                        && item.expected_text_hash.len() == 64
                }) {
                    return Err(safe_error("REANALYSIS_EVIDENCE_REQUIRED"));
                }
                let target = candidate
                    .segments
                    .iter()
                    .find(|segment| {
                        segment.page_start <= resolved_review.page_start
                            && segment.page_end >= resolved_review.page_end
                    })
                    .ok_or_else(|| safe_error("REANALYSIS_EVIDENCE_REQUIRED"))?;
                (
                    resolved_review.page_start,
                    resolved_review.page_end,
                    target.segment_id.clone(),
                )
            }
            _ => return Err(safe_error("REANALYSIS_REQUIRED")),
        };
        let mut resolution_marker = resolved_review;
        resolution_marker.analysis_revision = candidate.analysis_revision;
        resolution_marker.target_id = Some(resolved_target_id);
        resolution_marker.status = "resolved".to_string();
        resolution_marker.review_id =
            review_fingerprint(candidate.analysis_revision, &resolution_marker);
        candidate
            .review_items
            .retain(|item| item.review_id != resolution_marker.review_id);
        candidate.review_items.push(resolution_marker);
        candidate.run_id = prior.run_id.clone();
        candidate.manual_actions.clear();
        for action in prior
            .manual_actions
            .iter()
            .filter(|action| action.page < affected_start || action.page > affected_end)
        {
            let map_occurrence = |id: &str| {
                let old = prior
                    .occurrences
                    .iter()
                    .find(|item| item.occurrence_id == id)?;
                let matches = candidate
                    .occurrences
                    .iter()
                    .filter(|item| {
                        item.page == old.page && item.expected_text_hash == old.expected_text_hash
                    })
                    .collect::<Vec<_>>();
                (matches.len() == 1).then(|| matches[0].occurrence_id.clone())
            };
            let mut carried = action.clone();
            carried.analysis_revision = candidate.analysis_revision;
            carried.linked_occurrence_id = match action.linked_occurrence_id.as_deref() {
                Some(id) => {
                    Some(map_occurrence(id).ok_or_else(|| safe_error("REANALYSIS_CARRY_INVALID"))?)
                }
                None => None,
            };
            carried.action_id = manual_action_fingerprint(
                &candidate.run_id,
                &candidate.original_document_hash,
                &carried,
            );
            candidate.manual_actions.push(carried);
        }
        refresh_manifest_hash_with_key(&mut candidate, &self.manifest_hmac_key)?;
        if !all_manifest_revisions_match(&candidate)
            || !manifest_referential_integrity(&candidate)
            || !unique_ids(
                candidate
                    .manual_actions
                    .iter()
                    .map(|action| action.action_id.as_str()),
            )
        {
            return Err(safe_error("REFERENTIAL_INTEGRITY_INVALID"));
        }
        record.manifest = candidate.clone();
        Ok(candidate)
    }
    pub fn finalize_context(
        &self,
        run_id: &str,
        revision: u64,
        manifest_hash: &str,
        warnings_confirmed: bool,
    ) -> Result<(AnalysisManifestV1, std::path::PathBuf, serde_json::Value), String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        if !state.sessions.contains_key(run_id) {
            return Err(replay_or_unknown(&state, run_id));
        }
        let record = state.sessions.get(run_id).expect("checked above");
        match record.lifecycle {
            SessionLifecycle::Completed => return Err(safe_error("RUN_CONSUMED")),
            SessionLifecycle::CleanupRequired => return Err(safe_error("RUN_CLEANUP_REQUIRED")),
            SessionLifecycle::Finalizing => return Err(safe_error("RUN_FINALIZING")),
            SessionLifecycle::Ready => {}
        }
        let manifest = &record.manifest;
        // Confirmation is review intent only. It never bypasses missing/consumed
        // sessions, stale revision or hash, tampered referential integrity, invalid
        // coverage, original-file drift, or a trusted-finalization pipeline failure.
        if manifest.analysis_revision != revision || manifest.manifest_hash != manifest_hash {
            return Err(safe_error("STALE_ANALYSIS"));
        }
        restore_authorization_summary_for(manifest, &state.restore_capabilities)?;
        if !all_manifest_revisions_match(manifest) || !manifest_referential_integrity(manifest) {
            return Err(safe_error("STALE_ANALYSIS"));
        }
        if !valid_coverage(manifest) {
            return Err(safe_error("STALE_ANALYSIS"));
        }
        // A warning confirmation may waive unresolved findings, but it may
        // never waive the invariant that every blocking manifest item has a
        // corresponding review card. Missing mappings mean the authoritative
        // review projection is corrupt, not that the user accepted a warning.
        if !blocking_items_have_pending_reviews(manifest) {
            return Err(safe_error("STALE_ANALYSIS"));
        }
        let has_unresolved_review = manifest.approval_coverage.state
            == CoverageState::Indeterminate
            || manifest.required_region_coverage.blocking
            || manifest
                .review_items
                .iter()
                .any(|item| item.status != "resolved")
            || manifest
                .segments
                .iter()
                .any(|item| item.state == "review_required")
            || manifest
                .occurrences
                .iter()
                .any(|item| item.proposed_action == "review" || item.state != "confirmed")
            || manifest
                .regions
                .iter()
                .any(|item| item.state == "review_required" || item.state == "unconfirmed");
        if has_unresolved_review && !warnings_confirmed {
            return Err(safe_error("UNRESOLVED_REVIEW"));
        }
        let original = record
            .original
            .clone()
            .ok_or_else(|| safe_error("UNKNOWN_RUN"))?;
        let options = record
            .options
            .clone()
            .ok_or_else(|| safe_error("UNKNOWN_RUN"))?;
        let manifest_snapshot = manifest.clone();
        drop(manifest);
        drop(record);
        state
            .sessions
            .get_mut(run_id)
            .expect("checked above")
            .lifecycle = SessionLifecycle::Finalizing;
        Ok((manifest_snapshot, original, options))
    }

    pub fn restore_authorization_summary(
        &self,
        manifest: &AnalysisManifestV1,
    ) -> Result<RestoreAuthorizationSummary, String> {
        let state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        restore_authorization_summary_for(manifest, &state.restore_capabilities)
    }

    pub fn consume_restore_authorizations(
        &self,
        run_id: &str,
        revision: u64,
        manifest_hash: &str,
    ) -> Result<(), String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        let record = state
            .sessions
            .get(run_id)
            .ok_or_else(|| replay_or_unknown(&state, run_id))?;
        if record.lifecycle != SessionLifecycle::Finalizing
            || record.manifest.analysis_revision != revision
            || record.manifest.manifest_hash != manifest_hash
        {
            return Err(safe_error("FINALIZE_STATE_INVALID"));
        }
        restore_authorization_summary_for(&record.manifest, &state.restore_capabilities)?;
        state
            .restore_capabilities
            .retain(|_, capability| capability.run_id != run_id);
        Ok(())
    }

    pub fn finish_finalize(
        &self,
        run_id: &str,
        disposition: FinalizeDisposition,
    ) -> Result<(), String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        let lifecycle = state
            .sessions
            .get(run_id)
            .ok_or_else(|| replay_or_unknown(&state, run_id))?
            .lifecycle;
        if lifecycle != SessionLifecycle::Finalizing {
            return Err(safe_error("FINALIZE_STATE_INVALID"));
        }
        match disposition {
            FinalizeDisposition::RetryReady => {
                state
                    .sessions
                    .get_mut(run_id)
                    .expect("checked above")
                    .lifecycle = SessionLifecycle::Ready;
            }
            FinalizeDisposition::CleanupRequired => {
                state
                    .sessions
                    .get_mut(run_id)
                    .expect("checked above")
                    .lifecycle = SessionLifecycle::CleanupRequired;
            }
            FinalizeDisposition::Consumed => {
                state.sessions.remove(run_id);
                state
                    .restore_capabilities
                    .retain(|_, capability| capability.run_id != run_id);
                insert_completed_tombstone(&mut state, run_id.to_string(), "RUN_CONSUMED");
            }
            FinalizeDisposition::PublishedIndeterminate => {
                state.sessions.remove(run_id);
                state
                    .restore_capabilities
                    .retain(|_, capability| capability.run_id != run_id);
                insert_completed_tombstone(
                    &mut state,
                    run_id.to_string(),
                    "RUN_PUBLISHED_INDETERMINATE",
                );
            }
        }
        Ok(())
    }

    pub fn get(&self, run_id: &str) -> Result<AnalysisManifestV1, String> {
        let state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        if !state.sessions.contains_key(run_id) {
            return Err(replay_or_unknown(&state, run_id));
        }
        let record = state.sessions.get(run_id).expect("checked above");
        match record.lifecycle {
            SessionLifecycle::Ready => Ok(record.manifest.clone()),
            SessionLifecycle::Finalizing => Err(safe_error("RUN_FINALIZING")),
            SessionLifecycle::CleanupRequired => Err(safe_error("RUN_CLEANUP_REQUIRED")),
            SessionLifecycle::Completed => Err(safe_error("RUN_CONSUMED")),
        }
    }

    pub fn issue_restore_capability(
        &self,
        request: RestoreCapabilityRequest,
        authorization_event: &str,
    ) -> Result<RestoreCapabilityResponse, String> {
        if authorization_event != "native_trusted_ui" {
            return Err(safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"));
        }
        let mut state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        if state.restore_capabilities.len() >= MAX_TRUSTED_ENTITIES {
            return Err(safe_error("MANUAL_ACTION_AUTHORIZATION_LIMIT"));
        }
        let (original_document_hash, revision, occurrence) = {
            let record = state
                .sessions
                .get(&request.run_id)
                .ok_or_else(|| replay_or_unknown(&state, &request.run_id))?;
            if record.lifecycle != SessionLifecycle::Ready {
                return Err(safe_error("RUN_NOT_READY"));
            }
            let manifest = &record.manifest;
            if manifest.analysis_revision != request.analysis_revision
                || manifest.manifest_hash != request.manifest_hash
            {
                return Err(safe_error("STALE_ANALYSIS"));
            }
            let rects = validate_resolution_rects(&request.rects)?;
            let occurrence = manifest
                .occurrences
                .iter()
                .find(|item| {
                    item.occurrence_id == request.occurrence_id
                        && item.analysis_revision == manifest.analysis_revision
                        && matches!(item.state.as_str(), "confirmed" | "user_confirmed")
                        && item.proposed_action == "mask"
                        && item
                            .expected_text_hash
                            .eq_ignore_ascii_case(&request.expected_text_hash)
                        && normalize_rects(&item.rects) == normalize_rects(&rects)
                })
                .cloned()
                .ok_or_else(|| safe_error("MANUAL_ACTION_TARGET_INVALID"))?;
            if manifest.manual_actions.iter().any(|action| {
                action.mode == "restore"
                    && action.linked_occurrence_id.as_deref()
                        == Some(occurrence.occurrence_id.as_str())
            }) {
                return Err(safe_error("MANUAL_ACTION_TARGET_INVALID"));
            }
            (
                manifest.original_document_hash.clone(),
                manifest.analysis_revision,
                occurrence,
            )
        };
        let mut random = [0_u8; 32];
        getrandom::getrandom(&mut random)
            .map_err(|_| safe_error("MANUAL_ACTION_AUTHORIZATION_UNAVAILABLE"))?;
        let capability = document_hash(&random);
        let capability_hash = document_hash(capability.as_bytes());
        state.restore_capabilities.insert(
            capability_hash,
            RestoreCapabilityRecord {
                run_id: request.run_id.clone(),
                original_document_hash,
                issued_revision: revision,
                issued_manifest_hash: request.manifest_hash,
                occurrence_id: occurrence.occurrence_id,
                target_value_hash: occurrence.value_hash,
                page: occurrence.page,
                rects: occurrence.rects,
                expected_text_hash: occurrence.expected_text_hash,
                authorization_event: authorization_event.to_string(),
                claimed: false,
            },
        );
        Ok(RestoreCapabilityResponse { capability })
    }

    pub fn apply_manual_action(
        &self,
        request: ManualActionV1Request,
    ) -> Result<AnalysisManifestV1, String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        if !state.sessions.contains_key(&request.run_id) {
            return Err(replay_or_unknown(&state, &request.run_id));
        }
        let requested_restore_capability = request
            .restore_capability
            .as_deref()
            .map(|capability| document_hash(capability.as_bytes()));
        let requested_restore_authorization = requested_restore_capability
            .as_ref()
            .and_then(|hash| state.restore_capabilities.get(hash).cloned());
        let record = state
            .sessions
            .get_mut(&request.run_id)
            .expect("checked above");
        if record.lifecycle != SessionLifecycle::Ready {
            return Err(safe_error("RUN_NOT_READY"));
        }
        let manifest = &mut record.manifest;
        if manifest.analysis_revision != request.analysis_revision
            || manifest.manifest_hash != request.manifest_hash
        {
            return Err(safe_error("STALE_ANALYSIS"));
        }
        if !matches!(request.mode.as_str(), "mask" | "restore")
            || !matches!(request.source_kind.as_str(), "text_pdf" | "scan")
        {
            return Err(safe_error("MANUAL_ACTION_INVALID"));
        }
        if request.mode == "restore"
            && (request.source_kind != "text_pdf"
                || request.linked_occurrence_id.is_none()
                || request.expected_text_hash.is_none()
                || request.restore_capability.is_none()
                || request.target_region_id.is_some()
                || !request.protected_neighbor_refs.is_empty())
        {
            return Err(safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"));
        }
        if request.mode == "mask" && request.restore_capability.is_some() {
            return Err(safe_error("MANUAL_ACTION_TARGET_INVALID"));
        }
        let scan_manual = request.source_kind == "scan";
        let rects = validate_resolution_rects(&request.rects)?;
        let protected_neighbor_refs = if scan_manual {
            if !request.protected_neighbor_refs.is_empty() {
                return Err(safe_error("MANUAL_ACTION_TARGET_INVALID"));
            }
            Vec::new()
        } else if request.mode == "restore" {
            Vec::new()
        } else {
            validate_resolution_rects(&request.protected_neighbor_refs)
                .map_err(|_| safe_error("MANUAL_ACTION_EVIDENCE_REQUIRED"))?
        };
        let (page, rects, linked_occurrence_id, expected_text_hash, restore_authorization_hash) =
            if scan_manual {
                if request.linked_occurrence_id.is_some()
                    || request.target_region_id.is_some()
                    || request.expected_text_hash.is_some()
                    || !protected_neighbor_refs.is_empty()
                {
                    return Err(safe_error("MANUAL_ACTION_TARGET_INVALID"));
                }
                (request.page, rects, None, None, None)
            } else if request.mode == "restore" {
                let linked_id = request
                    .linked_occurrence_id
                    .as_deref()
                    .ok_or_else(|| safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"))?;
                let expected = request
                    .expected_text_hash
                    .as_deref()
                    .filter(|value| {
                        value.len() == 64
                            && value.chars().all(|character| character.is_ascii_hexdigit())
                    })
                    .map(str::to_ascii_lowercase)
                    .ok_or_else(|| safe_error("MANUAL_ACTION_EVIDENCE_REQUIRED"))?;
                let occurrence = manifest
                    .occurrences
                    .iter()
                    .find(|item| {
                        item.occurrence_id == linked_id
                            && item.analysis_revision == manifest.analysis_revision
                            && matches!(item.state.as_str(), "confirmed" | "user_confirmed")
                            && item.proposed_action == "mask"
                            && item.page == request.page
                            && item.expected_text_hash.eq_ignore_ascii_case(&expected)
                            && normalize_rects(&item.rects) == normalize_rects(&rects)
                    })
                    .cloned()
                    .ok_or_else(|| safe_error("MANUAL_ACTION_TARGET_INVALID"))?;
                request
                    .restore_capability
                    .as_deref()
                    .filter(|value| !value.is_empty())
                    .ok_or_else(|| safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"))?;
                let capability_hash = requested_restore_capability
                    .clone()
                    .ok_or_else(|| safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"))?;
                let authorization = requested_restore_authorization
                    .as_ref()
                    .ok_or_else(|| safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"))?;
                if authorization.claimed
                    || authorization.run_id != request.run_id
                    || authorization.original_document_hash != manifest.original_document_hash
                    || authorization.issued_revision != manifest.analysis_revision
                    || authorization.issued_manifest_hash != request.manifest_hash
                    || authorization.occurrence_id != occurrence.occurrence_id
                    || authorization.page != occurrence.page
                    || normalize_rects(&authorization.rects) != normalize_rects(&occurrence.rects)
                    || !authorization
                        .expected_text_hash
                        .eq_ignore_ascii_case(&expected)
                {
                    return Err(safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"));
                }
                (
                    occurrence.page,
                    occurrence.rects,
                    Some(occurrence.occurrence_id),
                    Some(expected),
                    Some(capability_hash),
                )
            } else if let Some(linked_id) = request.linked_occurrence_id.as_deref() {
                if request.target_region_id.is_some() {
                    return Err(safe_error("MANUAL_ACTION_INVALID"));
                }
                let occurrence = manifest
                    .occurrences
                    .iter()
                    .find(|item| {
                        item.occurrence_id == linked_id
                            && item.analysis_revision == manifest.analysis_revision
                            && item.state == "confirmed"
                            && item.proposed_action == "mask"
                    })
                    .ok_or_else(|| safe_error("MANUAL_ACTION_TARGET_INVALID"))?;
                (
                    occurrence.page,
                    occurrence.rects.clone(),
                    Some(occurrence.occurrence_id.clone()),
                    Some(occurrence.expected_text_hash.clone()),
                    None,
                )
            } else {
                let region_id = request
                    .target_region_id
                    .as_deref()
                    .ok_or_else(|| safe_error("MANUAL_ACTION_UNBOUND"))?;
                let region = manifest
                    .regions
                    .iter()
                    .find(|item| {
                        item.region_id == region_id
                            && item.analysis_revision == manifest.analysis_revision
                            && item.page == request.page
                            && matches!(item.state.as_str(), "confirmed" | "user_confirmed")
                    })
                    .ok_or_else(|| safe_error("MANUAL_ACTION_TARGET_INVALID"))?;
                if !rects
                    .iter()
                    .all(|rect| region.rects.iter().any(|bound| rect_contains(bound, rect)))
                {
                    return Err(safe_error("MANUAL_ACTION_OUT_OF_BOUNDS"));
                }
                let expected = request
                    .expected_text_hash
                    .filter(|value| {
                        value.len() == 64
                            && value.chars().all(|character| character.is_ascii_hexdigit())
                    })
                    .ok_or_else(|| safe_error("MANUAL_ACTION_EVIDENCE_REQUIRED"))?;
                (request.page, rects, None, Some(expected), None)
            };
        if !scan_manual && request.mode != "restore" {
            if !manifest.occurrences.iter().any(|occurrence| {
                occurrence.page == page
                    && normalize_rects(&occurrence.rects)
                        == normalize_rects(&protected_neighbor_refs)
            }) {
                return Err(safe_error("MANUAL_ACTION_EVIDENCE_REQUIRED"));
            }
            if rects.iter().any(|mask| {
                protected_neighbor_refs
                    .iter()
                    .any(|protected| rects_overlap(mask, protected))
            }) {
                return Err(safe_error("MANUAL_ACTION_PROTECTED_NEIGHBOR_OVERLAP"));
            }
        }
        let restore_authorization_for_claim = restore_authorization_hash.clone();
        manifest.manual_actions.push(ManualAction {
            action_id: String::new(),
            analysis_revision: manifest.analysis_revision,
            page,
            rects,
            mode: request.mode,
            source_kind: request.source_kind,
            linked_occurrence_id,
            expected_text_hash,
            protected_neighbor_refs,
            restore_authorization_hash,
        });
        let revision_marker = ReviewItem {
            review_id: "manual_action_transition".to_string(),
            analysis_revision: manifest.analysis_revision,
            kind: ReviewKind::Ocr,
            target_id: None,
            page_start: u32::MAX,
            page_end: u32::MAX,
            status: "resolved".to_string(),
            reason_codes: Vec::new(),
            requires_acknowledgment: false,
            common_only: false,
            provenance: "server_manual_action_v1".to_string(),
        };
        advance_revision(manifest, &revision_marker, u32::MAX, u32::MAX)?;
        refresh_manifest_hash_with_key(manifest, &self.manifest_hmac_key)?;
        let updated_manifest = manifest.clone();
        drop(manifest);
        if let Some(capability_hash) = restore_authorization_for_claim {
            state
                .restore_capabilities
                .get_mut(&capability_hash)
                .ok_or_else(|| safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"))?
                .claimed = true;
        }
        Ok(updated_manifest)
    }
    pub fn resolve(
        &self,
        request: ResolveMaskingReviewRequest,
    ) -> Result<AnalysisManifestV1, String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| safe_error("SESSION_LOCK_FAILED"))?;
        if !state.sessions.contains_key(&request.run_id) {
            return Err(replay_or_unknown(&state, &request.run_id));
        }
        let record = state
            .sessions
            .get_mut(&request.run_id)
            .expect("checked above");
        if record.lifecycle != SessionLifecycle::Ready {
            return Err(safe_error(match record.lifecycle {
                SessionLifecycle::Completed => "RUN_CONSUMED",
                SessionLifecycle::CleanupRequired => "RUN_CLEANUP_REQUIRED",
                SessionLifecycle::Finalizing => "RUN_FINALIZING",
                SessionLifecycle::Ready => unreachable!(),
            }));
        }
        let mut candidate = record.manifest.clone();
        let manifest = &mut candidate;
        if manifest.analysis_revision != request.analysis_revision
            || manifest.manifest_hash != request.manifest_hash
        {
            return Err(safe_error("STALE_ANALYSIS"));
        }
        let review = manifest
            .review_items
            .iter()
            .find(|item| item.review_id == request.review_id)
            .cloned()
            .ok_or_else(|| safe_error("UNKNOWN_REVIEW"))?;
        if review.status != "pending" {
            return Err(safe_error("DUPLICATE_REVIEW_RESOLUTION"));
        }
        if review.kind != request.resolution.kind() {
            return Err(safe_error("REVIEW_KIND_MISMATCH"));
        }
        match &request.resolution {
            ReviewResolution::Name { action } | ReviewResolution::Institution { action } => {
                if !matches!(action.as_str(), "mask" | "exclude") {
                    return Err(safe_error("INVALID_REVIEW_ACTION"));
                }
                let target = review
                    .target_id
                    .as_deref()
                    .ok_or_else(|| safe_error("REVIEW_TARGET_MISMATCH"))?;
                let document_hash = manifest.original_document_hash.clone();
                let occurrence = manifest
                    .occurrences
                    .iter_mut()
                    .find(|item| item.occurrence_id == target)
                    .ok_or_else(|| safe_error("REVIEW_TARGET_MISMATCH"))?;
                let old_occurrence_id = occurrence.occurrence_id.clone();
                occurrence.proposed_action = action.clone();
                occurrence.state = "confirmed".to_string();
                occurrence.occurrence_id = occurrence_fingerprint_bound(
                    &document_hash,
                    occurrence.analysis_revision,
                    &occurrence.segment_id,
                    occurrence.region_id.as_deref(),
                    occurrence.page,
                    &occurrence.rects,
                    &occurrence.tag,
                    &occurrence.category,
                    &occurrence.value_hash,
                    &occurrence.source,
                    &occurrence.policy,
                    &occurrence.proposed_action,
                );
                let new_occurrence_id = occurrence.occurrence_id.clone();
                for item in &mut manifest.review_items {
                    if item.target_id.as_deref() == Some(old_occurrence_id.as_str()) {
                        item.target_id = Some(new_occurrence_id.clone());
                    }
                }
                for action in &mut manifest.manual_actions {
                    if action.linked_occurrence_id.as_deref() == Some(old_occurrence_id.as_str()) {
                        action.linked_occurrence_id = Some(new_occurrence_id.clone());
                    }
                }
            }
            ReviewResolution::Acknowledge { acknowledged } => {
                let scanned_review = !review.common_only
                    && review.requires_acknowledgment
                    && review
                        .reason_codes
                        .iter()
                        .any(|code| code == "scanned_geometry_unavailable")
                    && review.target_id.as_deref().is_some_and(|target| {
                        manifest.segments.iter().any(|segment| {
                            segment.segment_id == target
                                && segment.source == "scanned_geometry_unavailable"
                                && segment.kind == "unknown"
                                && !segment.common_only
                                && review.page_start >= segment.page_start
                                && review.page_end <= segment.page_end
                        })
                    });
                if !acknowledged
                    || !review.requires_acknowledgment
                    || (!review.common_only && !scanned_review)
                {
                    return Err(safe_error("ACKNOWLEDGMENT_REQUIRED"));
                }
                let target = review
                    .target_id
                    .as_deref()
                    .ok_or_else(|| safe_error("REVIEW_TARGET_MISMATCH"))?;
                let segment = manifest
                    .segments
                    .iter_mut()
                    .find(|item| {
                        item.segment_id == target
                            && item.common_only == review.common_only
                            && review.page_start >= item.page_start
                            && review.page_end <= item.page_end
                            && (review.common_only || scanned_review)
                    })
                    .ok_or_else(|| safe_error("REVIEW_TARGET_MISMATCH"))?;
                segment.state = "user_confirmed".to_string();
            }
            ReviewResolution::Boundary {
                page_start,
                page_end,
                segment_kind,
            } => {
                let target = review
                    .target_id
                    .as_deref()
                    .ok_or_else(|| safe_error("REVIEW_TARGET_MISMATCH"))?;
                if page_end < page_start || !is_safe_segment_kind(segment_kind) {
                    return Err(safe_error("INVALID_BOUNDARY_CORRECTION"));
                }
                let segment = manifest
                    .segments
                    .iter_mut()
                    .find(|item| item.segment_id == target)
                    .ok_or_else(|| safe_error("REVIEW_TARGET_MISMATCH"))?;
                if segment.page_start != *page_start
                    || segment.page_end != *page_end
                    || segment.kind != *segment_kind
                {
                    return Err(safe_error("LOCAL_REANALYSIS_REQUIRED"));
                }
                segment.state = "user_confirmed".to_string();
                advance_revision(manifest, &review, *page_start, *page_end)?;
            }
            ReviewResolution::RegionGeometry { rects } => {
                let rects = validate_resolution_rects(rects)?;
                let target = review
                    .target_id
                    .as_deref()
                    .ok_or_else(|| safe_error("REVIEW_TARGET_MISMATCH"))?;
                let region_page = manifest
                    .regions
                    .iter()
                    .find(|item| item.region_id == target)
                    .map(|item| item.page)
                    .ok_or_else(|| safe_error("REVIEW_TARGET_MISMATCH"))?;
                let linked_geometry_is_covered = manifest
                    .occurrences
                    .iter()
                    .filter(|item| item.region_id.as_deref() == Some(target))
                    .all(|item| {
                        item.page == region_page
                            && item.rects.iter().all(|candidate| {
                                rects
                                    .iter()
                                    .any(|region_rect| rect_contains(region_rect, candidate))
                            })
                    });
                if !linked_geometry_is_covered {
                    return Err(safe_error("INVALID_REVIEW_GEOMETRY"));
                }
                let region = manifest
                    .regions
                    .iter_mut()
                    .find(|item| item.region_id == target)
                    .ok_or_else(|| safe_error("REVIEW_TARGET_MISMATCH"))?;
                region.rects = rects;
                region.state = "user_confirmed".to_string();
                region.confirmation_source = Some("user".to_string());
                advance_revision(manifest, &review, u32::MAX, u32::MAX)?;
            }
            ReviewResolution::Ocr { accepted } => {
                if !accepted {
                    return Err(safe_error("OCR_CONFIRMATION_REQUIRED"));
                }
                if !valid_review_target(manifest, &review) {
                    return Err(safe_error("REVIEW_TARGET_MISMATCH"));
                }
                // An acknowledgment cannot manufacture missing source geometry. Reanalysis is the only
                // safe way to turn OCR uncertainty into a finalizable occurrence.
                return Err(safe_error("LOCAL_REANALYSIS_REQUIRED"));
            }
        }
        if !request.resolution.creates_revision() {
            if let Some(current) = manifest
                .review_items
                .iter_mut()
                .find(|item| item.review_id == request.review_id)
            {
                current.status = "resolved".to_string();
            }
        }
        let run_id = manifest.run_id.clone();
        let document_hash = manifest.original_document_hash.clone();
        for action in &mut manifest.manual_actions {
            action.action_id = manual_action_fingerprint(&run_id, &document_hash, action);
        }
        refresh_manifest_hash_with_key(manifest, &self.manifest_hmac_key)?;
        if !manifest_referential_integrity(manifest) {
            return Err(safe_error("REFERENTIAL_INTEGRITY_INVALID"));
        }
        record.manifest = manifest.clone();
        Ok(manifest.clone())
    }
}

fn contains_raw_field(value: &serde_json::Value) -> bool {
    match value {
        serde_json::Value::Object(object) => object.iter().any(|(key, value)| {
            let normalized = key
                .chars()
                .filter(|character| *character != '_' && *character != '-')
                .flat_map(char::to_lowercase)
                .collect::<String>();
            matches!(
                normalized.as_str(),
                "text"
                    | "rawtext"
                    | "extractedtext"
                    | "value"
                    | "rawvalue"
                    | "plaintext"
                    | "candidatevalue"
                    | "content"
                    | "snippet"
            ) || contains_raw_field(value)
        }),
        serde_json::Value::Array(items) => items.iter().any(contains_raw_field),
        _ => false,
    }
}
pub fn canonical_profile(profile: &str) -> Result<&'static str, String> {
    match profile.trim() {
        "internal_review" => Ok("internal_review"),
        "official_dispatch" => Ok("official_dispatch"),
        "mixed" => Ok("mixed"),
        "legal" => Ok("legal"),
        _ => Err(safe_error("INVALID_PROFILE")),
    }
}

fn required_region_kinds(profile: &str) -> &'static [&'static str] {
    const INTERNAL: &[&str] = &["approval", "header_meta", "labeled_staff"];
    const DISPATCH: &[&str] = &[
        "recipient_reference",
        "sender_institution",
        "approval_staff",
        "dispatch_metadata",
        "footer_contact",
    ];
    const MIXED: &[&str] = &[
        "approval",
        "header_meta",
        "labeled_staff",
        "recipient_reference",
        "sender_institution",
        "approval_staff",
        "dispatch_metadata",
        "footer_contact",
    ];
    match profile {
        "internal_review" => INTERNAL,
        "official_dispatch" => DISPATCH,
        "mixed" => MIXED,
        _ => &[],
    }
}

fn valid_coverage(manifest: &AnalysisManifestV1) -> bool {
    let approval_counts_valid = match manifest.approval_coverage.state {
        CoverageState::Present => manifest.approval_coverage.signer_count > 0,
        CoverageState::Absent => {
            manifest.approval_coverage.signer_count == 0
                && manifest.approval_coverage.protected_neighbor_count == 0
        }
        CoverageState::Indeterminate => true,
    };
    let required = required_region_kinds(&manifest.profile);
    let actual = manifest
        .required_region_coverage
        .kinds
        .iter()
        .map(|coverage| coverage.kind.as_str())
        .collect::<HashSet<_>>();
    let has_indeterminate = manifest
        .required_region_coverage
        .kinds
        .iter()
        .any(|coverage| coverage.state == CoverageState::Indeterminate);
    manifest.approval_coverage.schema_version == 1
        && approval_counts_valid
        && !required.is_empty()
        && manifest.required_region_coverage.schema_version == 1
        && manifest.required_region_coverage.profile == manifest.profile
        && manifest.required_region_coverage.kinds.len() == required.len()
        && actual.len() == required.len()
        && required.iter().all(|kind| actual.contains(kind))
        && manifest.required_region_coverage.blocking == has_indeterminate
}

fn unique_ids<'a>(mut ids: impl Iterator<Item = &'a str>) -> bool {
    let mut seen = HashSet::new();
    ids.all(|id| seen.insert(id))
}
fn insert_completed_tombstone(state: &mut SessionState, run_id: String, code: &'static str) {
    if state
        .completed_tombstones
        .iter()
        .any(|(entry, _)| entry == &run_id)
    {
        return;
    }
    state.completed_tombstones.push_back((run_id, code));
    if state.completed_tombstones.len() > MAX_COMPLETED_TOMBSTONES {
        state.completed_tombstones.pop_front();
    }
}

fn reclaim_completed_sessions(state: &mut SessionState) {
    let completed = state
        .sessions
        .iter()
        .filter_map(|(run_id, record)| {
            (record.lifecycle == SessionLifecycle::Completed).then(|| run_id.clone())
        })
        .collect::<Vec<_>>();
    for run_id in completed {
        state.sessions.remove(&run_id);
        insert_completed_tombstone(state, run_id, "RUN_CONSUMED");
    }
}

fn replay_or_unknown(state: &SessionState, run_id: &str) -> String {
    state
        .completed_tombstones
        .iter()
        .find(|(entry, _)| entry == run_id)
        .map(|(_, code)| safe_error(code))
        .unwrap_or_else(|| safe_error("UNKNOWN_RUN"))
}
fn safe_error(code: &str) -> String {
    format!("MASKING_SESSION_{code}")
}

fn trusted_array<'a>(
    value: &'a serde_json::Value,
    key: &str,
) -> Result<&'a Vec<serde_json::Value>, String> {
    value
        .get(key)
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))
}
fn trusted_coverage_state(value: &serde_json::Value) -> Result<CoverageState, String> {
    match value.as_str() {
        Some("present") => Ok(CoverageState::Present),
        Some("absent") => Ok(CoverageState::Absent),
        Some("indeterminate") => Ok(CoverageState::Indeterminate),
        _ => Err(safe_error("TRUSTED_MANIFEST_INVALID")),
    }
}
fn trusted_approval_coverage(manifest: &serde_json::Value) -> Result<ApprovalCoverage, String> {
    let value = manifest
        .get("approval_coverage")
        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?;
    if !trusted_object_fields(
        value,
        &[
            "schema_version",
            "state",
            "signer_count",
            "protected_neighbor_count",
        ],
    ) {
        return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
    }
    Ok(ApprovalCoverage {
        schema_version: u32::try_from(
            value
                .get("schema_version")
                .and_then(serde_json::Value::as_u64)
                .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
        )
        .map_err(|_| safe_error("TRUSTED_MANIFEST_INVALID"))?,
        state: trusted_coverage_state(
            value
                .get("state")
                .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
        )?,
        signer_count: usize::try_from(
            value
                .get("signer_count")
                .and_then(serde_json::Value::as_u64)
                .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
        )
        .map_err(|_| safe_error("TRUSTED_MANIFEST_INVALID"))?,
        protected_neighbor_count: usize::try_from(
            value
                .get("protected_neighbor_count")
                .and_then(serde_json::Value::as_u64)
                .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
        )
        .map_err(|_| safe_error("TRUSTED_MANIFEST_INVALID"))?,
    })
}
fn trusted_required_region_coverage(
    manifest: &serde_json::Value,
) -> Result<RequiredRegionCoverage, String> {
    let value = manifest
        .get("required_region_coverage")
        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?;
    if !trusted_object_fields(value, &["schema_version", "profile", "kinds", "blocking"]) {
        return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
    }
    let kinds = trusted_array(value, "kinds")?
        .iter()
        .map(|kind| {
            if !trusted_object_fields(kind, &["kind", "state"]) {
                return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
            }
            Ok(RequiredRegionCoverageKind {
                kind: trusted_code(kind, "kind")?,
                state: trusted_coverage_state(
                    kind.get("state")
                        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
                )?,
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(RequiredRegionCoverage {
        schema_version: u32::try_from(
            value
                .get("schema_version")
                .and_then(serde_json::Value::as_u64)
                .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
        )
        .map_err(|_| safe_error("TRUSTED_MANIFEST_INVALID"))?,
        profile: trusted_code(value, "profile")?,
        kinds,
        blocking: value
            .get("blocking")
            .and_then(serde_json::Value::as_bool)
            .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?,
    })
}
fn trusted_id(value: &serde_json::Value, key: &str) -> Result<String, String> {
    let id = value
        .get(key)
        .and_then(serde_json::Value::as_str)
        .filter(|id| {
            id.len() >= 16
                && id.len() <= 128
                && id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        })
        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?;
    Ok(id.to_string())
}
fn trusted_rects_field(value: &serde_json::Value, key: &str) -> Result<Vec<Rect>, String> {
    let rects = value
        .as_object()
        .and_then(|object| object.get(key))
        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?;
    trusted_rects(&serde_json::json!({"rects": rects}))
}
fn trusted_optional_id(value: &serde_json::Value, key: &str) -> Result<Option<String>, String> {
    match value.get(key) {
        Some(serde_json::Value::Null) | None => Ok(None),
        Some(_) => trusted_id(value, key).map(Some),
    }
}
fn trusted_code(value: &serde_json::Value, key: &str) -> Result<String, String> {
    value
        .get(key)
        .and_then(serde_json::Value::as_str)
        .filter(|v| {
            !v.is_empty()
                && v.len() <= 128
                && v.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        })
        .map(str::to_string)
        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))
}
fn trusted_optional_code(value: &serde_json::Value, key: &str) -> Result<Option<String>, String> {
    match value.get(key) {
        Some(serde_json::Value::Null) | None => Ok(None),
        Some(_) => trusted_code(value, key).map(Some),
    }
}
fn trusted_state(value: &serde_json::Value, key: &str, allowed: &[&str]) -> Result<String, String> {
    let state = trusted_code(value, key)?;
    if allowed.contains(&state.as_str()) {
        Ok(state)
    } else {
        Err(safe_error("TRUSTED_MANIFEST_INVALID"))
    }
}
fn trusted_hash(value: &serde_json::Value, key: &str) -> Result<String, String> {
    let hash = value
        .get(key)
        .and_then(serde_json::Value::as_str)
        .filter(|v| v.len() == 64 && v.bytes().all(|b| b.is_ascii_hexdigit()))
        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?;
    Ok(hash.to_ascii_lowercase())
}
fn trusted_u64(value: &serde_json::Value, key: &str) -> Result<u64, String> {
    value
        .get(key)
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))
}
fn trusted_page(value: &serde_json::Value, key: &str) -> Result<u32, String> {
    trusted_u64(value, key)?
        .try_into()
        .map_err(|_| safe_error("TRUSTED_MANIFEST_INVALID"))
}
fn trusted_object_fields(value: &serde_json::Value, allowed: &[&str]) -> bool {
    value
        .as_object()
        .is_some_and(|object| object.keys().all(|key| allowed.contains(&key.as_str())))
}

fn pdf_page_count(bytes: &[u8]) -> Option<u32> {
    let document = lopdf::Document::load_mem(bytes).ok()?;
    document
        .get_pages()
        .len()
        .try_into()
        .ok()
        .filter(|count| *count > 0)
}
fn trusted_codes(value: &serde_json::Value, key: &str) -> Result<Vec<String>, String> {
    trusted_array(value, key)?
        .iter()
        .map(|v| {
            let object = serde_json::json!({"value": v});
            trusted_code(&object, "value")
        })
        .collect()
}
fn trusted_rects(value: &serde_json::Value) -> Result<Vec<Rect>, String> {
    let parsed = trusted_array(value, "rects")?
        .iter()
        .map(|rect| {
            if !trusted_object_fields(rect, &["x0", "y0", "x1", "y1"]) {
                return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
            }
            let x0 = rect.get("x0").and_then(serde_json::Value::as_f64);
            let y0 = rect.get("y0").and_then(serde_json::Value::as_f64);
            let x1 = rect.get("x1").and_then(serde_json::Value::as_f64);
            let y1 = rect.get("y1").and_then(serde_json::Value::as_f64);
            match (x0, y0, x1, y1) {
                (Some(x0), Some(y0), Some(x1), Some(y1))
                    if x0.is_finite()
                        && y0.is_finite()
                        && x1.is_finite()
                        && y1.is_finite()
                        && x0 >= 0.0
                        && y0 >= 0.0
                        && x1 > x0
                        && y1 > y0 =>
                {
                    Ok(Rect { x0, y0, x1, y1 })
                }
                _ => Err(safe_error("TRUSTED_MANIFEST_INVALID")),
            }
        })
        .collect::<Result<Vec<_>, _>>()?;
    if parsed.is_empty() {
        return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
    }
    Ok(normalize_rects(&parsed))
}
fn validate_segments(
    segments: &[AnalysisSegment],
    manifest_revision: u64,
    page_count: u32,
) -> Result<(), String> {
    if segments.is_empty() {
        return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
    }
    let mut ordered = segments.to_vec();
    ordered.sort_by_key(|s| s.page_start);
    let mut next = 0u32;
    for segment in ordered {
        if segment.analysis_revision != manifest_revision
            || segment.page_start != next
            || segment.page_end < segment.page_start
            || segment.page_end >= page_count
        {
            return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
        }
        next = segment
            .page_end
            .checked_add(1)
            .ok_or_else(|| safe_error("TRUSTED_MANIFEST_INVALID"))?;
    }
    if next != page_count {
        return Err(safe_error("TRUSTED_MANIFEST_INVALID"));
    }
    Ok(())
}
fn validate_resolution_rects(rects: &[Rect]) -> Result<Vec<Rect>, String> {
    if rects.is_empty()
        || rects.iter().any(|r| {
            !r.x0.is_finite()
                || !r.y0.is_finite()
                || !r.x1.is_finite()
                || !r.y1.is_finite()
                || r.x0 < 0.0
                || r.y0 < 0.0
                || r.x1 <= r.x0
                || r.y1 <= r.y0
        })
    {
        return Err(safe_error("INVALID_REVIEW_GEOMETRY"));
    }
    Ok(normalize_rects(rects))
}

fn rect_contains(outer: &Rect, inner: &Rect) -> bool {
    const EPSILON: f64 = 0.5;
    outer.x0 <= inner.x0 + EPSILON
        && outer.y0 <= inner.y0 + EPSILON
        && outer.x1 + EPSILON >= inner.x1
        && outer.y1 + EPSILON >= inner.y1
}
fn rects_overlap(left: &Rect, right: &Rect) -> bool {
    left.x0 < right.x1 && left.x1 > right.x0 && left.y0 < right.y1 && left.y1 > right.y0
}
fn valid_review_target(manifest: &AnalysisManifestV1, review: &ReviewItem) -> bool {
    let Some(target) = review.target_id.as_deref() else {
        return false;
    };
    let segment_target = || {
        manifest.segments.iter().any(|segment| {
            segment.segment_id == target
                && review.page_start >= segment.page_start
                && review.page_end <= segment.page_end
        })
    };
    match review.kind {
        ReviewKind::Name | ReviewKind::Institution => {
            manifest.occurrences.iter().any(|occurrence| {
                occurrence.occurrence_id == target
                    && occurrence.page >= review.page_start
                    && occurrence.page <= review.page_end
            })
        }
        ReviewKind::RegionGeometry => manifest.regions.iter().any(|region| {
            region.region_id == target
                && region.page >= review.page_start
                && region.page <= review.page_end
        }),
        ReviewKind::Acknowledge | ReviewKind::Boundary | ReviewKind::Ocr => segment_target(),
    }
}
fn all_manifest_revisions_match(manifest: &AnalysisManifestV1) -> bool {
    manifest
        .segments
        .iter()
        .all(|item| item.analysis_revision == manifest.analysis_revision)
        && manifest
            .regions
            .iter()
            .all(|item| item.analysis_revision == manifest.analysis_revision)
        && manifest
            .occurrences
            .iter()
            .all(|item| item.analysis_revision == manifest.analysis_revision)
        && manifest
            .review_items
            .iter()
            .all(|item| item.analysis_revision == manifest.analysis_revision)
        && manifest
            .manual_actions
            .iter()
            .all(|item| item.analysis_revision == manifest.analysis_revision)
        && manifest.threshold_version == THRESHOLD_VERSION
        && manifest.threshold_hash == manifest.threshold_artifact.content_hash
        && threshold_artifact(
            manifest.threshold_artifact.auto_mask_threshold,
            manifest.threshold_artifact.review_threshold,
        )
        .map(|artifact| artifact == manifest.threshold_artifact)
        .unwrap_or(false)
}
fn manifest_referential_integrity(manifest: &AnalysisManifestV1) -> bool {
    manifest.regions.iter().all(|region| {
        manifest.segments.iter().any(|segment| {
            segment.segment_id == region.segment_id
                && region.page >= segment.page_start
                && region.page <= segment.page_end
        })
    }) && manifest.occurrences.iter().all(|occurrence| {
        manifest.segments.iter().any(|segment| {
            segment.segment_id == occurrence.segment_id
                && occurrence.page >= segment.page_start
                && occurrence.page <= segment.page_end
        }) && occurrence.region_id.as_ref().map_or(true, |region_id| {
            manifest.regions.iter().any(|region| {
                region.region_id == *region_id
                    && region.segment_id == occurrence.segment_id
                    && region.page == occurrence.page
            })
        })
    }) && manifest
        .manual_actions
        .iter()
        .all(|action| manual_action_has_trusted_evidence(manifest, action))
        && manifest
            .review_items
            .iter()
            .all(|review| valid_review_target(manifest, review))
        && unique_ids(
            manifest
                .review_items
                .iter()
                .filter_map(|review| review.target_id.as_deref()),
        )
}

fn manual_action_has_trusted_evidence(
    manifest: &AnalysisManifestV1,
    action: &ManualAction,
) -> bool {
    if action.mode == "restore" {
        let Some(linked_id) = action.linked_occurrence_id.as_deref() else {
            return false;
        };
        let Some(expected_text_hash) = action.expected_text_hash.as_deref() else {
            return false;
        };
        return action.source_kind == "text_pdf"
            && action.restore_authorization_hash.is_some()
            && action.protected_neighbor_refs.is_empty()
            && manifest.occurrences.iter().any(|occurrence| {
                occurrence.occurrence_id == linked_id
                    && occurrence.page == action.page
                    && matches!(occurrence.state.as_str(), "confirmed" | "user_confirmed")
                    && occurrence.proposed_action == "mask"
                    && occurrence
                        .expected_text_hash
                        .eq_ignore_ascii_case(expected_text_hash)
                    && normalize_rects(&occurrence.rects) == normalize_rects(&action.rects)
            });
    }
    if action.mode != "mask" {
        return false;
    }
    if action.restore_authorization_hash.is_some() {
        return false;
    }
    if validate_resolution_rects(&action.rects).is_err() {
        return false;
    }
    if action.source_kind == "scan" {
        return action.linked_occurrence_id.is_none()
            && action.expected_text_hash.is_none()
            && action.protected_neighbor_refs.is_empty();
    }
    action.source_kind == "text_pdf"
        && !action.protected_neighbor_refs.is_empty()
        && action
            .protected_neighbor_refs
            .iter()
            .all(|rect| validate_resolution_rects(std::slice::from_ref(rect)).is_ok())
        && manifest
            .segments
            .iter()
            .any(|segment| action.page >= segment.page_start && action.page <= segment.page_end)
        && manifest.occurrences.iter().any(|occurrence| {
            occurrence.page == action.page
                && normalize_rects(&occurrence.rects)
                    == normalize_rects(&action.protected_neighbor_refs)
        })
        && !action.rects.iter().any(|mask| {
            action
                .protected_neighbor_refs
                .iter()
                .any(|protected| rects_overlap(mask, protected))
        })
        && action
            .linked_occurrence_id
            .as_ref()
            .map_or(true, |occurrence_id| {
                manifest
                    .occurrences
                    .iter()
                    .any(|occurrence| occurrence.occurrence_id == *occurrence_id)
            })
}

fn restore_authorization_summary_for(
    manifest: &AnalysisManifestV1,
    capabilities: &HashMap<String, RestoreCapabilityRecord>,
) -> Result<RestoreAuthorizationSummary, String> {
    let mut action_ids = Vec::new();
    let mut target_ids = Vec::new();
    let mut events = HashSet::new();
    for action in manifest
        .manual_actions
        .iter()
        .filter(|action| action.mode == "restore")
    {
        let capability_hash = action
            .restore_authorization_hash
            .as_deref()
            .ok_or_else(|| safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"))?;
        let capability = capabilities
            .get(capability_hash)
            .ok_or_else(|| safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"))?;
        let linked_id = action
            .linked_occurrence_id
            .as_deref()
            .ok_or_else(|| safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"))?;
        let occurrence = manifest
            .occurrences
            .iter()
            .find(|occurrence| {
                occurrence.occurrence_id == linked_id
                    && occurrence.page == action.page
                    && matches!(occurrence.state.as_str(), "confirmed" | "user_confirmed")
                    && occurrence.proposed_action == "mask"
                    && normalize_rects(&occurrence.rects) == normalize_rects(&action.rects)
                    && action.expected_text_hash.as_deref().is_some_and(|hash| {
                        occurrence.expected_text_hash.eq_ignore_ascii_case(hash)
                    })
            })
            .ok_or_else(|| safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"))?;
        if !capability.claimed
            || capability.run_id != manifest.run_id
            || capability.original_document_hash != manifest.original_document_hash
            || capability.issued_revision > manifest.analysis_revision
            || capability.issued_manifest_hash.is_empty()
            || capability.target_value_hash != occurrence.value_hash
            || capability.page != occurrence.page
            || normalize_rects(&capability.rects) != normalize_rects(&occurrence.rects)
            || !capability
                .expected_text_hash
                .eq_ignore_ascii_case(&occurrence.expected_text_hash)
        {
            return Err(safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"));
        }
        action_ids.push(action.action_id.clone());
        target_ids.push(occurrence.occurrence_id.clone());
        events.insert(capability.authorization_event.clone());
    }
    action_ids.sort();
    target_ids.sort();
    let authorization_event = if events.is_empty() {
        "none".to_string()
    } else if events.len() == 1 {
        events.into_iter().next().expect("one authorization event")
    } else {
        "mixed".to_string()
    };
    Ok(RestoreAuthorizationSummary {
        action_id_hash: canonical_json_hash(&serde_json::json!(action_ids))?,
        target_occurrence_id_hash: canonical_json_hash(&serde_json::json!(target_ids))?,
        authorization_event,
    })
}

fn blocking_items_have_pending_reviews(manifest: &AnalysisManifestV1) -> bool {
    let pending_for = |kind: ReviewKind, target: &str| {
        manifest.review_items.iter().any(|review| {
            review.status == "pending"
                && review.kind == kind
                && review.target_id.as_deref() == Some(target)
        })
    };
    manifest
        .segments
        .iter()
        .filter(|item| item.state == "review_required")
        .all(|item| {
            pending_for(ReviewKind::Acknowledge, &item.segment_id)
                || pending_for(ReviewKind::Boundary, &item.segment_id)
                || pending_for(ReviewKind::Ocr, &item.segment_id)
        })
        && manifest
            .regions
            .iter()
            .filter(|item| matches!(item.state.as_str(), "review_required" | "unconfirmed"))
            .all(|item| pending_for(ReviewKind::RegionGeometry, &item.region_id))
        && manifest
            .occurrences
            .iter()
            .filter(|item| item.proposed_action == "review" || item.state != "confirmed")
            .all(|item| {
                pending_for(ReviewKind::Name, &item.occurrence_id)
                    || pending_for(ReviewKind::Institution, &item.occurrence_id)
            })
}

fn review_kind_code(kind: &ReviewKind) -> &'static str {
    match kind {
        ReviewKind::Name => "name",
        ReviewKind::Institution => "institution",
        ReviewKind::Acknowledge => "acknowledge",
        ReviewKind::Boundary => "boundary",
        ReviewKind::Ocr => "ocr",
        ReviewKind::RegionGeometry => "region_geometry",
    }
}

fn coverage_page_range(manifest: &AnalysisManifestV1, kind: &str) -> (u32, u32) {
    let region_pages = manifest
        .regions
        .iter()
        .filter(|region| region.kind == kind)
        .map(|region| region.page)
        .collect::<Vec<_>>();
    if let (Some(first), Some(last)) = (region_pages.iter().min(), region_pages.iter().max()) {
        return (*first, *last);
    }
    let segment_pages = manifest
        .segments
        .iter()
        .flat_map(|segment| [segment.page_start, segment.page_end])
        .collect::<Vec<_>>();
    match (segment_pages.iter().min(), segment_pages.iter().max()) {
        (Some(first), Some(last)) => (*first, *last),
        _ => (0, 0),
    }
}

fn review_warning_category(manifest: &AnalysisManifestV1, review: &ReviewItem) -> String {
    match review.kind {
        ReviewKind::Name | ReviewKind::Institution => manifest
            .occurrences
            .iter()
            .find(|occurrence| {
                review.target_id.as_deref() == Some(occurrence.occurrence_id.as_str())
            })
            .map(|occurrence| occurrence.category.clone())
            .unwrap_or_else(|| review_kind_code(&review.kind).to_string()),
        ReviewKind::RegionGeometry => manifest
            .regions
            .iter()
            .find(|region| review.target_id.as_deref() == Some(region.region_id.as_str()))
            .map(|region| region.kind.clone())
            .unwrap_or_else(|| review_kind_code(&review.kind).to_string()),
        _ => review_kind_code(&review.kind).to_string(),
    }
}

pub(crate) fn finalization_save_confirmation(
    manifest: &AnalysisManifestV1,
) -> FinalizeSaveConfirmation {
    let pending_region_kinds = manifest
        .review_items
        .iter()
        .filter(|item| item.status != "resolved" && item.kind == ReviewKind::RegionGeometry)
        .filter_map(|item| {
            manifest
                .regions
                .iter()
                .find(|region| item.target_id.as_deref() == Some(region.region_id.as_str()))
                .map(|region| region.kind.clone())
        })
        .collect::<HashSet<_>>();
    let mut unresolved_reviews = manifest
        .review_items
        .iter()
        .filter(|item| item.status != "resolved")
        .map(|item| FinalizeSaveWarning {
            kind: review_kind_code(&item.kind).to_string(),
            target_id: item.target_id.clone(),
            category: review_warning_category(manifest, item),
            page_start: item.page_start,
            page_end: item.page_end,
            reason_codes: if item.reason_codes.is_empty() {
                vec!["unresolved_review".to_string()]
            } else {
                item.reason_codes.clone()
            },
        })
        .collect::<Vec<_>>();

    if manifest.approval_coverage.state == CoverageState::Indeterminate
        && !pending_region_kinds.contains("approval")
    {
        let (page_start, page_end) = coverage_page_range(manifest, "approval");
        unresolved_reviews.push(FinalizeSaveWarning {
            kind: "coverage".to_string(),
            target_id: Some("approval".to_string()),
            category: "approval".to_string(),
            page_start,
            page_end,
            reason_codes: vec!["indeterminate_coverage".to_string()],
        });
    }
    unresolved_reviews.extend(
        manifest
            .required_region_coverage
            .kinds
            .iter()
            .filter(|item| {
                item.state == CoverageState::Indeterminate
                    && !pending_region_kinds.contains(&item.kind)
                    && !(item.kind == "approval"
                        && manifest.approval_coverage.state == CoverageState::Indeterminate)
            })
            .map(|item| {
                let (page_start, page_end) = coverage_page_range(manifest, &item.kind);
                FinalizeSaveWarning {
                    kind: "coverage".to_string(),
                    target_id: Some(item.kind.clone()),
                    category: item.kind.clone(),
                    page_start,
                    page_end,
                    reason_codes: vec!["indeterminate_coverage".to_string()],
                }
            }),
    );

    FinalizeSaveConfirmation {
        status: if unresolved_reviews.is_empty() {
            "not_required".to_string()
        } else {
            "user_confirmed".to_string()
        },
        unresolved_reviews,
    }
}

fn is_safe_segment_kind(kind: &str) -> bool {
    matches!(
        kind,
        "internal_review" | "official_dispatch" | "attachment" | "legal"
    )
}

fn advance_revision(
    manifest: &mut AnalysisManifestV1,
    resolved: &ReviewItem,
    affected_start: u32,
    affected_end: u32,
) -> Result<(), String> {
    manifest.analysis_revision = manifest
        .analysis_revision
        .checked_add(1)
        .ok_or_else(|| safe_error("REVISION_OVERFLOW"))?;
    let revision = manifest.analysis_revision;
    let document_hash = manifest.original_document_hash.clone();
    let mut segment_ids = HashMap::new();
    for segment in &mut manifest.segments {
        let old_id = segment.segment_id.clone();
        segment.analysis_revision = revision;
        segment.segment_id = segment_fingerprint(&document_hash, revision, segment);
        segment_ids.insert(old_id, segment.segment_id.clone());
    }
    let mut region_ids = HashMap::new();
    for region in &mut manifest.regions {
        let old_id = region.region_id.clone();
        region.analysis_revision = revision;
        region.segment_id = segment_ids
            .get(&region.segment_id)
            .cloned()
            .ok_or_else(|| safe_error("REFERENTIAL_INTEGRITY_INVALID"))?;
        region.region_id = region_fingerprint(&document_hash, revision, region);
        region_ids.insert(old_id, region.region_id.clone());
    }
    let mut occurrence_ids = HashMap::new();
    for occurrence in &mut manifest.occurrences {
        let old_id = occurrence.occurrence_id.clone();
        occurrence.analysis_revision = revision;
        occurrence.segment_id = segment_ids
            .get(&occurrence.segment_id)
            .cloned()
            .ok_or_else(|| safe_error("REFERENTIAL_INTEGRITY_INVALID"))?;
        if let Some(region_id) = &mut occurrence.region_id {
            if let Some(replacement) = region_ids.get(region_id) {
                *region_id = replacement.clone();
            }
        }
        occurrence.occurrence_id = occurrence_fingerprint_bound(
            &document_hash,
            revision,
            &occurrence.segment_id,
            occurrence.region_id.as_deref(),
            occurrence.page,
            &occurrence.rects,
            &occurrence.tag,
            &occurrence.category,
            &occurrence.value_hash,
            &occurrence.source,
            &occurrence.policy,
            &occurrence.proposed_action,
        );
        occurrence_ids.insert(old_id, occurrence.occurrence_id.clone());
    }
    let run_id = manifest.run_id.clone();
    for action in &mut manifest.manual_actions {
        action.analysis_revision = revision;
        if let Some(linked) = &mut action.linked_occurrence_id {
            if let Some(replacement) = occurrence_ids.get(linked) {
                *linked = replacement.clone();
            }
        }
        action.action_id = manual_action_fingerprint(&run_id, &document_hash, action);
    }
    for item in &mut manifest.review_items {
        let old_id = item.review_id.clone();
        let old_status = item.status.clone();
        let outside = item.page_end < affected_start || item.page_start > affected_end;
        item.analysis_revision = revision;
        item.target_id = match item.kind {
            ReviewKind::Name | ReviewKind::Institution => item
                .target_id
                .as_ref()
                .and_then(|id| occurrence_ids.get(id))
                .cloned(),
            ReviewKind::RegionGeometry => item
                .target_id
                .as_ref()
                .and_then(|id| region_ids.get(id))
                .cloned(),
            ReviewKind::Acknowledge | ReviewKind::Boundary | ReviewKind::Ocr => item
                .target_id
                .as_ref()
                .and_then(|id| segment_ids.get(id))
                .cloned(),
        };
        item.review_id = review_fingerprint(revision, item);
        item.status = if old_id == resolved.review_id {
            "resolved".to_string()
        } else if outside {
            old_status
        } else {
            "pending".to_string()
        };
    }
    if !unique_ids(
        manifest
            .segments
            .iter()
            .map(|item| item.segment_id.as_str()),
    ) || !unique_ids(manifest.regions.iter().map(|item| item.region_id.as_str()))
        || !unique_ids(
            manifest
                .occurrences
                .iter()
                .map(|item| item.occurrence_id.as_str()),
        )
        || !unique_ids(
            manifest
                .review_items
                .iter()
                .map(|item| item.review_id.as_str()),
        )
        || !unique_ids(
            manifest
                .manual_actions
                .iter()
                .map(|item| item.action_id.as_str()),
        )
    {
        return Err(safe_error("REFERENTIAL_INTEGRITY_INVALID"));
    }
    if !manifest_referential_integrity(manifest) {
        return Err(safe_error("REFERENTIAL_INTEGRITY_INVALID"));
    }
    Ok(())
}

fn segment_fingerprint(document_hash: &str, revision: u64, segment: &AnalysisSegment) -> String {
    let material = serde_json::json!({"analysis_revision": revision, "common_only": segment.common_only,
        "document_hash": document_hash, "kind": segment.kind, "page_end": segment.page_end,
        "page_start": segment.page_start});
    format!(
        "seg_{}",
        &canonical_json_hash(&material).expect("canonical segment material")[..24]
    )
}

fn identity_rects(rects: &[Rect]) -> Vec<[u64; 4]> {
    const SCALE: f64 = 1_000_000.0;
    let mut identities = rects
        .iter()
        .map(|rect| {
            [rect.x0, rect.y0, rect.x1, rect.y1].map(|value| (value * SCALE + 0.5).floor() as u64)
        })
        .collect::<Vec<_>>();
    identities.sort();
    identities
}

fn region_fingerprint(document_hash: &str, revision: u64, region: &AnalysisRegion) -> String {
    let material = serde_json::json!({
        "analysis_revision": revision,
        "document_hash": document_hash,
        "kind": region.kind,
        "page_index": region.page,
        "rect_list": identity_rects(&region.rects),
    });
    format!(
        "region_{}",
        &canonical_json_hash(&material).expect("canonical region material")[..24]
    )
}

fn review_fingerprint(revision: u64, review: &ReviewItem) -> String {
    let material = serde_json::json!({"analysisRevision": revision, "kind": review.kind,
        "pageEnd": review.page_end, "pageStart": review.page_start, "reasonCodes": review.reason_codes,
        "targetId": review.target_id});
    format!(
        "review_{}",
        &canonical_json_hash(&material).expect("canonical review material")[..24]
    )
}
#[cfg(test)]
pub fn occurrence_fingerprint(
    document_hash: &str,
    analysis_revision: u64,
    page: u32,
    rects: &[Rect],
    tag: &str,
    category: &str,
    value_hash: &str,
    source: &str,
    policy: &str,
    proposed_action: &str,
) -> String {
    let normalized = identity_rects(rects);
    let material = serde_json::json!({
        "analysisRevision": analysis_revision, "category": category, "documentHash": document_hash,
        "page": page, "policy": policy, "proposedAction": proposed_action, "rects": normalized,
        "source": source, "tag": tag, "valueHash": value_hash,
    });
    format!(
        "occ_{}",
        &canonical_json_hash(&material).expect("canonical occurrence material")[..24]
    )
}

fn occurrence_fingerprint_bound(
    document_hash: &str,
    analysis_revision: u64,
    segment_id: &str,
    region_id: Option<&str>,
    page: u32,
    rects: &[Rect],
    tag: &str,
    category: &str,
    value_hash: &str,
    source: &str,
    policy: &str,
    proposed_action: &str,
) -> String {
    let normalized = identity_rects(rects);
    let material = serde_json::json!({
        "analysisRevision": analysis_revision, "category": category, "documentHash": document_hash,
        "page": page, "policy": policy, "proposedAction": proposed_action, "rects": normalized,
        "regionId": region_id, "segmentId": segment_id, "source": source, "tag": tag,
        "valueHash": value_hash,
    });
    format!(
        "occ_{}",
        &canonical_json_hash(&material).expect("canonical occurrence material")[..24]
    )
}
fn manual_action_fingerprint(run_id: &str, document_hash: &str, action: &ManualAction) -> String {
    let rects = normalize_rects(&action.rects)
        .into_iter()
        .map(|rect| [rect.x0, rect.y0, rect.x1, rect.y1])
        .collect::<Vec<_>>();
    let material = serde_json::json!({
        "schema": "ManualActionV1",
        "runId": run_id,
        "documentHash": document_hash,
        "analysisRevision": action.analysis_revision,
        "actionKind": action.mode,
        "occurrenceId": action.linked_occurrence_id,
        "page": action.page,
        "rects": rects,
        "sourceKind": action.source_kind,
        "expectedTextHash": action.expected_text_hash,
        "protectedNeighborRefs": action.protected_neighbor_refs,
        "restoreAuthorizationHash": action.restore_authorization_hash,
    });
    format!(
        "action_{}",
        &canonical_json_hash(&material).expect("canonical manual action material")[..24]
    )
}
fn rect_key(rect: &Rect) -> (u64, u64, u64, u64) {
    (
        rect.x0.to_bits(),
        rect.y0.to_bits(),
        rect.x1.to_bits(),
        rect.y1.to_bits(),
    )
}
fn normalize_rects(rects: &[Rect]) -> Vec<Rect> {
    let mut normalized = rects.to_vec();
    normalized.sort_by(|a, b| rect_key(a).cmp(&rect_key(b)));
    normalized
}

fn refresh_manifest_hash(manifest: &mut AnalysisManifestV1) -> Result<(), String> {
    refresh_manifest_hash_with_key(manifest, &[])
}

fn refresh_manifest_hash_with_key(
    manifest: &mut AnalysisManifestV1,
    key: &[u8],
) -> Result<(), String> {
    manifest.manifest_hash.clear();
    manifest
        .segments
        .sort_by(|a, b| a.segment_id.cmp(&b.segment_id));
    manifest
        .regions
        .sort_by(|a, b| a.region_id.cmp(&b.region_id));
    manifest
        .occurrences
        .sort_by(|a, b| a.occurrence_id.cmp(&b.occurrence_id));
    manifest
        .review_items
        .sort_by(|a, b| a.review_id.cmp(&b.review_id));
    manifest
        .manual_actions
        .sort_by(|a, b| a.action_id.cmp(&b.action_id));
    let value = serde_json::to_value(&*manifest)
        .map_err(|_| safe_error("CANONICAL_SERIALIZATION_FAILED"))?;
    let canonical = canonical_json(&value);
    manifest.manifest_hash = if key.is_empty() {
        sha256_hex(canonical.as_bytes())
    } else {
        hmac_sha256_hex(key, canonical.as_bytes())
    };
    Ok(())
}

pub(crate) fn canonical_json_hash(value: &impl Serialize) -> Result<String, String> {
    let value =
        serde_json::to_value(value).map_err(|_| safe_error("CANONICAL_SERIALIZATION_FAILED"))?;
    Ok(sha256_hex(canonical_json(&value).as_bytes()))
}

fn canonical_json(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::Object(map) => {
            let ordered: BTreeMap<_, _> = map.iter().collect();
            format!(
                "{{{}}}",
                ordered
                    .into_iter()
                    .map(|(key, value)| format!(
                        "{}:{}",
                        serde_json::to_string(key).unwrap_or_default(),
                        canonical_json(value)
                    ))
                    .collect::<Vec<_>>()
                    .join(",")
            )
        }
        serde_json::Value::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(canonical_json)
                .collect::<Vec<_>>()
                .join(",")
        ),
        _ => value.to_string(),
    }
}

pub fn document_hash(bytes: &[u8]) -> String {
    sha256_hex(bytes)
}
fn hmac_sha256_hex(key: &[u8], message: &[u8]) -> String {
    const BLOCK_SIZE: usize = 64;
    let mut normalized = [0_u8; BLOCK_SIZE];
    if key.len() > BLOCK_SIZE {
        normalized[..32].copy_from_slice(&sha256_bytes(key));
    } else {
        normalized[..key.len()].copy_from_slice(key);
    }
    let mut inner = Vec::with_capacity(BLOCK_SIZE + message.len());
    inner.extend(normalized.iter().map(|byte| byte ^ 0x36));
    inner.extend_from_slice(message);
    let inner_hash = sha256_bytes(&inner);
    let mut outer = Vec::with_capacity(BLOCK_SIZE + inner_hash.len());
    outer.extend(normalized.iter().map(|byte| byte ^ 0x5c));
    outer.extend_from_slice(&inner_hash);
    hex_bytes(&sha256_bytes(&outer))
}

// Small dependency-free SHA-256 implementation; hashes are the only document/value identity exposed.
fn sha256_hex(bytes: &[u8]) -> String {
    hex_bytes(&sha256_bytes(bytes))
}

fn hex_bytes(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn sha256_bytes(bytes: &[u8]) -> [u8; 32] {
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    let bit_len = (bytes.len() as u64).wrapping_mul(8);
    let mut data = bytes.to_vec();
    data.push(0x80);
    while (data.len() + 8) % 64 != 0 {
        data.push(0);
    }
    data.extend_from_slice(&bit_len.to_be_bytes());
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    for chunk in data.chunks_exact(64) {
        let mut w = [0u32; 64];
        for (i, word) in w.iter_mut().take(16).enumerate() {
            *word = u32::from_be_bytes(chunk[i * 4..i * 4 + 4].try_into().unwrap());
        }
        for i in 16..64 {
            w[i] = w[i - 16]
                .wrapping_add(
                    w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3),
                )
                .wrapping_add(w[i - 7])
                .wrapping_add(
                    w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10),
                );
        }
        let (mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh) =
            (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]);
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
        h[5] = h[5].wrapping_add(f);
        h[6] = h[6].wrapping_add(g);
        h[7] = h[7].wrapping_add(hh);
    }
    let mut result = [0_u8; 32];
    for (index, word) in h.iter().enumerate() {
        result[index * 4..index * 4 + 4].copy_from_slice(&word.to_be_bytes());
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use lopdf::dictionary;

    #[test]
    fn boundary_correction_kind_allowlist_matches_the_public_contract() {
        for kind in [
            "internal_review",
            "official_dispatch",
            "attachment",
            "legal",
        ] {
            assert!(is_safe_segment_kind(kind), "{kind} must be allowed");
        }
        for kind in ["mixed", "common", "unknown", "arbitrary"] {
            assert!(!is_safe_segment_kind(kind), "{kind} must be rejected");
        }
    }

    #[test]
    fn finalize_result_serializes_the_public_attestation_contract() {
        let result = FinalizeMaskingRunResult {
            run_id: "run-123".to_string(),
            analysis_revision: 7,
            manifest_hash: "b".repeat(64),
            final_path: "/safe/final.pdf".to_string(),
            final_hash: "a".repeat(64),
            final_hash_attested: true,
            occurrence_count: 3,
            applied_mask_count: 3,
            manual_mask_count: 0,
            restore_count: 0,
            effective_mask_count: 3,
            restore_authorization: RestoreAuthorizationSummary {
                action_id_hash: "c".repeat(64),
                target_occurrence_id_hash: "d".repeat(64),
                authorization_event: "none".to_string(),
            },
            save_confirmation: FinalizeSaveConfirmation {
                status: "not_required".to_string(),
                unresolved_reviews: Vec::new(),
            },
            status: "promoted",
        };

        assert_eq!(
            serde_json::to_value(result).expect("finalize result serializes"),
            serde_json::json!({
                "runId": "run-123",
                "analysisRevision": 7,
                "manifestHash": "b".repeat(64),
                "finalPath": "/safe/final.pdf",
                "finalHash": "a".repeat(64),
                "finalHashAttested": true,
                "occurrenceCount": 3,
                "appliedMaskCount": 3,
                "manualMaskCount": 0,
                "restoreCount": 0,
                "effectiveMaskCount": 3,
                "restoreAuthorization": {
                    "actionIdHash": "c".repeat(64),
                    "targetOccurrenceIdHash": "d".repeat(64),
                    "authorizationEvent": "none",
                },
                "saveConfirmation": {
                    "status": "not_required",
                    "unresolvedReviews": [],
                },
                "status": "promoted",
            })
        );
    }

    fn rect(x0: f64, y0: f64, x1: f64, y1: f64) -> Rect {
        Rect { x0, y0, x1, y1 }
    }
    fn one_page_pdf() -> Vec<u8> {
        let mut document = lopdf::Document::with_version("1.5");
        let pages = document.new_object_id();
        let page = document.new_object_id();
        let contents = document.add_object(lopdf::Stream::new(dictionary! {}, Vec::new()));
        document.objects.insert(
            pages,
            dictionary! {
                "Type" => "Pages",
                "Kids" => vec![page.into()],
                "Count" => 1,
            }
            .into(),
        );
        document.objects.insert(
            page,
            dictionary! {
                "Type" => "Page",
                "Parent" => pages,
                "MediaBox" => vec![0.into(), 0.into(), 10.into(), 10.into()],
                "Resources" => dictionary! {},
                "Contents" => contents,
            }
            .into(),
        );
        let catalog = document.add_object(dictionary! { "Type" => "Catalog", "Pages" => pages });
        document.trailer.set("Root", catalog);
        let mut bytes = Vec::new();
        document.save_to(&mut bytes).expect("valid one-page PDF");
        bytes
    }

    fn public_options() -> serde_json::Value {
        serde_json::json!({
            "rrn": true, "phone": true, "business_reg": true, "name": true, "address": true,
            "place": true, "legal_party": true, "company": true, "court": true, "case_title": true,
            "case_number": true, "law_firm": true, "attorney": true, "approval_line": true,
            "region_context": true, "doc_meta": true, "email": true, "pdf_redaction": true, "custom_keywords": "",
            "extract_engine": "auto", "profile": "mixed", "output_artifacts": "pdf_safe_report",
            "display_mode": "black", "deidentification_policy": "token", "region_scope": "national",
            "custom_regions": "", "return_text_preview": false,
            "auto_mask_threshold": 0.85, "review_threshold": 0.5
        })
    }

    fn trusted_reanalysis_manifest(
        original: &[u8],
        options: &serde_json::Value,
        revision: u64,
        segment_state: &str,
        pending_review_kind: Option<&str>,
    ) -> serde_json::Value {
        let document_hash = sha256_hex(original);
        let segment = AnalysisSegment {
            segment_id: String::new(),
            analysis_revision: revision,
            page_start: 0,
            page_end: 0,
            kind: "official_dispatch".to_string(),
            state: segment_state.to_string(),
            common_only: false,
            source: "trusted".to_string(),
        };
        let segment_id = segment_fingerprint(&document_hash, revision, &segment);
        let threshold = threshold_artifact(0.85, 0.5).expect("threshold artifact");
        let review_items = pending_review_kind.map_or_else(Vec::new, |kind| {
            vec![serde_json::json!({
                "review_id": "review_reanalysis_0001",
                "analysis_revision": revision,
                "kind": kind,
                "target_id": segment_id,
                "page_start": 0,
                "page_end": 0,
                "status": "pending",
                "reason_codes": ["reanalysis_required"],
                "requires_acknowledgment": true,
                "common_only": false,
                "provenance": "trusted"
            })]
        });
        serde_json::json!({
            "schema_version": 1,
            "original_document_hash": document_hash,
            "profile": "mixed",
            "coordinate_space": "pdf_points_top_left",
            "analysis_revision": revision,
            "segments": [{
                "segment_id": segment_id,
                "analysis_revision": revision,
                "page_start": 0,
                "page_end": 0,
                "kind": "official_dispatch",
                "state": segment_state,
                "common_only": false,
                "source": "trusted"
            }],
            "regions": [],
            "occurrences": [],
            "manual_actions": [],
            "review_items": review_items,
            "policy_version": POLICY_VERSION,
            "options_version": OPTIONS_VERSION,
            "options_hash": canonical_json_hash(options).expect("options hash"),
            "threshold_version": THRESHOLD_VERSION,
            "threshold_hash": threshold.content_hash,
            "threshold_artifact": {
                "version": threshold.version,
                "content_hash": threshold.content_hash,
                "auto_mask_threshold": threshold.auto_mask_threshold,
                "review_threshold": threshold.review_threshold
            },
            "approval_coverage": {
                "schema_version": 1,
                "state": "absent",
                "signer_count": 0,
                "protected_neighbor_count": 0
            },
            "required_region_coverage": {
                "schema_version": 1,
                "profile": "mixed",
                "kinds": required_region_kinds("mixed").iter().map(|kind| serde_json::json!({
                    "kind": kind,
                    "state": "absent"
                })).collect::<Vec<_>>(),
                "blocking": false
            }
        })
    }

    fn session_with_reanalysis_review(
        review_kind: &str,
    ) -> (
        MaskingRunSessions,
        Vec<u8>,
        serde_json::Value,
        AnalysisManifestV1,
    ) {
        let sessions = MaskingRunSessions::default();
        let original = one_page_pdf();
        let options = public_options();
        let trusted = trusted_reanalysis_manifest(
            &original,
            &options,
            1,
            "review_required",
            Some(review_kind),
        );
        let manifest = sessions
            .create_from_trusted(&original, "mixed", options.clone(), &trusted)
            .expect("initial trusted manifest");
        sessions
            .bind_private_context(
                &manifest.run_id,
                std::path::PathBuf::from("/nonexistent/in-memory.pdf"),
                options.clone(),
            )
            .expect("private reanalysis context");
        (sessions, original, options, manifest)
    }
    #[test]
    fn trusted_producer_authority_mismatches_fail_before_session_mutation() {
        let original = one_page_pdf();
        let options = public_options();
        for field in ["policy_version", "options_version", "options_hash"] {
            let mut trusted =
                trusted_reanalysis_manifest(&original, &options, 1, "confirmed", None);
            trusted[field] = serde_json::Value::String("mismatch".to_string());
            let sessions = MaskingRunSessions::default();
            assert_eq!(
                safe_error("TRUSTED_MANIFEST_AUTHORITY_MISMATCH"),
                sessions
                    .create_from_trusted(&original, "mixed", options.clone(), &trusted)
                    .expect_err("producer authority mismatch must fail")
            );
            assert!(sessions.get("uncreated-run").is_err());
        }
    }
    #[test]
    fn boundary_reanalysis_replaces_once_and_rejects_stale_replay() {
        let (sessions, original, options, prior) = session_with_reanalysis_review("boundary");
        let request = ResolveMaskingReviewRequest {
            run_id: prior.run_id.clone(),
            analysis_revision: prior.analysis_revision,
            manifest_hash: prior.manifest_hash.clone(),
            review_id: prior.review_items[0].review_id.clone(),
            resolution: ReviewResolution::Boundary {
                page_start: 0,
                page_end: 0,
                segment_kind: "official_dispatch".to_string(),
            },
        };
        let candidate = trusted_reanalysis_manifest(&original, &options, 2, "user_confirmed", None);

        let replaced = sessions
            .replace_from_trusted_reanalysis(request.clone(), &original, &candidate)
            .expect("corrected boundary reanalysis is accepted");

        assert_eq!(prior.run_id, replaced.run_id);
        assert_eq!(2, replaced.analysis_revision);
        assert_ne!(prior.manifest_hash, replaced.manifest_hash);
        assert_eq!(1, replaced.segments.len());
        assert_eq!("user_confirmed", replaced.segments[0].state);
        assert_eq!(
            1,
            replaced
                .review_items
                .iter()
                .filter(|review| review.status == "resolved" && review.kind == ReviewKind::Boundary)
                .count()
        );
        assert_eq!(
            replaced.manifest_hash,
            sessions
                .get(&prior.run_id)
                .expect("stored replacement")
                .manifest_hash
        );

        assert_eq!(
            safe_error("STALE_ANALYSIS"),
            sessions
                .replace_from_trusted_reanalysis(request, &original, &candidate)
                .expect_err("stale replay must fail")
        );
        let stored = sessions
            .get(&prior.run_id)
            .expect("stale replay leaves replacement stored");
        assert_eq!(replaced.analysis_revision, stored.analysis_revision);
        assert_eq!(replaced.manifest_hash, stored.manifest_hash);
        assert_eq!(1, stored.review_items.len());
    }

    #[test]
    fn boundary_reanalysis_rejects_a_range_outside_its_review_target() {
        let (sessions, _original, _options, prior) = session_with_reanalysis_review("boundary");
        let request = ResolveMaskingReviewRequest {
            run_id: prior.run_id.clone(),
            analysis_revision: prior.analysis_revision,
            manifest_hash: prior.manifest_hash.clone(),
            review_id: prior.review_items[0].review_id.clone(),
            resolution: ReviewResolution::Boundary {
                page_start: 0,
                page_end: 1,
                segment_kind: "official_dispatch".to_string(),
            },
        };

        assert_eq!(
            safe_error("BOUNDARY_CORRECTION_OUT_OF_RANGE"),
            sessions
                .reanalysis_context(&request)
                .expect_err("boundary correction must stay inside the review target segment")
        );
    }

    #[test]
    fn invalid_reanalysis_candidates_preserve_prior_session() {
        let (sessions, original, options, prior) = session_with_reanalysis_review("boundary");
        let prior_snapshot = serde_json::to_value(&prior).expect("serializable prior manifest");
        let request = ResolveMaskingReviewRequest {
            run_id: prior.run_id.clone(),
            analysis_revision: prior.analysis_revision,
            manifest_hash: prior.manifest_hash.clone(),
            review_id: prior.review_items[0].review_id.clone(),
            resolution: ReviewResolution::Boundary {
                page_start: 0,
                page_end: 0,
                segment_kind: "official_dispatch".to_string(),
            },
        };
        let wrong_revision =
            trusted_reanalysis_manifest(&original, &options, 3, "user_confirmed", None);
        assert_eq!(
            safe_error("REANALYSIS_MANIFEST_INVALID"),
            sessions
                .replace_from_trusted_reanalysis(request.clone(), &original, &wrong_revision)
                .expect_err("wrong revision must fail")
        );
        let after_wrong_revision = sessions.get(&prior.run_id).expect("prior session remains");
        assert_eq!(
            prior.analysis_revision,
            after_wrong_revision.analysis_revision
        );
        assert_eq!(prior.manifest_hash, after_wrong_revision.manifest_hash);
        assert_eq!(
            prior_snapshot,
            serde_json::to_value(&after_wrong_revision).expect("serializable stored manifest")
        );

        let missing_correction =
            trusted_reanalysis_manifest(&original, &options, 2, "confirmed", None);
        assert_eq!(
            safe_error("REANALYSIS_EVIDENCE_REQUIRED"),
            sessions
                .replace_from_trusted_reanalysis(request, &original, &missing_correction)
                .expect_err("missing correction must fail")
        );
        let after_missing_correction = sessions
            .get(&prior.run_id)
            .expect("prior session remains after missing evidence");
        assert_eq!(
            prior.analysis_revision,
            after_missing_correction.analysis_revision
        );
        assert_eq!(prior.manifest_hash, after_missing_correction.manifest_hash);
        assert_eq!(
            prior_snapshot,
            serde_json::to_value(&after_missing_correction).expect("serializable stored manifest")
        );
    }

    #[test]
    fn ocr_reanalysis_requires_fresh_occurrence_geometry_and_hash() {
        let (sessions, original, options, prior) = session_with_reanalysis_review("ocr");
        let prior_snapshot = serde_json::to_value(&prior).expect("serializable prior manifest");
        let request = ResolveMaskingReviewRequest {
            run_id: prior.run_id.clone(),
            analysis_revision: prior.analysis_revision,
            manifest_hash: prior.manifest_hash.clone(),
            review_id: prior.review_items[0].review_id.clone(),
            resolution: ReviewResolution::Ocr { accepted: true },
        };
        let candidate = trusted_reanalysis_manifest(&original, &options, 2, "user_confirmed", None);

        assert_eq!(
            safe_error("REANALYSIS_EVIDENCE_REQUIRED"),
            sessions
                .replace_from_trusted_reanalysis(request, &original, &candidate)
                .expect_err("missing OCR evidence must fail")
        );
        let stored = sessions
            .get(&prior.run_id)
            .expect("OCR failure leaves session untouched");
        assert_eq!(prior.analysis_revision, stored.analysis_revision);
        assert_eq!(prior.manifest_hash, stored.manifest_hash);
        assert_eq!(
            prior_snapshot,
            serde_json::to_value(&stored).expect("serializable stored manifest")
        );
    }

    fn manifest_with_common_only_review() -> AnalysisManifestV1 {
        let mut manifest = AnalysisManifestV1 {
            manifest_version: MANIFEST_VERSION,
            run_id: "run_test_acknowledgment".to_string(),
            original_document_hash: "a".repeat(64),
            analysis_revision: 1,
            manifest_hash: String::new(),
            profile: "mixed".to_string(),
            policy_version: POLICY_VERSION.to_string(),
            options_version: OPTIONS_VERSION.to_string(),
            options_hash: "b".repeat(64),
            threshold_version: THRESHOLD_VERSION.to_string(),
            threshold_hash: threshold_artifact_hash(0.85, 0.5).expect("threshold hash"),
            threshold_artifact: threshold_artifact(0.85, 0.5).expect("threshold artifact"),
            coordinate_space: "pdf_points_top_left".to_string(),
            approval_coverage: ApprovalCoverage {
                schema_version: 1,
                state: CoverageState::Absent,
                signer_count: 0,
                protected_neighbor_count: 0,
            },
            required_region_coverage: RequiredRegionCoverage {
                schema_version: 1,
                profile: "mixed".to_string(),
                kinds: required_region_kinds("mixed")
                    .iter()
                    .map(|kind| RequiredRegionCoverageKind {
                        kind: (*kind).to_string(),
                        state: CoverageState::Absent,
                    })
                    .collect(),
                blocking: false,
            },
            segments: vec![AnalysisSegment {
                segment_id: "segment_common_only_0001".to_string(),
                analysis_revision: 1,
                page_start: 0,
                page_end: 0,
                kind: "common".to_string(),
                state: "review_required".to_string(),
                common_only: true,
                source: "routing".to_string(),
            }],
            regions: vec![],
            occurrences: vec![],
            review_items: vec![ReviewItem {
                review_id: "review_acknowledge_0001".to_string(),
                analysis_revision: 1,
                kind: ReviewKind::Acknowledge,
                target_id: Some("segment_common_only_0001".to_string()),
                page_start: 0,
                page_end: 0,
                status: "pending".to_string(),
                reason_codes: vec!["ambiguous_boundary".to_string()],
                requires_acknowledgment: true,
                common_only: true,
                provenance: "routing".to_string(),
            }],
            manual_actions: vec![],
        };
        refresh_manifest_hash(&mut manifest).expect("canonical manifest");
        manifest
    }

    #[test]
    fn manual_action_v1_fingerprint_binds_run_occurrence_and_normalized_rects() {
        let mut action = ManualAction {
            action_id: String::new(),
            analysis_revision: 7,
            page: 2,
            rects: vec![rect(30.0, 40.0, 50.0, 60.0), rect(1.0, 2.0, 3.0, 4.0)],
            mode: "mask".to_string(),
            source_kind: "text_pdf".to_string(),
            linked_occurrence_id: Some("occurrence_bound_0001".to_string()),
            expected_text_hash: Some("b".repeat(64)),
            protected_neighbor_refs: vec![rect(52.0, 40.0, 62.0, 60.0)],
            restore_authorization_hash: None,
        };
        let first = manual_action_fingerprint("run_fixture_v1", &"a".repeat(64), &action);
        action.rects.reverse();
        assert_eq!(
            first,
            manual_action_fingerprint("run_fixture_v1", &"a".repeat(64), &action)
        );
        action.linked_occurrence_id = Some("occurrence_bound_0002".to_string());
        assert_ne!(
            first,
            manual_action_fingerprint("run_fixture_v1", &"a".repeat(64), &action)
        );
    }

    #[test]
    fn scan_manual_action_allows_unbound_rectangles_only_on_scanned_segments() {
        let sessions = MaskingRunSessions::default();
        let mut manifest = manifest_with_common_only_review();
        manifest.segments[0].kind = "unknown".to_string();
        manifest.segments[0].common_only = false;
        manifest.segments[0].source = "scanned_geometry_unavailable".to_string();
        manifest.review_items[0].common_only = false;
        manifest.review_items[0].reason_codes = vec!["scanned_geometry_unavailable".to_string()];
        refresh_manifest_hash(&mut manifest).expect("canonical scanned manifest");
        sessions
            .state
            .lock()
            .expect("session lock")
            .sessions
            .insert(
                manifest.run_id.clone(),
                SessionRecord {
                    manifest: manifest.clone(),
                    original: None,
                    options: None,
                    lifecycle: SessionLifecycle::Ready,
                },
            );

        let updated = sessions
            .apply_manual_action(ManualActionV1Request {
                run_id: manifest.run_id,
                analysis_revision: manifest.analysis_revision,
                manifest_hash: manifest.manifest_hash,
                page: 0,
                rects: vec![rect(10.0, 10.0, 50.0, 50.0)],
                mode: "mask".to_string(),
                source_kind: "scan".to_string(),
                linked_occurrence_id: None,
                target_region_id: None,
                expected_text_hash: None,
                protected_neighbor_refs: vec![],
                restore_capability: None,
            })
            .expect("scanned manual action should be accepted");

        assert_eq!(2, updated.analysis_revision);
        assert_eq!(1, updated.manual_actions.len());
        assert_eq!("scan", updated.manual_actions[0].source_kind);
        assert!(updated.manual_actions[0].protected_neighbor_refs.is_empty());
    }

    #[test]
    fn unbound_scan_restore_is_rejected_without_native_authorization() {
        let sessions = MaskingRunSessions::default();
        let manifest = manifest_with_common_only_review();
        sessions
            .state
            .lock()
            .expect("session lock")
            .sessions
            .insert(
                manifest.run_id.clone(),
                SessionRecord {
                    manifest: manifest.clone(),
                    original: None,
                    options: None,
                    lifecycle: SessionLifecycle::Ready,
                },
            );

        let error = sessions
            .apply_manual_action(ManualActionV1Request {
                run_id: manifest.run_id,
                analysis_revision: manifest.analysis_revision,
                manifest_hash: manifest.manifest_hash,
                page: 0,
                rects: vec![rect(10.0, 10.0, 50.0, 50.0)],
                mode: "restore".to_string(),
                source_kind: "scan".to_string(),
                linked_occurrence_id: None,
                target_region_id: None,
                expected_text_hash: None,
                protected_neighbor_refs: vec![],
                restore_capability: None,
            })
            .expect_err("unbound scan restore must be rejected");

        assert_eq!(safe_error("MANUAL_ACTION_AUTHORIZATION_REQUIRED"), error);
    }

    #[test]
    fn native_restore_capability_allows_confirmed_target_and_is_consumed_at_finalize() {
        let mut manifest = manifest_with_common_only_review();
        manifest.segments[0].state = "confirmed".to_string();
        manifest.review_items[0].status = "resolved".to_string();
        manifest.occurrences.push(AnalysisOccurrence {
            occurrence_id: "occurrence_confirmed_mask_0001".to_string(),
            segment_id: manifest.segments[0].segment_id.clone(),
            region_id: None,
            analysis_revision: manifest.analysis_revision,
            page: 0,
            rects: vec![rect(10.0, 10.0, 20.0, 20.0)],
            tag: "phone".to_string(),
            category: "phone".to_string(),
            value_hash: "c".repeat(64),
            expected_text_hash: "d".repeat(64),
            source: "common_detector".to_string(),
            policy: POLICY_VERSION.to_string(),
            proposed_action: "mask".to_string(),
            state: "confirmed".to_string(),
            provenance: "common_detector".to_string(),
        });
        refresh_manifest_hash(&mut manifest).expect("canonical restore manifest");
        let sessions = MaskingRunSessions::default();
        let original = manifest.original_document_hash.clone();
        let run_id = manifest.run_id.clone();
        let revision = manifest.analysis_revision;
        let manifest_hash = manifest.manifest_hash.clone();
        let target = manifest.occurrences[0].clone();
        sessions
            .state
            .lock()
            .expect("session lock")
            .sessions
            .insert(
                run_id.clone(),
                SessionRecord {
                    manifest: manifest.clone(),
                    original: Some(std::path::PathBuf::from("/tmp/original.pdf")),
                    options: Some(serde_json::json!({})),
                    lifecycle: SessionLifecycle::Ready,
                },
            );
        let issued = sessions
            .issue_restore_capability(
                RestoreCapabilityRequest {
                    run_id: run_id.clone(),
                    analysis_revision: revision,
                    manifest_hash: manifest_hash.clone(),
                    occurrence_id: target.occurrence_id.clone(),
                    rects: target.rects.clone(),
                    expected_text_hash: target.expected_text_hash.clone(),
                },
                "native_trusted_ui",
            )
            .expect("native restore capability should be issued");
        let updated = sessions
            .apply_manual_action(ManualActionV1Request {
                run_id: run_id.clone(),
                analysis_revision: revision,
                manifest_hash,
                page: target.page,
                rects: target.rects.clone(),
                mode: "restore".to_string(),
                source_kind: "text_pdf".to_string(),
                linked_occurrence_id: Some(target.occurrence_id.clone()),
                target_region_id: None,
                expected_text_hash: Some(target.expected_text_hash.clone()),
                protected_neighbor_refs: vec![],
                restore_capability: Some(issued.capability),
            })
            .expect("authorized restore should be accepted");
        assert_eq!("restore", updated.manual_actions[0].mode);
        assert!(updated.manual_actions[0]
            .restore_authorization_hash
            .is_some());
        assert_eq!(2, updated.analysis_revision);
        assert_eq!(original, updated.original_document_hash);
        let summary = sessions
            .restore_authorization_summary(&updated)
            .expect("claimed capability should produce a summary");
        assert_eq!("native_trusted_ui", summary.authorization_event);
        sessions
            .finalize_context(
                &run_id,
                updated.analysis_revision,
                &updated.manifest_hash,
                true,
            )
            .expect("explicitly authorized restore may overlap its confirmed target");
        sessions
            .consume_restore_authorizations(
                &run_id,
                updated.analysis_revision,
                &updated.manifest_hash,
            )
            .expect("finalize consumes the restore capability");
        sessions
            .finish_finalize(&run_id, FinalizeDisposition::Consumed)
            .expect("consumed finalize completes");
        assert_eq!(
            safe_error("RUN_CONSUMED"),
            sessions
                .get(&run_id)
                .expect_err("consumed session rejects replay")
        );
    }

    #[test]
    fn finalize_rejects_restore_without_native_capability() {
        let manifest_with_restore = |restore_rect: Rect| {
            let mut manifest = manifest_with_common_only_review();
            manifest.segments[0].state = "confirmed".to_string();
            manifest.review_items[0].status = "resolved".to_string();
            manifest.occurrences.push(AnalysisOccurrence {
                occurrence_id: "occurrence_confirmed_mask_0001".to_string(),
                segment_id: manifest.segments[0].segment_id.clone(),
                region_id: None,
                analysis_revision: manifest.analysis_revision,
                page: 0,
                rects: vec![rect(10.0, 10.0, 20.0, 20.0)],
                tag: "phone".to_string(),
                category: "phone".to_string(),
                value_hash: "c".repeat(64),
                expected_text_hash: "d".repeat(64),
                source: "common_detector".to_string(),
                policy: POLICY_VERSION.to_string(),
                proposed_action: "mask".to_string(),
                state: "confirmed".to_string(),
                provenance: "common_detector".to_string(),
            });
            manifest.manual_actions.push(ManualAction {
                action_id: "manual_restore_0001".to_string(),
                analysis_revision: manifest.analysis_revision,
                page: 0,
                rects: vec![restore_rect],
                mode: "restore".to_string(),
                source_kind: "scan".to_string(),
                linked_occurrence_id: None,
                expected_text_hash: None,
                protected_neighbor_refs: vec![],
                restore_authorization_hash: None,
            });
            refresh_manifest_hash(&mut manifest).expect("canonical restore manifest");
            manifest
        };
        let insert_ready = |sessions: &MaskingRunSessions, manifest: &AnalysisManifestV1| {
            sessions
                .state
                .lock()
                .expect("session lock")
                .sessions
                .insert(
                    manifest.run_id.clone(),
                    SessionRecord {
                        manifest: manifest.clone(),
                        original: Some(std::path::PathBuf::from("/tmp/original.pdf")),
                        options: Some(serde_json::json!({})),
                        lifecycle: SessionLifecycle::Ready,
                    },
                );
        };

        let blocked_sessions = MaskingRunSessions::default();
        let blocked = manifest_with_restore(rect(12.0, 12.0, 18.0, 18.0));
        insert_ready(&blocked_sessions, &blocked);
        assert!(matches!(
            blocked_sessions.finalize_context(
                &blocked.run_id,
                blocked.analysis_revision,
                &blocked.manifest_hash,
                true,
            ),
            Err(error) if error == "MASKING_SESSION_MANUAL_ACTION_AUTHORIZATION_REQUIRED"
        ));
    }

    #[test]
    fn scan_manual_action_allows_unbound_mask_rectangles_on_text_routing_segments() {
        let sessions = MaskingRunSessions::default();
        let manifest = manifest_with_common_only_review();
        sessions
            .state
            .lock()
            .expect("session lock")
            .sessions
            .insert(
                manifest.run_id.clone(),
                SessionRecord {
                    manifest: manifest.clone(),
                    original: None,
                    options: None,
                    lifecycle: SessionLifecycle::Ready,
                },
            );

        let updated = sessions
            .apply_manual_action(ManualActionV1Request {
                run_id: manifest.run_id,
                analysis_revision: manifest.analysis_revision,
                manifest_hash: manifest.manifest_hash,
                page: 0,
                rects: vec![rect(10.0, 10.0, 50.0, 50.0)],
                mode: "mask".to_string(),
                source_kind: "scan".to_string(),
                linked_occurrence_id: None,
                target_region_id: None,
                expected_text_hash: None,
                protected_neighbor_refs: vec![],
                restore_capability: None,
            })
            .expect("text routing pages must accept unbound scan mask actions");

        assert_eq!(2, updated.analysis_revision);
        assert_eq!(1, updated.manual_actions.len());
        assert_eq!("scan", updated.manual_actions[0].source_kind);
    }
    #[test]
    fn completed_tombstones_reject_replay_and_evict_oldest_deterministically() {
        let mut state = SessionState::default();
        for index in 0..=MAX_COMPLETED_TOMBSTONES {
            insert_completed_tombstone(&mut state, format!("run_{index}"), "RUN_CONSUMED");
        }
        assert_eq!(
            replay_or_unknown(&state, "run_0"),
            safe_error("UNKNOWN_RUN")
        );
        assert_eq!(
            replay_or_unknown(&state, &format!("run_{MAX_COMPLETED_TOMBSTONES}")),
            safe_error("RUN_CONSUMED")
        );
    }
    #[test]
    fn acknowledgement_confirms_common_only_segment_without_enabling_profile_rules() {
        let sessions = MaskingRunSessions::default();
        let manifest = manifest_with_common_only_review();
        sessions.state.lock().unwrap().sessions.insert(
            manifest.run_id.clone(),
            SessionRecord {
                manifest: manifest.clone(),
                original: None,
                options: None,
                lifecycle: SessionLifecycle::Ready,
            },
        );

        let resolved = sessions
            .resolve(ResolveMaskingReviewRequest {
                run_id: manifest.run_id,
                analysis_revision: manifest.analysis_revision,
                manifest_hash: manifest.manifest_hash,
                review_id: "review_acknowledge_0001".to_string(),
                resolution: ReviewResolution::Acknowledge { acknowledged: true },
            })
            .expect("acknowledgment should resolve the common-only warning");

        assert_eq!("user_confirmed", resolved.segments[0].state);
        assert!(resolved.segments[0].common_only);
        assert_eq!("resolved", resolved.review_items[0].status);
    }
    #[test]
    fn acknowledgement_confirms_scanned_segment_after_manual_review() {
        let sessions = MaskingRunSessions::default();
        let mut manifest = manifest_with_common_only_review();
        manifest.segments[0].kind = "unknown".to_string();
        manifest.segments[0].common_only = false;
        manifest.segments[0].source = "scanned_geometry_unavailable".to_string();
        manifest.review_items[0].common_only = false;
        manifest.review_items[0].reason_codes = vec!["scanned_geometry_unavailable".to_string()];
        refresh_manifest_hash(&mut manifest).expect("canonical scanned manifest");
        sessions.state.lock().unwrap().sessions.insert(
            manifest.run_id.clone(),
            SessionRecord {
                manifest: manifest.clone(),
                original: None,
                options: None,
                lifecycle: SessionLifecycle::Ready,
            },
        );

        let manual_updated = sessions
            .apply_manual_action(ManualActionV1Request {
                run_id: manifest.run_id.clone(),
                analysis_revision: manifest.analysis_revision,
                manifest_hash: manifest.manifest_hash.clone(),
                page: 0,
                rects: vec![rect(10.0, 10.0, 50.0, 50.0)],
                mode: "mask".to_string(),
                source_kind: "scan".to_string(),
                linked_occurrence_id: None,
                target_region_id: None,
                expected_text_hash: None,
                protected_neighbor_refs: vec![],
                restore_capability: None,
            })
            .expect("scanned manual action should precede acknowledgment");
        let review = manual_updated
            .review_items
            .iter()
            .find(|item| item.kind == ReviewKind::Acknowledge)
            .expect("scanned acknowledgment should remain pending after manual action");

        let resolved = sessions
            .resolve(ResolveMaskingReviewRequest {
                run_id: manual_updated.run_id.clone(),
                analysis_revision: manual_updated.analysis_revision,
                manifest_hash: manual_updated.manifest_hash.clone(),
                review_id: review.review_id.clone(),
                resolution: ReviewResolution::Acknowledge { acknowledged: true },
            })
            .expect("acknowledgment should resolve the scanned review warning");

        assert_eq!("user_confirmed", resolved.segments[0].state);
        assert!(!resolved.segments[0].common_only);
        assert_eq!("resolved", resolved.review_items[0].status);
    }
    #[test]
    fn finalize_requires_confirmation_for_unresolved_review_but_accepts_confirmed_intent() {
        let sessions = MaskingRunSessions::default();
        let manifest = manifest_with_common_only_review();
        sessions
            .state
            .lock()
            .expect("session lock")
            .sessions
            .insert(
                manifest.run_id.clone(),
                SessionRecord {
                    manifest: manifest.clone(),
                    original: Some(std::path::PathBuf::from("/tmp/original.pdf")),
                    options: Some(serde_json::json!({})),
                    lifecycle: SessionLifecycle::Ready,
                },
            );
        assert!(matches!(
            sessions.finalize_context(&manifest.run_id, 2, &manifest.manifest_hash, true),
            Err(error) if error == "MASKING_SESSION_STALE_ANALYSIS"
        ));
        assert!(matches!(
            sessions.finalize_context(&manifest.run_id, 1, &"f".repeat(64), true),
            Err(error) if error == "MASKING_SESSION_STALE_ANALYSIS"
        ));
        assert!(matches!(
            sessions.finalize_context(&manifest.run_id, 1, &manifest.manifest_hash, false),
            Err(error) if error == "MASKING_SESSION_UNRESOLVED_REVIEW"
        ));
        let confirmed = sessions
            .finalize_context(&manifest.run_id, 1, &manifest.manifest_hash, true)
            .expect("confirmed warning should reach trusted finalization");
        assert_eq!(manifest.run_id, confirmed.0.run_id);
    }
    #[test]
    fn finalize_claim_is_exclusive_and_completed_runs_cannot_replay() {
        let sessions = std::sync::Arc::new(MaskingRunSessions::default());
        let mut manifest = manifest_with_common_only_review();
        manifest.segments[0].state = "confirmed".to_string();
        manifest.review_items[0].status = "resolved".to_string();
        refresh_manifest_hash(&mut manifest).expect("canonical manifest");
        sessions
            .state
            .lock()
            .expect("session lock")
            .sessions
            .insert(
                manifest.run_id.clone(),
                SessionRecord {
                    manifest: manifest.clone(),
                    original: Some(std::path::PathBuf::from("/tmp/original.pdf")),
                    options: Some(serde_json::json!({})),
                    lifecycle: SessionLifecycle::Ready,
                },
            );

        let first = {
            let sessions = std::sync::Arc::clone(&sessions);
            let run_id = manifest.run_id.clone();
            let hash = manifest.manifest_hash.clone();
            std::thread::spawn(move || sessions.finalize_context(&run_id, 1, &hash, false))
        };
        let second = {
            let sessions = std::sync::Arc::clone(&sessions);
            let run_id = manifest.run_id.clone();
            let hash = manifest.manifest_hash.clone();
            std::thread::spawn(move || sessions.finalize_context(&run_id, 1, &hash, false))
        };
        let outcomes = [
            first.join().expect("first join"),
            second.join().expect("second join"),
        ];
        assert_eq!(1, outcomes.iter().filter(|outcome| outcome.is_ok()).count());
        assert!(outcomes.iter().any(|outcome| {
            matches!(outcome, Err(error) if error == "MASKING_SESSION_RUN_FINALIZING")
        }));
        sessions
            .finish_finalize(&manifest.run_id, FinalizeDisposition::Consumed)
            .expect("complete claimed finalization");
        assert!(matches!(
            sessions.finalize_context(&manifest.run_id, 1, &manifest.manifest_hash, false),
            Err(error) if error == "MASKING_SESSION_RUN_CONSUMED"
        ));
        let completed = sessions.state.lock().expect("session lock");
        assert!(!completed.sessions.contains_key(&manifest.run_id));
        assert!(completed
            .completed_tombstones
            .iter()
            .any(|(run_id, _)| run_id == &manifest.run_id));
    }
    #[test]
    fn finalize_cleanup_outcomes_allow_only_clean_precommit_retry() {
        let sessions = MaskingRunSessions::default();
        let manifest = manifest_with_common_only_review();
        sessions
            .state
            .lock()
            .expect("session lock")
            .sessions
            .insert(
                manifest.run_id.clone(),
                SessionRecord {
                    manifest: manifest.clone(),
                    original: None,
                    options: None,
                    lifecycle: SessionLifecycle::Finalizing,
                },
            );

        sessions
            .finish_finalize(&manifest.run_id, FinalizeDisposition::RetryReady)
            .expect("clean pre-commit cleanup restores readiness");
        assert!(sessions.get(&manifest.run_id).is_ok());

        sessions
            .state
            .lock()
            .expect("session lock")
            .sessions
            .get_mut(&manifest.run_id)
            .expect("session")
            .lifecycle = SessionLifecycle::Finalizing;
        sessions
            .finish_finalize(&manifest.run_id, FinalizeDisposition::CleanupRequired)
            .expect("dirty cleanup is retained as non-ready");
        assert!(matches!(
            sessions.get(&manifest.run_id),
            Err(error) if error == "MASKING_SESSION_RUN_CLEANUP_REQUIRED"
        ));
    }
    #[test]
    fn exact_page_coverage_rejects_empty_and_trailing_omissions() {
        let segment = |start, end| AnalysisSegment {
            segment_id: "segment_coverage_0001".to_string(),
            analysis_revision: 1,
            page_start: start,
            page_end: end,
            kind: "document".to_string(),
            state: "confirmed".to_string(),
            common_only: false,
            source: "trusted".to_string(),
        };
        assert!(validate_segments(&[], 1, 2).is_err());
        assert!(validate_segments(&[segment(0, 0)], 1, 2).is_err());
        assert!(validate_segments(&[segment(0, 1)], 1, 2).is_ok());
        assert_eq!(
            pdf_page_count(b"/Type /Page /Type /Pages /Type /Page"),
            None
        );
        let mut document = lopdf::Document::with_version("1.5");
        let pages = document.new_object_id();
        let first = document.new_object_id();
        let second = document.new_object_id();
        document.objects.insert(
            pages,
            lopdf::dictionary! {
                "Type" => "Pages",
                "Kids" => vec![first.into(), second.into()],
                "Count" => 2,
            }
            .into(),
        );
        for page in [first, second] {
            let contents =
                document.add_object(lopdf::Stream::new(lopdf::dictionary! {}, Vec::new()));
            document.objects.insert(
                page,
                lopdf::dictionary! {
                    "Type" => "Page",
                    "Parent" => pages,
                    "MediaBox" => vec![0.into(), 0.into(), 10.into(), 10.into()],
                    "Resources" => lopdf::dictionary! {},
                    "Contents" => contents,
                }
                .into(),
            );
        }
        let catalog =
            document.add_object(lopdf::dictionary! { "Type" => "Catalog", "Pages" => pages });
        document.trailer.set("Root", catalog);
        let mut bytes = Vec::new();
        document.save_to(&mut bytes).expect("valid PDF");
        assert_eq!(pdf_page_count(&bytes), Some(2));
    }

    #[test]
    fn trusted_schema_rejects_unknown_and_sensitive_aliases() {
        assert!(!trusted_object_fields(
            &serde_json::json!({"segment_id": "safe", "content": "secret"}),
            &["segment_id"],
        ));
        assert!(contains_raw_field(
            &serde_json::json!({"snippet": "secret"})
        ));
    }

    #[test]
    fn occurrence_id_matches_versioned_shared_vector() {
        let vector: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/occurrence-id-v2.json"))
                .expect("canonical occurrence ID vector");
        assert_eq!(
            vector["version"].as_str(),
            Some("occurrence-id-v2"),
            "unsupported occurrence ID vector"
        );

        let rects = vector["rects"]
            .as_array()
            .expect("occurrence vector rectangles")
            .iter()
            .map(|value| {
                rect(
                    value["x0"].as_f64().expect("rectangle x0"),
                    value["y0"].as_f64().expect("rectangle y0"),
                    value["x1"].as_f64().expect("rectangle x1"),
                    value["y1"].as_f64().expect("rectangle y1"),
                )
            })
            .collect::<Vec<_>>();
        let occurrence_id = occurrence_fingerprint(
            vector["document_hash"].as_str().expect("document hash"),
            vector["analysis_revision"]
                .as_u64()
                .expect("analysis revision"),
            vector["page_index"].as_u64().expect("page index") as u32,
            &rects,
            vector["tag"].as_str().expect("tag"),
            vector["category"].as_str().expect("category"),
            vector["value_hash"].as_str().expect("value hash"),
            vector["source"].as_str().expect("source"),
            vector["policy"].as_str().expect("policy"),
            vector["proposed_action"].as_str().expect("proposed action"),
        );

        assert_eq!(
            vector["expected_occurrence_id"]
                .as_str()
                .expect("expected occurrence ID"),
            occurrence_id,
        );
    }

    #[test]
    fn manifest_hmac_uses_standard_sha256_construction() {
        assert_eq!(
            hmac_sha256_hex(b"key", b"The quick brown fox jumps over the lazy dog"),
            "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
        );
    }

    #[test]
    fn revision_remaps_region_references_and_preserves_outside_pending_review() {
        let document_hash = "c".repeat(64);
        let region_rect = rect(10.0, 10.0, 80.0, 30.0);
        let first_occurrence_rect = rect(20.0, 12.0, 40.0, 28.0);
        let second_occurrence_rect = rect(20.0, 50.0, 40.0, 66.0);
        let first_occurrence_id = occurrence_fingerprint(
            &document_hash,
            1,
            0,
            std::slice::from_ref(&first_occurrence_rect),
            "profile_value",
            "approval_staff",
            &"d".repeat(64),
            "profile_layout",
            POLICY_VERSION,
            "review",
        );
        let second_occurrence_id = occurrence_fingerprint(
            &document_hash,
            1,
            1,
            std::slice::from_ref(&second_occurrence_rect),
            "name",
            "person_name",
            &"e".repeat(64),
            "common_detector",
            POLICY_VERSION,
            "review",
        );
        let mut manifest = AnalysisManifestV1 {
            manifest_version: MANIFEST_VERSION,
            run_id: "run_revision_remap_0001".to_string(),
            original_document_hash: document_hash,
            analysis_revision: 1,
            manifest_hash: String::new(),
            profile: "mixed".to_string(),
            policy_version: POLICY_VERSION.to_string(),
            options_version: OPTIONS_VERSION.to_string(),
            options_hash: "f".repeat(64),
            threshold_version: THRESHOLD_VERSION.to_string(),
            threshold_hash: threshold_artifact_hash(0.85, 0.5).expect("threshold hash"),
            threshold_artifact: threshold_artifact(0.85, 0.5).expect("threshold artifact"),
            coordinate_space: "pdf_points_top_left".to_string(),
            approval_coverage: ApprovalCoverage {
                schema_version: 1,
                state: CoverageState::Absent,
                signer_count: 0,
                protected_neighbor_count: 0,
            },
            required_region_coverage: RequiredRegionCoverage {
                schema_version: 1,
                profile: "mixed".to_string(),
                kinds: required_region_kinds("mixed")
                    .iter()
                    .map(|kind| RequiredRegionCoverageKind {
                        kind: (*kind).to_string(),
                        state: CoverageState::Absent,
                    })
                    .collect(),
                blocking: false,
            },
            segments: vec![
                AnalysisSegment {
                    segment_id: "segment_page_zero_0001".to_string(),
                    analysis_revision: 1,
                    page_start: 0,
                    page_end: 0,
                    kind: "internal_review".to_string(),
                    state: "confirmed".to_string(),
                    common_only: false,
                    source: "routing".to_string(),
                },
                AnalysisSegment {
                    segment_id: "segment_page_one_0001".to_string(),
                    analysis_revision: 1,
                    page_start: 1,
                    page_end: 1,
                    kind: "official_dispatch".to_string(),
                    state: "confirmed".to_string(),
                    common_only: false,
                    source: "routing".to_string(),
                },
            ],
            regions: vec![AnalysisRegion {
                region_id: "region_page_zero_0001".to_string(),
                segment_id: "segment_page_zero_0001".to_string(),
                analysis_revision: 1,
                page: 0,
                rects: vec![region_rect],
                kind: "approval".to_string(),
                state: "review_required".to_string(),
                confirmation_source: None,
                reason_codes: vec!["geometry_review".to_string()],
                source: "official_layout".to_string(),
            }],
            occurrences: vec![
                AnalysisOccurrence {
                    occurrence_id: first_occurrence_id.clone(),
                    segment_id: "segment_page_zero_0001".to_string(),
                    region_id: Some("region_page_zero_0001".to_string()),
                    analysis_revision: 1,
                    page: 0,
                    rects: vec![first_occurrence_rect],
                    tag: "profile_value".to_string(),
                    category: "approval_staff".to_string(),
                    value_hash: "d".repeat(64),
                    expected_text_hash: "1".repeat(64),
                    source: "profile_layout".to_string(),
                    policy: POLICY_VERSION.to_string(),
                    proposed_action: "review".to_string(),
                    state: "review_required".to_string(),
                    provenance: "profile_layout".to_string(),
                },
                AnalysisOccurrence {
                    occurrence_id: second_occurrence_id.clone(),
                    segment_id: "segment_page_one_0001".to_string(),
                    region_id: None,
                    analysis_revision: 1,
                    page: 1,
                    rects: vec![second_occurrence_rect],
                    tag: "name".to_string(),
                    category: "person_name".to_string(),
                    value_hash: "e".repeat(64),
                    expected_text_hash: "2".repeat(64),
                    source: "common_detector".to_string(),
                    policy: POLICY_VERSION.to_string(),
                    proposed_action: "review".to_string(),
                    state: "review_required".to_string(),
                    provenance: "common_detector".to_string(),
                },
            ],
            review_items: vec![
                ReviewItem {
                    review_id: "review_geometry_page0_0001".to_string(),
                    analysis_revision: 1,
                    kind: ReviewKind::RegionGeometry,
                    target_id: Some("region_page_zero_0001".to_string()),
                    page_start: 0,
                    page_end: 0,
                    status: "pending".to_string(),
                    reason_codes: vec!["geometry_review".to_string()],
                    requires_acknowledgment: true,
                    common_only: false,
                    provenance: "official_layout".to_string(),
                },
                ReviewItem {
                    review_id: "review_name_page_one_001".to_string(),
                    analysis_revision: 1,
                    kind: ReviewKind::Name,
                    target_id: Some(second_occurrence_id),
                    page_start: 1,
                    page_end: 1,
                    status: "pending".to_string(),
                    reason_codes: vec!["detector_review_required".to_string()],
                    requires_acknowledgment: false,
                    common_only: false,
                    provenance: "common_detector".to_string(),
                },
            ],
            manual_actions: vec![],
        };
        let resolved_review = manifest.review_items[0].clone();

        advance_revision(&mut manifest, &resolved_review, 0, 0)
            .expect("referentially valid revision advance");

        assert_eq!(2, manifest.analysis_revision);
        assert_eq!(
            "resolved",
            manifest
                .review_items
                .iter()
                .find(|item| item.page_start == 0)
                .unwrap()
                .status
        );
        assert_eq!(
            "pending",
            manifest
                .review_items
                .iter()
                .find(|item| item.page_start == 1)
                .unwrap()
                .status
        );
        let remapped_region = &manifest.regions[0];
        assert_ne!("region_page_zero_0001", remapped_region.region_id);
        let remapped_occurrence = manifest
            .occurrences
            .iter()
            .find(|item| item.page == 0)
            .unwrap();
        assert_eq!(
            Some(remapped_region.region_id.as_str()),
            remapped_occurrence.region_id.as_deref()
        );
        assert!(manifest
            .review_items
            .iter()
            .all(|item| valid_review_target(&manifest, item)));
        assert_ne!(first_occurrence_id, remapped_occurrence.occurrence_id);
    }

    #[test]
    fn public_options_reject_unknown_fields() {
        let options = serde_json::json!({
            "rrn": true, "phone": true, "business_reg": true, "name": true, "address": true,
            "place": true, "legal_party": true, "company": true, "court": true, "case_title": true,
            "case_number": true, "law_firm": true, "attorney": true, "approval_line": true,
            "region_context": true, "doc_meta": true, "email": true, "pdf_redaction": true, "custom_keywords": "",
            "extract_engine": "auto", "profile": "mixed", "output_artifacts": "pdf_safe_report",
            "display_mode": "black", "deidentification_policy": "token", "region_scope": "document",
            "custom_regions": "", "return_text_preview": false, "unexpected": true
        });
        assert_eq!(
            Err(safe_error("OPTIONS_INVALID")),
            canonical_public_options(options, "mixed")
        );
    }

    #[test]
    fn public_options_accept_frontend_default_scope_and_engine() {
        let mut options = public_options();
        options["profile"] = serde_json::Value::String("internal_review".to_string());
        options["region_scope"] = serde_json::Value::String("national".to_string());
        options["extract_engine"] = serde_json::Value::String("marker".to_string());

        assert!(canonical_public_options(options, "internal_review").is_ok());
    }

    #[test]
    fn public_profile_authority_is_server_generated_per_analysis_revision() {
        let source = b"profile authority fixture";
        let source_hash = sha256_hex(source);
        let mut client_options = public_options();
        client_options["profile"] = serde_json::Value::String("internal_review".to_string());
        client_options["profile_authority"] = serde_json::json!({
            "document_sha256": "f".repeat(64),
            "analysis_revision": 99,
            "profile": "official_dispatch",
            "decision_code": "profile_confirmed",
        });

        assert_eq!(
            Err(safe_error("OPTIONS_INVALID")),
            canonical_public_options(client_options, "internal_review")
        );

        let mut canonical_options = public_options();
        canonical_options["profile"] = serde_json::Value::String("internal_review".to_string());
        let canonical = canonical_public_options(canonical_options, "internal_review")
            .expect("client options without authority are valid");

        let initial = with_server_profile_authority(canonical.clone(), &source_hash, 1)
            .expect("server injects initial authority");
        assert_eq!(
            Some(&serde_json::json!({
                "document_sha256": source_hash,
                "analysis_revision": 1,
                "profile": "internal_review",
                "decision_code": "profile_confirmed",
            })),
            initial.get("profile_authority")
        );
        let mut initial_policy_material = initial.clone();
        initial_policy_material
            .as_object_mut()
            .expect("serialized options object")
            .remove("profile_authority");
        assert_eq!(
            canonical_json_hash(&canonical).expect("canonical hash"),
            canonical_json_hash(&initial_policy_material).expect("authority-free initial hash")
        );

        let reanalysis = with_server_profile_authority(canonical.clone(), &source_hash, 2)
            .expect("server refreshes authority for reanalysis");
        assert_eq!(
            Some(&serde_json::json!(2)),
            reanalysis
                .get("profile_authority")
                .and_then(|authority| authority.get("analysis_revision"))
        );
        let mut reanalysis_policy_material = reanalysis.clone();
        reanalysis_policy_material
            .as_object_mut()
            .expect("serialized options object")
            .remove("profile_authority");
        assert_eq!(
            canonical_json_hash(&canonical).expect("canonical hash"),
            canonical_json_hash(&reanalysis_policy_material)
                .expect("authority-free reanalysis hash")
        );
    }

    #[test]
    fn public_options_hash_changes_when_email_detection_changes() {
        let enabled = canonical_public_options(public_options(), "mixed").expect("valid options");
        let mut disabled = public_options();
        disabled["email"] = serde_json::Value::Bool(false);
        let disabled = canonical_public_options(disabled, "mixed").expect("valid options");

        assert_ne!(
            canonical_json_hash(&enabled).expect("enabled hash"),
            canonical_json_hash(&disabled).expect("disabled hash"),
        );
    }

    #[test]
    fn public_options_hash_changes_when_custom_keywords_change() {
        let empty_keyword =
            canonical_public_options(public_options(), "mixed").expect("valid options");
        let mut with_keyword = public_options();
        with_keyword["custom_keywords"] = serde_json::Value::String("공사기간 연장".to_string());
        let with_keyword = canonical_public_options(with_keyword, "mixed").expect("valid options");

        assert_ne!(
            canonical_json_hash(&empty_keyword).expect("empty keyword hash"),
            canonical_json_hash(&with_keyword).expect("custom keyword hash"),
        );
    }

    #[test]
    fn consumed_run_cannot_be_replayed() {
        let sessions = MaskingRunSessions::default();
        let manifest = manifest_with_common_only_review();
        sessions.state.lock().unwrap().sessions.insert(
            manifest.run_id.clone(),
            SessionRecord {
                manifest: manifest.clone(),
                original: Some(std::path::PathBuf::from("/tmp/original.pdf")),
                options: Some(serde_json::json!({})),
                lifecycle: SessionLifecycle::Completed,
            },
        );
        assert!(
            matches!(sessions.get(&manifest.run_id), Err(error) if error == safe_error("RUN_CONSUMED"))
        );
    }
}
