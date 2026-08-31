use crate::masking_run_session::{
    self, AnalyzeMaskingRunRequest, FinalizeMaskingRunRequest, ManualActionV1Request,
    MaskingRunSessions, Rect, ResolveMaskingReviewRequest, ReviewKind, ReviewResolution,
};
use crate::path_security::NativeSaveTargetBinding;
use crate::{
    analyze_masking_run_core, finalize_masking_run_core, register_native_save_target_core,
    resolve_masking_review_core, AllowedFileAccess,
};
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;
use tauri::Manager;

const FIXTURE: &[u8] = include_bytes!("../resources/public_native_qa_fixture.pdf");
const AMBIGUOUS_FIXTURE: &[u8] =
    include_bytes!("../resources/public_native_qa_ambiguous_fixture.pdf");
const CLEAN_FIXTURE: &[u8] = include_bytes!("../resources/public_native_qa_clean_fixture.pdf");
const MANUAL_FIXTURE: &[u8] = include_bytes!("../resources/public_native_qa_manual_fixture.pdf");
const SCAN_FIXTURE: &[u8] = include_bytes!("../resources/public_native_qa_scan_fixture.pdf");
const REPEAT_FIXTURE: &[u8] = include_bytes!("../resources/public_native_qa_repeat_fixture.pdf");
const OFFICIAL_DISPATCH_FIXTURE_OCCURRENCE_COUNT: usize = 19;
const OFFICIAL_DISPATCH_FIXTURE_PENDING_REVIEW_COUNT: usize = 10;
const NON_PERSON_NAME_VALUE: &str = "공사기간";
const FULL_STEPS: [&str; 22] = [
    "public_analyze_completed",
    "public_mixed_boundary_blocked",
    "public_ambiguous_common_only_blocked",
    "public_scan_manual_review_required",
    "public_repeated_occurrence_scoped",
    "public_review_cards_resolved",
    "public_manual_combined_resolved",
    "public_legal_advisory_isolated",
    "public_unresolved_review_blocked",
    "public_unresolved_review_confirmed",
    "public_stale_revision_blocked",
    "public_stale_manifest_hash_blocked",
    "public_tampered_manifest_blocked",
    "public_forged_resolution_blocked",
    "public_intrinsic_failure_blocked",
    "public_destination_bypass_blocked",
    "public_destination_authorized",
    "public_destination_token_issued",
    "public_threshold_hash_bound",
    "public_clean_document_verified",
    "public_atomic_promotion_failure_blocked",
    "public_finalize_promoted",
];
const PLUMBING_STEPS: [&str; 13] = [
    "public_analyze_completed",
    "public_unresolved_review_blocked",
    "public_unresolved_review_confirmed",
    "public_stale_revision_blocked",
    "public_stale_manifest_hash_blocked",
    "public_tampered_manifest_blocked",
    "public_forged_resolution_blocked",
    "public_destination_bypass_blocked",
    "public_destination_authorized",
    "public_destination_token_issued",
    "public_threshold_hash_bound",
    "public_atomic_promotion_failure_blocked",
    "public_finalize_promoted",
];

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct NativeQaThresholdBinding {
    threshold_version: String,
    threshold_hash: String,
    threshold_value_hash: String,
    auto_mask_threshold: f64,
    review_threshold: f64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct NativeQaRequest {
    schema_version: u32,
    scenario: String,
    nonce: String,
    threshold_version: String,
    threshold_hash: String,
    threshold_value_hash: String,
    auto_mask_threshold: f64,
    review_threshold: f64,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PublicAction {
    name: &'static str,
    outcome: &'static str,
    error_code: Option<&'static str>,
    request_evidence: serde_json::Value,
    result_evidence: serde_json::Value,
    request_hash: String,
    result_hash: String,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PublicActionReceiptV1 {
    schema: &'static str,
    schema_version: u32,
    scenario: String,
    nonce: String,
    binary_hash: String,
    run_id: String,
    analysis_revision: u64,
    manifest_hash: String,
    threshold_version: String,
    threshold_hash: String,
    threshold_value_hash: String,
    scenario_steps: Vec<&'static str>,
    actions: Vec<PublicAction>,
    canonical_receipt_hash: String,
    receipt_auth: String,
}
#[derive(Serialize)]
struct NativeReceiptEvent {
    event: &'static str,
    source: &'static str,
    receipt: PublicActionReceiptV1,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct OfficialDispatchFixtureEvidence {
    occurrence_count: usize,
    pending_review_count: usize,
    confirmed_profile_route: bool,
    profile_layout_occurrence: bool,
    name_review: bool,
    institution_review: bool,
    acknowledge_review: bool,
    profile_authority_missing_review: bool,
    non_person_name_candidate_excluded: bool,
}

const fn official_dispatch_fixture_semantics_observed(
    evidence: OfficialDispatchFixtureEvidence,
) -> bool {
    evidence.occurrence_count == OFFICIAL_DISPATCH_FIXTURE_OCCURRENCE_COUNT
        && evidence.pending_review_count == OFFICIAL_DISPATCH_FIXTURE_PENDING_REVIEW_COUNT
        && evidence.confirmed_profile_route
        && evidence.profile_layout_occurrence
        && evidence.name_review
        && evidence.institution_review
        && !evidence.acknowledge_review
        && !evidence.profile_authority_missing_review
        && evidence.non_person_name_candidate_excluded
}

fn official_dispatch_fixture_evidence(
    manifest: &masking_run_session::AnalysisManifestV1,
) -> OfficialDispatchFixtureEvidence {
    OfficialDispatchFixtureEvidence {
        occurrence_count: manifest.occurrences.len(),
        pending_review_count: manifest
            .review_items
            .iter()
            .filter(|item| item.status == "pending")
            .count(),
        confirmed_profile_route: manifest.segments.iter().any(|segment| {
            segment.kind == "official_dispatch"
                && segment.state == "confirmed"
                && !segment.common_only
        }),
        profile_layout_occurrence: manifest.occurrences.iter().any(|occurrence| {
            occurrence.source == "profile_layout"
                && matches!(
                    occurrence.category.as_str(),
                    "approval_staff" | "dispatch_metadata" | "recipient_reference"
                )
        }),
        name_review: manifest
            .review_items
            .iter()
            .any(|item| item.status == "pending" && item.kind == ReviewKind::Name),
        institution_review: manifest
            .review_items
            .iter()
            .any(|item| item.status == "pending" && item.kind == ReviewKind::Institution),
        acknowledge_review: manifest
            .review_items
            .iter()
            .any(|item| item.status == "pending" && item.kind == ReviewKind::Acknowledge),
        profile_authority_missing_review: manifest.review_items.iter().any(|item| {
            item.status == "pending"
                && item
                    .reason_codes
                    .iter()
                    .any(|reason| reason == "profile_authority_missing")
        }),
        non_person_name_candidate_excluded: !manifest.occurrences.iter().any(|occurrence| {
            occurrence.value_hash
                == masking_run_session::document_hash(NON_PERSON_NAME_VALUE.as_bytes())
        }),
    }
}

fn options(profile: &str, binding: &NativeQaThresholdBinding) -> serde_json::Value {
    serde_json::json!({
        "rrn":true,"phone":true,"business_reg":true,"name":true,"address":true,"place":true,
        "legal_party":true,"company":true,"court":true,"case_title":true,"case_number":true,
        "law_firm":true,"attorney":true,"approval_line":true,"region_context":true,"doc_meta":true,"email":true,
        "pdf_redaction":true,"custom_keywords":"","extract_engine":"auto","profile":profile,
        "output_artifacts":"pdf_safe_report","display_mode":"black","deidentification_policy":"token",
        "region_scope":"national","custom_regions":"","return_text_preview":false,
        "auto_mask_threshold":binding.auto_mask_threshold,
        "review_threshold":binding.review_threshold
    })
}

fn threshold_value_hash(auto_mask_threshold: f64, review_threshold: f64) -> Result<String, String> {
    masking_run_session::canonical_json_hash(&serde_json::json!({
        "autoMaskThreshold": auto_mask_threshold,
        "reviewThreshold": review_threshold,
    }))
}

fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}
fn threshold_artifact_hash(
    auto_mask_threshold: f64,
    review_threshold: f64,
) -> Result<String, String> {
    masking_run_session::canonical_json_hash(&serde_json::json!({
        "auto_threshold": auto_mask_threshold,
        "policy_version": masking_run_session::POLICY_VERSION,
        "review_threshold": review_threshold,
    }))
}

fn parse_threshold_binding(request: &NativeQaRequest) -> Result<NativeQaThresholdBinding, String> {
    if request.threshold_version != masking_run_session::THRESHOLD_VERSION
        || !valid_hash(&request.threshold_hash)
        || !valid_hash(&request.threshold_value_hash)
        || !request.auto_mask_threshold.is_finite()
        || !request.review_threshold.is_finite()
        || !(0.0..=1.0).contains(&request.auto_mask_threshold)
        || !(0.0..=1.0).contains(&request.review_threshold)
        || request.auto_mask_threshold < request.review_threshold
    {
        return Err("NATIVE_QA_THRESHOLD_BINDING_INVALID".to_string());
    }
    let expected_value_hash =
        threshold_value_hash(request.auto_mask_threshold, request.review_threshold)?;
    if request.threshold_value_hash != expected_value_hash {
        return Err("NATIVE_QA_THRESHOLD_BINDING_MISMATCH".to_string());
    }
    let expected_artifact_hash =
        threshold_artifact_hash(request.auto_mask_threshold, request.review_threshold)?;
    if request.threshold_hash != expected_artifact_hash {
        return Err("NATIVE_QA_THRESHOLD_BINDING_MISMATCH".to_string());
    }
    Ok(NativeQaThresholdBinding {
        threshold_version: request.threshold_version.clone(),
        threshold_hash: request.threshold_hash.clone(),
        threshold_value_hash: request.threshold_value_hash.clone(),
        auto_mask_threshold: request.auto_mask_threshold,
        review_threshold: request.review_threshold,
    })
}
fn verify_threshold_binding(
    manifest: &masking_run_session::AnalysisManifestV1,
    binding: &NativeQaThresholdBinding,
) -> Result<(), String> {
    let expected_value_hash =
        threshold_value_hash(binding.auto_mask_threshold, binding.review_threshold)?;
    if manifest.threshold_version != binding.threshold_version
        || manifest.threshold_hash != binding.threshold_hash
        || manifest.threshold_artifact.version != binding.threshold_version
        || manifest.threshold_artifact.content_hash != binding.threshold_hash
        || manifest.threshold_artifact.auto_mask_threshold != binding.auto_mask_threshold
        || manifest.threshold_artifact.review_threshold != binding.review_threshold
        || expected_value_hash != binding.threshold_value_hash
        || threshold_artifact_hash(binding.auto_mask_threshold, binding.review_threshold)?
            != binding.threshold_hash
    {
        return Err("NATIVE_QA_THRESHOLD_BINDING_MISMATCH".to_string());
    }
    Ok(())
}
fn fixture_path_for(bytes: &[u8], name: &str) -> Result<PathBuf, String> {
    let root = std::env::temp_dir().join(format!(
        "document-masker-native-qa-{}",
        masking_run_session::document_hash(bytes)
    ));
    match fs::create_dir(&root) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => return Err("NATIVE_QA_FIXTURE_UNAVAILABLE".to_string()),
    }
    let metadata =
        fs::symlink_metadata(&root).map_err(|_| "NATIVE_QA_FIXTURE_UNAVAILABLE".to_string())?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err("NATIVE_QA_FIXTURE_UNAVAILABLE".to_string());
    }
    let path = root.join(name);
    match OpenOptions::new().write(true).create_new(true).open(&path) {
        Ok(mut file) => file
            .write_all(bytes)
            .map_err(|_| "NATIVE_QA_FIXTURE_UNAVAILABLE".to_string())?,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            let existing_metadata = fs::symlink_metadata(&path)
                .map_err(|_| "NATIVE_QA_FIXTURE_UNAVAILABLE".to_string())?;
            if existing_metadata.file_type().is_symlink() || !existing_metadata.is_file() {
                return Err("NATIVE_QA_FIXTURE_UNAVAILABLE".to_string());
            }
            let existing =
                fs::read(&path).map_err(|_| "NATIVE_QA_FIXTURE_UNAVAILABLE".to_string())?;
            if masking_run_session::document_hash(&existing)
                != masking_run_session::document_hash(bytes)
            {
                return Err("NATIVE_QA_FIXTURE_UNAVAILABLE".to_string());
            }
        }
        Err(_) => return Err("NATIVE_QA_FIXTURE_UNAVAILABLE".to_string()),
    }
    Ok(path)
}

