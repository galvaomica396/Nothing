use rfd::FileDialog;
use serde::{de::DeserializeOwned, de::Error as _, Deserialize, Serialize};
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;
use tauri::Manager;

// allow: SIZE_OK — Tauri command aggregator plus app bootstrap. The path
// security guards, runtime/Python path discovery, and the macOS activation FFI
// were split into `path_security`, `runtime_paths`, and `platform_macos`
// (R3 module separation); the IPC commands and app builder remain here.

#[allow(dead_code)]
mod contracts_generated;
mod coordinate_batch;
mod coordinate_templates;
mod manual_revalidation;
mod masking_run_session;
mod native_qa;
mod path_security;
mod platform_macos;
mod process_util;
mod qa_drive;
mod runtime_paths;

use manual_revalidation::{
    report_allows_final_save, restore_reexposes_masked_region, write_manual_revalidation_report,
    ApplyResult, RestoreRect,
};
use masking_run_session::{
    finalization_save_confirmation, AnalysisManifestV1, AnalyzeMaskingRunRequest,
    FinalizeDisposition, FinalizeMaskingRunRequest, FinalizeMaskingRunResult,
    ManualActionV1Request, MaskingRunSessions, ResolveMaskingReviewRequest,
    RestoreCapabilityRequest,
};

// Re-exported for the commands below and their path-security checks.
pub(crate) use path_security::{
    canonicalize_existing_dir, canonicalize_existing_file, has_extension, AllowedFileAccess,
};
pub(crate) use runtime_paths::{
    resolve_lifecycle_runtime_paths, resolve_python, resolve_runtime_paths,
};

#[cfg(test)]
use path_security::safe_copy_new_at;
use path_security::{
    canonicalize_registered_artifact_dir, canonicalize_registered_document,
    canonicalize_registered_native_save_target, canonicalize_registered_pdf,
    consume_registered_native_save_target, copy_optional_artifact, optional_registered_artifact,
    optional_registered_masked_text_artifact, optional_registered_report_artifact,
    remove_intermediate_file_if_outside_dir, safe_copy_new, stage_copy_overwrite_exact,
    stage_copy_overwrite_exact_from_file, ExactOverwriteTransaction, NativeSaveTargetBinding,
    NativeSaveTargetRegistration, SaveError,
};
use platform_macos::{activate_macos_app, set_macos_activation_policy, show_macos_application};

#[derive(Debug, Deserialize, Serialize)]
struct ManualBoxPayload {
    page: u32,
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
    mode: String,
    tag: Option<String>,
}

