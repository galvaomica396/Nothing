use rfd::FileDialog;
use serde::{de::DeserializeOwned, de::Error as _, Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;
use tauri::Manager;

// allow: SIZE_OK — Tauri command aggregator plus app bootstrap. The path
// security guards, runtime/Python path discovery, and the macOS activation FFI
// were split into `path_security`, `runtime_paths`, and `platform_macos`
// (R3 module separation); the IPC commands and app builder remain here.

mod coordinate_batch;
mod coordinate_templates;
mod manual_revalidation;
mod path_security;
mod platform_macos;
mod process_util;
mod runtime_paths;

use manual_revalidation::{
    report_allows_final_save, restore_reexposes_masked_region, write_manual_revalidation_report,
    ApplyResult, RestoreRect,
};

// Re-exported for the commands below and their path-security checks.
pub(crate) use path_security::{
    canonicalize_existing_dir, canonicalize_existing_file, has_extension, AllowedFileAccess,
};
pub(crate) use runtime_paths::{resolve_python, resolve_runtime_paths};

#[cfg(test)]
use path_security::safe_copy_new_at;
use path_security::{
    canonicalize_registered_artifact_dir, canonicalize_registered_document,
    canonicalize_registered_native_save_target, canonicalize_registered_pdf,
    copy_optional_artifact, optional_registered_artifact, optional_registered_masked_text_artifact,
    optional_registered_report_artifact, remove_intermediate_file_if_outside_dir, safe_copy_new,
    stage_copy_overwrite_exact, ExactOverwriteTransaction, SaveError,
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
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChooseFinalPdfPathPayload {
    default_file_name: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct FinalPdfSaveTarget {
    output_path: String,
    save_token: String,
}

impl From<ChooseFinalPdfPathPayload> for ChooseFinalPdfPathRequest {
    fn from(payload: ChooseFinalPdfPathPayload) -> Self {
        Self {
            default_file_name: payload.default_file_name,
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

fn validate_masking_options(opts: &MaskingOptions) -> Result<(), String> {
    if !matches!(
        opts.output_artifacts.as_str(),
        "pdf_safe_report" | "pdf_masked_txt_safe_report"
    ) {
        return Err("MASKING_OPTIONS_REJECTED: 지원하지 않는 산출물 옵션입니다.".to_string());
    }
    if !matches!(
        opts.deidentification_policy.as_str(),
        "token" | "partial" | "pseudonym"
    ) {
        return Err("MASKING_OPTIONS_REJECTED: 지원하지 않는 비식별 정책입니다.".to_string());
    }
    if !matches!(
        opts.display_mode.as_str(),
        "black" | "label_en" | "label_ko" | "pseudonym"
    ) {
        return Err("MASKING_OPTIONS_REJECTED: 지원하지 않는 표시 모드입니다.".to_string());
    }
    if opts.return_text_preview {
        return Err(
            "MASKING_OPTIONS_REJECTED: 원문 텍스트 미리보기는 허용되지 않습니다.".to_string(),
        );
    }
    Ok(())
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
    access: tauri::State<'_, AllowedFileAccess>,
    request: ChooseFinalPdfPathRequest,
) -> Result<Option<FinalPdfSaveTarget>, String> {
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
    let target = access.register_native_save_target(&path)?;
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

#[tauri::command]
fn run_masking_pipeline(
    app: tauri::AppHandle,
    access: tauri::State<'_, AllowedFileAccess>,
    input_file: String,
    original_file: Option<String>,
    output_dir: String,
    opts: MaskingOptions,
) -> Result<MaskingResult, String> {
    if input_file.trim().is_empty() {
        return Err("입력 파일 경로가 비어 있습니다.".to_string());
    }
    validate_masking_options(&opts)?;

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

    let runtime = resolve_runtime_paths(&app)?;
    let root = runtime.repo_root;
    let script_path = runtime.pipeline_script;
    let opts_json = serde_json::to_string(&opts).map_err(|e| format!("옵션 직렬화 실패: {e}"))?;

    let mut command = if let Some(engine_path) = runtime.masking_engine.clone() {
        Command::new(engine_path)
    } else {
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
    let captured =
        process_util::run_capturing_with_timeout(command, process_util::SINGLE_RUN_TIMEOUT)
            .map_err(|_| {
                "PROCESS_EXECUTION_FAILED: 파이프라인을 실행할 수 없습니다.".to_string()
            })?;

    if captured.timed_out {
        return Err(format!(
            "파이프라인이 제한 시간({}분)을 초과하여 중단되었습니다.",
            process_util::SINGLE_RUN_TIMEOUT.as_secs() / 60
        ));
    }
    if captured.stdout_truncated {
        return Err(
            "PROCESS_OUTPUT_LIMIT: 파이프라인 출력이 허용 크기를 초과했습니다.".to_string(),
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
            "{code}: 파이프라인 오류: {}",
            process_util::sanitize_stderr(&stderr)
        ));
    }

    let stdout = String::from_utf8_lossy(&captured.stdout).trim().to_string();
    let parsed: MaskingResult = serde_json::from_str(&stdout).map_err(|_| {
        format!(
            "PROCESS_RESULT_INVALID: {}",
            process_util::summarize_parse_failure(&stdout)
        )
    })?;
    access.allow_artifact_dir(&outdir);
    register_result_paths(&access, &parsed, &opts.deidentification_policy);

    Ok(parsed)
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
    let root = runtime.repo_root;
    let script_path = runtime.manual_boxes_script;

    let payload_json =
        serde_json::to_string(&boxes).map_err(|e| format!("박스 직렬화 실패: {e}"))?;

    let mut command = if let Some(engine_path) = runtime.masking_engine.clone() {
        let mut packaged = Command::new(engine_path);
        packaged.arg("--manual-boxes");
        packaged
    } else {
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
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(AllowedFileAccess::default())
        .manage(CanvasLaunchRegistry::default())
        .manage(coordinate_batch::CoordinateBatchRegistry)
        .setup(|app| {
            set_macos_activation_policy(app);
            show_macos_application(app)?;
            ensure_main_window(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            pick_input_pdf,
            choose_final_pdf_path,
            pick_input_document,
            pick_input_documents,
            pick_output_dir,
            default_output_dir_for_document,
            read_pdf_bytes,
            read_text_file,
            get_preview_workdir,
            create_canvas_launch_token,
            take_canvas_launch_payload,
            open_mask_canvas_window,
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
        .build(tauri::generate_context!())
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
            pdf_redaction: true,
            custom_keywords: String::new(),
            extract_engine: "auto".to_string(),
            profile: "official".to_string(),
            output_artifacts: "pdf_safe_report".to_string(),
            display_mode: "black".to_string(),
            deidentification_policy: "token".to_string(),
            region_scope: "all".to_string(),
            custom_regions: String::new(),
            return_text_preview: false,
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
            .register_native_save_target(&selected)
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