fn fixture_path() -> Result<PathBuf, String> {
    fixture_path_for(FIXTURE, "public-native-qa-fixture.pdf")
}
fn current_binary_hash() -> Result<String, String> {
    let executable =
        std::env::current_exe().map_err(|_| "NATIVE_QA_EXECUTABLE_UNAVAILABLE".to_string())?;
    let metadata = std::fs::symlink_metadata(&executable)
        .map_err(|_| "NATIVE_QA_EXECUTABLE_UNAVAILABLE".to_string())?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("NATIVE_QA_EXECUTABLE_UNAVAILABLE".to_string());
    }
    std::fs::read(executable)
        .map(|bytes| masking_run_session::document_hash(&bytes))
        .map_err(|_| "NATIVE_QA_EXECUTABLE_UNAVAILABLE".to_string())
}
fn action_hash(
    receipt: &PublicActionReceiptV1,
    action: &PublicAction,
    phase: &str,
) -> Result<String, String> {
    masking_run_session::canonical_json_hash(&serde_json::json!({
        "phase":phase,"scenario":receipt.scenario,"name":action.name,"outcome":action.outcome,
        "errorCode":action.error_code,"requestEvidence":action.request_evidence,
        "resultEvidence":action.result_evidence,"nonce":receipt.nonce,"binaryHash":receipt.binary_hash,
        "runId":receipt.run_id,"analysisRevision":receipt.analysis_revision,"manifestHash":receipt.manifest_hash,
        "thresholdVersion":receipt.threshold_version,"thresholdHash":receipt.threshold_hash,
        "thresholdValueHash":receipt.threshold_value_hash,
    }))
}
fn hash_evidence(value: &serde_json::Value) -> Result<String, String> {
    masking_run_session::canonical_json_hash(value)
}

fn request_evidence(
    operation_code: &'static str,
    fixture_hash: &str,
    actual_request: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let request_evidence_hash = hash_evidence(&actual_request)?;
    Ok(serde_json::json!({
        "operationCode": operation_code,
        "fixtureHash": fixture_hash,
        "actualRequest": actual_request,
        "requestEvidenceHash": request_evidence_hash,
    }))
}

fn result_evidence(
    result_code: &'static str,
    count: usize,
    actual_result: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let result_evidence_hash = hash_evidence(&actual_result)?;
    Ok(serde_json::json!({
        "resultCode": result_code,
        "observed": true,
        "count": count,
        "actualResult": actual_result,
        "resultEvidenceHash": result_evidence_hash,
    }))
}

fn evidence_is_hash(value: Option<&serde_json::Value>) -> bool {
    value
        .and_then(serde_json::Value::as_str)
        .is_some_and(|value| {
            value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
        })
}

fn record(
    receipt: &mut PublicActionReceiptV1,
    name: &'static str,
    outcome: &'static str,
    error_code: Option<&'static str>,
    request_evidence: serde_json::Value,
    result_evidence: serde_json::Value,
) -> Result<(), String> {
    if receipt.actions.len() >= receipt.scenario_steps.len()
        || receipt.scenario_steps[receipt.actions.len()] != name
        || request_evidence.as_object().is_none_or(|value| {
            value.len() != 4
                || !matches!(value.get("operationCode"), Some(serde_json::Value::String(code)) if code == name)
                || !evidence_is_hash(value.get("fixtureHash"))
                || !evidence_is_hash(value.get("requestEvidenceHash"))
                || !matches!(value.get("actualRequest"), Some(serde_json::Value::Object(_)))
        })
        || result_evidence.as_object().is_none_or(|value| {
            value.len() != 5
                || !matches!(value.get("resultCode"), Some(serde_json::Value::String(_)))
                || !matches!(value.get("observed"), Some(serde_json::Value::Bool(true)))
                || !matches!(value.get("count"), Some(serde_json::Value::Number(count)) if count.is_u64())
                || !evidence_is_hash(value.get("resultEvidenceHash"))
                || !matches!(value.get("actualResult"), Some(serde_json::Value::Object(_)))
        })
    {
        return Err("NATIVE_QA_ACTION_EVIDENCE_INVALID".to_string());
    }
    let mut action = PublicAction {
        name,
        outcome,
        error_code,
        request_evidence,
        result_evidence,
        request_hash: String::new(),
        result_hash: String::new(),
    };
    action.request_hash = action_hash(receipt, &action, "request")?;
    action.result_hash = action_hash(receipt, &action, "result")?;
    receipt.actions.push(action);
    Ok(())
}
fn exact_error<T>(
    result: Result<T, String>,
    expected: &str,
    action: &'static str,
    code: &'static str,
    actual_request: serde_json::Value,
    actual_result: serde_json::Value,
    receipt: &mut PublicActionReceiptV1,
) -> Result<(), String> {
    match result {
        Err(error) if error == expected => record(
            receipt,
            action,
            "blocked",
            Some(code),
            request_evidence(
                action,
                &masking_run_session::document_hash(FIXTURE),
                actual_request,
            )?,
            result_evidence(code, 1, actual_result)?,
        ),
        Err(_) => Err(format!("NATIVE_QA_EXPECTED_{action}_NOT_OBSERVED")),
        Ok(_) => Err(format!("NATIVE_QA_EXPECTED_{action}_NOT_OBSERVED")),
    }
}

fn request_for(
    manifest: &masking_run_session::AnalysisManifestV1,
    destination: &Path,
    token: String,
) -> FinalizeMaskingRunRequest {
    request_for_with_confirmation(manifest, destination, token, false)
}

fn request_for_with_confirmation(
    manifest: &masking_run_session::AnalysisManifestV1,
    destination: &Path,
    token: String,
    warnings_confirmed: bool,
) -> FinalizeMaskingRunRequest {
    FinalizeMaskingRunRequest {
        run_id: manifest.run_id.clone(),
        analysis_revision: manifest.analysis_revision,
        manifest_hash: manifest.manifest_hash.clone(),
        destination: destination.display().to_string(),
        save_token: token,
        warnings_confirmed,
    }
}
fn finalize_actual_request(
    request: &FinalizeMaskingRunRequest,
    binding_code: &str,
) -> serde_json::Value {
    serde_json::json!({
        "operationKind": "finalize",
        "runIdHash": masking_run_session::document_hash(request.run_id.as_bytes()),
        "requestedRevision": request.analysis_revision,
        "requestedManifestHash": request.manifest_hash,
        "saveTokenHash": masking_run_session::document_hash(request.save_token.as_bytes()),
        "destinationHash": masking_run_session::document_hash(request.destination.as_bytes()),
        "bindingCode": binding_code,
    })
}
fn finalize_actual_request_with_confirmation(
    request: &FinalizeMaskingRunRequest,
    binding_code: &str,
) -> serde_json::Value {
    let mut evidence = finalize_actual_request(request, binding_code);
    if let Some(object) = evidence.as_object_mut() {
        object.insert(
            "warningsConfirmed".to_string(),
            serde_json::Value::Bool(request.warnings_confirmed),
        );
    }
    evidence
}