#[derive(Debug, Serialize)]
struct FinalizeResult {
    final_output_file: String,
    copied_files: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct QaFileStat {
    exists: bool,
    size: u64,
}

#[derive(Debug)]
pub struct ApplyManualBoxesRequest {
    input_pdf: String,
    original_pdf: String,
    output_dir: String,
    display_mode: String,
    report_path: Option<String>,
    boxes: Vec<ManualBoxPayload>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApplyManualBoxesPayload {
    input_pdf: String,
    original_pdf: String,
    output_dir: String,
    display_mode: String,
    report_path: Option<String>,
    boxes: Vec<ManualBoxPayload>,
}

impl From<ApplyManualBoxesPayload> for ApplyManualBoxesRequest {
    fn from(payload: ApplyManualBoxesPayload) -> Self {
        Self {
            input_pdf: payload.input_pdf,
            original_pdf: payload.original_pdf,
            output_dir: payload.output_dir,
            display_mode: payload.display_mode,
            report_path: payload.report_path,
            boxes: payload.boxes,
        }
    }
}

impl<'de, R: tauri::Runtime> tauri::ipc::CommandArg<'de, R> for ApplyManualBoxesRequest {
    fn from_command(
        command: tauri::ipc::CommandItem<'de, R>,
    ) -> Result<Self, tauri::ipc::InvokeError> {
        parse_flat_command_payload::<R, ApplyManualBoxesPayload>(command).map(Self::from)
    }
}

#[derive(Debug)]
pub struct FinalizeManualOutputRequest {
    preview_pdf: String,
    original_pdf: String,
    output_dir: String,
    extracted_path: Option<String>,
    masked_path: Option<String>,
    report_path: Option<String>,
    copy_report: Option<bool>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FinalizeManualOutputPayload {
    preview_pdf: String,
    original_pdf: String,
    output_dir: String,
    extracted_path: Option<String>,
    masked_path: Option<String>,
    report_path: Option<String>,
    copy_report: Option<bool>,
}

impl From<FinalizeManualOutputPayload> for FinalizeManualOutputRequest {
    fn from(payload: FinalizeManualOutputPayload) -> Self {
        Self {
            preview_pdf: payload.preview_pdf,
            original_pdf: payload.original_pdf,
            output_dir: payload.output_dir,
            extracted_path: payload.extracted_path,
            masked_path: payload.masked_path,
            report_path: payload.report_path,
            copy_report: payload.copy_report,
        }
    }
}

impl<'de, R: tauri::Runtime> tauri::ipc::CommandArg<'de, R> for FinalizeManualOutputRequest {
    fn from_command(
        command: tauri::ipc::CommandItem<'de, R>,
    ) -> Result<Self, tauri::ipc::InvokeError> {
        parse_flat_command_payload::<R, FinalizeManualOutputPayload>(command).map(Self::from)
    }
}
#[derive(Debug)]
pub struct ChooseFinalPdfPathRequest {
    default_file_name: String,
    mode: Option<String>,
    run_id: Option<String>,
    analysis_revision: Option<u64>,
    manifest_hash: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChooseFinalPdfPathPayload {
    default_file_name: String,
    mode: Option<String>,
    run_id: Option<String>,
    analysis_revision: Option<u64>,
    manifest_hash: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct FinalPdfSaveTarget {
    output_path: String,
    save_token: String,
}

#[derive(Clone, Copy)]
struct QaDriveEnabled(bool);

impl From<ChooseFinalPdfPathPayload> for ChooseFinalPdfPathRequest {
    fn from(payload: ChooseFinalPdfPathPayload) -> Self {
        Self {
            default_file_name: payload.default_file_name,
            mode: payload.mode,
            run_id: payload.run_id,
            analysis_revision: payload.analysis_revision,
            manifest_hash: payload.manifest_hash,
        }
    }
}

impl ChooseFinalPdfPathRequest {
    fn binding(&self) -> Result<NativeSaveTargetBinding, String> {
        match (
            self.mode.as_deref(),
            self.run_id.as_deref(),
            self.analysis_revision,
            self.manifest_hash.as_deref(),
        ) {
            (None, Some(run_id), Some(analysis_revision), Some(manifest_hash))
                if !run_id.trim().is_empty() && !manifest_hash.trim().is_empty() =>
            {
                Ok(NativeSaveTargetBinding::Public {
                    run_id: run_id.to_string(),
                    analysis_revision,
                    manifest_hash: manifest_hash.to_string(),
                })
            }
            (Some("legacy_direct"), None, None, None) => Ok(NativeSaveTargetBinding::LegacyManual),
            _ => Err("MASKING_SESSION_DESTINATION_REJECTED".to_string()),
        }
    }
}

impl<'de, R: tauri::Runtime> tauri::ipc::CommandArg<'de, R> for ChooseFinalPdfPathRequest {
    fn from_command(
        command: tauri::ipc::CommandItem<'de, R>,
    ) -> Result<Self, tauri::ipc::InvokeError> {
        parse_flat_command_payload::<R, ChooseFinalPdfPathPayload>(command).map(Self::from)
    }
}

#[derive(Debug)]
pub struct FinalizeManualOutputToSelectedPathRequest {
    preview_pdf: String,
    original_pdf: String,
    extracted_path: Option<String>,
    masked_path: Option<String>,
    report_path: Option<String>,
    copy_report: Option<bool>,
    output_path: String,
    save_token: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FinalizeManualOutputToSelectedPathPayload {
    preview_pdf: String,
    original_pdf: String,
    extracted_path: Option<String>,
    masked_path: Option<String>,
    report_path: Option<String>,
    copy_report: Option<bool>,
    output_path: String,
    save_token: String,
}

impl From<FinalizeManualOutputToSelectedPathPayload> for FinalizeManualOutputToSelectedPathRequest {
    fn from(payload: FinalizeManualOutputToSelectedPathPayload) -> Self {
        Self {
            preview_pdf: payload.preview_pdf,
            original_pdf: payload.original_pdf,
            extracted_path: payload.extracted_path,
            masked_path: payload.masked_path,
            report_path: payload.report_path,
            copy_report: payload.copy_report,
            output_path: payload.output_path,
            save_token: payload.save_token,
        }
    }
}

impl<'de, R: tauri::Runtime> tauri::ipc::CommandArg<'de, R>
    for FinalizeManualOutputToSelectedPathRequest
{
    fn from_command(
        command: tauri::ipc::CommandItem<'de, R>,
    ) -> Result<Self, tauri::ipc::InvokeError> {
        parse_flat_command_payload::<R, FinalizeManualOutputToSelectedPathPayload>(command)
            .map(Self::from)
    }
}

pub fn parse_flat_command_payload<R, T>(
    command: tauri::ipc::CommandItem<'_, R>,
) -> Result<T, tauri::ipc::InvokeError>
where
    R: tauri::Runtime,
    T: DeserializeOwned,
{
    let payload = match command.message.payload() {
        tauri::ipc::InvokeBody::Json(value) => value.clone(),
        tauri::ipc::InvokeBody::Raw(_) => {
            let error = serde_json::Error::custom(format!(
                "command {} expected a JSON object payload",
                command.name
            ));
            return Err(tauri::Error::InvalidArgs(command.name, command.key, error).into());
        }
    };
    serde_json::from_value(payload)
        .map_err(|error| tauri::Error::InvalidArgs(command.name, command.key, error).into())
}

#[derive(Debug, Deserialize, Serialize)]
struct MaskingOptions {
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
}

#[derive(Debug, Deserialize, Serialize)]
struct MaskingResult {
    extracted_path: Option<String>,
    masked_path: Option<String>,
    report_path: Option<String>,
    report: serde_json::Value,
    #[serde(default)]
    runtime_manifest: serde_json::Value,
    extracted_text: String,
    masked_text: String,
}

fn normalize_masking_profile(profile: &str) -> Result<&'static str, String> {
    masking_run_session::canonical_profile(profile)
        .map_err(|_| "MASKING_OPTIONS_REJECTED: 지원하지 않는 문서 유형입니다.".to_string())
}

fn validate_masking_options(opts: &MaskingOptions) -> Result<(), String> {
    let options = serde_json::to_value(opts)
        .map_err(|_| "MASKING_OPTIONS_REJECTED: 옵션을 확인할 수 없습니다.".to_string())?;
    masking_run_session::canonical_public_options(options, &opts.profile)
        .map(|_| ())
        .map_err(|_| "MASKING_OPTIONS_REJECTED: 안전하지 않은 옵션입니다.".to_string())
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct CanvasWindowLaunchPayload {
    target_path: String,
    original_path: String,
    output_dir: String,
    report_path: String,
    mode: String,
    saved_at: u64,
}

#[derive(Default)]
struct CanvasLaunchRegistry {
    launches: Mutex<HashMap<String, CanvasWindowLaunchPayload>>,
}

fn register_result_paths(
    access: &AllowedFileAccess,
    result: &MaskingResult,
    requested_policy: &str,
) {
    for path in [&result.extracted_path, &result.report_path]
        .into_iter()
        .flatten()
        .filter(|path| !path.trim().is_empty())
    {
        let path_buf = PathBuf::from(path.trim());
        if let Some(parent) = path_buf.parent() {
            access.allow_artifact_dir(parent);
        }
        access.allow_disposable_artifact_path(&path_buf);
    }
    if let Some(report_path) = result
        .report_path
        .as_deref()
        .filter(|path| !path.trim().is_empty())
    {
        access.allow_report_artifact_path(Path::new(report_path.trim()));
    }
    let report_policy = result
        .report
        .get("text_deidentification")
        .and_then(|value| value.get("policy"))
        .and_then(serde_json::Value::as_str);
    let runtime_outputs = result
        .runtime_manifest
        .get("outputs")
        .or_else(|| result.report.get("outputs"));
    let preview_path = runtime_outputs
        .and_then(|outputs| {
            outputs
                .get("preview_pdf_source_file")
                .or_else(|| outputs.get("masked_pdf_file"))
        })
        .and_then(serde_json::Value::as_str)
        .filter(|path| !path.trim().is_empty());
    if report_policy == Some(requested_policy) {
        if let (Some(masked_path), Some(preview_path)) =
            (result.masked_path.as_deref(), preview_path)
        {
            access.allow_masked_text_artifact_path(
                Path::new(masked_path.trim()),
                Path::new(preview_path.trim()),
                requested_policy,
            );
        }
    }
    for key in [
        "preview_pdf_source_file",
        "masked_pdf_file",
        "labeled_pdf_file",
    ] {
        if let Some(path) = runtime_outputs
            .and_then(|outputs| outputs.get(key))
            .and_then(serde_json::Value::as_str)
            .filter(|path| !path.trim().is_empty())
        {
            access.allow_disposable_artifact_path(Path::new(path.trim()));
        }
    }
}

fn register_copied_outputs(access: &AllowedFileAccess, result: &FinalizeResult) {
    let final_path = PathBuf::from(&result.final_output_file);
    if let Some(parent) = final_path.parent() {
        access.allow_artifact_dir(parent);
    }
    access.allow_final_output_path(&final_path);
    for copied in &result.copied_files {
        let copied_path = PathBuf::from(copied);
        if let Some(parent) = copied_path.parent() {
            access.allow_artifact_dir(parent);
        }
        access.allow_final_output_path(&copied_path);
    }
}
pub(crate) fn register_native_save_target_core(
    access: &AllowedFileAccess,
    path: &Path,
    binding: NativeSaveTargetBinding,
) -> Result<NativeSaveTargetRegistration, String> {
    access.register_native_save_target(path, binding)
}

#[tauri::command]
fn pick_input_pdf(access: tauri::State<'_, AllowedFileAccess>) -> Option<String> {
    FileDialog::new()
        .add_filter("PDF", &["pdf"])
        .pick_file()
        .map(|p| {
            access.allow_pdf_path(&p);
            p.display().to_string()
        })
}
#[tauri::command]
fn choose_final_pdf_path(
    qa_drive: tauri::State<'_, QaDriveEnabled>,
    access: tauri::State<'_, AllowedFileAccess>,
    request: ChooseFinalPdfPathRequest,
) -> Result<Option<FinalPdfSaveTarget>, String> {
    let binding = request.binding()?;
    if qa_drive.0 {
        if let Some(configured_path) = std::env::var_os("MASK_TOOL_QA_FINAL_OUTPUT_PATH") {
            let configured_path = PathBuf::from(configured_path);
            let parent = configured_path
                .parent()
                .filter(|value| !value.as_os_str().is_empty())
                .ok_or_else(|| "QA_DRIVE_OUTPUT_INVALID".to_string())?;
            let canonical_parent = canonicalize_existing_dir(parent)
                .map_err(|_| "QA_DRIVE_OUTPUT_INVALID".to_string())?;
            let allowed = std::env::var_os("MASK_TOOL_ALLOWED_DIRS")
                .map(|value| std::env::split_paths(&value).collect::<Vec<_>>())
                .unwrap_or_default()
                .into_iter()
                .filter_map(|root| canonicalize_existing_dir(&root).ok())
                .any(|root| canonical_parent.starts_with(root));
            if !allowed {
                return Err("QA_DRIVE_OUTPUT_OUTSIDE_ALLOWED_DIRS".to_string());
            }
            let target = register_native_save_target_core(&*access, &configured_path, binding)?;
            return Ok(Some(FinalPdfSaveTarget {
                output_path: target.output_path.display().to_string(),
                save_token: target.save_token,
            }));
        }
    }
    access.clear_native_save_target()?;
    let default_file_name = request
        .default_file_name
        .trim()
        .split(['/', '\\'])
        .next_back()
        .filter(|name| !name.is_empty())
        .unwrap_or("masked");
    let Some(path) = FileDialog::new()
        .add_filter("PDF", &["pdf"])
        .set_file_name(default_file_name)
        .save_file()
    else {
        return Ok(None);
    };
    let target = register_native_save_target_core(&access, &path, binding)?;
    Ok(Some(FinalPdfSaveTarget {
        output_path: target.output_path.display().to_string(),
        save_token: target.save_token,
    }))
}

#[tauri::command]
fn pick_input_document(access: tauri::State<'_, AllowedFileAccess>) -> Option<String> {
    FileDialog::new()
        .add_filter("PDF", &["pdf"])
        .pick_file()
        .map(|p| {
            access.allow_document_path(&p);
            p.display().to_string()
        })
}

#[tauri::command]
fn qa_register_input_document(
    qa_drive: tauri::State<'_, QaDriveEnabled>,
    access: tauri::State<'_, AllowedFileAccess>,
    path: String,
) -> Result<String, String> {
    if !qa_drive.0 {
        return Err("QA_DRIVE_DISABLED".to_string());
    }
    let path = qa_drive::allowed_document_path(&path)?;
    access.allow_document_path(&path);
    Ok(path.display().to_string())
}

#[tauri::command]
fn qa_stat_final_output(
    qa_drive: tauri::State<'_, QaDriveEnabled>,
    path: String,
) -> Result<QaFileStat, String> {
    if !qa_drive.0 {
        return Err("QA_DRIVE_DISABLED".to_string());
    }
    let path = qa_drive::allowed_document_path(&path)
        .map_err(|_| "QA_DRIVE_FINAL_OUTPUT_STAT_FAILED".to_string())?;
    let metadata =
        std::fs::metadata(path).map_err(|_| "QA_DRIVE_FINAL_OUTPUT_STAT_FAILED".to_string())?;
    if !metadata.is_file() || metadata.len() == 0 {
        return Err("QA_DRIVE_FINAL_OUTPUT_STAT_FAILED".to_string());
    }
    Ok(QaFileStat {
        exists: true,
        size: metadata.len(),
    })
}

#[tauri::command]
fn qa_drive_response(
    qa_drive: tauri::State<'_, QaDriveEnabled>,
    bridge: tauri::State<'_, qa_drive::Bridge>,
    response: qa_drive::Response,
) -> Result<(), String> {
    if !qa_drive.0 {
        return Err("QA_DRIVE_DISABLED".to_string());
    }
    bridge.receive(response)
}

#[tauri::command]
fn pick_input_documents(access: tauri::State<'_, AllowedFileAccess>) -> Option<Vec<String>> {
    FileDialog::new()
        .add_filter("PDF", &["pdf"])
        .pick_files()
        .map(|paths| {
            paths
                .into_iter()
                .map(|p| {
                    access.allow_document_path(&p);
                    p.display().to_string()
                })
                .collect()
        })
}

#[tauri::command]
fn pick_output_dir(access: tauri::State<'_, AllowedFileAccess>) -> Option<String> {
    FileDialog::new().pick_folder().map(|p| {
        access.allow_artifact_dir(&p);
        p.display().to_string()
    })
}

fn default_output_dir_for_document_path(
    access: &AllowedFileAccess,
    document_path: &str,
    original_file: Option<&str>,
) -> Result<PathBuf, String> {
    if let Some(original_file) = original_file
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        let original_path = Path::new(original_file);
        if original_path.is_absolute() || original_path.parent().is_some() {
            return Err(
                "원본 파일 덮어쓰기용 경로는 기본 출력 폴더로 사용할 수 없습니다.".to_string(),
            );
        }
    }

    let canonical_document = canonicalize_registered_document(access, document_path, "입력 PDF")?;
    let requested_parent = Path::new(document_path.trim())
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .map(Path::to_path_buf);
    let parent_dir = match requested_parent {
        Some(parent) => canonicalize_existing_dir(&parent)?,
        None => canonical_document
            .parent()
            .map(Path::to_path_buf)
            .ok_or_else(|| "입력 PDF의 상위 폴더를 확인할 수 없습니다.".to_string())?,
    };
    access.allow_artifact_dir(&parent_dir);
    if !access.artifact_is_allowed(&parent_dir) {
        return Err("기본 출력 폴더 등록에 실패했습니다.".to_string());
    }
    Ok(parent_dir)
}

#[tauri::command]
fn default_output_dir_for_document(
    access: tauri::State<'_, AllowedFileAccess>,
    document_path: String,
    original_file: Option<String>,
) -> Result<String, String> {
    default_output_dir_for_document_path(&access, &document_path, original_file.as_deref())
        .map(|path| path.display().to_string())
}

#[tauri::command]
fn read_pdf_bytes(
    access: tauri::State<'_, AllowedFileAccess>,
    path: String,
) -> Result<Vec<u8>, String> {
    if path.trim().is_empty() {
        return Err("PDF 경로가 비어 있습니다.".to_string());
    }
    let canonical = canonicalize_existing_file(Path::new(path.trim()), "PDF 파일")?;
    if !has_extension(&canonical, &["pdf"]) {
        return Err("PDF 파일만 미리보기로 읽을 수 있습니다.".to_string());
    }
    if !access.pdf_is_allowed(&canonical) {
        return Err("파일 선택기 또는 마스킹 결과로 등록된 PDF만 읽을 수 있습니다.".to_string());
    }
    let meta = std::fs::metadata(&canonical).map_err(|e| format!("PDF 파일 확인 실패: {e}"))?;
    if meta.len() > 100 * 1024 * 1024 {
        return Err("PDF 파일이 미리보기 제한보다 큽니다.".to_string());
    }
    std::fs::read(&canonical).map_err(|e| format!("PDF 파일 읽기 실패: {e}"))
}
fn configure_mask_tool_allowed_dirs(
    command: &mut std::process::Command,
    roots: &[&Path],
) -> Result<(), String> {
    let canonical_roots = roots
        .iter()
        .map(|root| canonicalize_existing_dir(root))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| "MASKING_SESSION_PATH_CAPABILITY_INVALID".to_string())?;
    let joined = std::env::join_paths(canonical_roots)
        .map_err(|_| "MASKING_SESSION_PATH_CAPABILITY_INVALID".to_string())?;
    command.env("MASK_TOOL_ALLOWED_DIRS", joined);
    Ok(())
}

fn configure_direct_pipeline_allowed_dirs(
    command: &mut std::process::Command,
    input_path: &Path,
    output_dir: &Path,
) -> Result<(), String> {
    let input_dir = input_path
        .parent()
        .ok_or_else(|| "MASKING_SESSION_PATH_CAPABILITY_INVALID".to_string())?;
    configure_mask_tool_allowed_dirs(command, &[input_dir, output_dir])
}

fn stable_pipeline_failure(stderr: &[u8]) -> Option<String> {
    let payload: serde_json::Value = serde_json::from_slice(stderr).ok()?;
    if payload.get("event").and_then(serde_json::Value::as_str) != Some("pipeline_failure")
        || payload
            .get("schemaVersion")
            .and_then(serde_json::Value::as_u64)
            != Some(1)
        || payload
            .get("rawTextReturned")
            .and_then(serde_json::Value::as_bool)
            != Some(false)
    {
        return None;
    }
    let code = payload
        .pointer("/error/code")
        .and_then(serde_json::Value::as_str)?;
    if !code.starts_with("MASKING_PIPELINE_")
        || !code
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
    {
        return None;
    }
    Some(code.to_string())
}

fn safe_pipeline_diagnostic_token(value: Option<&serde_json::Value>) -> Option<&str> {
    let value = value?.as_str()?;
    let bytes = value.as_bytes();
    if bytes.is_empty()
        || bytes.len() > 64
        || !bytes[0].is_ascii_lowercase()
        || !bytes[1..]
            .iter()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_')
    {
        return None;
    }
    Some(value)
}

fn safe_pipeline_diagnostic_hash(value: Option<&serde_json::Value>) -> Option<&str> {
    let value = value?.as_str()?;
    if value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        Some(value)
    } else {
        None
    }
}

fn safe_pipeline_diagnostic_occurrence_id(value: Option<&serde_json::Value>) -> Option<&str> {
    let value = value?.as_str()?;
    if value.len() == 28
        && value.starts_with("occ_")
        && value[4..].bytes().all(|byte| byte.is_ascii_hexdigit())
    {
        Some(value)
    } else {
        None
    }
}

fn safe_pipeline_diagnostic_page(value: Option<&serde_json::Value>) -> Option<u64> {
    value?.as_u64().filter(|page| *page <= 2_000)
}

fn stable_pipeline_failure_detail(stderr: &[u8], code: &str) -> String {
    let Ok(payload) = serde_json::from_slice::<serde_json::Value>(stderr) else {
        return code.to_string();
    };
    let Some(diagnostics) = payload
        .pointer("/error/diagnostics")
        .and_then(serde_json::Value::as_array)
    else {
        return code.to_string();
    };
    if diagnostics.is_empty() || diagnostics.len() > 16 {
        return code.to_string();
    }
    let mut safe_diagnostics: Vec<serde_json::Value> = diagnostics
        .iter()
        .filter_map(|diagnostic| {
            let object = diagnostic.as_object()?;
            let allowed = [
                "kind",
                "reason_code",
                "count",
                "occurrence_id",
                "category",
                "page",
                "rect_fingerprint",
                "expected_text_hash",
                "observed_text_hash",
            ];
            if object.keys().any(|key| !allowed.contains(&key.as_str())) {
                return None;
            }
            let kind = safe_pipeline_diagnostic_token(object.get("kind"))?;
            let reason_code = safe_pipeline_diagnostic_token(object.get("reason_code"))?;
            let count = object.get("count")?.as_u64()?;
            if count == 0 || count > 10_000 {
                return None;
            }
            let mut safe = serde_json::Map::from_iter([
                (
                    "kind".to_string(),
                    serde_json::Value::String(kind.to_string()),
                ),
                (
                    "reason_code".to_string(),
                    serde_json::Value::String(reason_code.to_string()),
                ),
                ("count".to_string(), serde_json::json!(count)),
            ]);
            if object.get("occurrence_id").is_some() {
                safe.insert(
                    "occurrence_id".to_string(),
                    serde_json::Value::String(
                        safe_pipeline_diagnostic_occurrence_id(object.get("occurrence_id"))?
                            .to_string(),
                    ),
                );
            }
            if object.get("category").is_some() {
                safe.insert(
                    "category".to_string(),
                    serde_json::Value::String(
                        safe_pipeline_diagnostic_token(object.get("category"))?.to_string(),
                    ),
                );
            }
            if object.get("page").is_some() {
                safe.insert(
                    "page".to_string(),
                    serde_json::json!(safe_pipeline_diagnostic_page(object.get("page"))?),
                );
            }
            for field in [
                "rect_fingerprint",
                "expected_text_hash",
                "observed_text_hash",
            ] {
                if object.get(field).is_some() {
                    safe.insert(
                        field.to_string(),
                        serde_json::Value::String(
                            safe_pipeline_diagnostic_hash(object.get(field))?.to_string(),
                        ),
                    );
                }
            }
            Some(serde_json::Value::Object(safe))
        })
        .collect();
    if safe_diagnostics.len() != diagnostics.len() {
        return code.to_string();
    }
    if safe_diagnostics.len() > 4 {
        let final_diagnostic = safe_diagnostics.pop().expect("diagnostics is non-empty");
        safe_diagnostics.truncate(3);
        safe_diagnostics.push(final_diagnostic);
    }
    let Ok(serialized) = serde_json::to_string(&safe_diagnostics) else {
        return code.to_string();
    };
    let detail = format!("{code};diagnostics={serialized}");
    if detail.len() > 2048 {
        code.to_string()
    } else {
        detail
    }
}

const MASKING_DEBUG_LOG_MAX_BYTES: u64 = 1024 * 1024;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct MaskingIpcError {
    code: String,
    stage: String,
    detail: String,
}

#[derive(Debug, Clone)]
struct MaskingFailureMetadata {
    error: MaskingIpcError,
    exit_code: Option<i32>,
    stdout_bytes: usize,
    stderr_bytes: usize,
    stderr_tail: String,
}

thread_local! {
    static MASKING_FAILURE_METADATA: RefCell<Option<MaskingFailureMetadata>> = const { RefCell::new(None) };
}

fn session_guard_error(code: &str) -> MaskingIpcError {
    let stable_code = code
        .split(|character: char| !character.is_ascii_uppercase() && character != '_')
        .find(|candidate| {
            candidate.starts_with("MASKING_")
                || candidate.starts_with("PROCESS_")
                || candidate.starts_with("SAVE_")
        })
        .unwrap_or("MASKING_SESSION_REQUEST_REJECTED");
    MaskingIpcError {
        code: stable_code.to_string(),
        stage: "session_guard".to_string(),
        detail: stable_code.to_string(),
    }
}

fn remember_masking_failure(metadata: MaskingFailureMetadata) {
    MASKING_FAILURE_METADATA.with(|slot| *slot.borrow_mut() = Some(metadata));
}

fn clear_masking_failure() {
    MASKING_FAILURE_METADATA.with(|slot| *slot.borrow_mut() = None);
}

fn take_masking_failure(code: &str) -> MaskingFailureMetadata {
    MASKING_FAILURE_METADATA.with(|slot| {
        slot.borrow_mut()
            .take()
            .unwrap_or_else(|| MaskingFailureMetadata {
                error: session_guard_error(code),
                exit_code: None,
                stdout_bytes: 0,
                stderr_bytes: 0,
                stderr_tail: String::new(),
            })
    })
}

fn sanitized_stderr_tail(stderr: &[u8]) -> String {
    let tail_start = stderr.len().saturating_sub(2048);
    process_util::sanitize_stderr(&String::from_utf8_lossy(&stderr[tail_start..]))
}

fn lifecycle_spawn_failure(code: &str, error: &std::io::Error) -> MaskingIpcError {
    let stable_code = if error.kind() == std::io::ErrorKind::NotFound {
        "MASKING_SESSION_ANALYZER_UNAVAILABLE"
    } else {
        code
    };
    MaskingIpcError {
        code: stable_code.to_string(),
        stage: "spawn".to_string(),
        detail: format!("io_kind={:?}", error.kind()),
    }
}

fn lifecycle_capture_failure(
    fallback_code: &str,
    captured: &process_util::CapturedOutput,
) -> Option<MaskingIpcError> {
    if captured.timed_out {
        return Some(MaskingIpcError {
            code: fallback_code.to_string(),
            stage: "timeout".to_string(),
            detail: sanitized_stderr_tail(&captured.stderr),
        });
    }
    if captured.stdout_truncated || captured.stderr_truncated {
        return Some(MaskingIpcError {
            code: fallback_code.to_string(),
            stage: "output_parse".to_string(),
            detail: format!(
                "stdout_bytes={}; stderr_bytes={}; output_truncated",
                captured.stdout.len(),
                captured.stderr.len(),
            ),
        });
    }
    if !captured.status.success() {
        if let Some(code) = stable_pipeline_failure(&captured.stderr) {
            let detail = stable_pipeline_failure_detail(&captured.stderr, &code);
            return Some(MaskingIpcError {
                detail,
                code,
                stage: "pipeline_failure_code".to_string(),
            });
        }
        return Some(MaskingIpcError {
            code: fallback_code.to_string(),
            stage: "nonzero_exit".to_string(),
            detail: sanitized_stderr_tail(&captured.stderr),
        });
    }
    None
}

fn lifecycle_output_parse_failure(
    code: &str,
    captured: &process_util::CapturedOutput,
) -> MaskingIpcError {
    MaskingIpcError {
        code: code.to_string(),
        stage: "output_parse".to_string(),
        detail: process_util::summarize_parse_failure(&String::from_utf8_lossy(&captured.stdout)),
    }
}

fn remember_lifecycle_failure(
    error: MaskingIpcError,
    captured: Option<&process_util::CapturedOutput>,
) {
    let metadata = match captured {
        None => MaskingFailureMetadata {
            error,
            exit_code: None,
            stdout_bytes: 0,
            stderr_bytes: 0,
            stderr_tail: String::new(),
        },
        Some(captured) => MaskingFailureMetadata {
            stderr_tail: sanitized_stderr_tail(&captured.stderr),
            exit_code: captured.status.code(),
            stdout_bytes: captured.stdout.len(),
            stderr_bytes: captured.stderr.len(),
            error,
        },
    };
    remember_masking_failure(metadata);
}

fn append_masking_debug_log(app: &tauri::AppHandle, metadata: &MaskingFailureMetadata) {
    let Ok(app_data_dir) = app.path().app_data_dir() else {
        return;
    };
    if std::fs::create_dir_all(&app_data_dir).is_err() {
        return;
    }
    let path = app_data_dir.join("masking-debug.log");
    if std::fs::metadata(&path)
        .map(|metadata| metadata.len() >= MASKING_DEBUG_LOG_MAX_BYTES)
        .unwrap_or(false)
        && std::fs::File::create(&path).is_err()
    {
        return;
    }
    let record = serde_json::json!({
        "code": metadata.error.code,
        "stage": metadata.error.stage,
        "detail": metadata.error.detail,
        "exitCode": metadata.exit_code,
        "stdoutBytes": metadata.stdout_bytes,
        "stderrBytes": metadata.stderr_bytes,
        "stderrTail": metadata.stderr_tail,
    });
    let Ok(line) = serde_json::to_string(&record) else {
        return;
    };
    use std::io::Write as _;
    let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    else {
        return;
    };
    let _ = writeln!(file, "{line}");
}
fn create_private_staging_dir(path: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        let mut builder = std::fs::DirBuilder::new();
        builder.mode(0o700);
        builder.create(path)
    }
    #[cfg(not(unix))]
    {
        std::fs::create_dir(path)
    }
}

fn build_analyzer_payload(
    input_path: &Path,
    options: serde_json::Value,
    analysis_revision: u64,
) -> Result<serde_json::Value, String> {
    if analysis_revision == 0 {
        return Err("MASKING_SESSION_REANALYSIS_FAILED".to_string());
    }
    let mut options = options
        .as_object()
        .cloned()
        .ok_or_else(|| "MASKING_SESSION_OPTIONS_INVALID".to_string())?;
    // `analysis_revision` is an internal analyzer input. It is deliberately
    // added after canonical public-option validation so the engine cannot fall
    // back to revision 1 when a public session is being reanalyzed.
    options.insert(
        "analysis_revision".to_string(),
        serde_json::json!(analysis_revision),
    );
    Ok(serde_json::json!({
        "input": input_path,
        "options": serde_json::Value::Object(options),
    }))
}

pub(crate) fn analyze_masking_run_core(
    app: &tauri::AppHandle,
    access: &AllowedFileAccess,
    sessions: &MaskingRunSessions,
    request: AnalyzeMaskingRunRequest,
) -> Result<AnalysisManifestV1, String> {
    if request.input_file.trim().is_empty() {
        return Err("MASKING_SESSION_INVALID_INPUT".to_string());
    }
    let input_path = canonicalize_registered_document(access, &request.input_file, "입력 파일")
        .map_err(|_| "MASKING_SESSION_INPUT_ACCESS_DENIED".to_string())?;
    let input_size = std::fs::metadata(&input_path)
        .map_err(|_| "MASKING_SESSION_INPUT_READ_FAILED".to_string())?
        .len();
    if input_size > 100 * 1024 * 1024 {
        return Err("MASKING_SESSION_INPUT_TOO_LARGE".to_string());
    }
    let original =
        std::fs::read(&input_path).map_err(|_| "MASKING_SESSION_INPUT_READ_FAILED".to_string())?;
    let runtime = resolve_lifecycle_runtime_paths(app)
        .map_err(|_| "MASKING_SESSION_ANALYZER_UNAVAILABLE".to_string())?;
    let options = masking_run_session::canonical_public_options(request.options, &request.profile)
        .map_err(|_| "MASKING_SESSION_OPTIONS_INVALID".to_string())?;
    let reanalysis_context = request
        .reanalysis
        .as_ref()
        .map(|reanalysis| {
            sessions
                .analysis_reanalysis_context(reanalysis, &input_path, &original, &request.profile)
                .map_err(|_| "MASKING_SESSION_REANALYSIS_UNAVAILABLE".to_string())
        })
        .transpose()?;
    let analysis_revision = reanalysis_context
        .as_ref()
        .map(|context| {
            context
                .analysis_revision
                .checked_add(1)
                .ok_or_else(|| "MASKING_SESSION_REANALYSIS_FAILED".to_string())
        })
        .transpose()?
        .unwrap_or(1);
    let analyzer_options = masking_run_session::with_server_profile_authority(
        options.clone(),
        &masking_run_session::document_hash(&original),
        analysis_revision,
    )
    .map_err(|_| "MASKING_SESSION_OPTIONS_INVALID".to_string())?;
    if let Some(reanalysis) = request.reanalysis.as_ref() {
        if reanalysis.analysis_revision.checked_add(1) != Some(analysis_revision) {
            return Err("MASKING_SESSION_REANALYSIS_UNAVAILABLE".to_string());
        }
    }
    let analyzer_payload =
        build_analyzer_payload(&input_path, analyzer_options, analysis_revision)?;
    let analyzer_request = serde_json::to_vec(&analyzer_payload)
        .map_err(|_| "MASKING_SESSION_OPTIONS_INVALID".to_string())?;
    let mut command = runtime_paths::lifecycle_command(&runtime, "analyze")?;
    command.arg("--request-stdin");
    let mut session_hash_key = [0_u8; 32];
    getrandom::getrandom(&mut session_hash_key)
        .map_err(|_| "MASKING_SESSION_ANALYZER_UNAVAILABLE".to_string())?;
    let session_hash_key_hex = session_hash_key
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    command.env("MASKING_SESSION_HASH_KEY_HEX", session_hash_key_hex);
    configure_mask_tool_allowed_dirs(
        &mut command,
        &[input_path
            .parent()
            .ok_or_else(|| "MASKING_SESSION_INPUT_ACCESS_DENIED".to_string())?],
    )?;
    let captured = process_util::run_capturing_with_timeout_and_stdin(
        command,
        process_util::SINGLE_RUN_TIMEOUT,
        analyzer_request,
    )
    .map_err(|error| {
        let failure = lifecycle_spawn_failure("MASKING_SESSION_ANALYZER_FAILED", &error);
        remember_lifecycle_failure(failure, None);
        "MASKING_SESSION_ANALYZER_FAILED".to_string()
    })?;
    if let Some(failure) = lifecycle_capture_failure("MASKING_SESSION_ANALYZER_FAILED", &captured) {
        remember_lifecycle_failure(failure, Some(&captured));
        return Err("MASKING_SESSION_ANALYZER_FAILED".to_string());
    }
    let payload: serde_json::Value = serde_json::from_slice(&captured.stdout).map_err(|_| {
        let failure = lifecycle_output_parse_failure("MASKING_SESSION_ANALYZER_INVALID", &captured);
        remember_lifecycle_failure(failure, Some(&captured));
        "MASKING_SESSION_ANALYZER_INVALID".to_string()
    })?;
    let trusted = payload.get("analysis_manifest").ok_or_else(|| {
        let failure = lifecycle_output_parse_failure("MASKING_SESSION_ANALYZER_INVALID", &captured);
        remember_lifecycle_failure(failure, Some(&captured));
        "MASKING_SESSION_ANALYZER_INVALID".to_string()
    })?;
    let manifest = match request.reanalysis {
        Some(reanalysis) => sessions.replace_from_trusted_analysis(
            reanalysis,
            &original,
            &request.profile,
            options,
            trusted,
        )?,
        None => {
            let manifest = sessions.create_from_trusted(
                &original,
                &request.profile,
                options.clone(),
                trusted,
            )?;
            sessions.bind_private_context(&manifest.run_id, input_path, options)?;
            manifest
        }
    };
    Ok(manifest)
}
#[tauri::command]
fn analyze_masking_run(
    app: tauri::AppHandle,
    access: tauri::State<'_, AllowedFileAccess>,
    sessions: tauri::State<'_, MaskingRunSessions>,
    request: AnalyzeMaskingRunRequest,
) -> Result<AnalysisManifestV1, MaskingIpcError> {
    clear_masking_failure();
    analyze_masking_run_core(&app, &access, &sessions, request).map_err(|code| {
        let metadata = take_masking_failure(&code);
        append_masking_debug_log(&app, &metadata);
        metadata.error
    })
}

fn reanalysis_profile_authority_revision(
    resolution: &masking_run_session::ReviewResolution,
    current_revision: u64,
    successor_revision: u64,
) -> Option<u64> {
    match resolution {
        masking_run_session::ReviewResolution::Boundary { .. } => Some(current_revision),
        masking_run_session::ReviewResolution::Ocr { accepted: true } => Some(successor_revision),
        _ => None,
    }
}

pub(crate) fn resolve_masking_review_core(
    app: &tauri::AppHandle,
    sessions: &MaskingRunSessions,
    request: ResolveMaskingReviewRequest,
) -> Result<AnalysisManifestV1, String> {
    let requires_reanalysis = matches!(
        &request.resolution,
        masking_run_session::ReviewResolution::Boundary { .. }
            | masking_run_session::ReviewResolution::Ocr { accepted: true }
    );
    if !requires_reanalysis {
        return sessions.resolve(request);
    }
    let context = sessions
        .reanalysis_context(&request)
        .map_err(|_| "MASKING_SESSION_REANALYSIS_UNAVAILABLE".to_string())?;
    let original = std::fs::read(&context.original)
        .map_err(|_| "MASKING_SESSION_REANALYSIS_INPUT_READ_FAILED".to_string())?;
    let revision = request
        .analysis_revision
        .checked_add(1)
        .ok_or_else(|| "MASKING_SESSION_REANALYSIS_FAILED".to_string())?;
    let authority_revision = reanalysis_profile_authority_revision(
        &request.resolution,
        request.analysis_revision,
        revision,
    )
    .ok_or_else(|| "MASKING_SESSION_REANALYSIS_REQUIRED".to_string())?;
    let analyzer_options = masking_run_session::with_server_profile_authority(
        context.options.clone(),
        &context.original_document_hash,
        authority_revision,
    )
    .map_err(|_| "MASKING_SESSION_REANALYSIS_FAILED".to_string())?;
    let reanalysis = match &request.resolution {
        masking_run_session::ReviewResolution::Boundary {
            page_start,
            page_end,
            segment_kind,
        } => {
            serde_json::json!({
                "kind": "boundary", "page_start": page_start, "page_end": page_end,
                "segment_kind": segment_kind, "analysis_revision": revision,
            })
        }
        masking_run_session::ReviewResolution::Ocr { accepted: true } => serde_json::json!({
            "kind": "ocr", "page_start": 0, "page_end": 0, "analysis_revision": revision,
        }),
        _ => return Err("MASKING_SESSION_REANALYSIS_REQUIRED".to_string()),
    };
    let runtime = resolve_lifecycle_runtime_paths(app)
        .map_err(|_| "MASKING_SESSION_ANALYZER_UNAVAILABLE".to_string())?;
    let analyzer_request = serde_json::to_vec(&serde_json::json!({
        "input": context.original,
        "options": analyzer_options,
        "reanalysis": reanalysis,
    }))
    .map_err(|_| "MASKING_SESSION_REANALYSIS_FAILED".to_string())?;
    let mut command = runtime_paths::lifecycle_command(&runtime, "analyze")?;
    command.arg("--request-stdin");
    configure_mask_tool_allowed_dirs(
        &mut command,
        &[context
            .original
            .parent()
            .ok_or_else(|| "MASKING_SESSION_REANALYSIS_INPUT_READ_FAILED".to_string())?],
    )?;
    let captured = process_util::run_capturing_with_timeout_and_stdin(
        command,
        process_util::SINGLE_RUN_TIMEOUT,
        analyzer_request,
    )
    .map_err(|error| {
        let failure = lifecycle_spawn_failure("MASKING_SESSION_ANALYZER_FAILED", &error);
        remember_lifecycle_failure(failure, None);
        "MASKING_SESSION_ANALYZER_FAILED".to_string()
    })?;
    if let Some(failure) = lifecycle_capture_failure("MASKING_SESSION_ANALYZER_FAILED", &captured) {
        remember_lifecycle_failure(failure, Some(&captured));
        return Err("MASKING_SESSION_ANALYZER_FAILED".to_string());
    }
    let payload: serde_json::Value = serde_json::from_slice(&captured.stdout).map_err(|_| {
        let failure = lifecycle_output_parse_failure("MASKING_SESSION_ANALYZER_INVALID", &captured);
        remember_lifecycle_failure(failure, Some(&captured));
        "MASKING_SESSION_ANALYZER_INVALID".to_string()
    })?;
    let trusted = payload.get("analysis_manifest").ok_or_else(|| {
        let failure = lifecycle_output_parse_failure("MASKING_SESSION_ANALYZER_INVALID", &captured);
        remember_lifecycle_failure(failure, Some(&captured));
        "MASKING_SESSION_ANALYZER_INVALID".to_string()
    })?;
    sessions
        .replace_from_trusted_reanalysis(request, &original, trusted)
        .map_err(|_| "MASKING_SESSION_REANALYSIS_REJECTED".to_string())
}
#[tauri::command]
fn resolve_masking_review(
    app: tauri::AppHandle,
    sessions: tauri::State<'_, MaskingRunSessions>,
    request: ResolveMaskingReviewRequest,
) -> Result<AnalysisManifestV1, MaskingIpcError> {
    clear_masking_failure();
    resolve_masking_review_core(&app, &sessions, request).map_err(|code| {
        let metadata = take_masking_failure(&code);
        append_masking_debug_log(&app, &metadata);
        metadata.error
    })
}
#[tauri::command]
fn apply_manual_action_v1(
    sessions: tauri::State<'_, MaskingRunSessions>,
    request: ManualActionV1Request,
) -> Result<AnalysisManifestV1, String> {
    sessions.apply_manual_action(request)
}

#[tauri::command]
fn issue_restore_capability(
    sessions: tauri::State<'_, MaskingRunSessions>,
    request: RestoreCapabilityRequest,
) -> Result<masking_run_session::RestoreCapabilityResponse, String> {
    sessions.issue_restore_capability(request, "native_trusted_ui")
}

#[tauri::command]
fn get_masking_run_state(
    sessions: tauri::State<'_, MaskingRunSessions>,
    run_id: String,
) -> Result<AnalysisManifestV1, String> {
    sessions.get(run_id.trim())
}
#[derive(Clone)]
struct FinalizePublication {
    final_path: String,
    verified_staging_hash: String,
}

fn precommit_finalize_failure(primary: String, cleanup: Result<(), String>) -> String {
    match cleanup {
        Ok(()) => format!("MASKING_SESSION_PRECOMMIT_RETRYABLE;cause={primary}"),
        Err(cleanup) => {
            format!("MASKING_SESSION_PRECOMMIT_CLEANUP_REQUIRED;cause={primary};cleanup={cleanup}")
        }
    }
}

fn published_finalize_failure(
    _publication: &FinalizePublication,
    primary: String,
    _cleanup: Result<(), String>,
) -> String {
    // Publication errors are public IPC values. Do not expose destination
    // paths, hashes, or underlying filesystem failures across that boundary.
    if primary == "MASKING_SESSION_PROMOTION_RESTORE_FAILED" {
        "MASKING_SESSION_PUBLISHED_RESTORE_FAILED".to_string()
    } else {
        "MASKING_SESSION_PUBLISHED_POSTCOMMIT".to_string()
    }
}

fn finalize_io_outcome(
    committed: bool,
    publication: Option<&FinalizePublication>,
    result: Result<FinalizeMaskingRunResult, String>,
    cleanup: Result<(), String>,
) -> Result<FinalizeMaskingRunResult, String> {
    match (committed, result, cleanup) {
        (false, Err(primary), cleanup) => Err(precommit_finalize_failure(primary, cleanup)),
        (true, Ok(result), Ok(())) => Ok(result),
        (true, Ok(_), Err(cleanup)) => Err(published_finalize_failure(
            publication.expect("publication evidence exists after commit"),
            "MASKING_SESSION_CLEANUP_FAILED".to_string(),
            Err(cleanup),
        )),
        (true, Err(primary), cleanup) => Err(published_finalize_failure(
            publication.expect("publication evidence exists after commit"),
            primary,
            cleanup,
        )),
        (false, Ok(_), _) => Err("MASKING_SESSION_FINALIZE_STATE_INVALID".to_string()),
    }
}
fn verified_final_hash(expected_hash: &str, output: &[u8]) -> Result<String, String> {
    let final_hash = masking_run_session::document_hash(output);
    if final_hash != expected_hash {
        return Err("MASKING_SESSION_PROMOTION_FAILED".to_string());
    }
    Ok(final_hash)
}

fn open_regular_staging_file(path: &Path) -> Result<std::fs::File, String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        #[cfg(target_os = "macos")]
        const O_NOFOLLOW: i32 = 0x100;
        #[cfg(not(target_os = "macos"))]
        const O_NOFOLLOW: i32 = 0x20000;
        let file = std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(O_NOFOLLOW)
            .open(path)
            .map_err(|_| "MASKING_SESSION_FINALIZE_UNVERIFIED".to_string())?;
        if !file
            .metadata()
            .map_err(|_| "MASKING_SESSION_FINALIZE_UNVERIFIED".to_string())?
            .is_file()
        {
            return Err("MASKING_SESSION_FINALIZE_UNVERIFIED".to_string());
        }
        Ok(file)
    }
    #[cfg(not(unix))]
    {
        let metadata = std::fs::symlink_metadata(path)
            .map_err(|_| "MASKING_SESSION_FINALIZE_UNVERIFIED".to_string())?;
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return Err("MASKING_SESSION_FINALIZE_UNVERIFIED".to_string());
        }
        std::fs::File::open(path).map_err(|_| "MASKING_SESSION_FINALIZE_UNVERIFIED".to_string())
    }
}

#[derive(Debug, Clone, Copy)]
struct ExpectedFinalizeCounts {
    effective_mask_count: usize,
    manual_mask_count: usize,
    restore_count: usize,
}

fn expected_finalized_counts(
    manifest: &AnalysisManifestV1,
) -> Result<ExpectedFinalizeCounts, String> {
    let effective_excluded_occurrence_ids: HashSet<&str> = manifest
        .occurrences
        .iter()
        .filter(|occurrence| {
            manifest.manual_actions.iter().any(|action| {
                action.linked_occurrence_id.as_deref() == Some(occurrence.occurrence_id.as_str())
                    || (action.page == occurrence.page
                        && action.protected_neighbor_refs == occurrence.rects)
            })
        })
        .map(|occurrence| occurrence.occurrence_id.as_str())
        .collect();
    let automatic_mask_count = manifest
        .occurrences
        .iter()
        .filter(|occurrence| {
            occurrence.proposed_action == "mask"
                && matches!(occurrence.state.as_str(), "confirmed" | "user_confirmed")
                && !effective_excluded_occurrence_ids.contains(occurrence.occurrence_id.as_str())
        })
        .count();
    let manual_mask_count = manifest
        .manual_actions
        .iter()
        .filter(|action| action.mode == "mask")
        .count();
    let restore_count = manifest
        .manual_actions
        .iter()
        .filter(|action| action.mode == "restore")
        .count();
    Ok(ExpectedFinalizeCounts {
        effective_mask_count: automatic_mask_count
            .checked_add(manual_mask_count)
            .ok_or_else(|| "MASKING_SESSION_FINALIZE_UNVERIFIED".to_string())?,
        manual_mask_count,
        restore_count,
    })
}

fn expected_finalized_mask_count(manifest: &AnalysisManifestV1) -> Result<usize, String> {
    Ok(expected_finalized_counts(manifest)?.effective_mask_count)
}