fn finalize_error_result(
    error_code: &str,
    manifest: &masking_run_session::AnalysisManifestV1,
    status_code: &str,
) -> serde_json::Value {
    serde_json::json!({
        "errorCode": error_code,
        "currentRevision": manifest.analysis_revision,
        "currentManifestHash": manifest.manifest_hash,
        "statusCode": status_code,
    })
}

fn resolve_actual_request(
    request: &ResolveMaskingReviewRequest,
    resolution_kind: &str,
) -> serde_json::Value {
    serde_json::json!({
        "operationKind": "resolve",
        "runIdHash": masking_run_session::document_hash(request.run_id.as_bytes()),
        "requestedRevision": request.analysis_revision,
        "requestedManifestHash": request.manifest_hash,
        "reviewIdHash": masking_run_session::document_hash(request.review_id.as_bytes()),
        "resolutionKind": resolution_kind,
    })
}
fn resolve_pending(
    app: &tauri::AppHandle,
    sessions: &MaskingRunSessions,
    mut manifest: masking_run_session::AnalysisManifestV1,
) -> Result<masking_run_session::AnalysisManifestV1, String> {
    while let Some(review) = manifest
        .review_items
        .iter()
        .find(|item| item.status == "pending")
        .cloned()
    {
        let resolution = match review.kind {
            ReviewKind::Acknowledge => ReviewResolution::Acknowledge { acknowledged: true },
            ReviewKind::Name => ReviewResolution::Name {
                action: "mask".to_string(),
            },
            ReviewKind::Institution => ReviewResolution::Institution {
                action: "mask".to_string(),
            },
            ReviewKind::Boundary => {
                let target = review
                    .target_id
                    .as_deref()
                    .ok_or_else(|| "NATIVE_QA_REVIEW_TARGET_MISSING".to_string())?;
                let segment = manifest
                    .segments
                    .iter()
                    .find(|segment| segment.segment_id == target)
                    .ok_or_else(|| "NATIVE_QA_REVIEW_SEGMENT_MISSING".to_string())?;
                let segment_kind = if segment.kind == "unknown" {
                    "attachment"
                } else {
                    &segment.kind
                };
                ReviewResolution::Boundary {
                    page_start: segment.page_start,
                    page_end: segment.page_end,
                    segment_kind: segment_kind.to_string(),
                }
            }
            ReviewKind::Ocr => ReviewResolution::Ocr { accepted: true },
            ReviewKind::RegionGeometry => {
                let target = review
                    .target_id
                    .as_deref()
                    .ok_or_else(|| "NATIVE_QA_REVIEW_TARGET_MISSING".to_string())?;
                let region = manifest
                    .regions
                    .iter()
                    .find(|region| region.region_id == target)
                    .ok_or_else(|| "NATIVE_QA_REVIEW_REGION_MISSING".to_string())?;
                ReviewResolution::RegionGeometry {
                    rects: region.rects.clone(),
                }
            }
            _ => return Err("NATIVE_QA_REVIEW_KIND_UNSUPPORTED".to_string()),
        };
        manifest = resolve_masking_review_core(
            app,
            sessions,
            ResolveMaskingReviewRequest {
                run_id: manifest.run_id.clone(),
                analysis_revision: manifest.analysis_revision,
                manifest_hash: manifest.manifest_hash.clone(),
                review_id: review.review_id,
                resolution,
            },
        )?;
    }
    Ok(manifest)
}
fn seal_receipt(mut receipt: PublicActionReceiptV1) -> Result<PublicActionReceiptV1, String> {
    if receipt.actions.len() != receipt.scenario_steps.len()
        || receipt
            .actions
            .iter()
            .map(|action| action.name)
            .ne(receipt.scenario_steps.iter().copied())
    {
        return Err("NATIVE_QA_ACTIONS_INCOMPLETE".to_string());
    }
    for index in 0..receipt.actions.len() {
        let action = PublicAction {
            name: receipt.actions[index].name,
            outcome: receipt.actions[index].outcome,
            error_code: receipt.actions[index].error_code,
            request_evidence: receipt.actions[index].request_evidence.clone(),
            result_evidence: receipt.actions[index].result_evidence.clone(),
            request_hash: String::new(),
            result_hash: String::new(),
        };
        receipt.actions[index].request_hash = action_hash(&receipt, &action, "request")?;
        receipt.actions[index].result_hash = action_hash(&receipt, &action, "result")?;
    }
    let value =
        serde_json::to_value(&receipt).map_err(|_| "NATIVE_QA_SERIALIZATION_FAILED".to_string())?;
    let mut unsigned = value
        .as_object()
        .cloned()
        .ok_or_else(|| "NATIVE_QA_SERIALIZATION_FAILED".to_string())?;
    unsigned.remove("canonicalReceiptHash");
    unsigned.remove("receiptAuth");
    receipt.canonical_receipt_hash = masking_run_session::canonical_json_hash(&unsigned)?;
    let action_bindings = receipt
        .actions
        .iter()
        .map(|action| {
            serde_json::json!({
                "requestHash": action.request_hash,
                "resultHash": action.result_hash,
                "requestEvidence": action.request_evidence,
                "resultEvidence": action.result_evidence,
            })
        })
        .collect::<Vec<_>>();
    receipt.receipt_auth = masking_run_session::canonical_json_hash(&serde_json::json!({
        "domain":"DocumentMaskerNativeQaReceiptAuthV1",
        "nonce":receipt.nonce,"binaryHash":receipt.binary_hash,
        "canonicalReceiptHash":receipt.canonical_receipt_hash,"actions":action_bindings,
    }))?;
    Ok(receipt)
}

fn require_action(
    receipt: &PublicActionReceiptV1,
    name: &str,
    outcome: &str,
    error_code: Option<&str>,
) -> Result<(), String> {
    if receipt.actions.iter().any(|action| {
        action.name == name && action.outcome == outcome && action.error_code == error_code
    }) {
        Ok(())
    } else {
        Err("NATIVE_QA_REQUIRED_ACTION_UNOBSERVED".to_string())
    }
}

fn copy_action(
    receipt: &mut PublicActionReceiptV1,
    source: &PublicActionReceiptV1,
    name: &str,
) -> Result<(), String> {
    let action = source
        .actions
        .iter()
        .find(|action| action.name == name)
        .ok_or_else(|| "NATIVE_QA_REQUIRED_ACTION_UNOBSERVED".to_string())?;
    record(
        receipt,
        action.name,
        action.outcome,
        action.error_code,
        action.request_evidence.clone(),
        action.result_evidence.clone(),
    )
}

fn bind_observation(
    receipt: &mut PublicActionReceiptV1,
    name: &'static str,
    result_code: &'static str,
    count: usize,
    actual_request: serde_json::Value,
    actual_result: serde_json::Value,
) -> Result<(), String> {
    let fixture_hash = actual_request
        .get("fixtureHash")
        .or_else(|| actual_request.get("inputHash"))
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| "NATIVE_QA_EVIDENCE_INVALID".to_string())?
        .to_string();
    let request = request_evidence(name, &fixture_hash, actual_request)?;
    let result = result_evidence(result_code, count, actual_result)?;
    if let Some(action) = receipt
        .actions
        .iter_mut()
        .find(|action| action.name == name)
    {
        action.request_evidence = request;
        action.result_evidence = result;
        return Ok(());
    }
    let (outcome, error_code) = match name {
        "public_mixed_boundary_blocked" => ("blocked", Some("MIXED_BOUNDARY_REVIEW_REQUIRED")),
        "public_ambiguous_common_only_blocked" => {
            ("blocked", Some("AMBIGUOUS_COMMON_ONLY_REVIEW_REQUIRED"))
        }
        "public_scan_manual_review_required" => ("pass", None),
        "public_intrinsic_failure_blocked" => ("blocked", Some("INTRINSIC_VERIFICATION_FAILED")),
        _ => ("pass", None),
    };
    record(receipt, name, outcome, error_code, request, result)
}

fn analyze_path(
    app: &tauri::AppHandle,
    access: &AllowedFileAccess,
    sessions: &MaskingRunSessions,
    path: &Path,
    profile: &str,
    binding: &NativeQaThresholdBinding,
) -> Result<masking_run_session::AnalysisManifestV1, String> {
    access.allow_document_path(path);
    analyze_masking_run_core(
        app,
        access,
        sessions,
        AnalyzeMaskingRunRequest {
            input_file: path.display().to_string(),
            profile: profile.to_string(),
            options: options(profile, binding),
        },
    )
}

fn rects_overlap(left: &[Rect], right: &[Rect]) -> bool {
    left.iter().any(|a| {
        right
            .iter()
            .any(|b| a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0)
    })
}