pub(crate) fn finalize_masking_run_core(
    app: &tauri::AppHandle,
    access: &AllowedFileAccess,
    sessions: &MaskingRunSessions,
    request: FinalizeMaskingRunRequest,
) -> Result<FinalizeMaskingRunResult, String> {
    let (manifest, original, options) = sessions.finalize_context(
        &request.run_id,
        request.analysis_revision,
        &request.manifest_hash,
        request.warnings_confirmed,
    )?;
    let restore_authorization = sessions.restore_authorization_summary(&manifest)?;
    let expected_counts = expected_finalized_counts(&manifest)?;
    let preflight = (|| -> Result<_, String> {
        if manifest.policy_version != masking_run_session::POLICY_VERSION
            || manifest.options_version != masking_run_session::OPTIONS_VERSION
            || masking_run_session::canonical_json_hash(&options)
                .map_err(|_| "MASKING_SESSION_POLICY_VERSION_INVALID".to_string())?
                != manifest.options_hash
            || manifest.threshold_version != masking_run_session::THRESHOLD_VERSION
            || manifest.threshold_hash != manifest.threshold_artifact.content_hash
            || manifest.threshold_artifact.version != masking_run_session::THRESHOLD_VERSION
            || manifest.threshold_artifact.content_hash.is_empty()
            || !manifest.threshold_artifact.auto_mask_threshold.is_finite()
            || !manifest.threshold_artifact.review_threshold.is_finite()
            || manifest.threshold_artifact.review_threshold
                > manifest.threshold_artifact.auto_mask_threshold
        {
            return Err("MASKING_SESSION_POLICY_VERSION_INVALID".to_string());
        }
        let original_bytes = std::fs::read(&original)
            .map_err(|_| "MASKING_SESSION_ORIGINAL_READ_FAILED".to_string())?;
        if masking_run_session::document_hash(&original_bytes) != manifest.original_document_hash {
            return Err("MASKING_SESSION_ORIGINAL_CHANGED".to_string());
        }
        let binding = NativeSaveTargetBinding::Public {
            run_id: request.run_id.clone(),
            analysis_revision: request.analysis_revision,
            manifest_hash: request.manifest_hash.clone(),
        };
        let destination_registration = consume_registered_native_save_target(
            &access,
            &request.destination,
            &request.save_token,
            &binding,
        )
        .map_err(|_| "MASKING_SESSION_DESTINATION_REJECTED".to_string())?;
        let destination = destination_registration.output_path;
        let parent = destination
            .parent()
            .ok_or_else(|| "MASKING_SESSION_DESTINATION_REJECTED".to_string())?;
        let parent = canonicalize_existing_dir(parent)
            .map_err(|_| "MASKING_SESSION_DESTINATION_REJECTED".to_string())?;
        let cache_dir = app
            .path()
            .app_cache_dir()
            .map_err(|_| "MASKING_SESSION_STAGING_FAILED".to_string())?;
        let staging_root = cache_dir.join("authoritative_masking");
        std::fs::create_dir_all(&staging_root)
            .map_err(|_| "MASKING_SESSION_STAGING_FAILED".to_string())?;
        if std::fs::symlink_metadata(&staging_root)
            .map_err(|_| "MASKING_SESSION_STAGING_FAILED".to_string())?
            .file_type()
            .is_symlink()
        {
            return Err("MASKING_SESSION_STAGING_FAILED".to_string());
        }
        let staging = staging_root.join(&manifest.run_id);
        create_private_staging_dir(&staging).map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                "MASKING_SESSION_STAGING_COLLISION".to_string()
            } else {
                "MASKING_SESSION_STAGING_FAILED".to_string()
            }
        })?;
        Ok((
            destination,
            destination_registration.overwrite_confirmed,
            parent,
            staging,
        ))
    })();
    let (destination, overwrite_confirmed, parent, staging) = match preflight {
        Ok(values) => values,
        Err(error) => {
            sessions.finish_finalize(
                &request.run_id,
                if error == "MASKING_SESSION_STAGING_COLLISION" {
                    FinalizeDisposition::CleanupRequired
                } else {
                    FinalizeDisposition::RetryReady
                },
            )?;
            return Err(error);
        }
    };
    let staging_manifest = staging.join("immutable-manifest.json");
    let staging_output = staging.join("finalized.pdf");
    let mut committed = false;
    let mut publication = None;
    let mut audit_path: Option<PathBuf> = None;
    let save_confirmation = masking_run_session::finalization_save_confirmation(&manifest);
    let result = (|| {
        let manifest_payload = serde_json::to_vec(&manifest)
            .map_err(|_| "MASKING_SESSION_FINALIZE_INVALID".to_string())?;
        std::fs::write(&staging_manifest, manifest_payload)
            .map_err(|_| "MASKING_SESSION_STAGING_FAILED".to_string())?;
        std::fs::OpenOptions::new()
            .write(true)
            .open(&staging_manifest)
            .and_then(|file| file.sync_all())
            .map_err(|_| "MASKING_SESSION_STAGING_FAILED".to_string())?;
        let mut finalize_options = options.clone();
        let finalize_options_object = finalize_options
            .as_object_mut()
            .ok_or_else(|| "MASKING_SESSION_OPTIONS_INVALID".to_string())?;
        finalize_options_object.insert(
            "run_id".to_string(),
            serde_json::Value::String(manifest.run_id.clone()),
        );
        finalize_options_object.insert(
            "analysis_revision".to_string(),
            serde_json::json!(manifest.analysis_revision),
        );
        finalize_options_object.insert(
            "options_hash".to_string(),
            serde_json::Value::String(manifest.options_hash.clone()),
        );
        finalize_options_object.insert(
            "threshold_version".to_string(),
            serde_json::Value::String(manifest.threshold_artifact.version.clone()),
        );
        finalize_options_object.insert(
            "threshold_hash".to_string(),
            serde_json::Value::String(manifest.threshold_artifact.content_hash.clone()),
        );
        finalize_options_object.insert(
            "threshold_artifact".to_string(),
            serde_json::json!({
                "version": manifest.threshold_artifact.version,
                "content_hash": manifest.threshold_artifact.content_hash,
                "auto_mask_threshold": manifest.threshold_artifact.auto_mask_threshold,
                "review_threshold": manifest.threshold_artifact.review_threshold,
            }),
        );
        finalize_options_object.insert(
            "auto_mask_threshold".to_string(),
            serde_json::json!(manifest.threshold_artifact.auto_mask_threshold),
        );
        finalize_options_object.insert(
            "review_threshold".to_string(),
            serde_json::json!(manifest.threshold_artifact.review_threshold),
        );
        finalize_options_object.insert(
            "warnings_confirmed".to_string(),
            serde_json::json!(request.warnings_confirmed),
        );
        let runtime = resolve_lifecycle_runtime_paths(app)
            .map_err(|_| "MASKING_SESSION_ANALYZER_UNAVAILABLE".to_string())?;
        let mut command = runtime_paths::lifecycle_command(&runtime, "trusted-finalize")?;
        command.arg("--request-stdin");
        configure_mask_tool_allowed_dirs(
            &mut command,
            &[
                original
                    .parent()
                    .ok_or_else(|| "MASKING_SESSION_ORIGINAL_CHANGED".to_string())?,
                &staging,
            ],
        )?;
        let finalize_request = serde_json::to_vec(&serde_json::json!({
            "input": original,
            "original": original,
            "manifest": staging_manifest,
            "staging_output": staging_output,
            "options": finalize_options,
        }))
        .map_err(|_| "MASKING_SESSION_OPTIONS_INVALID".to_string())?;
        let captured = process_util::run_capturing_with_timeout_and_stdin(
            command,
            process_util::SINGLE_RUN_TIMEOUT,
            finalize_request,
        )
        .map_err(|error| {
            let failure = lifecycle_spawn_failure("MASKING_SESSION_FINALIZE_FAILED", &error);
            remember_lifecycle_failure(failure, None);
            "MASKING_SESSION_FINALIZE_FAILED".to_string()
        })?;
        if let Some(failure) =
            lifecycle_capture_failure("MASKING_SESSION_FINALIZE_FAILED", &captured)
        {
            remember_lifecycle_failure(failure, Some(&captured));
            return Err("MASKING_SESSION_FINALIZE_FAILED".to_string());
        }
        let value: serde_json::Value = serde_json::from_slice(&captured.stdout).map_err(|_| {
            let failure =
                lifecycle_output_parse_failure("MASKING_SESSION_FINALIZE_INVALID", &captured);
            remember_lifecycle_failure(failure, Some(&captured));
            "MASKING_SESSION_FINALIZE_INVALID".to_string()
        })?;
        let expected_applied_mask_count = expected_counts.effective_mask_count;
        let occurrence_count = value
            .get("occurrenceCount")
            .and_then(serde_json::Value::as_u64)
            .and_then(|count| usize::try_from(count).ok())
            .ok_or_else(|| "MASKING_SESSION_FINALIZE_UNVERIFIED".to_string())?;
        if occurrence_count != expected_applied_mask_count {
            return Err("MASKING_SESSION_FINALIZE_UNVERIFIED".to_string());
        }
        if value
            .get("appliedMaskCount")
            .and_then(serde_json::Value::as_u64)
            .and_then(|count| usize::try_from(count).ok())
            != Some(expected_applied_mask_count)
        {
            return Err("MASKING_SESSION_FINALIZE_UNVERIFIED".to_string());
        }
        if value
            .get("manualMaskCount")
            .and_then(serde_json::Value::as_u64)
            .and_then(|count| usize::try_from(count).ok())
            != Some(expected_counts.manual_mask_count)
            || value
                .get("restoreCount")
                .and_then(serde_json::Value::as_u64)
                .and_then(|count| usize::try_from(count).ok())
                != Some(expected_counts.restore_count)
        {
            return Err("MASKING_SESSION_FINALIZE_UNVERIFIED".to_string());
        }
        let source = staging_output.clone();
        let mut staged_file = open_regular_staging_file(&source)?;
        staged_file
            .sync_all()
            .map_err(|_| "MASKING_SESSION_FINALIZE_UNVERIFIED".to_string())?;
        let mut staged = Vec::new();
        staged_file
            .read_to_end(&mut staged)
            .map_err(|_| "MASKING_SESSION_FINALIZE_UNVERIFIED".to_string())?;
        let expected_hash = value
            .get("staging_hash")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| "MASKING_SESSION_FINALIZE_UNVERIFIED".to_string())?;
        if masking_run_session::document_hash(&staged) != expected_hash
            || value.pointer("/verification/verified") != Some(&serde_json::Value::Bool(true))
        {
            return Err("MASKING_SESSION_FINALIZE_UNVERIFIED".to_string());
        }
        // The safe report is durable before publication. A report failure
        // therefore cannot leave a newly published PDF without its audit
        // record; a later promotion failure removes this precommit record.
        let audit_dir = app
            .path()
            .app_cache_dir()
            .map_err(|_| "MASKING_SESSION_AUDIT_REPORT_FAILED".to_string())?
            .join("finalization_safe_reports");
        std::fs::create_dir_all(&audit_dir)
            .map_err(|_| "MASKING_SESSION_AUDIT_REPORT_FAILED".to_string())?;
        let report_path = audit_dir.join(format!(
            "{}.{}.safe_report.json",
            manifest.run_id, manifest.analysis_revision
        ));
        audit_path = Some(report_path.clone());
        let audit_payload = serde_json::to_vec(&serde_json::json!({
            "schemaVersion": 1,
            "runId": manifest.run_id,
            "analysisRevision": manifest.analysis_revision,
            "manifestHash": manifest.manifest_hash,
            "finalHash": expected_hash,
            "manualMaskCount": expected_counts.manual_mask_count,
            "restoreCount": expected_counts.restore_count,
            "effectiveMaskCount": expected_counts.effective_mask_count,
            "restoreAuthorization": &restore_authorization,
            "saveConfirmation": &save_confirmation,
        }))
        .map_err(|_| "MASKING_SESSION_AUDIT_REPORT_FAILED".to_string())?;
        std::fs::write(&report_path, audit_payload)
            .map_err(|_| "MASKING_SESSION_AUDIT_REPORT_FAILED".to_string())?;
        std::fs::File::open(&report_path)
            .and_then(|file| file.sync_all())
            .map_err(|_| "MASKING_SESSION_AUDIT_REPORT_FAILED".to_string())?;
        std::fs::File::open(&audit_dir)
            .and_then(|directory| directory.sync_all())
            .map_err(|_| "MASKING_SESSION_AUDIT_REPORT_FAILED".to_string())?;
        publication = Some(FinalizePublication {
            final_path: destination.display().to_string(),
            verified_staging_hash: expected_hash.to_string(),
        });
        let transaction = stage_copy_overwrite_exact_from_file(
            &mut staged_file,
            &destination,
            overwrite_confirmed,
        )
        .map_err(|error| {
            if error == SaveError::RestoreFailed {
                committed = true;
                "MASKING_SESSION_PROMOTION_RESTORE_FAILED".to_string()
            } else {
                "MASKING_SESSION_PROMOTION_FAILED".to_string()
            }
        })?;
        if std::fs::File::open(&parent)
            .and_then(|directory| directory.sync_all())
            .is_err()
        {
            let rollback = transaction.rollback();
            if rollback.is_err() {
                committed = true;
                return Err("MASKING_SESSION_PROMOTION_RESTORE_FAILED".to_string());
            }
            return Err("MASKING_SESSION_PROMOTION_FAILED".to_string());
        }
        transaction.commit().map_err(|error| {
            if error == SaveError::RestoreFailed {
                committed = true;
                "MASKING_SESSION_PROMOTION_RESTORE_FAILED".to_string()
            } else {
                "MASKING_SESSION_PROMOTION_FAILED".to_string()
            }
        })?;
        committed = true;
        std::fs::File::open(&parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|_| "MASKING_SESSION_PUBLISHED_POSTCOMMIT".to_string())?;
        let final_hash = std::fs::read(&destination)
            .map_err(|_| "MASKING_SESSION_PUBLISHED_POSTCOMMIT".to_string())
            .and_then(|published| {
                verified_final_hash(
                    &publication
                        .as_ref()
                        .expect("publication evidence exists after commit")
                        .verified_staging_hash,
                    &published,
                )
            })?;
        sessions
            .consume_restore_authorizations(
                &request.run_id,
                manifest.analysis_revision,
                &manifest.manifest_hash,
            )
            .map_err(|_| "MASKING_SESSION_FINALIZE_UNVERIFIED".to_string())?;
        sessions
            .finish_finalize(&request.run_id, FinalizeDisposition::Consumed)
            .map_err(|_| "MASKING_SESSION_PUBLISHED_STATE_INDETERMINATE".to_string())?;
        Ok(FinalizeMaskingRunResult {
            run_id: manifest.run_id.clone(),
            analysis_revision: manifest.analysis_revision,
            manifest_hash: manifest.manifest_hash.clone(),
            final_path: publication
                .as_ref()
                .expect("publication evidence exists after commit")
                .final_path
                .clone(),
            final_hash,
            final_hash_attested: true,
            occurrence_count,
            applied_mask_count: expected_applied_mask_count,
            manual_mask_count: expected_counts.manual_mask_count,
            restore_count: expected_counts.restore_count,
            effective_mask_count: expected_counts.effective_mask_count,
            restore_authorization,
            save_confirmation: save_confirmation.clone(),
            status: "promoted",
        })
    })();
    let cleanup = (|| {
        std::fs::remove_dir_all(&staging)
            .map_err(|_| "MASKING_SESSION_CLEANUP_FAILED".to_string())?;
        if !committed {
            if let Some(path) = audit_path.take() {
                std::fs::remove_file(path)
                    .map_err(|_| "MASKING_SESSION_AUDIT_REPORT_CLEANUP_FAILED".to_string())?;
            }
        }
        Ok(())
    })();
    if !committed {
        sessions.finish_finalize(
            &request.run_id,
            if cleanup.is_ok() {
                FinalizeDisposition::RetryReady
            } else {
                FinalizeDisposition::CleanupRequired
            },
        )?;
    } else if result.is_err() {
        sessions
            .finish_finalize(&request.run_id, FinalizeDisposition::PublishedIndeterminate)
            .map_err(|_| "MASKING_SESSION_PUBLISHED_STATE_INDETERMINATE".to_string())?;
    }
    finalize_io_outcome(committed, publication.as_ref(), result, cleanup)
}
#[tauri::command]
fn finalize_masking_run(
    app: tauri::AppHandle,
    access: tauri::State<'_, AllowedFileAccess>,
    sessions: tauri::State<'_, MaskingRunSessions>,
    request: FinalizeMaskingRunRequest,
) -> Result<FinalizeMaskingRunResult, MaskingIpcError> {
    clear_masking_failure();
    finalize_masking_run_core(&app, &access, &sessions, request).map_err(|code| {
        let metadata = take_masking_failure(&code);
        append_masking_debug_log(&app, &metadata);
        metadata.error
    })
}

#[tauri::command]
fn read_text_file(
    access: tauri::State<'_, AllowedFileAccess>,
    path: String,
) -> Result<String, String> {
    if path.trim().is_empty() {
        return Err("텍스트 파일 경로가 비어 있습니다.".to_string());
    }
    let path_buf = canonicalize_existing_file(Path::new(path.trim()), "텍스트 파일")?;
    let role_allowed = if has_extension(&path_buf, &["txt"]) {
        access.masked_text_artifact_is_allowed(&path_buf)
    } else if has_extension(&path_buf, &["json"]) {
        access.report_artifact_is_allowed(&path_buf)
    } else {
        false
    };
    if !role_allowed {
        return Err("등록된 비식별 TXT 또는 안전 리포트만 읽을 수 있습니다.".to_string());
    }
    let meta = std::fs::metadata(&path_buf).map_err(|e| format!("텍스트 파일 확인 실패: {e}"))?;
    if meta.len() > 2 * 1024 * 1024 {
        return Err("텍스트 파일이 미리보기 제한보다 큽니다.".to_string());
    }
    std::fs::read_to_string(&path_buf).map_err(|e| format!("텍스트 파일 읽기 실패: {e}"))
}

#[tauri::command]
fn get_preview_workdir(
    app: tauri::AppHandle,
    access: tauri::State<'_, AllowedFileAccess>,
) -> Result<String, String> {
    let cache_dir = app
        .path()
        .app_cache_dir()
        .or_else(|_| app.path().app_data_dir())
        .map_err(|e| format!("미리보기 작업폴더 경로 확인 실패: {e}"))?;
    let dir = cache_dir.join("preview_runs");
    std::fs::create_dir_all(&dir).map_err(|e| format!("미리보기 작업폴더 생성 실패: {e}"))?;
    access.allow_artifact_dir(&dir);
    Ok(dir.display().to_string())
}

#[tauri::command]
fn create_canvas_launch_token(
    access: tauri::State<'_, AllowedFileAccess>,
    registry: tauri::State<'_, CanvasLaunchRegistry>,
    payload: CanvasWindowLaunchPayload,
) -> Result<String, String> {
    let target_path = if payload.target_path.trim().is_empty() {
        String::new()
    } else {
        canonicalize_registered_pdf(&access, &payload.target_path, "캔버스 대상 PDF")?
            .display()
            .to_string()
    };
    let original_path = if payload.original_path.trim().is_empty() {
        String::new()
    } else {
        canonicalize_registered_pdf(&access, &payload.original_path, "캔버스 원본 PDF")?
            .display()
            .to_string()
    };
    let output_dir = if payload.output_dir.trim().is_empty() {
        String::new()
    } else {
        canonicalize_registered_artifact_dir(&access, &payload.output_dir)?
            .display()
            .to_string()
    };
    let report_path = if payload.report_path.trim().is_empty() {
        String::new()
    } else {
        optional_registered_artifact(&access, Some(payload.report_path.clone()))
            .ok_or_else(|| "캔버스 안전 리포트 확인 실패".to_string())?
            .display()
            .to_string()
    };
    let mode = if payload.mode == "restore" {
        "restore".to_string()
    } else {
        "mask".to_string()
    };
    let token = canvas_launch_token();
    let safe_payload = CanvasWindowLaunchPayload {
        target_path,
        original_path,
        output_dir,
        report_path,
        mode,
        saved_at: payload.saved_at,
    };
    registry
        .launches
        .lock()
        .map_err(|_| "캔버스 실행 상태 잠금 실패".to_string())?
        .insert(token.clone(), safe_payload);
    Ok(token)
}

#[tauri::command]
fn take_canvas_launch_payload(
    registry: tauri::State<'_, CanvasLaunchRegistry>,
    token: String,
) -> Result<Option<CanvasWindowLaunchPayload>, String> {
    if token.trim().is_empty() {
        return Ok(None);
    }
    Ok(registry
        .launches
        .lock()
        .map_err(|_| "캔버스 실행 상태 잠금 실패".to_string())?
        .remove(token.trim()))
}

fn canvas_launch_token() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    format!("{:x}-{:x}", std::process::id(), now)
}

#[tauri::command]
fn open_mask_canvas_window(
    app: tauri::AppHandle,
    target_path: Option<String>,
) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("mask_canvas") {
        window
            .set_focus()
            .map_err(|e| format!("캔버스 창 포커스 실패: {e}"))?;
        return Ok(());
    }

    let token = target_path.as_deref().unwrap_or_default().trim();
    let app_url = if token.is_empty() {
        "index.html?mode=canvas".to_string()
    } else {
        format!("index.html?mode=canvas&target=launch&token={token}")
    };

    tauri::WebviewWindowBuilder::new(&app, "mask_canvas", tauri::WebviewUrl::App(app_url.into()))
        .title("마스킹 작업 캔버스")
        .inner_size(1280.0, 900.0)
        .min_inner_size(960.0, 700.0)
        .build()
        .map_err(|e| format!("캔버스 창 생성 실패: {e}"))?;
    Ok(())
}