fn resolve_manual_pending(
    app: &tauri::AppHandle,
    sessions: &MaskingRunSessions,
    mut manifest: masking_run_session::AnalysisManifestV1,
) -> Result<masking_run_session::AnalysisManifestV1, String> {
    let mut names_by_page = std::collections::HashMap::<u32, usize>::new();
    while let Some(review) = manifest
        .review_items
        .iter()
        .find(|item| item.status == "pending")
        .cloned()
    {
        let resolution = match review.kind {
            ReviewKind::Acknowledge => ReviewResolution::Acknowledge { acknowledged: true },
            ReviewKind::Name => {
                let count = names_by_page.entry(review.page_start).or_default();
                let action = if *count == 1 { "exclude" } else { "mask" };
                *count += 1;
                ReviewResolution::Name {
                    action: action.to_string(),
                }
            }
            ReviewKind::Institution => ReviewResolution::Institution {
                action: "mask".to_string(),
            },
            ReviewKind::Boundary => {
                let target = review
                    .target_id
                    .as_deref()
                    .ok_or_else(|| "NATIVE_QA_REVIEW_TARGET_MISSING".to_string())?;
                let segment = manifest
                    .segments
                    .iter()
                    .find(|segment| segment.segment_id == target)
                    .ok_or_else(|| "NATIVE_QA_REVIEW_SEGMENT_MISSING".to_string())?;
                let segment_kind = if segment.kind == "unknown" {
                    "common"
                } else {
                    &segment.kind
                };
                ReviewResolution::Boundary {
                    page_start: segment.page_start,
                    page_end: segment.page_end,
                    segment_kind: segment_kind.to_string(),
                }
            }
            ReviewKind::Ocr => ReviewResolution::Ocr { accepted: true },
            ReviewKind::RegionGeometry => {
                let target = review
                    .target_id
                    .as_deref()
                    .ok_or_else(|| "NATIVE_QA_REVIEW_TARGET_MISSING".to_string())?;
                let region = manifest
                    .regions
                    .iter()
                    .find(|region| region.region_id == target)
                    .ok_or_else(|| "NATIVE_QA_REVIEW_REGION_MISSING".to_string())?;
                ReviewResolution::RegionGeometry {
                    rects: region.rects.clone(),
                }
            }
            _ => return Err("NATIVE_QA_REVIEW_KIND_UNSUPPORTED".to_string()),
        };
        manifest = resolve_masking_review_core(
            app,
            sessions,
            ResolveMaskingReviewRequest {
                run_id: manifest.run_id.clone(),
                analysis_revision: manifest.analysis_revision,
                manifest_hash: manifest.manifest_hash.clone(),
                review_id: review.review_id,
                resolution,
            },
        )?;
    }
    Ok(manifest)
}

fn observe_manual_and_intrinsic(
    app: &tauri::AppHandle,
    access: &AllowedFileAccess,
    sessions: &MaskingRunSessions,
    binding: &NativeQaThresholdBinding,
) -> Result<(serde_json::Value, serde_json::Value), String> {
    let fixture = fixture_path_for(MANUAL_FIXTURE, "public-native-qa-manual.pdf")?;
    let mut manifest = analyze_path(
        app,
        access,
        sessions,
        &fixture,
        "official_dispatch",
        binding,
    )?;
    let pending_before = manifest
        .review_items
        .iter()
        .filter(|item| item.status == "pending")
        .count();
    manifest = resolve_manual_pending(app, sessions, manifest)?;
    let (target, protected_neighbor) = manifest
        .occurrences
        .iter()
        .enumerate()
        .find_map(|(index, target)| {
            manifest
                .occurrences
                .iter()
                .skip(index + 1)
                .find(|neighbor| {
                    neighbor.page == target.page && !rects_overlap(&neighbor.rects, &target.rects)
                })
                .map(|neighbor| (target.clone(), neighbor.clone()))
        })
        .ok_or_else(|| "NATIVE_QA_MANUAL_PAIR_UNOBSERVED".to_string())?;
    let linked_occurrence_hash =
        masking_run_session::document_hash(target.occurrence_id.as_bytes());
    let neighbor_ref_count = protected_neighbor.rects.len();
    manifest = sessions.apply_manual_action(ManualActionV1Request {
        run_id: manifest.run_id.clone(),
        analysis_revision: manifest.analysis_revision,
        manifest_hash: manifest.manifest_hash.clone(),
        page: target.page,
        rects: target.rects.clone(),
        mode: "mask".to_string(),
        source_kind: "text_pdf".to_string(),
        linked_occurrence_id: Some(target.occurrence_id.clone()),
        target_region_id: None,
        expected_text_hash: None,
        protected_neighbor_refs: protected_neighbor.rects.clone(),
        restore_capability: None,
    })?;
    if manifest.manual_actions.len() != 1
        || manifest
            .review_items
            .iter()
            .any(|item| item.status == "pending")
    {
        return Err("NATIVE_QA_MANUAL_RESOLUTION_UNOBSERVED".to_string());
    }
    let binding = NativeSaveTargetBinding::Public {
        run_id: manifest.run_id.clone(),
        analysis_revision: manifest.analysis_revision,
        manifest_hash: manifest.manifest_hash.clone(),
    };
    let root = fixture
        .parent()
        .ok_or_else(|| "NATIVE_QA_FIXTURE_UNAVAILABLE".to_string())?;
    let destination = root.join("manual-final.pdf");
    let _ = std::fs::remove_file(&destination);
    let registration = register_native_save_target_core(access, &destination, binding)?;
    let original =
        std::fs::read(&fixture).map_err(|_| "NATIVE_QA_FIXTURE_UNAVAILABLE".to_string())?;
    let source_before_hash = masking_run_session::document_hash(&original);
    let mut changed = original.clone();
    changed.extend_from_slice(b"\n");
    let source_after_hash = masking_run_session::document_hash(&changed);
    std::fs::write(&fixture, &changed).map_err(|_| "NATIVE_QA_FIXTURE_UNAVAILABLE".to_string())?;
    let intrinsic = finalize_masking_run_core(
        app,
        access,
        sessions,
        request_for(&manifest, &destination, registration.save_token.clone()),
    );
    std::fs::write(&fixture, &original).map_err(|_| "NATIVE_QA_FIXTURE_UNAVAILABLE".to_string())?;
    let intrinsic_destination_absent = !destination.exists();
    if intrinsic != Err("MASKING_SESSION_ORIGINAL_CHANGED".to_string()) || destination.exists() {
        return Err("NATIVE_QA_INTRINSIC_FAILURE_UNOBSERVED".to_string());
    }
    let finalized = finalize_masking_run_core(
        app,
        access,
        sessions,
        request_for(&manifest, &destination, registration.save_token),
    )?;
    if finalized.status != "promoted" || !destination.is_file() {
        return Err("NATIVE_QA_MANUAL_FINALIZE_UNOBSERVED".to_string());
    }
    Ok((
        serde_json::json!({
            "fixtureHash": masking_run_session::document_hash(MANUAL_FIXTURE),
            "linkedOccurrenceHash": linked_occurrence_hash,
            "neighborRefCount": neighbor_ref_count,
            "pendingReviewCount": pending_before,
        }),
        serde_json::json!({
            "manualActionCount": manifest.manual_actions.len(),
            "pendingReviewCount": manifest.review_items.iter().filter(|item| item.status == "pending").count(),
            "promotedFinalHash": finalized.final_hash,
            "sourceBeforeHash": source_before_hash,
            "sourceAfterHash": source_after_hash,
            "intrinsicErrorCode": "MASKING_SESSION_ORIGINAL_CHANGED",
            "destinationAbsent": intrinsic_destination_absent,
        }),
    ))
}

fn observe_clean_document(
    app: &tauri::AppHandle,
    access: &AllowedFileAccess,
    sessions: &MaskingRunSessions,
    binding: &NativeQaThresholdBinding,
) -> Result<(serde_json::Value, serde_json::Value), String> {
    let fixture = fixture_path_for(CLEAN_FIXTURE, "public-native-qa-clean.pdf")?;
    let manifest = analyze_path(app, access, sessions, &fixture, "mixed", binding)?;
    if !manifest.occurrences.is_empty()
        || manifest
            .review_items
            .iter()
            .any(|item| item.status == "pending")
    {
        return Err("NATIVE_QA_CLEAN_DOCUMENT_UNOBSERVED".to_string());
    }
    let binding = NativeSaveTargetBinding::Public {
        run_id: manifest.run_id.clone(),
        analysis_revision: manifest.analysis_revision,
        manifest_hash: manifest.manifest_hash.clone(),
    };
    let destination = fixture
        .parent()
        .ok_or_else(|| "NATIVE_QA_FIXTURE_UNAVAILABLE".to_string())?
        .join("clean-final.pdf");
    let _ = std::fs::remove_file(&destination);
    let registration = register_native_save_target_core(access, &destination, binding)?;
    let finalized = finalize_masking_run_core(
        app,
        access,
        sessions,
        request_for(&manifest, &destination, registration.save_token),
    )?;
    let source_hash = masking_run_session::document_hash(CLEAN_FIXTURE);
    if finalized.status != "promoted" || finalized.final_hash != source_hash {
        return Err("NATIVE_QA_CLEAN_DOCUMENT_UNOBSERVED".to_string());
    }
    Ok((
        serde_json::json!({
            "fixtureHash": source_hash,
            "inputManifestHash": manifest.manifest_hash,
        }),
        serde_json::json!({
            "sourceHash": source_hash,
            "finalHash": finalized.final_hash,
            "occurrenceCount": manifest.occurrences.len(),
            "pendingReviewCount": manifest.review_items.iter().filter(|item| item.status == "pending").count(),
        }),
    ))
}
fn plumbing(
    app: &tauri::AppHandle,
    nonce: String,
    binding: &NativeQaThresholdBinding,
) -> Result<PublicActionReceiptV1, String> {
    let binary_hash = current_binary_hash()?;
    let fixture = fixture_path()?;
    let fixture_hash = masking_run_session::document_hash(FIXTURE);
    let access = app.state::<AllowedFileAccess>();
    let sessions = app.state::<MaskingRunSessions>();
    access.allow_document_path(&fixture);
    let mut manifest = analyze_masking_run_core(
        app,
        &access,
        &sessions,
        AnalyzeMaskingRunRequest {
            input_file: fixture.display().to_string(),
            profile: "official_dispatch".to_string(),
            options: options("official_dispatch", binding),
        },
    )?;
    verify_threshold_binding(&manifest, binding)?;
    let fixture_evidence = official_dispatch_fixture_evidence(&manifest);
    if !official_dispatch_fixture_semantics_observed(fixture_evidence) {
        return Err("NATIVE_QA_FIXTURE_SEMANTICS_UNOBSERVED".to_string());
    }
    let threshold_value_hash = binding.threshold_value_hash.clone();
    let mut receipt = PublicActionReceiptV1 {
        schema: "PublicActionReceiptV1",
        schema_version: 1,
        scenario: "public-document-plumbing".to_string(),
        nonce,
        binary_hash,
        run_id: manifest.run_id.clone(),
        analysis_revision: manifest.analysis_revision,
        manifest_hash: manifest.manifest_hash.clone(),
        threshold_version: binding.threshold_version.clone(),
        threshold_hash: binding.threshold_hash.clone(),
        threshold_value_hash,
        scenario_steps: PLUMBING_STEPS.to_vec(),
        actions: Vec::new(),
        canonical_receipt_hash: String::new(),
        receipt_auth: String::new(),
    };
    record(
        &mut receipt,
        "public_analyze_completed",
        "pass",
        None,
        request_evidence(
            "public_analyze_completed",
            &fixture_hash,
            serde_json::json!({
                "inputHash": fixture_hash,
                "profileHash": masking_run_session::document_hash(b"official_dispatch"),
                "optionsHash": hash_evidence(&options("official_dispatch", binding))?,
            }),
        )?,
        result_evidence(
            "ANALYZE_COMPLETED",
            manifest.occurrences.len(),
            serde_json::json!({
                "runIdHash": masking_run_session::document_hash(manifest.run_id.as_bytes()),
                "analysisRevision": manifest.analysis_revision,
                "manifestHash": manifest.manifest_hash,
                "reviewCount": manifest.review_items.len(),
                "nameReviewPending": fixture_evidence.name_review,
                "institutionReviewPending": fixture_evidence.institution_review,
                "nonPersonNameCandidateExcluded": fixture_evidence.non_person_name_candidate_excluded,
            }),
        )?,
    )?;
    let root = fixture
        .parent()
        .ok_or_else(|| "NATIVE_QA_FIXTURE_UNAVAILABLE".to_string())?;
    let blocked = root.join("blocked.pdf");
    let unresolved = request_for(&manifest, &blocked, "forged".to_string());
    let unresolved_evidence = finalize_actual_request(&unresolved, "unregistered");
    exact_error(
        finalize_masking_run_core(app, &access, &sessions, unresolved),
        "MASKING_SESSION_UNRESOLVED_REVIEW",
        "public_unresolved_review_blocked",
        "UNRESOLVED_REVIEW",
        unresolved_evidence,
        finalize_error_result(
            "MASKING_SESSION_UNRESOLVED_REVIEW",
            &manifest,
            "unresolved_review",
        ),
        &mut receipt,
    )?;
    let confirmed_manifest = analyze_masking_run_core(
        app,
        &access,
        &sessions,
        AnalyzeMaskingRunRequest {
            input_file: fixture.display().to_string(),
            profile: "official_dispatch".to_string(),
            options: options("official_dispatch", binding),
        },
    )?;
    let confirmed_destination = root.join("confirmed-unresolved.pdf");
    let _ = fs::remove_file(&confirmed_destination);
    let confirmed_binding = NativeSaveTargetBinding::Public {
        run_id: confirmed_manifest.run_id.clone(),
        analysis_revision: confirmed_manifest.analysis_revision,
        manifest_hash: confirmed_manifest.manifest_hash.clone(),
    };
    let confirmed_registration =
        register_native_save_target_core(&access, &confirmed_destination, confirmed_binding)?;
    let confirmed_request = request_for_with_confirmation(
        &confirmed_manifest,
        &confirmed_destination,
        confirmed_registration.save_token,
        true,
    );
    let confirmed_evidence =
        finalize_actual_request_with_confirmation(&confirmed_request, "registered");
    let confirmed = finalize_masking_run_core(app, &access, &sessions, confirmed_request)?;
    if confirmed.status != "promoted"
        || confirmed.save_confirmation.status != "user_confirmed"
        || confirmed.save_confirmation.unresolved_reviews.is_empty()
        || confirmed
            .save_confirmation
            .unresolved_reviews
            .iter()
            .any(|warning| warning.category.is_empty() || warning.page_end < warning.page_start)
        || !confirmed_destination.is_file()
    {
        return Err("NATIVE_QA_UNRESOLVED_CONFIRMATION_UNOBSERVED".to_string());
    }
    record(
        &mut receipt,
        "public_unresolved_review_confirmed",
        "pass",
        None,
        request_evidence(
            "public_unresolved_review_confirmed",
            &fixture_hash,
            confirmed_evidence,
        )?,
        result_evidence(
            "UNRESOLVED_REVIEW_CONFIRMED",
            1,
            serde_json::json!({
                "statusCode": "unresolved_review_confirmed",
                "confirmationStatus": confirmed.save_confirmation.status,
                "unresolvedReviewCount": confirmed.save_confirmation.unresolved_reviews.len(),
                "categoryPageEvidence": confirmed.save_confirmation.unresolved_reviews.iter().all(|warning| !warning.category.is_empty() && warning.page_end >= warning.page_start),
                "finalHash": confirmed.final_hash,
                "confirmedRunIdHash": masking_run_session::document_hash(confirmed_manifest.run_id.as_bytes()),
            }),
        )?,
    )?;
    let _ = fs::remove_file(&confirmed_destination);
    let stale_revision = manifest.analysis_revision.saturating_sub(1);
    let stale = FinalizeMaskingRunRequest {
        analysis_revision: stale_revision,
        ..request_for(&manifest, &blocked, "forged".to_string())
    };
    let stale_evidence = finalize_actual_request(&stale, "unregistered");
    exact_error(
        finalize_masking_run_core(app, &access, &sessions, stale),
        "MASKING_SESSION_STALE_ANALYSIS",
        "public_stale_revision_blocked",
        "STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED",
        stale_evidence,
        finalize_error_result(
            "MASKING_SESSION_STALE_ANALYSIS",
            &manifest,
            "stale_revision",
        ),
        &mut receipt,
    )?;
    let stale_hash = FinalizeMaskingRunRequest {
        manifest_hash: "0".repeat(64),
        ..request_for(&manifest, &blocked, "forged".to_string())
    };
    let stale_hash_evidence = finalize_actual_request(&stale_hash, "unregistered");
    exact_error(
        finalize_masking_run_core(app, &access, &sessions, stale_hash),
        "MASKING_SESSION_STALE_ANALYSIS",
        "public_stale_manifest_hash_blocked",
        "STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED",
        stale_hash_evidence,
        finalize_error_result(
            "MASKING_SESSION_STALE_ANALYSIS",
            &manifest,
            "stale_manifest",
        ),
        &mut receipt,
    )?;
    let tampered = FinalizeMaskingRunRequest {
        manifest_hash: "f".repeat(64),
        ..request_for(&manifest, &blocked, "forged".to_string())
    };
    let tampered_evidence = finalize_actual_request(&tampered, "unregistered");
    exact_error(
        finalize_masking_run_core(app, &access, &sessions, tampered),
        "MASKING_SESSION_STALE_ANALYSIS",
        "public_tampered_manifest_blocked",
        "PUBLIC_FINALIZE_REJECTED",
        tampered_evidence,
        finalize_error_result(
            "MASKING_SESSION_STALE_ANALYSIS",
            &manifest,
            "tampered_manifest",
        ),
        &mut receipt,
    )?;
    let forged_resolution = ResolveMaskingReviewRequest {
        run_id: manifest.run_id.clone(),
        analysis_revision: manifest.analysis_revision,
        manifest_hash: manifest.manifest_hash.clone(),
        review_id: "forged".to_string(),
        resolution: ReviewResolution::Acknowledge { acknowledged: true },
    };
    let forged_evidence = resolve_actual_request(&forged_resolution, "acknowledge");
    exact_error(
        resolve_masking_review_core(app, &sessions, forged_resolution),
        "MASKING_SESSION_UNKNOWN_REVIEW",
        "public_forged_resolution_blocked",
        "REVIEW_RESOLUTION_REJECTED",
        forged_evidence,
        finalize_error_result(
            "MASKING_SESSION_UNKNOWN_REVIEW",
            &manifest,
            "unknown_review",
        ),
        &mut receipt,
    )?;
    manifest = resolve_pending(app, &sessions, manifest)?;
    receipt.run_id = manifest.run_id.clone();
    receipt.analysis_revision = manifest.analysis_revision;
    receipt.manifest_hash = manifest.manifest_hash.clone();
    let save_target_binding = NativeSaveTargetBinding::Public {
        run_id: manifest.run_id.clone(),
        analysis_revision: manifest.analysis_revision,
        manifest_hash: manifest.manifest_hash.clone(),
    };
    let bypass = request_for(&manifest, &blocked, "forged".to_string());
    let bypass_evidence = finalize_actual_request(&bypass, "unregistered");
    exact_error(
        finalize_masking_run_core(app, &access, &sessions, bypass),
        "MASKING_SESSION_DESTINATION_REJECTED",
        "public_destination_bypass_blocked",
        "PUBLIC_FINALIZE_REJECTED",
        bypass_evidence,
        finalize_error_result(
            "MASKING_SESSION_DESTINATION_REJECTED",
            &manifest,
            "destination_rejected",
        ),
        &mut receipt,
    )?;
    let failure_dir = root.join("promotion-denied");
    match fs::create_dir(&failure_dir) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => return Err("NATIVE_QA_DESTINATION_UNAVAILABLE".to_string()),
    }
    let failure_destination = failure_dir.join("final.pdf");
    let _ = fs::remove_file(&failure_destination);
    let _ = fs::remove_dir(&failure_destination);
    let registration = register_native_save_target_core(
        &access,
        &failure_destination,
        save_target_binding.clone(),
    )?;
    let staging_probe = app
        .path()
        .app_cache_dir()
        .map_err(|_| "NATIVE_QA_DESTINATION_UNAVAILABLE".to_string())?
        .join("authoritative_masking")
        .join(&manifest.run_id);
    let blocked_destination = failure_destination.clone();
    let promotion_blocker = thread::spawn(move || {
        for _ in 0..2_000 {
            if staging_probe.is_dir() {
                return fs::create_dir(&blocked_destination).is_ok();
            }
            thread::sleep(Duration::from_millis(5));
        }
        false
    });
    record(
        &mut receipt,
        "public_destination_authorized",
        "pass",
        None,
        request_evidence(
            "public_destination_authorized",
            &fixture_hash,
            serde_json::json!({"destinationHash": masking_run_session::document_hash(failure_destination.to_string_lossy().as_bytes()), "manifestHash": manifest.manifest_hash, "bindingCode": "public"}),
        )?,
        result_evidence(
            "DESTINATION_AUTHORIZED",
            1,
            serde_json::json!({"saveTokenHash": masking_run_session::document_hash(registration.save_token.as_bytes()), "bindingCode": "registered"}),
        )?,
    )?;
    if registration.save_token.is_empty() {
        return Err("NATIVE_QA_TOKEN_UNAVAILABLE".to_string());
    }
    record(
        &mut receipt,
        "public_destination_token_issued",
        "pass",
        None,
        request_evidence(
            "public_destination_token_issued",
            &fixture_hash,
            serde_json::json!({"manifestHash": manifest.manifest_hash, "bindingCode": "registered"}),
        )?,
        result_evidence(
            "SAVE_TOKEN_ISSUED",
            1,
            serde_json::json!({"saveTokenHash": masking_run_session::document_hash(registration.save_token.as_bytes()), "nonempty": !registration.save_token.is_empty()}),
        )?,
    )?;
    verify_threshold_binding(&manifest, binding)?;
    record(
        &mut receipt,
        "public_threshold_hash_bound",
        "pass",
        None,
        request_evidence(
            "public_threshold_hash_bound",
            &fixture_hash,
            serde_json::json!({
                "thresholdHash": binding.threshold_hash,
                "thresholdVersion": binding.threshold_version,
                "thresholdValueHash": binding.threshold_value_hash,
                "autoMaskThreshold": binding.auto_mask_threshold,
                "reviewThreshold": binding.review_threshold,
            }),
        )?,
        result_evidence(
            "THRESHOLD_HASH_BOUND",
            1,
            serde_json::json!({
                "artifactHash": manifest.threshold_artifact.content_hash,
                "thresholdValueHash": binding.threshold_value_hash,
                "autoMaskThreshold": manifest.threshold_artifact.auto_mask_threshold,
                "reviewThreshold": manifest.threshold_artifact.review_threshold,
            }),
        )?,
    )?;
    let failure_request = request_for(&manifest, &failure_destination, registration.save_token);
    let failure_evidence = finalize_actual_request(&failure_request, "registered");
    let failure = finalize_masking_run_core(app, &access, &sessions, failure_request);
    let promotion_blocked = promotion_blocker.join().unwrap_or(false);
    if !promotion_blocked {
        return Err("NATIVE_QA_DESTINATION_UNAVAILABLE".to_string());
    }
    exact_error(
        failure,
        "MASKING_SESSION_PRECOMMIT_RETRYABLE;cause=MASKING_SESSION_PROMOTION_FAILED",
        "public_atomic_promotion_failure_blocked",
        "ATOMIC_PROMOTION_FAILED",
        failure_evidence,
        finalize_error_result(
            "MASKING_SESSION_PRECOMMIT_RETRYABLE;cause=MASKING_SESSION_PROMOTION_FAILED",
            &manifest,
            "promotion_failed",
        ),
        &mut receipt,
    )?;
    let destination = root.join("final.pdf");
    let registration =
        register_native_save_target_core(&access, &destination, save_target_binding)?;
    let finalized = finalize_masking_run_core(
        app,
        &access,
        &sessions,
        request_for(&manifest, &destination, registration.save_token),
    )?;
    if finalized.status != "promoted"
        || std::fs::read(&destination)
            .map(|bytes| masking_run_session::document_hash(&bytes))
            .ok()
            .as_deref()
            != Some(finalized.final_hash.as_str())
    {
        return Err("NATIVE_QA_PROMOTION_UNOBSERVED".to_string());
    }
    record(
        &mut receipt,
        "public_finalize_promoted",
        "pass",
        None,
        request_evidence(
            "public_finalize_promoted",
            &fixture_hash,
            serde_json::json!({"destinationHash": masking_run_session::document_hash(destination.to_string_lossy().as_bytes()), "manifestHash": manifest.manifest_hash}),
        )?,
        result_evidence(
            "PROMOTED",
            1,
            serde_json::json!({"statusCode": finalized.status, "finalHash": finalized.final_hash}),
        )?,
    )?;
    seal_receipt(receipt)
}
fn full(
    app: &tauri::AppHandle,
    nonce: String,
    binding: &NativeQaThresholdBinding,
) -> Result<PublicActionReceiptV1, String> {
    let access = app.state::<AllowedFileAccess>();
    let sessions = app.state::<MaskingRunSessions>();

    let mixed_fixture = fixture_path_for(FIXTURE, "public-native-qa-mixed.pdf")?;
    let mixed = analyze_path(app, &access, &sessions, &mixed_fixture, "mixed", binding)?;
    let mixed_routes_confidently = ["internal_review", "official_dispatch"].iter().all(|kind| {
        mixed.segments.iter().any(|segment| {
            segment.kind == *kind && segment.state == "confirmed" && !segment.common_only
        })
    }) && mixed.segments.iter().all(|segment| !segment.common_only);
    let boundary_blocked = mixed.review_items.iter().any(|item| {
        item.status == "pending"
            && matches!(item.kind, ReviewKind::Boundary | ReviewKind::RegionGeometry)
    });
    if !mixed_routes_confidently || !boundary_blocked {
        return Err("NATIVE_QA_MIXED_REVIEW_UNOBSERVED".to_string());
    }

    let ambiguous_fixture = fixture_path_for(AMBIGUOUS_FIXTURE, "public-native-qa-ambiguous.pdf")?;
    let ambiguous = analyze_path(
        app,
        &access,
        &sessions,
        &ambiguous_fixture,
        "mixed",
        binding,
    )?;
    let ambiguous_common_only = ambiguous.review_items.iter().any(|item| {
        item.status == "pending"
            && item.kind == ReviewKind::Acknowledge
            && item.common_only
            && item.requires_acknowledgment
            && item
                .reason_codes
                .iter()
                .any(|reason| reason == "ambiguous_boundary")
    });
    let ambiguous_root = ambiguous_fixture
        .parent()
        .ok_or_else(|| "NATIVE_QA_FIXTURE_UNAVAILABLE".to_string())?;
    let ambiguous_finalize_blocked = matches!(
        finalize_masking_run_core(
            app,
            &access,
            &sessions,
            request_for(
                &ambiguous,
                &ambiguous_root.join("ambiguous-blocked.pdf"),
                "forged".to_string(),
            ),
        ),
        Err(ref error) if error == "MASKING_SESSION_UNRESOLVED_REVIEW"
    );
    if !ambiguous_common_only || !ambiguous_finalize_blocked {
        return Err("NATIVE_QA_AMBIGUOUS_REVIEW_UNOBSERVED".to_string());
    }
    let repeat_fixture = fixture_path_for(REPEAT_FIXTURE, "public-native-qa-repeat.pdf")?;
    let repeated_manifest =
        analyze_path(app, &access, &sessions, &repeat_fixture, "mixed", binding)?;
    let mut repeated =
        std::collections::HashMap::<&str, Vec<&masking_run_session::AnalysisOccurrence>>::new();
    for occurrence in &repeated_manifest.occurrences {
        repeated
            .entry(occurrence.value_hash.as_str())
            .or_default()
            .push(occurrence);
    }
    let repeated_scoped = repeated.values().any(|items| {
        items.len() > 1
            && items
                .windows(2)
                .any(|pair| pair[0].page != pair[1].page || pair[0].rects != pair[1].rects)
    });
    if !repeated_scoped {
        return Err("NATIVE_QA_REPEATED_OCCURRENCE_UNOBSERVED".to_string());
    }

    let scan_fixture = fixture_path_for(SCAN_FIXTURE, "public-native-qa-scan.pdf")?;
    access.allow_document_path(&scan_fixture);
    let scan_manifest = analyze_masking_run_core(
        app,
        &access,
        &sessions,
        AnalyzeMaskingRunRequest {
            input_file: scan_fixture.display().to_string(),
            profile: "official_dispatch".to_string(),
            options: options("official_dispatch", binding),
        },
    )?;
    let scan_segment_count = scan_manifest
        .segments
        .iter()
        .filter(|segment| {
            segment.source == "scanned_geometry_unavailable"
                && segment.kind == "unknown"
                && !segment.common_only
        })
        .count();
    let pending_scan_review_count = scan_manifest
        .review_items
        .iter()
        .filter(|review| {
            review.kind == ReviewKind::Acknowledge
                && review.status == "pending"
                && review.requires_acknowledgment
                && review
                    .reason_codes
                    .iter()
                    .any(|code| code == "scanned_geometry_unavailable")
        })
        .count();
    if scan_segment_count == 0 || pending_scan_review_count == 0 {
        return Err("NATIVE_QA_SCAN_MANUAL_REVIEW_UNOBSERVED".to_string());
    }

    let legal_tags = [
        "CASE_NUMBER",
        "LEGAL_PARTY",
        "COURT",
        "CASE_TITLE",
        "LAW_FIRM",
        "ATTORNEY",
    ];
    if mixed
        .occurrences
        .iter()
        .any(|item| legal_tags.contains(&item.tag.as_str()))
    {
        return Err("NATIVE_QA_LEGAL_ISOLATION_UNOBSERVED".to_string());
    }
    let pending_boundary_count = mixed
        .review_items
        .iter()
        .filter(|item| {
            item.status == "pending"
                && matches!(item.kind, ReviewKind::Boundary | ReviewKind::RegionGeometry)
        })
        .count();
    let mixed_pending_review_id_hash = hash_evidence(&serde_json::json!(mixed
        .review_items
        .iter()
        .filter(|item| item.status == "pending")
        .map(|item| &item.review_id)
        .collect::<Vec<_>>()))?;
    let pending_common_only_count = ambiguous
        .review_items
        .iter()
        .filter(|item| item.status == "pending" && item.common_only && item.requires_acknowledgment)
        .count();
    let ambiguous_pending_review_id_hash = hash_evidence(&serde_json::json!(ambiguous
        .review_items
        .iter()
        .filter(|item| item.status == "pending" && item.common_only && item.requires_acknowledgment)
        .map(|item| &item.review_id)
        .collect::<Vec<_>>()))?;
    let review_pending_before = mixed
        .review_items
        .iter()
        .filter(|item| item.status == "pending")
        .count();
    let resolved_mixed = resolve_pending(app, &sessions, mixed.clone())?;
    let repeat_items = repeated
        .values()
        .find(|items| {
            items.len() > 1
                && items
                    .windows(2)
                    .any(|pair| pair[0].page != pair[1].page || pair[0].rects != pair[1].rects)
        })
        .ok_or_else(|| "NATIVE_QA_REPEATED_OCCURRENCE_UNOBSERVED".to_string())?;
    let repeat_evidence = (
        repeat_items[0].value_hash.clone(),
        repeat_items.len(),
        hash_evidence(&serde_json::json!(repeat_items
            .iter()
            .map(|item| serde_json::json!({"page":item.page,"rects":item.rects}))
            .collect::<Vec<_>>()))?,
    );
    let legal_tag_set_hash = hash_evidence(&serde_json::json!(legal_tags))?;

    let (manual_request, manual_result) =
        observe_manual_and_intrinsic(app, &access, &sessions, binding)?;
    let (clean_request, clean_result) = observe_clean_document(app, &access, &sessions, binding)?;
    let base = plumbing(app, nonce.clone(), binding)?;
    for (name, outcome, error_code) in [
        ("public_analyze_completed", "pass", None),
        (
            "public_unresolved_review_blocked",
            "blocked",
            Some("UNRESOLVED_REVIEW"),
        ),
        ("public_unresolved_review_confirmed", "pass", None),
        (
            "public_stale_revision_blocked",
            "blocked",
            Some("STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED"),
        ),
        (
            "public_stale_manifest_hash_blocked",
            "blocked",
            Some("STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED"),
        ),
        (
            "public_tampered_manifest_blocked",
            "blocked",
            Some("PUBLIC_FINALIZE_REJECTED"),
        ),
        (
            "public_forged_resolution_blocked",
            "blocked",
            Some("REVIEW_RESOLUTION_REJECTED"),
        ),
        (
            "public_destination_bypass_blocked",
            "blocked",
            Some("PUBLIC_FINALIZE_REJECTED"),
        ),
        ("public_destination_authorized", "pass", None),
        ("public_destination_token_issued", "pass", None),
        ("public_threshold_hash_bound", "pass", None),
        (
            "public_atomic_promotion_failure_blocked",
            "blocked",
            Some("ATOMIC_PROMOTION_FAILED"),
        ),
        ("public_finalize_promoted", "pass", None),
    ] {
        require_action(&base, name, outcome, error_code)?;
    }

    let mut receipt = PublicActionReceiptV1 {
        schema: "PublicActionReceiptV1",
        schema_version: 1,
        scenario: "public-document-all".to_string(),
        nonce,
        binary_hash: base.binary_hash.clone(),
        run_id: base.run_id.clone(),
        analysis_revision: base.analysis_revision,
        manifest_hash: base.manifest_hash.clone(),
        threshold_version: base.threshold_version.clone(),
        threshold_hash: base.threshold_hash.clone(),
        threshold_value_hash: base.threshold_value_hash.clone(),
        scenario_steps: FULL_STEPS.to_vec(),
        actions: Vec::new(),
        canonical_receipt_hash: String::new(),
        receipt_auth: String::new(),
    };
    copy_action(&mut receipt, &base, "public_analyze_completed")?;
    // Mixed-fixture contract (T40 recalibration): the shipped
    // public-native-qa-mixed.pdf yields 10 pending review cards, 4 of them
    // boundary/geometry pending (pendingBoundaryCount). Totals below are
    // computed dynamically; scripts/e2e_tauri_local_smoke.py mirrors the
    // pending total as MIXED_FIXTURE_PENDING_REVIEW_COUNT.
    bind_observation(
        &mut receipt,
        "public_mixed_boundary_blocked",
        "MIXED_BOUNDARY_OBSERVED",
        pending_boundary_count,
        serde_json::json!({"fixtureHash": masking_run_session::document_hash(FIXTURE), "manifestHash": mixed.manifest_hash, "pendingBoundaryCount": pending_boundary_count, "pendingReviewIdHash": mixed_pending_review_id_hash}),
        serde_json::json!({"boundaryBlocked": boundary_blocked, "pendingBoundaryCount": pending_boundary_count, "pendingReviewCount": review_pending_before}),
    )?;
    bind_observation(
        &mut receipt,
        "public_ambiguous_common_only_blocked",
        "AMBIGUOUS_COMMON_ONLY_OBSERVED",
        pending_common_only_count,
        serde_json::json!({"fixtureHash": masking_run_session::document_hash(AMBIGUOUS_FIXTURE), "manifestHash": ambiguous.manifest_hash, "pendingCommonOnlyCount": pending_common_only_count, "pendingReviewIdHash": ambiguous_pending_review_id_hash}),
        serde_json::json!({"commonOnlyBlocked": ambiguous_common_only && ambiguous_finalize_blocked, "pendingCommonOnlyCount": pending_common_only_count, "pendingReviewCount": ambiguous.review_items.iter().filter(|item| item.status == "pending").count()}),
    )?;
    bind_observation(
        &mut receipt,
        "public_scan_manual_review_required",
        "SCANNED_GEOMETRY_REVIEW_OBSERVED",
        pending_scan_review_count,
        serde_json::json!({"inputHash": masking_run_session::document_hash(SCAN_FIXTURE), "profileHash": masking_run_session::document_hash(b"official_dispatch"), "optionsHash": hash_evidence(&options("official_dispatch", binding))?}),
        serde_json::json!({"scanSegmentCount": scan_segment_count, "pendingScanReviewCount": pending_scan_review_count, "manifestHash": scan_manifest.manifest_hash}),
    )?;
    bind_observation(
        &mut receipt,
        "public_repeated_occurrence_scoped",
        "REPEATED_OCCURRENCE_SCOPE_OBSERVED",
        repeat_evidence.1,
        serde_json::json!({"inputHash": masking_run_session::document_hash(REPEAT_FIXTURE), "duplicateValueHash": repeat_evidence.0, "distinctPageOrRectHash": repeat_evidence.2}),
        serde_json::json!({"duplicateOccurrenceCount": repeat_evidence.1, "occurrenceCount": repeated_manifest.occurrences.len(), "scoped": repeated_scoped, "manifestHash": repeated_manifest.manifest_hash}),
    )?;
    bind_observation(
        &mut receipt,
        "public_review_cards_resolved",
        "REVIEW_RESOLUTION_OBSERVED",
        review_pending_before,
        serde_json::json!({"fixtureHash": masking_run_session::document_hash(FIXTURE), "pendingBefore": review_pending_before, "manifestHash": mixed.manifest_hash}),
        serde_json::json!({"pendingAfter": resolved_mixed.review_items.iter().filter(|item| item.status == "pending").count(), "resolvedRevision": resolved_mixed.analysis_revision, "resolvedManifestHash": resolved_mixed.manifest_hash}),
    )?;
    bind_observation(
        &mut receipt,
        "public_manual_combined_resolved",
        "MANUAL_AND_INTRINSIC_OBSERVED",
        1,
        manual_request.clone(),
        serde_json::json!({"manualActionCount": manual_result["manualActionCount"], "pendingReviewCount": manual_result["pendingReviewCount"], "linkedOccurrenceHash": manual_request["linkedOccurrenceHash"], "neighborRefCount": manual_request["neighborRefCount"], "promotedFinalHash": manual_result["promotedFinalHash"]}),
    )?;
    bind_observation(
        &mut receipt,
        "public_legal_advisory_isolated",
        "LEGAL_TAGS_ABSENT",
        0,
        serde_json::json!({"fixtureHash": masking_run_session::document_hash(FIXTURE), "checkedTagSetHash": legal_tag_set_hash, "manifestHash": mixed.manifest_hash}),
        serde_json::json!({"matchedCount": 0usize, "occurrenceCount": mixed.occurrences.len()}),
    )?;
    for name in [
        "public_unresolved_review_blocked",
        "public_unresolved_review_confirmed",
        "public_stale_revision_blocked",
        "public_stale_manifest_hash_blocked",
        "public_tampered_manifest_blocked",
        "public_forged_resolution_blocked",
    ] {
        copy_action(&mut receipt, &base, name)?;
    }
    bind_observation(
        &mut receipt,
        "public_intrinsic_failure_blocked",
        "MASKING_SESSION_ORIGINAL_CHANGED",
        1,
        serde_json::json!({"fixtureHash": manual_request["fixtureHash"], "sourceBeforeHash": manual_result["sourceBeforeHash"], "sourceAfterHash": manual_result["sourceAfterHash"]}),
        serde_json::json!({"errorCode": manual_result["intrinsicErrorCode"], "destinationAbsent": manual_result["destinationAbsent"]}),
    )?;
    for name in [
        "public_destination_bypass_blocked",
        "public_destination_authorized",
        "public_destination_token_issued",
        "public_threshold_hash_bound",
    ] {
        copy_action(&mut receipt, &base, name)?;
    }
    bind_observation(
        &mut receipt,
        "public_clean_document_verified",
        "CLEAN_DOCUMENT_HASH_MATCHED",
        0,
        clean_request,
        clean_result,
    )?;
    copy_action(
        &mut receipt,
        &base,
        "public_atomic_promotion_failure_blocked",
    )?;
    copy_action(&mut receipt, &base, "public_finalize_promoted")?;
    seal_receipt(receipt)
}
pub(crate) fn dispatch_from_stdin(app: &tauri::AppHandle) -> Result<(), String> {
    let mut input = String::new();
    std::io::stdin()
        .read_to_string(&mut input)
        .map_err(|_| "NATIVE_QA_STDIN_UNREADABLE".to_string())?;
    let request: NativeQaRequest =
        serde_json::from_str(&input).map_err(|_| "NATIVE_QA_REQUEST_INVALID".to_string())?;
    if request.schema_version != 1
        || request.nonce.len() < 32
        || request.nonce.len() > 256
        || !request
            .nonce
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
    {
        return Err("NATIVE_QA_REQUEST_INVALID".to_string());
    }
    let binding = parse_threshold_binding(&request)?;
    let receipt = match request.scenario.as_str() {
        "public-document-plumbing" => plumbing(app, request.nonce, &binding)?,
        "public-document-all" => full(app, request.nonce, &binding)?,
        _ => return Err("NATIVE_QA_SCENARIO_INVALID".to_string()),
    };
    let line = serde_json::to_string(&NativeReceiptEvent {
        event: "public_action_receipt",
        source: "native",
        receipt,
    })
    .map_err(|_| "NATIVE_QA_SERIALIZATION_FAILED".to_string())?;
    std::io::stdout()
        .write_all(format!("{line}\n").as_bytes())
        .map_err(|_| "NATIVE_QA_STDOUT_UNWRITABLE".to_string())
}
#[cfg(test)]
mod tests {
    use super::*;