fn run_masking_pipeline_core(
    app: &tauri::AppHandle,
    access: &AllowedFileAccess,
    input_file: String,
    original_file: Option<String>,
    output_dir: String,
    mut opts: MaskingOptions,
) -> Result<MaskingResult, String> {
    if input_file.trim().is_empty() {
        return Err("입력 파일 경로가 비어 있습니다.".to_string());
    }
    validate_masking_options(&opts)?;
    opts.profile = normalize_masking_profile(&opts.profile)?.to_string();
    if opts.profile != "legal" {
        return Err("MASKING_SESSION_PUBLIC_DIRECT_PIPELINE_REJECTED".to_string());
    }

    let input_path = canonicalize_registered_document(&access, &input_file, "입력 파일")?;
    let original_path = original_file
        .as_deref()
        .filter(|path| !path.trim().is_empty())
        .map(|path| canonicalize_registered_document(&access, path, "원본 파일"))
        .transpose()?;
    let outdir = if output_dir.trim().is_empty() {
        return Err("출력 폴더 경로가 비어 있습니다.".to_string());
    } else {
        canonicalize_registered_artifact_dir(&access, &output_dir)?
    };

    let runtime = resolve_runtime_paths(app)?;
    let use_live_scripts = runtime.development_discovery;
    let root = runtime.repo_root;
    let script_path = runtime.pipeline_script;
    let opts_json = serde_json::to_string(&opts).map_err(|e| format!("옵션 직렬화 실패: {e}"))?;

    let mut command = if use_live_scripts {
        if !script_path.exists() {
            return Err("PROCESS_RUNTIME_UNAVAILABLE: 마스킹 엔진을 찾지 못했습니다.".to_string());
        }
        let (py, py_args) = resolve_python(&root)?;
        let mut fallback = Command::new(py);
        for arg in py_args {
            fallback.arg(arg);
        }
        fallback.arg(script_path);
        fallback
    } else if let Some(engine_path) = runtime.masking_engine.clone() {
        Command::new(engine_path)
    } else {
        return Err(
            "PROCESS_RUNTIME_UNAVAILABLE: 패키지 마스킹 엔진을 찾지 못했습니다.".to_string(),
        );
    };
    command
        .arg("--repo-root")
        .arg(root.display().to_string())
        .arg("--input")
        .arg(input_path.display().to_string())
        .arg("--outdir")
        .arg(outdir.display().to_string());
    if let Some(original_path) = original_path {
        command
            .arg("--original")
            .arg(original_path.display().to_string());
    }
    command.arg("--opts").arg(opts_json);
    configure_direct_pipeline_allowed_dirs(&mut command, &input_path, &outdir)?;
    let captured =
        process_util::run_capturing_with_timeout(command, process_util::SINGLE_RUN_TIMEOUT)
            .map_err(|error| {
                let failure = lifecycle_spawn_failure("PROCESS_EXECUTION_FAILED", &error);
                remember_lifecycle_failure(failure, None);
                "PROCESS_EXECUTION_FAILED".to_string()
            })?;

    if captured.timed_out {
        let failure = MaskingIpcError {
            code: "PROCESS_TIMEOUT".to_string(),
            stage: "timeout".to_string(),
            detail: sanitized_stderr_tail(&captured.stderr),
        };
        remember_lifecycle_failure(failure, Some(&captured));
        return Err("PROCESS_TIMEOUT".to_string());
    }
    if let Some(failure) = lifecycle_capture_failure("PROCESS_FAILED", &captured) {
        remember_lifecycle_failure(failure, Some(&captured));
        return Err("PROCESS_FAILED".to_string());
    }

    let stdout = String::from_utf8_lossy(&captured.stdout).trim().to_string();
    let parsed: MaskingResult = serde_json::from_str(&stdout).map_err(|_| {
        let failure = lifecycle_output_parse_failure("PROCESS_RESULT_INVALID", &captured);
        remember_lifecycle_failure(failure, Some(&captured));
        "PROCESS_RESULT_INVALID".to_string()
    })?;
    access.allow_artifact_dir(&outdir);
    register_result_paths(&access, &parsed, &opts.deidentification_policy);

    Ok(parsed)
}

#[tauri::command]
fn run_masking_pipeline(
    app: tauri::AppHandle,
    access: tauri::State<'_, AllowedFileAccess>,
    input_file: String,
    original_file: Option<String>,
    output_dir: String,
    opts: MaskingOptions,
) -> Result<MaskingResult, MaskingIpcError> {
    clear_masking_failure();
    run_masking_pipeline_core(&app, &access, input_file, original_file, output_dir, opts).map_err(
        |code| {
            let metadata = take_masking_failure(&code);
            append_masking_debug_log(&app, &metadata);
            metadata.error
        },
    )
}

#[tauri::command]
fn apply_manual_boxes(
    app: tauri::AppHandle,
    access: tauri::State<'_, AllowedFileAccess>,
    request: ApplyManualBoxesRequest,
) -> Result<ApplyResult, String> {
    let ApplyManualBoxesRequest {
        input_pdf,
        original_pdf,
        output_dir,
        display_mode,
        report_path,
        boxes,
    } = request;

    if input_pdf.trim().is_empty() {
        return Err("입력 PDF 경로가 비어 있습니다.".to_string());
    }
    if output_dir.trim().is_empty() {
        return Err("출력 폴더 경로가 비어 있습니다.".to_string());
    }
    if boxes.is_empty() {
        return Err("저장할 박스가 없습니다.".to_string());
    }

    let input_pdf = canonicalize_registered_pdf(&access, &input_pdf, "입력 PDF")?;
    let original_pdf = canonicalize_registered_pdf(&access, &original_pdf, "원본 PDF")?;
    let output_dir = canonicalize_registered_artifact_dir(&access, &output_dir)?;
    let base_report = match report_path {
        Some(path) if !path.trim().is_empty() => Some(
            optional_registered_report_artifact(&access, Some(path))
                .ok_or_else(|| "수동 재검증 기준 리포트 확인 실패".to_string())?,
        ),
        _ => None,
    };

    let runtime = resolve_runtime_paths(&app)?;
    let use_live_scripts = runtime.development_discovery;
    let root = runtime.repo_root;
    let script_path = runtime.manual_boxes_script;

    let payload_json =
        serde_json::to_string(&boxes).map_err(|e| format!("박스 직렬화 실패: {e}"))?;

    let mut command = if use_live_scripts {
        if !script_path.exists() {
            return Err(
                "PROCESS_RUNTIME_UNAVAILABLE: 보조 스크립트를 찾지 못했습니다.".to_string(),
            );
        }
        let (py, py_args) = resolve_python(&root)?;
        let mut fallback = Command::new(py);
        for arg in py_args {
            fallback.arg(arg);
        }
        fallback.arg(script_path);
        fallback
    } else if let Some(engine_path) = runtime.masking_engine.clone() {
        let mut packaged = Command::new(engine_path);
        packaged.arg("--manual-boxes");
        packaged
    } else {
        return Err("PROCESS_RUNTIME_UNAVAILABLE: 패키지 보조 엔진을 찾지 못했습니다.".to_string());
    };
    command
        .arg("--input")
        .arg(input_pdf.display().to_string())
        .arg("--original")
        .arg(original_pdf.display().to_string())
        .arg("--outdir")
        .arg(output_dir.display().to_string())
        .arg("--boxes")
        .arg(payload_json)
        .arg("--display-mode")
        .arg(display_mode);
    configure_mask_tool_allowed_dirs(
        &mut command,
        &[
            input_pdf
                .parent()
                .ok_or_else(|| "입력 PDF 경로가 올바르지 않습니다.".to_string())?,
            original_pdf
                .parent()
                .ok_or_else(|| "원본 PDF 경로가 올바르지 않습니다.".to_string())?,
            &output_dir,
        ],
    )?;
    let captured =
        process_util::run_capturing_with_timeout(command, process_util::SINGLE_RUN_TIMEOUT)
            .map_err(|_| {
                "PROCESS_EXECUTION_FAILED: 보조 스크립트를 실행할 수 없습니다.".to_string()
            })?;

    if captured.timed_out {
        return Err(format!(
            "보조 스크립트가 제한 시간({}분)을 초과하여 중단되었습니다.",
            process_util::SINGLE_RUN_TIMEOUT.as_secs() / 60
        ));
    }
    if captured.stdout_truncated {
        return Err(
            "PROCESS_OUTPUT_LIMIT: 보조 스크립트 출력이 허용 크기를 초과했습니다.".to_string(),
        );
    }
    if !captured.status.success() {
        let stderr = String::from_utf8_lossy(&captured.stderr);
        let code = if captured.stderr_truncated {
            "PROCESS_ERROR_OUTPUT_LIMIT"
        } else {
            "PROCESS_FAILED"
        };
        return Err(format!(
            "{code}: 보조 스크립트 오류: {}",
            process_util::sanitize_stderr(&stderr)
        ));
    }

    let stdout = String::from_utf8_lossy(&captured.stdout).trim().to_string();
    let mut parsed: ApplyResult = serde_json::from_str(&stdout).map_err(|_| {
        format!(
            "PROCESS_RESULT_INVALID: {}",
            process_util::summarize_parse_failure(&stdout)
        )
    })?;
    access.allow_artifact_dir(&output_dir);
    access.allow_disposable_artifact_path(Path::new(&parsed.output_file));

    // 복원(위험 증가)이 적용된 경우에만 재검증 리포트를 생성·첨부한다. 마스킹만 추가한
    // 보정은 노출을 줄이기만 하므로 기존 리포트를 그대로 둔다. v4.2.0 정책상 이 재검증
    // 리포트는 저장을 막지 않는다 — 프론트가 경고를 산출하는 자문 신호로만 쓰이며,
    // 사용자가 "그대로 저장"을 확정하면 finalize 는 그대로 진행된다.
    if parsed.requires_revalidation == Some(true) {
        let restore_rects: Vec<RestoreRect> = boxes
            .iter()
            .filter(|b| b.mode == "restore")
            .map(|b| RestoreRect {
                page: b.page,
                x0: b.x0,
                y0: b.y0,
                x1: b.x1,
                y1: b.y1,
            })
            .collect();
        // 자문 passed 판정: (1) 기준 리포트가 깨끗하고, (2) 수동 적용이 성공했으며
        // (건너뜀 없음), (3) 어떤 복원도 마스킹된 영역을 재노출하지 않았다. 하나라도
        // 어긋나면 리포트를 failed 로 표시해 프론트가 경고를 띄우게 한다(저장 자체는
        // 사용자 재량으로 계속 진행 가능 — finalize 는 이 값으로 차단하지 않는다).
        let base_allows = report_allows_final_save(base_report.as_deref()).is_ok();
        let apply_ok =
            parsed.status.as_deref() == Some("applied") && parsed.skipped_boxes.unwrap_or(0) == 0;
        let reexposes = restore_reexposes_masked_region(base_report.as_deref(), &restore_rects);
        let passed = base_allows && apply_ok && !reexposes;

        let revalidation_report = write_manual_revalidation_report(&output_dir, &parsed, passed)?;
        access.allow_report_artifact_path(&revalidation_report);
        parsed.revalidation_status = Some(if passed { "passed" } else { "failed" }.to_string());
        parsed.revalidation_report = Some(revalidation_report.display().to_string());
    }

    Ok(parsed)
}

#[tauri::command]
fn finalize_manual_output(
    access: tauri::State<'_, AllowedFileAccess>,
    request: FinalizeManualOutputRequest,
) -> Result<FinalizeResult, String> {
    finalize_manual_output_path(&access, request)
}
#[tauri::command]
fn finalize_manual_output_to_selected_path(
    access: tauri::State<'_, AllowedFileAccess>,
    request: FinalizeManualOutputToSelectedPathRequest,
) -> Result<FinalizeResult, String> {
    finalize_manual_output_to_selected_path_path(&access, request)
}

fn finalize_manual_output_to_selected_path_path(
    access: &AllowedFileAccess,
    request: FinalizeManualOutputToSelectedPathRequest,
) -> Result<FinalizeResult, String> {
    let selected_target = canonicalize_registered_native_save_target(
        access,
        &request.output_path,
        &request.save_token,
    )?;
    let output_dir = selected_target
        .output_path
        .parent()
        .ok_or_else(|| SaveError::OutputDirRejected.to_string())?
        .to_path_buf();
    access.allow_artifact_dir(&output_dir);
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    let finalize_request = FinalizeManualOutputRequest {
        preview_pdf: request.preview_pdf,
        original_pdf: request.original_pdf,
        output_dir: output_dir.display().to_string(),
        extracted_path: request.extracted_path,
        masked_path: request.masked_path,
        report_path: request.report_path,
        copy_report: request.copy_report,
    };
    finalize_manual_output_path_with_pending_copiers(
        access,
        finalize_request,
        timestamp,
        move |preview, _outdir, _stem, _suffix, _ext| {
            stage_copy_overwrite_exact(
                preview,
                &selected_target.output_path,
                selected_target.overwrite_confirmed,
            )
            .map(PendingFinalCopy::ExactOverwrite)
        },
        copy_optional_artifact,
    )
}

enum PendingFinalCopy {
    New(PathBuf),
    ExactOverwrite(ExactOverwriteTransaction),
}

impl PendingFinalCopy {
    fn path(&self) -> &Path {
        match self {
            Self::New(path) => path,
            Self::ExactOverwrite(transaction) => transaction.path(),
        }
    }

    fn commit(self) -> Result<PathBuf, SaveError> {
        match self {
            Self::New(path) => Ok(path),
            Self::ExactOverwrite(transaction) => transaction.commit(),
        }
    }

    fn rollback(self) -> Result<(), SaveError> {
        match self {
            Self::New(path) => match std::fs::remove_file(path) {
                Ok(()) => Ok(()),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
                Err(_) => Err(SaveError::Io),
            },
            Self::ExactOverwrite(transaction) => transaction.rollback(),
        }
    }
}

// 최종 저장은 항상 사용자 재량(v4.2.0). 검증 리포트 내용은 프론트 경고의 근거일 뿐,
// 저장을 막지 않는다 — 따라서 여기서는 report_allows_final_save 로 저장을 차단하지
// 않는다. finalize 가 여전히 Err 를 반환하는 경우는 진짜 실패뿐이다:
//   1. 미리보기/출력 폴더가 등록된 안전 경로가 아님(canonicalize_registered_* 경로 보안).
//   2. 최종 산출물이 출력 폴더 밖으로 벗어남(starts_with 경계 검증).
//   3. 파일 복사 등 파일 IO 실패.
// 리포트 파싱 실패·부재는 더 이상 저장을 막지 않는다(경고는 프론트 몫).
fn finalize_manual_output_path(
    access: &AllowedFileAccess,
    request: FinalizeManualOutputRequest,
) -> Result<FinalizeResult, String> {
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    finalize_manual_output_path_with(access, request, timestamp, safe_copy_new)
}

#[cfg(test)]
fn finalize_manual_output_path_at(
    access: &AllowedFileAccess,
    request: FinalizeManualOutputRequest,
    timestamp: u64,
) -> Result<FinalizeResult, String> {
    finalize_manual_output_path_with(
        access,
        request,
        timestamp,
        |src, outdir, stem, suffix, ext| {
            safe_copy_new_at(src, outdir, stem, suffix, ext, timestamp)
        },
    )
}

fn finalize_manual_output_path_with<F>(
    access: &AllowedFileAccess,
    request: FinalizeManualOutputRequest,
    timestamp: u64,
    copy_final: F,
) -> Result<FinalizeResult, String>
where
    F: FnOnce(&Path, &Path, &str, &str, &str) -> Result<PathBuf, SaveError>,
{
    finalize_manual_output_path_with_copiers(
        access,
        request,
        timestamp,
        copy_final,
        copy_optional_artifact,
    )
}

fn finalize_manual_output_path_with_copiers<F, G>(
    access: &AllowedFileAccess,
    request: FinalizeManualOutputRequest,
    timestamp: u64,
    copy_final: F,
    copy_optional: G,
) -> Result<FinalizeResult, String>
where
    F: FnOnce(&Path, &Path, &str, &str, &str) -> Result<PathBuf, SaveError>,
    G: FnMut(
        &AllowedFileAccess,
        Option<PathBuf>,
        &str,
        &Path,
        &str,
        u64,
    ) -> Result<Option<String>, SaveError>,
{
    finalize_manual_output_path_with_pending_copiers(
        access,
        request,
        timestamp,
        |preview, outdir, stem, suffix, ext| {
            copy_final(preview, outdir, stem, suffix, ext).map(PendingFinalCopy::New)
        },
        copy_optional,
    )
}