    fn receipt() -> PublicActionReceiptV1 {
        PublicActionReceiptV1 {
            schema: "PublicActionReceiptV1",
            schema_version: 1,
            scenario: "public-document-plumbing".to_string(),
            nonce: "a".repeat(32),
            binary_hash: "b".repeat(64),
            run_id: "run".to_string(),
            analysis_revision: 1,
            manifest_hash: "m".repeat(64),
            threshold_version: "v1".to_string(),
            threshold_hash: "t".repeat(64),
            threshold_value_hash: "u".repeat(64),
            scenario_steps: PLUMBING_STEPS.to_vec(),
            actions: Vec::new(),
            canonical_receipt_hash: String::new(),
            receipt_auth: String::new(),
        }
    }

    #[test]
    fn recorder_requires_declared_order_and_hashes_observed_action() {
        let mut receipt = receipt();
        let evidence = || {
            (
                request_evidence(
                    "public_analyze_completed",
                    &masking_run_session::document_hash(FIXTURE),
                    serde_json::json!({"inputHash": masking_run_session::document_hash(FIXTURE)}),
                ),
                result_evidence(
                    "ANALYZE_COMPLETED",
                    OFFICIAL_DISPATCH_FIXTURE_OCCURRENCE_COUNT,
                    serde_json::json!({"occurrenceCount": OFFICIAL_DISPATCH_FIXTURE_OCCURRENCE_COUNT}),
                ),
            )
        };
        assert!(record(
            &mut receipt,
            "public_unresolved_review_blocked",
            "blocked",
            Some("UNRESOLVED_REVIEW"),
            request_evidence(
                "public_unresolved_review_blocked",
                &masking_run_session::document_hash(FIXTURE),
                serde_json::json!({"expectedErrorCode": "MASKING_SESSION_UNRESOLVED_REVIEW"}),
            )
            .expect("request evidence"),
            result_evidence(
                "UNRESOLVED_REVIEW",
                1,
                serde_json::json!({"errorCode": "MASKING_SESSION_UNRESOLVED_REVIEW"}),
            )
            .expect("result evidence"),
        )
        .is_err());
        let (request, result) = evidence();
        record(
            &mut receipt,
            "public_analyze_completed",
            "pass",
            None,
            request.expect("request evidence"),
            result.expect("result evidence"),
        )
        .expect("declared first action");
        assert_eq!(receipt.actions.len(), 1);
        assert!(!receipt.actions[0].request_hash.is_empty());
        assert!(!receipt.actions[0].result_hash.is_empty());
    }

    #[test]
    fn plumbing_contract_has_thirteen_observable_actions() {
        assert_eq!(PLUMBING_STEPS.len(), 13);
        assert_eq!(PLUMBING_STEPS.last(), Some(&"public_finalize_promoted"));
    }

    #[test]
    fn official_dispatch_fixture_semantics_require_authority_activated_profile_detection() {
        let observed = OfficialDispatchFixtureEvidence {
            occurrence_count: OFFICIAL_DISPATCH_FIXTURE_OCCURRENCE_COUNT,
            pending_review_count: OFFICIAL_DISPATCH_FIXTURE_PENDING_REVIEW_COUNT,
            confirmed_profile_route: true,
            profile_layout_occurrence: true,
            name_review: true,
            institution_review: true,
            acknowledge_review: false,
            profile_authority_missing_review: false,
            non_person_name_candidate_excluded: true,
        };
        assert!(official_dispatch_fixture_semantics_observed(observed));

        for missing_contract in [
            OfficialDispatchFixtureEvidence {
                occurrence_count: OFFICIAL_DISPATCH_FIXTURE_OCCURRENCE_COUNT - 1,
                ..observed
            },
            OfficialDispatchFixtureEvidence {
                pending_review_count: OFFICIAL_DISPATCH_FIXTURE_PENDING_REVIEW_COUNT - 1,
                ..observed
            },
            OfficialDispatchFixtureEvidence {
                confirmed_profile_route: false,
                ..observed
            },
            OfficialDispatchFixtureEvidence {
                profile_layout_occurrence: false,
                ..observed
            },
            OfficialDispatchFixtureEvidence {
                name_review: false,
                ..observed
            },
            OfficialDispatchFixtureEvidence {
                institution_review: false,
                ..observed
            },
            OfficialDispatchFixtureEvidence {
                acknowledge_review: true,
                ..observed
            },
            OfficialDispatchFixtureEvidence {
                profile_authority_missing_review: true,
                ..observed
            },
            OfficialDispatchFixtureEvidence {
                non_person_name_candidate_excluded: false,
                ..observed
            },
        ] {
            assert!(!official_dispatch_fixture_semantics_observed(
                missing_contract
            ));
        }
    }