fn finalize_manual_output_path_with_pending_copiers<F, G>(
    access: &AllowedFileAccess,
    request: FinalizeManualOutputRequest,
    timestamp: u64,
    copy_final: F,
    mut copy_optional: G,
) -> Result<FinalizeResult, String>
where
    F: FnOnce(&Path, &Path, &str, &str, &str) -> Result<PendingFinalCopy, SaveError>,
    G: FnMut(
        &AllowedFileAccess,
        Option<PathBuf>,
        &str,
        &Path,
        &str,
        u64,
    ) -> Result<Option<String>, SaveError>,
{
    let FinalizeManualOutputRequest {
        preview_pdf,
        original_pdf,
        output_dir,
        extracted_path,
        masked_path,
        report_path,
        copy_report,
    } = request;
    // Kept in the request only for IPC compatibility; intentionally never published.
    drop(extracted_path);

    let preview = canonicalize_registered_pdf(access, &preview_pdf, "미리보기 PDF")?;
    let outdir = canonicalize_registered_artifact_dir(access, &output_dir)?;
    // 리포트는 등록된 아티팩트일 때만 참조한다(경로 보안 경계 유지). 단 리포트 내용은
    // 저장 가부 판단에 쓰지 않는다 — 검증 결과는 프론트가 경고로만 활용한다.
    // `extracted_path` remains in the IPC payload for compatibility, but raw extracted text is
    // never a publishable artifact. Only the policy-transformed masked text may leave the
    // runtime artifact directory.
    let masked_artifact = optional_registered_masked_text_artifact(access, masked_path, &preview);
    let report_artifact = optional_registered_report_artifact(access, report_path);

    let stem_src = if original_pdf.trim().is_empty() {
        preview
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("masked")
            .to_string()
    } else {
        PathBuf::from(original_pdf)
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("masked")
            .to_string()
    };
    let pending_final = copy_final(&preview, &outdir, &stem_src, "final_masked", "pdf")
        .map_err(|error| error.to_string())?;
    let final_abs = pending_final.path().to_path_buf();
    if !final_abs.starts_with(&outdir) {
        return match pending_final.rollback() {
            Ok(()) => Err(SaveError::EscapesOutputDir.to_string()),
            Err(error) => Err(error.to_string()),
        };
    }

    let mut copied_files = Vec::new();
    let mut published_outputs = Vec::new();
    let copy_result = (|| -> Result<(), SaveError> {
        if let Some(path) = copy_optional(
            access,
            masked_artifact.clone(),
            "masked",
            &outdir,
            &stem_src,
            timestamp,
        )? {
            published_outputs.push(PathBuf::from(&path));
            copied_files.push(path);
        }
        // 방어적 기본값: 프론트가 copy_report를 넘기지 않아도 안전 리포트를 사용자
        // 출력 폴더로 복사하지 않는다(리포트는 내부에만 존재해야 함). 프론트가 명시적으로
        // Some(true)를 넘길 때만 복사한다.
        if copy_report.unwrap_or(false) {
            if let Some(path) = copy_optional(
                access,
                report_artifact.clone(),
                "report",
                &outdir,
                &stem_src,
                timestamp,
            )? {
                published_outputs.push(PathBuf::from(&path));
                copied_files.push(path);
            }
        }
        Ok(())
    })();
    if let Err(error) = copy_result {
        for path in published_outputs.iter().rev() {
            let _ = std::fs::remove_file(path);
        }
        return match pending_final.rollback() {
            Ok(()) => Err(error.to_string()),
            Err(rollback_error) => Err(rollback_error.to_string()),
        };
    }

    let final_abs = match pending_final.commit() {
        Ok(path) => path,
        Err(error) => {
            for path in published_outputs.iter().rev() {
                let _ = std::fs::remove_file(path);
            }
            return Err(error.to_string());
        }
    };

    for source in [masked_artifact].into_iter().flatten() {
        remove_intermediate_file_if_outside_dir(access, &source, &outdir);
    }
    if copy_report.unwrap_or(false) {
        if let Some(source) = report_artifact {
            remove_intermediate_file_if_outside_dir(access, &source, &outdir);
        }
    }
    remove_intermediate_file_if_outside_dir(access, &preview, &outdir);

    let result = FinalizeResult {
        final_output_file: final_abs.display().to_string(),
        copied_files,
    };
    register_copied_outputs(access, &result);
    Ok(result)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let context = tauri::generate_context!();
    if std::env::args().any(|arg| arg == "--public-native-qa-stdin") {
        let app = tauri::Builder::default()
            .manage(AllowedFileAccess::default())
            .manage(MaskingRunSessions::default())
            .build(context)
            .unwrap_or_else(|_| std::process::exit(1));
        if let Err(error) = native_qa::dispatch_from_stdin(app.handle()) {
            eprintln!("{error}");
            std::process::exit(1);
        }
        return;
    }
    let qa_drive_environment = std::env::var("MASK_TOOL_QA_DRIVE").ok();
    let qa_drive_enabled = qa_drive::is_enabled(std::env::args(), qa_drive_environment.as_deref());
    if std::env::args().any(|arg| arg == "--qa-drive-stdin") && !qa_drive_enabled {
        eprintln!("QA_DRIVE_ENV_REQUIRED");
        std::process::exit(2);
    }
    let qa_drive_bridge = qa_drive::Bridge::default();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(qa_drive_bridge.clone())
        .manage(QaDriveEnabled(qa_drive_enabled))
        .manage(AllowedFileAccess::default())
        .manage(CanvasLaunchRegistry::default())
        .manage(coordinate_batch::CoordinateBatchRegistry)
        .manage(MaskingRunSessions::default())
        .setup(move |app| {
            set_macos_activation_policy(app);
            show_macos_application(app)?;
            ensure_main_window(app)?;
            if qa_drive_enabled {
                qa_drive::start(app.handle().clone(), qa_drive_bridge.clone());
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            pick_input_pdf,
            choose_final_pdf_path,
            pick_input_document,
            qa_register_input_document,
            qa_stat_final_output,
            qa_drive_response,
            pick_input_documents,
            pick_output_dir,
            default_output_dir_for_document,
            read_pdf_bytes,
            read_text_file,
            get_preview_workdir,
            create_canvas_launch_token,
            take_canvas_launch_payload,
            open_mask_canvas_window,
            analyze_masking_run,
            resolve_masking_review,
            apply_manual_action_v1,
            issue_restore_capability,
            get_masking_run_state,
            finalize_masking_run,
            run_masking_pipeline,
            apply_manual_boxes,
            finalize_manual_output,
            finalize_manual_output_to_selected_path,
            coordinate_templates::list_coordinate_templates,
            coordinate_templates::load_coordinate_template,
            coordinate_templates::save_coordinate_template,
            coordinate_templates::delete_coordinate_template,
            coordinate_batch::enumerate_coordinate_batch_targets,
            coordinate_batch::preflight_coordinate_batch,
            coordinate_batch::start_coordinate_batch,
            coordinate_batch::cancel_coordinate_batch,
            coordinate_batch::retry_coordinate_batch
        ])
        .build(context)
        .expect("error while building tauri application");

    app.run(|_, _| {});
}

fn ensure_main_window(app: &mut tauri::App) -> tauri::Result<()> {
    if app.get_webview_window("main").is_some() {
        return Ok(());
    }

    let config = app
        .config()
        .app
        .windows
        .iter()
        .find(|window| window.label == "main")
        .cloned()
        .ok_or_else(|| tauri::Error::AssetNotFound("main window config".into()))?;
    let window = tauri::WebviewWindowBuilder::from_config(app.handle(), &config)?.build()?;
    window.set_focusable(true)?;
    window.center()?;
    window.unminimize()?;
    window.show()?;
    window.set_focus()?;
    if !window.is_visible()? {
        window.show()?;
    }
    activate_macos_app();
    window.set_focus()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn qa_drive_requires_flag_and_explicit_environment_opt_in() {
        assert!(!qa_drive::is_enabled(["document-masker"], None));
        assert!(!qa_drive::is_enabled(
            ["document-masker", "--qa-drive-stdin"],
            None,
        ));
        assert!(qa_drive::is_enabled(
            ["document-masker", "--qa-drive-stdin"],
            Some("1"),
        ));
    }
    #[test]
    fn reanalysis_commands_receive_only_the_original_document_parent_capability() {
        let root = std::env::temp_dir().join(format!(
            "masking_reanalysis_capability_{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("input parent");
        let mut command = std::process::Command::new("true");
        configure_mask_tool_allowed_dirs(&mut command, &[&root]).expect("capability");
        let allowed = command
            .get_envs()
            .find_map(|(key, value)| {
                (key.to_string_lossy() == "MASK_TOOL_ALLOWED_DIRS")
                    .then(|| value.and_then(|value| value.to_str()).map(str::to_string))
                    .flatten()
            })
            .expect("allowed directory environment");
        assert_eq!(
            allowed,
            root.canonicalize()
                .expect("canonical root")
                .to_string_lossy()
                .to_string()
        );
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn analyzer_payload_binds_successor_revision_without_reanalysis_shape_drift() {
        let payload = build_analyzer_payload(
            Path::new("/tmp/original.pdf"),
            serde_json::json!({"profile": "mixed"}),
            2,
        )
        .expect("analyzer payload");

        assert_eq!(
            Some(&serde_json::json!(2)),
            payload
                .get("options")
                .and_then(|options| options.get("analysis_revision"))
        );
        assert!(payload.get("reanalysis").is_none());
    }
    #[test]
    fn boundary_profile_authority_uses_prior_revision_while_ocr_uses_successor() {
        let boundary = masking_run_session::ReviewResolution::Boundary {
            page_start: 1,
            page_end: 1,
            segment_kind: "official_dispatch".to_string(),
        };
        let ocr = masking_run_session::ReviewResolution::Ocr { accepted: true };

        assert_eq!(
            Some(7),
            reanalysis_profile_authority_revision(&boundary, 7, 8)
        );
        assert_eq!(Some(8), reanalysis_profile_authority_revision(&ocr, 7, 8));
    }
    #[test]
    fn direct_pipeline_commands_receive_input_and_output_capabilities() {
        let root = std::env::temp_dir().join(format!(
            "masking_direct_pipeline_capability_{}",
            std::process::id()
        ));
        let input_dir = root.join("input");
        let output_dir = root.join("output");
        let input_file = input_dir.join("document.pdf");
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&input_dir).expect("input directory");
        fs::create_dir_all(&output_dir).expect("output directory");
        fs::write(&input_file, b"fixture").expect("input file");

        let mut command = std::process::Command::new("true");
        configure_direct_pipeline_allowed_dirs(&mut command, &input_file, &output_dir)
            .expect("capability");
        let allowed = command
            .get_envs()
            .find_map(|(key, value)| {
                (key.to_string_lossy() == "MASK_TOOL_ALLOWED_DIRS")
                    .then(|| value.and_then(|value| value.to_str()).map(str::to_string))
                    .flatten()
            })
            .expect("allowed directory environment");
        let expected = std::env::join_paths([
            input_dir.canonicalize().expect("canonical input"),
            output_dir.canonicalize().expect("canonical output"),
        ])
        .expect("joined paths");

        assert_eq!(allowed, expected.to_string_lossy());
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn finalize_io_outcomes_are_publication_aware() {
        let publication = FinalizePublication {
            final_path: "/safe/final.pdf".to_string(),
            verified_staging_hash: "a".repeat(64),
        };

        assert_eq!(
            finalize_io_outcome(
                false,
                None,
                Err("MASKING_SESSION_FINALIZE_FAILED".to_string()),
                Ok(()),
            ),
            Err(
                "MASKING_SESSION_PRECOMMIT_RETRYABLE;cause=MASKING_SESSION_FINALIZE_FAILED"
                    .to_string()
            )
        );
        assert_eq!(
            finalize_io_outcome(
                false,
                None,
                Err("MASKING_SESSION_FINALIZE_FAILED".to_string()),
                Err("MASKING_SESSION_CLEANUP_FAILED".to_string()),
            ),
            Err(
                "MASKING_SESSION_PRECOMMIT_CLEANUP_REQUIRED;cause=MASKING_SESSION_FINALIZE_FAILED;cleanup=MASKING_SESSION_CLEANUP_FAILED"
                    .to_string()
            )
        );

        for (primary, cleanup) in [
            ("MASKING_SESSION_PROMOTION_FAILED", Ok(())),
            (
                "MASKING_SESSION_PROMOTION_FAILED",
                Err("MASKING_SESSION_CLEANUP_FAILED".to_string()),
            ),
            ("MASKING_SESSION_PUBLISHED_STATE_INDETERMINATE", Ok(())),
        ] {
            let error =
                finalize_io_outcome(true, Some(&publication), Err(primary.to_string()), cleanup)
                    .expect_err("post-commit failures must be reported");
            assert_eq!(error, "MASKING_SESSION_PUBLISHED_POSTCOMMIT");
            assert!(!error.contains("final.pdf"));
        }
        let cleanup_error = finalize_io_outcome(
            true,
            Some(&publication),
            Ok(FinalizeMaskingRunResult {
                run_id: "run-123".to_string(),
                analysis_revision: 7,
                manifest_hash: "b".repeat(64),
                final_path: publication.final_path.clone(),
                final_hash: publication.verified_staging_hash.clone(),
                final_hash_attested: true,
                occurrence_count: 1,
                applied_mask_count: 1,
                manual_mask_count: 0,
                restore_count: 0,
                effective_mask_count: 1,
                restore_authorization: masking_run_session::RestoreAuthorizationSummary {
                    action_id_hash: "c".repeat(64),
                    target_occurrence_id_hash: "d".repeat(64),
                    authorization_event: "none".to_string(),
                },
                save_confirmation: masking_run_session::FinalizeSaveConfirmation {
                    status: "not_required".to_string(),
                    unresolved_reviews: Vec::new(),
                },
                status: "promoted",
            }),
            Err("MASKING_SESSION_CLEANUP_FAILED".to_string()),
        )
        .expect_err("post-commit cleanup failures must remain publication-aware");
        assert_eq!(cleanup_error, "MASKING_SESSION_PUBLISHED_POSTCOMMIT");
    }

    #[test]
    fn expected_finalized_mask_count_matches_ts_canonical_formula() {
        let occurrence = |occurrence_id: &str,
                          proposed_action: &str,
                          state: &str,
                          rects: Vec<masking_run_session::Rect>| {
            masking_run_session::AnalysisOccurrence {
                occurrence_id: occurrence_id.to_string(),
                segment_id: "segment-1".to_string(),
                region_id: None,
                analysis_revision: 7,
                page: 0,
                rects,
                tag: "person".to_string(),
                category: "name".to_string(),
                value_hash: "a".repeat(64),
                expected_text_hash: "b".repeat(64),
                source: "ocr".to_string(),
                policy: "default".to_string(),
                proposed_action: proposed_action.to_string(),
                state: state.to_string(),
                provenance: "ocr".to_string(),
            }
        };
        let manifest = AnalysisManifestV1 {
            manifest_version: 1,
            run_id: "run-1".to_string(),
            original_document_hash: "a".repeat(64),
            analysis_revision: 7,
            manifest_hash: "b".repeat(64),
            profile: "mixed".to_string(),
            policy_version: "masking-policy-v1".to_string(),
            options_version: "options-v2".to_string(),
            options_hash: "c".repeat(64),
            threshold_version: "thresholds-v2".to_string(),
            threshold_hash: "d".repeat(64),
            threshold_artifact: masking_run_session::ThresholdArtifactV1 {
                version: "thresholds-v2".to_string(),
                content_hash: "d".repeat(64),
                auto_mask_threshold: 0.85,
                review_threshold: 0.5,
            },
            coordinate_space: "pdf_points_top_left".to_string(),
            approval_coverage: masking_run_session::ApprovalCoverage {
                schema_version: 1,
                state: masking_run_session::CoverageState::Present,
                signer_count: 0,
                protected_neighbor_count: 0,
            },
            required_region_coverage: masking_run_session::RequiredRegionCoverage {
                schema_version: 1,
                profile: "mixed".to_string(),
                kinds: Vec::new(),
                blocking: false,
            },
            segments: Vec::new(),
            regions: Vec::new(),
            occurrences: vec![
                occurrence(
                    "mask-final",
                    "mask",
                    "confirmed",
                    vec![masking_run_session::Rect {
                        x0: 1.0,
                        y0: 1.0,
                        x1: 2.0,
                        y1: 2.0,
                    }],
                ),
                occurrence(
                    "mask-replaced",
                    "mask",
                    "user_confirmed",
                    vec![masking_run_session::Rect {
                        x0: 3.0,
                        y0: 3.0,
                        x1: 4.0,
                        y1: 4.0,
                    }],
                ),
                occurrence(
                    "mask-protected",
                    "mask",
                    "confirmed",
                    vec![masking_run_session::Rect {
                        x0: 5.0,
                        y0: 5.0,
                        x1: 6.0,
                        y1: 6.0,
                    }],
                ),
                occurrence("review", "review", "confirmed", Vec::new()),
            ],
            review_items: Vec::new(),
            manual_actions: vec![
                masking_run_session::ManualAction {
                    action_id: "manual-replacement".to_string(),
                    analysis_revision: 7,
                    page: 0,
                    rects: Vec::new(),
                    mode: "mask".to_string(),
                    source_kind: "text_pdf".to_string(),
                    linked_occurrence_id: Some("mask-replaced".to_string()),
                    expected_text_hash: None,
                    protected_neighbor_refs: vec![masking_run_session::Rect {
                        x0: 5.0,
                        y0: 5.0,
                        x1: 6.0,
                        y1: 6.0,
                    }],
                    restore_authorization_hash: None,
                },
                masking_run_session::ManualAction {
                    action_id: "manual-standalone".to_string(),
                    analysis_revision: 7,
                    page: 0,
                    rects: Vec::new(),
                    mode: "mask".to_string(),
                    source_kind: "text_pdf".to_string(),
                    linked_occurrence_id: None,
                    expected_text_hash: None,
                    protected_neighbor_refs: Vec::new(),
                    restore_authorization_hash: None,
                },
            ],
        };

        assert_eq!(expected_finalized_mask_count(&manifest), Ok(3));
    }

    fn captured_output_for_classifier(
        status_command: &str,
        stdout: &[u8],
        stderr: &[u8],
        timed_out: bool,
    ) -> process_util::CapturedOutput {
        process_util::CapturedOutput {
            status: Command::new(status_command)
                .status()
                .expect("classifier status command"),
            stdout: stdout.to_vec(),
            stderr: stderr.to_vec(),
            timed_out,
            stdout_truncated: false,
            stderr_truncated: false,
        }
    }

    #[test]
    fn lifecycle_classifier_marks_missing_executable_as_spawn_failure() {
        let error = std::io::Error::new(std::io::ErrorKind::NotFound, "missing executable");

        let failure = lifecycle_spawn_failure("MASKING_SESSION_ANALYZER_FAILED", &error);

        assert_eq!(failure.code, "MASKING_SESSION_ANALYZER_UNAVAILABLE");
        assert_eq!(failure.stage, "spawn");
    }

    #[test]
    fn lifecycle_classifier_passes_through_pipeline_failure_code() {
        let stderr = br#"{"event":"pipeline_failure","schemaVersion":1,"rawTextReturned":false,"error":{"code":"MASKING_PIPELINE_INPUT_REJECTED"}}"#;
        let captured = captured_output_for_classifier("false", b"", stderr, false);

        let failure = lifecycle_capture_failure("MASKING_SESSION_ANALYZER_FAILED", &captured)
            .expect("nonzero pipeline failure is classified");

        assert_eq!(failure.code, "MASKING_PIPELINE_INPUT_REJECTED");
        assert_eq!(failure.stage, "pipeline_failure_code");
        assert_eq!(failure.detail, "MASKING_PIPELINE_INPUT_REJECTED");
    }

    #[test]
    fn lifecycle_classifier_keeps_only_safe_intrinsic_diagnostics() {
        let stderr = br#"{"event":"pipeline_failure","schemaVersion":1,"rawTextReturned":false,"error":{"code":"MASKING_PIPELINE_TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED","diagnostics":[{"kind":"occurrence_failure","reason_code":"residual_text_in_saved_rectangle","count":2},{"kind":"pii_non_exposure","reason_code":"final_output_not_published","count":1}]}}"#;
        let captured = captured_output_for_classifier("false", b"", stderr, false);

        let failure = lifecycle_capture_failure("MASKING_SESSION_FINALIZE_FAILED", &captured)
            .expect("intrinsic diagnostics are classified");

        assert_eq!(
            failure.code,
            "MASKING_PIPELINE_TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED"
        );
        assert!(failure.detail.contains("\"kind\":\"occurrence_failure\""));
        assert!(failure.detail.contains("residual_text_in_saved_rectangle"));
        assert!(failure.detail.contains("final_output_not_published"));
    }

    #[test]
    fn lifecycle_classifier_preserves_only_privacy_safe_occurrence_context() {
        let stderr = br#"{"event":"pipeline_failure","schemaVersion":1,"rawTextReturned":false,"error":{"code":"MASKING_PIPELINE_TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED","diagnostics":[{"kind":"occurrence_failure","reason_code":"expected_text_hash_mismatch","count":1,"occurrence_id":"occ_0123456789abcdef01234567","category":"dispatch_metadata","page":1,"rect_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","expected_text_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]}}"#;
        let captured = captured_output_for_classifier("false", b"", stderr, false);

        let failure = lifecycle_capture_failure("MASKING_SESSION_FINALIZE_FAILED", &captured)
            .expect("contextual intrinsic diagnostics are classified");

        assert!(failure
            .detail
            .contains("\"occurrence_id\":\"occ_0123456789abcdef01234567\""));
        assert!(failure
            .detail
            .contains("\"category\":\"dispatch_metadata\""));
        assert!(failure.detail.contains("\"page\":1"));
        assert!(failure.detail.contains("\"rect_fingerprint\":\"aaaaaaaa"));
        assert!(failure.detail.contains("\"expected_text_hash\":\"bbbb"));
    }

    #[test]
    fn lifecycle_classifier_rejects_raw_occurrence_diagnostic_context() {
        let stderr = br#"{"event":"pipeline_failure","schemaVersion":1,"rawTextReturned":false,"error":{"code":"MASKING_PIPELINE_TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED","diagnostics":[{"kind":"occurrence_failure","reason_code":"expected_text_hash_mismatch","count":1,"category":"dispatch_metadata","raw_value":"sensitive"}]}}"#;
        let captured = captured_output_for_classifier("false", b"", stderr, false);

        let failure = lifecycle_capture_failure("MASKING_SESSION_FINALIZE_FAILED", &captured)
            .expect("unsafe diagnostic context falls back safely");

        assert_eq!(
            failure.detail,
            "MASKING_PIPELINE_TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED"
        );
        assert!(!failure.detail.contains("sensitive"));
    }

    #[test]
    fn lifecycle_classifier_marks_garbage_stdout_as_output_parse_failure() {
        let captured = captured_output_for_classifier("true", b"not-json", b"", false);

        let failure = lifecycle_output_parse_failure("MASKING_SESSION_ANALYZER_INVALID", &captured);

        assert_eq!(failure.code, "MASKING_SESSION_ANALYZER_INVALID");
        assert_eq!(failure.stage, "output_parse");
        assert!(failure.detail.contains("8바이트"));
    }

    #[test]
    fn lifecycle_classifier_marks_timeout_without_losing_the_stable_code() {
        let captured = captured_output_for_classifier("true", b"", b"engine timeout", true);

        let failure = lifecycle_capture_failure("MASKING_SESSION_FINALIZE_FAILED", &captured)
            .expect("timeout is classified");

        assert_eq!(failure.code, "MASKING_SESSION_FINALIZE_FAILED");
        assert_eq!(failure.stage, "timeout");
        assert!(failure.detail.contains("내용 생략"));
    }

    #[test]
    fn finalized_output_hash_must_match_verified_staging_hash() {
        let staged = b"verified staged output";
        let expected_hash = masking_run_session::document_hash(staged);
        assert_eq!(
            verified_final_hash(&expected_hash, staged).expect("matching hash"),
            expected_hash
        );
        assert_eq!(
            verified_final_hash(&expected_hash, b"different published output"),
            Err("MASKING_SESSION_PROMOTION_FAILED".to_string())
        );
    }
    #[test]
    fn choose_final_pdf_path_binding_accepts_public_session() {
        let binding = ChooseFinalPdfPathRequest {
            default_file_name: "final.pdf".to_string(),
            mode: None,
            run_id: Some("run-a".to_string()),
            analysis_revision: Some(7),
            manifest_hash: Some("manifest-a".to_string()),
        }
        .binding()
        .expect("complete public session must bind");

        assert_eq!(
            binding,
            NativeSaveTargetBinding::Public {
                run_id: "run-a".to_string(),
                analysis_revision: 7,
                manifest_hash: "manifest-a".to_string(),
            }
        );
    }

    #[test]
    fn choose_final_pdf_path_binding_accepts_legacy_direct_marker() {
        let binding = ChooseFinalPdfPathRequest {
            default_file_name: "final.pdf".to_string(),
            mode: Some("legacy_direct".to_string()),
            run_id: None,
            analysis_revision: None,
            manifest_hash: None,
        }
        .binding()
        .expect("explicit legacy-direct request must bind");

        assert_eq!(binding, NativeSaveTargetBinding::LegacyManual);
    }

    #[test]
    fn choose_final_pdf_path_binding_requires_all_public_fields() {
        let err = ChooseFinalPdfPathRequest {
            default_file_name: "final.pdf".to_string(),
            mode: None,
            run_id: None,
            analysis_revision: None,
            manifest_hash: None,
        }
        .binding()
        .expect_err("tuple-less save authority must be rejected");

        assert_eq!(err, "MASKING_SESSION_DESTINATION_REJECTED");

        let err = ChooseFinalPdfPathRequest {
            default_file_name: "final.pdf".to_string(),
            mode: None,
            run_id: Some("run-a".to_string()),
            analysis_revision: None,
            manifest_hash: Some("manifest-a".to_string()),
        }
        .binding()
        .expect_err("partial public binding must be rejected");

        assert_eq!(err, "MASKING_SESSION_DESTINATION_REJECTED");
    }

    #[test]
    fn choose_final_pdf_path_binding_rejects_mixed_marker_and_session_fields() {
        let err = ChooseFinalPdfPathRequest {
            default_file_name: "final.pdf".to_string(),
            mode: Some("legacy_direct".to_string()),
            run_id: Some("run-a".to_string()),
            analysis_revision: Some(7),
            manifest_hash: Some("manifest-a".to_string()),
        }
        .binding()
        .expect_err("legacy marker and public session must not mix");

        assert_eq!(err, "MASKING_SESSION_DESTINATION_REJECTED");
    }

    #[test]
    fn handle_bound_promotion_copies_held_staging_bytes() {
        let root = temp_security_root("handle_bound_promotion");
        let staging = root.join("staging.pdf");
        let destination = root.join("published.pdf");
        fs::write(&staging, b"verified staging bytes").expect("staging");
        let mut source = open_regular_staging_file(&staging).expect("held staging file");
        let replacement = root.join("replacement.pdf");
        fs::write(&replacement, b"replacement bytes").expect("replacement");
        fs::rename(&replacement, &staging).expect("replace staging path");
        stage_copy_overwrite_exact_from_file(&mut source, &destination, false)
            .expect("promotion from held handle")
            .commit()
            .expect("commit");
        assert_eq!(
            fs::read(&destination).expect("published bytes"),
            b"verified staging bytes"
        );
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn masking_promotion_denies_unconfirmed_overwrite() {
        let root = temp_security_root("masking_overwrite_denial");
        let source = root.join("staged.pdf");
        let destination = root.join("published.pdf");
        fs::write(&source, b"staged").expect("staging output");
        fs::write(&destination, b"existing").expect("published output");

        assert!(matches!(
            stage_copy_overwrite_exact(&source, &destination, false),
            Err(SaveError::OverwriteConfirmationRequired)
        ));
        assert_eq!(
            fs::read(&destination).expect("existing output"),
            b"existing"
        );
        let _ = fs::remove_dir_all(root);
    }

    fn valid_masking_options() -> MaskingOptions {
        MaskingOptions {
            rrn: true,
            phone: true,
            business_reg: true,
            name: true,
            address: true,
            place: true,
            legal_party: true,
            company: true,
            court: true,
            case_title: true,
            case_number: true,
            law_firm: true,
            attorney: true,
            approval_line: true,
            region_context: true,
            doc_meta: true,
            email: true,
            pdf_redaction: true,
            custom_keywords: String::new(),
            extract_engine: "auto".to_string(),
            profile: "mixed".to_string(),
            output_artifacts: "pdf_safe_report".to_string(),
            display_mode: "black".to_string(),
            deidentification_policy: "token".to_string(),
            region_scope: "national".to_string(),
            custom_regions: String::new(),
            return_text_preview: false,
            auto_mask_threshold: 0.85,
            review_threshold: 0.5,
        }
    }

    #[test]
    fn masking_options_accept_only_explicit_safe_artifact_and_policy_matrix() {
        for output_artifacts in ["pdf_safe_report", "pdf_masked_txt_safe_report"] {
            for policy in ["token", "partial", "pseudonym"] {
                for display_mode in ["black", "label_en", "label_ko", "pseudonym"] {
                    let mut opts = valid_masking_options();
                    opts.output_artifacts = output_artifacts.to_string();
                    opts.deidentification_policy = policy.to_string();
                    opts.display_mode = display_mode.to_string();
                    validate_masking_options(&opts).expect("supported option matrix");
                }
            }
        }
    }

    #[test]
    fn masking_options_reject_raw_text_and_implicit_policy_values() {
        for output_artifacts in ["txt만", "txt+pdf", "raw_text", "pdf"] {
            let mut opts = valid_masking_options();
            opts.output_artifacts = output_artifacts.to_string();
            assert!(
                validate_masking_options(&opts).is_err(),
                "{output_artifacts}"
            );
        }
        for policy in ["", "mask", "hash", "raw"] {
            let mut opts = valid_masking_options();
            opts.deidentification_policy = policy.to_string();
            assert!(validate_masking_options(&opts).is_err(), "{policy}");
        }
        for display_mode in ["", "label", "raw", "custom"] {
            let mut opts = valid_masking_options();
            opts.display_mode = display_mode.to_string();
            assert!(validate_masking_options(&opts).is_err(), "{display_mode}");
        }
        let mut opts = valid_masking_options();
        opts.return_text_preview = true;
        assert!(validate_masking_options(&opts).is_err());
    }

    #[test]
    fn masking_profiles_preserve_canonical_values_and_fail_closed() {
        for profile in ["internal_review", "official_dispatch", "mixed", "legal"] {
            assert_eq!(normalize_masking_profile(profile).unwrap(), profile);
        }
        assert!(normalize_masking_profile("official").is_err());
        for profile in ["", "unknown", "dispatch", "Internal_Review"] {
            assert!(normalize_masking_profile(profile).is_err(), "{profile}");
        }
    }

    fn temp_security_root(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "makiiing_security_test_{}_{}",
            name,
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("security root");
        root
    }

    #[test]
    fn default_output_registers_document_parent_dir() {
        let root = temp_security_root("default_output_parent_dir");
        let source_dir = root.join("source");
        let document_path = source_dir.join("source.pdf");
        fs::create_dir_all(&source_dir).expect("source dir");
        fs::write(&document_path, b"%PDF-1.4\n").expect("document");
        let access = AllowedFileAccess::default();
        access.allow_document_path(&document_path);

        let output_dir = default_output_dir_for_document_path(
            &access,
            document_path.to_str().expect("document path"),
            None,
        )
        .expect("default output dir");

        assert_eq!(
            output_dir,
            source_dir.canonicalize().expect("canonical source dir")
        );
        assert!(access.artifact_is_allowed(&output_dir));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn default_output_rejects_original_file_path_hint() {
        let root = temp_security_root("default_output_overwrite_hint");
        let source_dir = root.join("source");
        let document_path = source_dir.join("source.pdf");
        fs::create_dir_all(&source_dir).expect("source dir");
        fs::write(&document_path, b"%PDF-1.4\n").expect("document");
        let access = AllowedFileAccess::default();
        access.allow_document_path(&document_path);

        let err = default_output_dir_for_document_path(
            &access,
            document_path.to_str().expect("document path"),
            Some("nested/source.pdf"),
        )
        .expect_err("overwrite hint should be rejected");

        assert!(err.contains("덮어쓰기용 경로"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn default_output_rejects_unregistered_document() {
        let root = temp_security_root("default_output_unregistered");
        let source_dir = root.join("source");
        let document_path = source_dir.join("source.pdf");
        fs::create_dir_all(&source_dir).expect("source dir");
        fs::write(&document_path, b"%PDF-1.4\n").expect("document");
        let access = AllowedFileAccess::default();

        let err = default_output_dir_for_document_path(
            &access,
            document_path.to_str().expect("document path"),
            None,
        )
        .expect_err("unregistered document should be rejected");

        assert!(err.contains("등록된 경로"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn default_output_rejects_non_pdf_file() {
        let root = temp_security_root("default_output_non_pdf");
        let document_path = root.join("source.txt");
        fs::write(&document_path, b"text").expect("document");
        let access = AllowedFileAccess::default();

        let err = default_output_dir_for_document_path(
            &access,
            document_path.to_str().expect("document path"),
            None,
        )
        .expect_err("non-pdf document should be rejected");

        assert!(err.contains("PDF"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn default_output_rejects_missing_document() {
        let root = temp_security_root("default_output_missing");
        let document_path = root.join("missing.pdf");
        let access = AllowedFileAccess::default();

        let err = default_output_dir_for_document_path(
            &access,
            document_path.to_str().expect("document path"),
            None,
        )
        .expect_err("missing document should be rejected");

        assert!(err.contains("확인 실패"));
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn default_output_symlink_rejected() {
        use std::os::unix::fs::symlink;

        let root = temp_security_root("default_output_symlink_parent");
        let real_dir = root.join("real_source");
        let linked_dir = root.join("linked_source");
        let document_path = linked_dir.join("source.pdf");
        fs::create_dir_all(&real_dir).expect("real dir");
        fs::write(real_dir.join("source.pdf"), b"%PDF-1.4\n").expect("document");
        symlink(&real_dir, &linked_dir).expect("source dir symlink");
        let access = AllowedFileAccess::default();
        access.allow_document_path(&document_path);

        let err = default_output_dir_for_document_path(
            &access,
            document_path.to_str().expect("document path"),
            None,
        )
        .expect_err("symlink parent should be rejected");

        assert!(err.contains("심볼릭 링크 폴더"));
        assert!(!access.artifact_is_allowed(&real_dir.canonicalize().expect("canonical real dir")));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_copy_report_defaults_to_no_copy() {
        // 안전 리포트는 내부 세션 디렉터리에 있고, copy_report 기본값(None/Some(false))
        // 에서는 사용자 출력 폴더로 복사되지 않으며 Some(true)에서만 복사된다.
        let root = temp_security_root("finalize_copy_report_default");
        let internal_dir = root.join("internal");
        let outdir = root.join("out");
        fs::create_dir_all(&internal_dir).expect("internal dir");
        fs::create_dir_all(&outdir).expect("out dir");
        let report = internal_dir.join("doc.safe_report.20260101_000000.json");
        fs::write(&report, b"{\"raw_values_saved\":false}").expect("report");
        let report_artifact: Option<PathBuf> = Some(report.clone());
        let ts = 42u64;
        let mut copied: Vec<String> = Vec::new();
        let access = AllowedFileAccess::default();
        let should_copy_report = |value: Option<bool>| value.unwrap_or(false);

        // 미지정(None) → 미복사 (finalize의 copy_report.unwrap_or(false) 기본값 반영)
        let copy_report: Option<bool> = None;
        if should_copy_report(copy_report) {
            if let Some(p) = copy_optional_artifact(
                &access,
                report_artifact.clone(),
                "report",
                &outdir,
                "doc",
                ts,
            )
            .expect("explicit report copy")
            {
                copied.push(p);
            }
        }
        assert!(copied.is_empty(), "report must not be copied by default");
        assert!(
            fs::read_dir(&outdir).expect("read out").next().is_none(),
            "output dir must stay empty when copy_report is unset"
        );
        assert!(report.exists(), "internal report must remain intact");

        // 명시적 Some(true) → 복사
        let copy_report: Option<bool> = Some(true);
        if should_copy_report(copy_report) {
            if let Some(p) = copy_optional_artifact(
                &access,
                report_artifact.clone(),
                "report",
                &outdir,
                "doc",
                ts,
            )
            .expect("explicit report copy")
            {
                copied.push(p);
            }
        }
        assert_eq!(
            copied.len(),
            1,
            "explicit copy_report=true copies the report"
        );
        let _ = fs::remove_dir_all(root);
    }

    fn write_finalize_request(
        preview: &Path,
        outdir: &Path,
        report: Option<&Path>,
    ) -> FinalizeManualOutputRequest {
        FinalizeManualOutputRequest {
            preview_pdf: preview.to_str().expect("preview path").to_string(),
            original_pdf: String::new(),
            output_dir: outdir.to_str().expect("outdir path").to_string(),
            extracted_path: None,
            masked_path: None,
            report_path: report.map(|p| p.to_str().expect("report path").to_string()),
            copy_report: None,
        }
    }
    #[test]
    fn finalize_to_selected_path_overwrites_exact_registered_pdf_and_authorizes_output() {
        let root = temp_security_root("finalize_selected_target")
            .canonicalize()
            .expect("canon");
        let preview_dir = root.join("preview");
        let outdir = root.join("out");
        fs::create_dir_all(&preview_dir).expect("preview dir");
        fs::create_dir_all(&outdir).expect("out dir");
        let preview = preview_dir.join("masked.pdf");
        let selected = outdir.join("chosen-name.pdf");
        fs::write(&preview, b"new masked content").expect("preview");
        fs::write(&selected, b"old content").expect("existing selected output");

        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&preview_dir);
        access.allow_artifact_dir(&outdir);
        access.allow_disposable_artifact_path(&preview);
        let registration = access
            .register_native_save_target(&selected, NativeSaveTargetBinding::LegacyManual)
            .expect("native selected output");

        let result = finalize_manual_output_to_selected_path_path(
            &access,
            FinalizeManualOutputToSelectedPathRequest {
                preview_pdf: preview.to_str().unwrap().to_string(),
                original_pdf: String::new(),
                extracted_path: None,
                masked_path: None,
                report_path: None,
                copy_report: None,
                output_path: selected.to_str().unwrap().to_string(),
                save_token: registration.save_token,
            },
        )
        .expect("registered selected target must finalize");

        assert_eq!(PathBuf::from(&result.final_output_file), selected);
        assert_eq!(
            fs::read(&selected).expect("final bytes"),
            b"new masked content"
        );
        assert!(
            access.pdf_is_allowed(&selected),
            "final output must be authorized"
        );
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn finalize_to_selected_path_rejects_unselected_target() {
        let root = temp_security_root("finalize_unselected_target")
            .canonicalize()
            .expect("canon");
        let preview_dir = root.join("preview");
        let outdir = root.join("out");
        fs::create_dir_all(&preview_dir).expect("preview dir");
        fs::create_dir_all(&outdir).expect("out dir");
        let preview = preview_dir.join("masked.pdf");
        fs::write(&preview, b"masked content").expect("preview");

        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&preview_dir);
        access.allow_artifact_dir(&outdir);
        access.allow_disposable_artifact_path(&preview);

        let error = finalize_manual_output_to_selected_path_path(
            &access,
            FinalizeManualOutputToSelectedPathRequest {
                preview_pdf: preview.to_str().unwrap().to_string(),
                original_pdf: String::new(),
                extracted_path: None,
                masked_path: None,
                report_path: None,
                copy_report: None,
                output_path: outdir.join("unselected.pdf").to_str().unwrap().to_string(),
                save_token: "not-issued".to_string(),
            },
        )
        .expect_err("frontend-provided target must be rejected");

        assert!(error.contains("정확한 경로"));
        assert!(
            preview.exists(),
            "rejected finalize must not consume the preview"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn finalized_output_is_registered_as_immutable_continuation_source() {
        let root = temp_security_root("finalize_continuation")
            .canonicalize()
            .expect("canon");
        let source_dir = root.join("source");
        let preview_dir = root.join("preview");
        let outdir = root.join("out");
        fs::create_dir_all(&source_dir).expect("source dir");
        fs::create_dir_all(&preview_dir).expect("preview dir");
        fs::create_dir_all(&outdir).expect("out dir");
        let original = source_dir.join("document.pdf");
        let preview = preview_dir.join("document.manual.pdf");
        fs::write(&original, b"%PDF-1.4\noriginal").expect("original");
        fs::write(&preview, b"%PDF-1.4\nmasked-preview").expect("preview");

        let access = AllowedFileAccess::default();
        access.allow_pdf_path(&original);
        access.allow_artifact_dir(&preview_dir);
        access.allow_artifact_dir(&outdir);
        access.allow_disposable_artifact_path(&preview);
        let mut request = write_finalize_request(&preview, &outdir, None);
        request.original_pdf = original.to_str().expect("original path").to_string();

        let first =
            finalize_manual_output_path_at(&access, request, 42).expect("first final snapshot");
        let first_path = PathBuf::from(&first.final_output_file);

        assert!(
            !preview.exists(),
            "committed disposable preview must be removed"
        );
        assert_eq!(
            fs::read(&original).expect("original bytes"),
            b"%PDF-1.4\noriginal",
            "restore reference must remain immutable"
        );
        assert_eq!(
            fs::read(&first_path).expect("first final bytes"),
            b"%PDF-1.4\nmasked-preview"
        );
        assert_eq!(
            canonicalize_registered_pdf(&access, &first.final_output_file, "연속 편집 PDF")
                .expect("final output must be an authorized continuation source"),
            first_path.canonicalize().expect("canonical first final")
        );

        let second = finalize_manual_output_path_at(
            &access,
            write_finalize_request(&first_path, &outdir, None),
            42,
        )
        .expect("second final snapshot");
        assert_ne!(first.final_output_file, second.final_output_file);
        assert_eq!(
            fs::read(&first_path).expect("first final remains"),
            b"%PDF-1.4\nmasked-preview",
            "later snapshots must not mutate the earlier final"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_saves_despite_residual_hits_report() {
        // v4.2.0 사용자 재량 저장의 회귀 가드: residual_hits>0 (과거 정책이면 차단됐을)
        // 리포트가 첨부돼도 finalize 는 성공한다. 리포트 내용은 저장을 막지 않는다.
        let root = temp_security_root("finalize_residual_hits")
            .canonicalize()
            .expect("canon");
        let outdir = root.join("out");
        fs::create_dir_all(&outdir).expect("out dir");
        let preview = outdir.join("doc.masked.pdf");
        fs::write(&preview, b"%PDF-1.4\n").expect("preview");
        let report = outdir.join("doc.safe_report.json");
        let dirty = serde_json::json!({
            "product_checks": { "quality_gate_passed": false, "needs_manual_review": true },
            "missing_targets_count": 2,
            "document_redaction": { "verification": { "residual_hits": 3 } }
        });
        fs::write(&report, dirty.to_string()).expect("report");

        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&outdir);
        access.allow_pdf_path(&preview);

        let request = write_finalize_request(&preview, &outdir, Some(&report));
        let result = finalize_manual_output_path(&access, request)
            .expect("save must proceed at user discretion despite residual hits");
        assert!(
            PathBuf::from(&result.final_output_file).exists(),
            "final masked output must be written"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_saves_despite_needs_review_and_failed_revalidation_report() {
        // needs_manual_review=true + manual_revalidation 실패 리포트로도 finalize 는
        // 성공해야 한다(사용자 재량 저장). 재검증 실패는 프론트 경고일 뿐 저장 차단이 아니다.
        let root = temp_security_root("finalize_failed_reval")
            .canonicalize()
            .expect("canon");
        let outdir = root.join("out");
        fs::create_dir_all(&outdir).expect("out dir");
        let preview = outdir.join("doc.masked.pdf");
        fs::write(&preview, b"%PDF-1.4\n").expect("preview");
        let report = outdir.join("doc.manual_revalidation.safe_report.json");
        let failing = serde_json::json!({
            "product_checks": { "quality_gate_passed": false, "needs_manual_review": true },
            "manual_revalidation": { "status": "failed", "verified": false },
            "document_redaction": {
                "missing_targets_count": 1,
                "verification": { "residual_hits": 1 }
            }
        });
        fs::write(&report, failing.to_string()).expect("report");

        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&outdir);
        access.allow_pdf_path(&preview);

        let request = write_finalize_request(&preview, &outdir, Some(&report));
        let result = finalize_manual_output_path(&access, request)
            .expect("save must proceed despite needs_manual_review + failed revalidation");
        assert!(
            PathBuf::from(&result.final_output_file).exists(),
            "final masked output must be written"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_saves_with_missing_report() {
        // 리포트 부재도 저장을 막지 않는다(경고는 프론트 몫).
        let root = temp_security_root("finalize_no_report")
            .canonicalize()
            .expect("canon");
        let outdir = root.join("out");
        fs::create_dir_all(&outdir).expect("out dir");
        let preview = outdir.join("doc.masked.pdf");
        fs::write(&preview, b"%PDF-1.4\n").expect("preview");

        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&outdir);
        access.allow_pdf_path(&preview);

        let request = write_finalize_request(&preview, &outdir, None);
        let result = finalize_manual_output_path(&access, request)
            .expect("missing report must not block save");
        assert!(PathBuf::from(&result.final_output_file).exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_twice_at_same_timestamp_creates_distinct_outputs() {
        let root = temp_security_root("finalize_same_timestamp")
            .canonicalize()
            .expect("canon");
        let outdir = root.join("out");
        fs::create_dir_all(&outdir).expect("out dir");
        let preview = outdir.join("doc.masked.pdf");
        fs::write(&preview, b"%PDF-1.4\nmasked").expect("preview");
        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&outdir);
        access.allow_disposable_artifact_path(&preview);

        let first = finalize_manual_output_path_at(
            &access,
            write_finalize_request(&preview, &outdir, None),
            42,
        )
        .expect("first finalize");
        let second = finalize_manual_output_path_at(
            &access,
            write_finalize_request(&preview, &outdir, None),
            42,
        )
        .expect("second finalize");

        assert_ne!(first.final_output_file, second.final_output_file);
        assert_eq!(
            fs::read(&first.final_output_file).expect("first bytes"),
            b"%PDF-1.4\nmasked"
        );
        assert_eq!(
            fs::read(&second.final_output_file).expect("second bytes"),
            b"%PDF-1.4\nmasked"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_ignores_raw_extracted_text_and_only_publishes_masked_text() {
        let root = temp_security_root("finalize_masked_text_only")
            .canonicalize()
            .expect("canon");
        let preview_dir = root.join("preview");
        let outdir = root.join("out");
        fs::create_dir_all(&preview_dir).expect("preview dir");
        fs::create_dir_all(&outdir).expect("out dir");
        let preview = preview_dir.join("doc.masked.pdf");
        let extracted = preview_dir.join("doc.extracted.txt");
        let masked = preview_dir.join("doc.masked.txt");
        fs::write(&preview, b"%PDF-1.4\nmasked").expect("preview");
        fs::write(&extracted, b"raw source text").expect("extracted");
        fs::write(&masked, b"[PERSON_1]").expect("masked");

        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&outdir);
        access.allow_artifact_dir(&preview_dir);
        access.allow_disposable_artifact_path(&preview);
        access.allow_disposable_artifact_path(&extracted);
        access.allow_masked_text_artifact_path(&masked, &preview, "token");
        let mut request = write_finalize_request(&preview, &outdir, None);
        request.extracted_path = Some(extracted.to_str().expect("extracted path").to_string());
        request.masked_path = Some(masked.to_str().expect("masked path").to_string());

        let result = finalize_manual_output_path_at(&access, request, 42)
            .expect("masked-text export must succeed");

        assert_eq!(result.copied_files.len(), 1);
        assert!(result.copied_files[0].contains("_masked_"));
        assert_eq!(
            fs::read(&result.copied_files[0]).expect("published masked text"),
            b"[PERSON_1]"
        );
        assert!(
            extracted.exists(),
            "raw text is ignored rather than copied or mutated by finalize"
        );
        assert!(
            fs::read_dir(&outdir)
                .expect("output entries")
                .all(|entry| !entry
                    .expect("output entry")
                    .file_name()
                    .to_string_lossy()
                    .contains("extracted")),
            "raw extracted text must never be published"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_rejects_raw_extracted_text_substituted_as_masked_path() {
        let root = temp_security_root("finalize_rejects_role_swap")
            .canonicalize()
            .expect("canon");
        let preview_dir = root.join("preview");
        let outdir = root.join("out");
        fs::create_dir_all(&preview_dir).expect("preview dir");
        fs::create_dir_all(&outdir).expect("out dir");
        let preview = preview_dir.join("doc.masked.pdf");
        let extracted = preview_dir.join("doc.extracted.txt");
        fs::write(&preview, b"%PDF-1.4\nmasked").expect("preview");
        fs::write(&extracted, b"raw source text").expect("extracted");

        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&outdir);
        access.allow_artifact_dir(&preview_dir);
        access.allow_disposable_artifact_path(&preview);
        access.allow_disposable_artifact_path(&extracted);
        let mut request = write_finalize_request(&preview, &outdir, None);
        request.extracted_path = Some(extracted.to_str().expect("extracted path").to_string());
        request.masked_path = Some(extracted.to_str().expect("extracted path").to_string());

        let result = finalize_manual_output_path_at(&access, request, 42)
            .expect("the PDF remains saveable while the invalid optional artifact is ignored");

        assert!(
            result.copied_files.is_empty(),
            "raw text role swap must never publish an optional TXT"
        );
        assert_eq!(fs::read_dir(&outdir).expect("out entries").count(), 1);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_rolls_back_all_published_outputs_when_optional_copy_fails() {
        let root = temp_security_root("finalize_optional_rollback")
            .canonicalize()
            .expect("canon");
        let preview_dir = root.join("preview");
        let outdir = root.join("out");
        fs::create_dir_all(&preview_dir).expect("preview dir");
        fs::create_dir_all(&outdir).expect("out dir");
        let preview = preview_dir.join("doc.masked.pdf");
        let extracted = preview_dir.join("doc.extracted.txt");
        let masked = preview_dir.join("doc.masked.txt");
        fs::write(&preview, b"%PDF-1.4\nmasked").expect("preview");
        fs::write(&extracted, b"extracted").expect("extracted");
        fs::write(&masked, b"masked").expect("masked");

        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&outdir);
        access.allow_artifact_dir(&preview_dir);
        access.allow_disposable_artifact_path(&preview);
        access.allow_disposable_artifact_path(&extracted);
        access.allow_masked_text_artifact_path(&masked, &preview, "token");
        let mut request = write_finalize_request(&preview, &outdir, None);
        request.extracted_path = Some(extracted.to_str().expect("extracted path").to_string());
        request.masked_path = Some(masked.to_str().expect("masked path").to_string());

        let error = finalize_manual_output_path_with_copiers(
            &access,
            request,
            42,
            |src, output, stem, suffix, ext| safe_copy_new_at(src, output, stem, suffix, ext, 42),
            |_access, source, suffix, output, stem, timestamp| {
                if suffix == "masked" {
                    return Err(SaveError::Io);
                }
                let source = source.expect("optional source");
                let destination = output.join(format!("{stem}_{suffix}_{timestamp}.txt"));
                fs::copy(source, &destination).expect("injected optional copy");
                Ok(Some(destination.display().to_string()))
            },
        )
        .expect_err("optional copy failure must abort the transaction");

        assert_eq!(error, SaveError::Io.to_string());
        assert!(
            fs::read_dir(&outdir)
                .expect("outdir entries")
                .next()
                .is_none(),
            "a failed finalize must leave no published outputs"
        );
        assert!(preview.exists(), "preview must remain retryable");
        assert!(extracted.exists(), "extracted source must remain retryable");
        assert!(masked.exists(), "masked source must remain retryable");
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn selected_path_optional_failure_restores_original_and_preserves_preview() {
        let root = temp_security_root("selected_optional_rollback")
            .canonicalize()
            .expect("canon");
        let preview_dir = root.join("preview");
        let outdir = root.join("out");
        fs::create_dir_all(&preview_dir).expect("preview dir");
        fs::create_dir_all(&outdir).expect("out dir");
        let preview = preview_dir.join("doc.masked.pdf");
        let masked = preview_dir.join("doc.masked.txt");
        let selected = outdir.join("selected.pdf");
        fs::write(&preview, b"%PDF-1.4\nnew masked").expect("preview");
        fs::write(&masked, b"masked text").expect("masked");
        fs::write(&selected, b"%PDF-1.4\nprevious final").expect("existing final");

        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&outdir);
        access.allow_artifact_dir(&preview_dir);
        access.allow_disposable_artifact_path(&preview);
        access.allow_masked_text_artifact_path(&masked, &preview, "token");
        let mut request = write_finalize_request(&preview, &outdir, None);
        request.masked_path = Some(masked.to_str().expect("masked path").to_string());

        let error = finalize_manual_output_path_with_pending_copiers(
            &access,
            request,
            42,
            |source, _output, _stem, _suffix, _ext| {
                stage_copy_overwrite_exact(source, &selected, true)
                    .map(PendingFinalCopy::ExactOverwrite)
            },
            |_access, _source, suffix, _output, _stem, _timestamp| {
                assert_eq!(suffix, "masked");
                Err(SaveError::Io)
            },
        )
        .expect_err("optional copy failure must roll back the selected target");

        assert_eq!(error, SaveError::Io.to_string());
        assert_eq!(
            fs::read(&selected).expect("restored selected target"),
            b"%PDF-1.4\nprevious final"
        );
        assert!(
            preview.exists(),
            "preview must remain retryable until commit"
        );
        assert!(
            masked.exists(),
            "masked text must remain retryable until commit"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_rejects_unregistered_output_dir() {
        let root = temp_security_root("finalize_unregistered_outdir")
            .canonicalize()
            .expect("canon");
        let preview_dir = root.join("preview");
        let outdir = root.join("out");
        fs::create_dir_all(&preview_dir).expect("preview dir");
        fs::create_dir_all(&outdir).expect("out dir");
        let preview = preview_dir.join("doc.masked.pdf");
        fs::write(&preview, b"%PDF-1.4\nmasked").expect("preview");
        let access = AllowedFileAccess::default();
        access.allow_disposable_artifact_path(&preview);

        let error =
            finalize_manual_output_path(&access, write_finalize_request(&preview, &outdir, None))
                .expect_err("unregistered outdir must be rejected");

        assert!(error.contains("등록된 경로"));
        assert!(fs::read_dir(&outdir)
            .expect("outdir entries")
            .next()
            .is_none());
        assert!(preview.exists());
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn finalize_rejects_symlink_preview_without_touching_target() {
        use std::os::unix::fs::symlink;

        let root = temp_security_root("finalize_preview_symlink")
            .canonicalize()
            .expect("canon");
        let preview_dir = root.join("preview");
        let outdir = root.join("out");
        fs::create_dir_all(&preview_dir).expect("preview dir");
        fs::create_dir_all(&outdir).expect("out dir");
        let target = preview_dir.join("target.pdf");
        let preview = preview_dir.join("linked.masked.pdf");
        fs::write(&target, b"%PDF-1.4\nsentinel").expect("target");
        symlink(&target, &preview).expect("preview symlink");
        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&outdir);
        access.allow_pdf_path(&preview);

        let error =
            finalize_manual_output_path(&access, write_finalize_request(&preview, &outdir, None))
                .expect_err("symlink preview must be rejected");

        assert!(error.contains("심볼릭 링크"));
        assert_eq!(
            fs::read(&target).expect("target bytes"),
            b"%PDF-1.4\nsentinel"
        );
        assert!(fs::read_dir(&outdir)
            .expect("outdir entries")
            .next()
            .is_none());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_still_enforces_preview_path_security() {
        // 경로 보안 경계는 그대로 유지: 등록되지 않은(그리고 허용 폴더 밖의) 미리보기
        // PDF 는 여전히 거부된다. 이건 저장 게이트가 아니라 보안 경계다.
        let root = temp_security_root("finalize_security")
            .canonicalize()
            .expect("canon");
        let outdir = root.join("out");
        let other = root.join("other");
        fs::create_dir_all(&outdir).expect("out dir");
        fs::create_dir_all(&other).expect("other dir");
        // 미리보기를 허용 폴더 밖(other)에 두고 등록하지 않는다.
        let preview = other.join("doc.masked.pdf");
        fs::write(&preview, b"%PDF-1.4\n").expect("preview");

        let access = AllowedFileAccess::default();
        access.allow_artifact_dir(&outdir);

        let request = write_finalize_request(&preview, &outdir, None);
        let err = finalize_manual_output_path(&access, request)
            .expect_err("unregistered preview must be rejected by path security");
        assert!(err.contains("등록된"));
        let _ = fs::remove_dir_all(root);
    }
}