    #[test]
    fn threshold_binding_uses_supplied_values_and_hashes() {
        let auto_mask_threshold = 0.73;
        let review_threshold = 0.41;
        let request = NativeQaRequest {
            schema_version: 1,
            scenario: "public-document-plumbing".to_string(),
            nonce: "n".repeat(32),
            threshold_version: masking_run_session::THRESHOLD_VERSION.to_string(),
            threshold_hash: threshold_artifact_hash(auto_mask_threshold, review_threshold)
                .expect("artifact hash"),
            threshold_value_hash: threshold_value_hash(auto_mask_threshold, review_threshold)
                .expect("value hash"),
            auto_mask_threshold,
            review_threshold,
        };
        let binding = parse_threshold_binding(&request).expect("valid threshold binding");
        let configured = options("official_dispatch", &binding);
        assert_eq!(
            configured["auto_mask_threshold"].as_f64(),
            Some(auto_mask_threshold)
        );
        assert_eq!(
            configured["review_threshold"].as_f64(),
            Some(review_threshold)
        );
        assert_eq!(binding.threshold_hash, request.threshold_hash);
        assert_eq!(binding.threshold_value_hash, request.threshold_value_hash);
    }

    #[test]
    fn threshold_binding_rejects_malformed_and_mismatched_hashes() {
        let mut request = NativeQaRequest {
            schema_version: 1,
            scenario: "public-document-plumbing".to_string(),
            nonce: "n".repeat(32),
            threshold_version: masking_run_session::THRESHOLD_VERSION.to_string(),
            threshold_hash: "A".repeat(64),
            threshold_value_hash: "b".repeat(64),
            auto_mask_threshold: 0.73,
            review_threshold: 0.41,
        };
        assert_eq!(
            parse_threshold_binding(&request).expect_err("uppercase hash must fail"),
            "NATIVE_QA_THRESHOLD_BINDING_INVALID"
        );
        request.threshold_hash =
            threshold_artifact_hash(request.auto_mask_threshold, request.review_threshold)
                .expect("artifact hash");
        assert_eq!(
            parse_threshold_binding(&request).expect_err("value hash mismatch must fail"),
            "NATIVE_QA_THRESHOLD_BINDING_MISMATCH"
        );
    }

    #[test]
    fn stdin_request_requires_threshold_binding_fields() {
        let result = serde_json::from_str::<NativeQaRequest>(
            r#"{"schemaVersion":1,"scenario":"public-document-plumbing","nonce":"nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn"}"#,
        );
        assert!(result.is_err());
    }
}
