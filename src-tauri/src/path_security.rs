use std::collections::{HashMap, HashSet};
use std::fmt;
use std::fs::{File, OpenOptions};
use std::io::{self, Seek};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

const MAX_NAME_COLLISIONS: u32 = 10_000;
const MAX_TEMP_NAME_ATTEMPTS: u32 = 100;
static TEMP_FILE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum SaveError {
    SourceRejected,
    OutputDirRejected,
    EscapesOutputDir,
    Io,
    NameCollisionExhausted,
    OverwriteConfirmationRequired,
    RestoreFailed,
}

impl fmt::Display for SaveError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self {
            Self::SourceRejected => "SAVE_SOURCE_REJECTED: 저장 원본을 확인할 수 없습니다.",
            Self::OutputDirRejected => "SAVE_OUTPUT_DIR_REJECTED: 저장 폴더를 확인할 수 없습니다.",
            Self::EscapesOutputDir => "SAVE_PATH_ESCAPE: 저장 경로가 허용된 폴더를 벗어났습니다.",
            Self::Io => "SAVE_IO: 파일 저장 처리에 실패했습니다.",
            Self::NameCollisionExhausted => {
                "SAVE_NAME_COLLISION: 사용 가능한 저장 이름을 만들 수 없습니다."
            }
            Self::OverwriteConfirmationRequired => {
                "SAVE_OVERWRITE_RECONFIRM_REQUIRED: PDF 확장자를 적용한 실제 저장 경로에 기존 파일이 있습니다. 저장 다이얼로그에서 .pdf 파일명을 직접 입력해 덮어쓰기를 다시 확인해 주세요."
            }
            Self::RestoreFailed => {
                "SAVE_RESTORE_FAILED: 기존 파일 복원에 실패했습니다. 저장 폴더의 복구 파일을 확인해 주세요."
            }
        };
        formatter.write_str(message)
    }
}

impl std::error::Error for SaveError {}

#[derive(Default)]
pub(crate) struct AllowedFileAccess {
    selected_files: Mutex<HashSet<PathBuf>>,
    disposable_artifacts: Mutex<HashSet<PathBuf>>,
    final_outputs: Mutex<HashSet<PathBuf>>,
    native_save_target: Mutex<Option<PendingNativeSaveTarget>>,
    artifact_dirs: Mutex<HashSet<PathBuf>>,
    masked_text_artifacts: Mutex<HashMap<PathBuf, MaskedTextProvenance>>,
    report_artifacts: Mutex<HashSet<PathBuf>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct MaskedTextProvenance {
    preview_pdf: PathBuf,
    policy: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum NativeSaveTargetBinding {
    Public {
        run_id: String,
        analysis_revision: u64,
        manifest_hash: String,
    },
    LegacyManual,
}

#[derive(Debug)]
pub(crate) struct NativeSaveTargetRegistration {
    pub(crate) output_path: PathBuf,
    pub(crate) save_token: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PendingNativeSaveTarget {
    output_path: PathBuf,
    save_token: String,
    overwrite_confirmed: bool,
    binding: NativeSaveTargetBinding,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ConsumedNativeSaveTarget {
    pub(crate) output_path: PathBuf,
    pub(crate) overwrite_confirmed: bool,
}

impl AllowedFileAccess {
    pub(crate) fn allow_pdf_path(&self, path: &Path) {
        if let Ok(canonical) = canonicalize_existing_file(path, "PDF 파일") {
            if has_extension(&canonical, &["pdf"]) {
                if let Ok(mut files) = self.selected_files.lock() {
                    files.insert(canonical);
                }
            }
        }
    }

    pub(crate) fn allow_document_path(&self, path: &Path) {
        if let Ok(canonical) = canonicalize_existing_file(path, "문서 파일") {
            if has_extension(&canonical, &["pdf"]) {
                if let Ok(mut files) = self.selected_files.lock() {
                    files.insert(canonical);
                }
            }
        }
    }

    pub(crate) fn allow_disposable_artifact_path(&self, path: &Path) {
        if let Ok(canonical) = canonicalize_existing_file(path, "임시 산출물") {
            let selected = self
                .selected_files
                .lock()
                .map(|files| files.contains(&canonical))
                .unwrap_or(true);
            if selected {
                return;
            }
            if let Ok(mut files) = self.disposable_artifacts.lock() {
                files.insert(canonical);
            }
        }
    }

    pub(crate) fn allow_final_output_path(&self, path: &Path) {
        if let Ok(canonical) = canonicalize_existing_file(path, "최종 산출물") {
            if let Ok(mut files) = self.final_outputs.lock() {
                files.insert(canonical);
            }
        }
    }
    pub(crate) fn register_native_save_target(
        &self,
        path: &Path,
        binding: NativeSaveTargetBinding,
    ) -> Result<NativeSaveTargetRegistration, String> {
        let normalized = normalize_pdf_save_path(path);
        let normalization_changed = normalized != path;
        let target = canonicalize_save_target(path)?;
        let target_exists = match std::fs::symlink_metadata(&target) {
            Ok(_) => true,
            Err(error) if error.kind() == io::ErrorKind::NotFound => false,
            Err(_) => return Err(SaveError::Io.to_string()),
        };
        if normalization_changed && target_exists {
            return Err(SaveError::OverwriteConfirmationRequired.to_string());
        }
        let save_token = native_save_target_token()?;
        let registration = PendingNativeSaveTarget {
            output_path: target.clone(),
            save_token: save_token.clone(),
            overwrite_confirmed: target_exists,
            binding,
        };
        *self
            .native_save_target
            .lock()
            .map_err(|_| "저장 대상 등록에 실패했습니다.".to_string())? = Some(registration);
        Ok(NativeSaveTargetRegistration {
            output_path: target,
            save_token,
        })
    }

    pub(crate) fn clear_native_save_target(&self) -> Result<(), String> {
        *self
            .native_save_target
            .lock()
            .map_err(|_| "저장 대상 등록 해제에 실패했습니다.".to_string())? = None;
        Ok(())
    }

    #[cfg(test)]
    fn validate_native_save_target(
        &self,
        path: &Path,
        save_token: &str,
        binding: &NativeSaveTargetBinding,
    ) -> Result<ConsumedNativeSaveTarget, String> {
        let target = canonicalize_save_target(path)?;
        let pending = self
            .native_save_target
            .lock()
            .map_err(|_| "저장 대상 확인에 실패했습니다.".to_string())?
            .clone()
            .ok_or_else(native_save_target_rejected)?;
        if !paths_equal(&pending.output_path, &target)
            || pending.save_token != save_token.trim()
            || pending.binding != *binding
        {
            return Err(native_save_target_rejected());
        }
        if !pending.overwrite_confirmed {
            match std::fs::symlink_metadata(&target) {
                Ok(_) => return Err(SaveError::OverwriteConfirmationRequired.to_string()),
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Err(_) => return Err(SaveError::Io.to_string()),
            }
        }
        Ok(ConsumedNativeSaveTarget {
            output_path: target,
            overwrite_confirmed: pending.overwrite_confirmed,
        })
    }

    fn reserve_native_save_target(
        &self,
        path: &Path,
        save_token: &str,
        binding: &NativeSaveTargetBinding,
    ) -> Result<ConsumedNativeSaveTarget, String> {
        let target = canonicalize_save_target(path)?;
        let mut target_slot = self
            .native_save_target
            .lock()
            .map_err(|_| "저장 대상 확인에 실패했습니다.".to_string())?;
        let pending = target_slot
            .as_ref()
            .ok_or_else(native_save_target_rejected)?;
        if !paths_equal(&pending.output_path, &target)
            || pending.save_token != save_token.trim()
            || pending.binding != *binding
        {
            return Err(native_save_target_rejected());
        }
        if !pending.overwrite_confirmed {
            match std::fs::symlink_metadata(&target) {
                Ok(_) => return Err(SaveError::OverwriteConfirmationRequired.to_string()),
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Err(_) => return Err(SaveError::Io.to_string()),
            }
        }
        let pending = target_slot.take().expect("pending target was checked");
        Ok(ConsumedNativeSaveTarget {
            output_path: target,
            overwrite_confirmed: pending.overwrite_confirmed,
        })
    }

    pub(crate) fn allow_masked_text_artifact_path(
        &self,
        path: &Path,
        preview_pdf: &Path,
        policy: &str,
    ) {
        let Ok(canonical) = canonicalize_existing_file(path, "비식별 TXT") else {
            return;
        };
        let Ok(preview) = canonicalize_existing_file(preview_pdf, "마스킹 미리보기") else {
            return;
        };
        if !has_extension(&canonical, &["txt"])
            || !generated_masked_text_name(&canonical)
            || !has_extension(&preview, &["pdf"])
            || !matches!(policy, "token" | "partial" | "pseudonym")
        {
            return;
        }
        if let Ok(mut artifacts) = self.masked_text_artifacts.lock() {
            artifacts.insert(
                canonical.clone(),
                MaskedTextProvenance {
                    preview_pdf: preview,
                    policy: policy.to_string(),
                },
            );
        }
        self.allow_disposable_artifact_path(&canonical);
    }

    pub(crate) fn allow_report_artifact_path(&self, path: &Path) {
        if let Ok(canonical) = canonicalize_existing_file(path, "안전 리포트") {
            if has_extension(&canonical, &["json"]) {
                if let Ok(mut reports) = self.report_artifacts.lock() {
                    reports.insert(canonical);
                }
            }
        }
        self.allow_disposable_artifact_path(path);
    }

    pub(crate) fn allow_artifact_dir(&self, path: &Path) {
        if let Ok(canonical) = canonicalize_existing_dir(path) {
            if let Ok(mut dirs) = self.artifact_dirs.lock() {
                dirs.insert(canonical);
            }
        }
    }

    pub(crate) fn pdf_is_allowed(&self, path: &Path) -> bool {
        self.registered_file_is_allowed(path) || self.path_is_in_allowed_artifact_dir(path)
    }

    pub(crate) fn artifact_is_allowed(&self, path: &Path) -> bool {
        self.path_is_in_allowed_artifact_dir(path)
    }

    pub(crate) fn document_is_allowed(&self, path: &Path) -> bool {
        self.registered_file_is_allowed(path) || self.path_is_in_allowed_artifact_dir(path)
    }

    pub(crate) fn disposable_artifact_is_allowed(&self, path: &Path) -> bool {
        let Ok(canonical) = canonicalize_existing_file(path, "임시 산출물") else {
            return false;
        };
        let selected = self
            .selected_files
            .lock()
            .map(|files| {
                files
                    .iter()
                    .any(|candidate| paths_equal(candidate, &canonical))
            })
            .unwrap_or(true);
        if selected {
            return false;
        }
        self.disposable_artifacts
            .lock()
            .map(|files| {
                files
                    .iter()
                    .any(|candidate| paths_equal(candidate, &canonical))
            })
            .unwrap_or(false)
    }

    pub(crate) fn masked_text_artifact_is_allowed_for_preview(
        &self,
        path: &Path,
        preview_pdf: &Path,
    ) -> bool {
        let Ok(canonical) = canonicalize_existing_file(path, "비식별 TXT") else {
            return false;
        };
        let Ok(preview) = canonicalize_existing_file(preview_pdf, "마스킹 미리보기") else {
            return false;
        };
        self.masked_text_artifacts
            .lock()
            .ok()
            .and_then(|artifacts| {
                artifacts
                    .iter()
                    .find(|(candidate, _)| paths_equal(candidate, &canonical))
                    .map(|(_, provenance)| provenance.clone())
            })
            .map(|provenance| {
                paths_equal(&provenance.preview_pdf, &preview)
                    && matches!(
                        provenance.policy.as_str(),
                        "token" | "partial" | "pseudonym"
                    )
            })
            .unwrap_or(false)
    }

    pub(crate) fn masked_text_artifact_is_allowed(&self, path: &Path) -> bool {
        let Ok(canonical) = canonicalize_existing_file(path, "비식별 TXT") else {
            return false;
        };
        self.masked_text_artifacts
            .lock()
            .map(|artifacts| {
                artifacts
                    .keys()
                    .any(|candidate| paths_equal(candidate, &canonical))
            })
            .unwrap_or(false)
    }

    pub(crate) fn report_artifact_is_allowed(&self, path: &Path) -> bool {
        let Ok(canonical) = canonicalize_existing_file(path, "안전 리포트") else {
            return false;
        };
        self.report_artifacts
            .lock()
            .map(|reports| {
                reports
                    .iter()
                    .any(|candidate| paths_equal(candidate, &canonical))
            })
            .unwrap_or(false)
    }

    fn registered_file_is_allowed(&self, path: &Path) -> bool {
        [
            &self.selected_files,
            &self.disposable_artifacts,
            &self.final_outputs,
        ]
        .into_iter()
        .any(|files| {
            files
                .lock()
                .map(|files| files.iter().any(|candidate| paths_equal(candidate, path)))
                .unwrap_or(false)
        })
    }

    fn path_is_in_allowed_artifact_dir(&self, path: &Path) -> bool {
        self.artifact_dirs
            .lock()
            .map(|dirs| dirs.iter().any(|dir| path_is_within(path, dir)))
            .unwrap_or(false)
    }
}

pub(crate) fn canonicalize_existing_file(path: &Path, label: &str) -> Result<PathBuf, String> {
    if path.as_os_str().is_empty() {
        return Err(format!("{label} 경로가 비어 있습니다."));
    }
    let meta = std::fs::symlink_metadata(path).map_err(|e| format!("{label} 확인 실패: {e}"))?;
    if meta.file_type().is_symlink() {
        return Err(format!("{label}은 심볼릭 링크를 사용할 수 없습니다."));
    }
    if !meta.is_file() {
        return Err(format!("{label}은 파일이어야 합니다."));
    }
    path.canonicalize()
        .map_err(|e| format!("{label} 경로 정규화 실패: {e}"))
}

pub(crate) fn canonicalize_existing_dir(path: &Path) -> Result<PathBuf, String> {
    canonicalize_existing_dir_with_policy(path, true)
}

fn canonicalize_existing_dir_with_policy(
    path: &Path,
    reject_final_symlink: bool,
) -> Result<PathBuf, String> {
    if path.as_os_str().is_empty() {
        return Err("폴더 경로가 비어 있습니다.".to_string());
    }
    let meta = std::fs::symlink_metadata(path).map_err(|e| format!("폴더 확인 실패: {e}"))?;
    if reject_final_symlink && meta.file_type().is_symlink() {
        return Err("심볼릭 링크 폴더는 사용할 수 없습니다.".to_string());
    }
    if !meta.is_dir() && !(meta.file_type().is_symlink() && !reject_final_symlink) {
        return Err("폴더 경로가 아닙니다.".to_string());
    }
    path.canonicalize()
        .map_err(|e| format!("폴더 경로 정규화 실패: {e}"))
}

fn canonicalize_existing_parent_dir(path: &Path) -> Result<PathBuf, String> {
    // The directory is an ancestor of the file being addressed, not the
    // caller's final path component. Resolve it before all I/O so a junction
    // or symlink in this component cannot remain as a writable raw alias.
    canonicalize_existing_dir_with_policy(path, false)
}

pub(crate) fn has_extension(path: &Path, allowed: &[&str]) -> bool {
    path.extension()
        .and_then(|s| s.to_str())
        .map(|ext| {
            allowed
                .iter()
                .any(|allowed_ext| ext.eq_ignore_ascii_case(allowed_ext))
        })
        .unwrap_or(false)
}

fn paths_equal(left: &Path, right: &Path) -> bool {
    #[cfg(windows)]
    {
        return path_components_equal(left, right);
    }
    #[cfg(not(windows))]
    {
        left == right
    }
}

fn path_is_within(path: &Path, root: &Path) -> bool {
    #[cfg(windows)]
    {
        let root_components: Vec<_> = root.components().collect();
        let path_components: Vec<_> = path.components().collect();
        root_components.len() <= path_components.len()
            && root_components
                .iter()
                .zip(path_components.iter())
                .all(|(left, right)| path_component_equal(left, right))
    }
    #[cfg(not(windows))]
    {
        path == root || path.starts_with(root)
    }
}

#[cfg(windows)]
fn path_components_equal(left: &Path, right: &Path) -> bool {
    let left_components: Vec<_> = left.components().collect();
    let right_components: Vec<_> = right.components().collect();
    left_components.len() == right_components.len()
        && left_components
            .iter()
            .zip(right_components.iter())
            .all(|(left, right)| path_component_equal(left, right))
}

#[cfg(windows)]
fn path_component_equal(left: &Component<'_>, right: &Component<'_>) -> bool {
    match (left, right) {
        (Component::Prefix(left), Component::Prefix(right)) => {
            left.as_os_str().to_string_lossy().to_lowercase()
                == right.as_os_str().to_string_lossy().to_lowercase()
        }
        (Component::Normal(left), Component::Normal(right)) => {
            left.to_string_lossy().to_lowercase() == right.to_string_lossy().to_lowercase()
        }
        _ => left == right,
    }
}
pub(crate) fn normalize_pdf_save_path(path: &Path) -> PathBuf {
    if has_extension(path, &["pdf"]) {
        return path.to_path_buf();
    }

    let mut normalized = path.as_os_str().to_os_string();
    normalized.push(".pdf");
    PathBuf::from(normalized)
}

fn canonicalize_save_target(path: &Path) -> Result<PathBuf, String> {
    let normalized = normalize_pdf_save_path(path);
    let file_name = normalized
        .file_name()
        .filter(|name| !name.is_empty())
        .ok_or_else(|| "저장 파일 이름이 비어 있습니다.".to_string())?;
    let parent = normalized
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .ok_or_else(|| "저장 대상의 상위 폴더를 확인할 수 없습니다.".to_string())?;
    let canonical_parent = canonicalize_existing_parent_dir(parent)?;
    let target = canonical_parent.join(file_name);

    match std::fs::symlink_metadata(&target) {
        Ok(meta) if meta.file_type().is_symlink() => {
            Err("저장 대상은 심볼릭 링크를 사용할 수 없습니다.".to_string())
        }
        Ok(meta) if !meta.is_file() => Err("저장 대상은 파일이어야 합니다.".to_string()),
        Ok(_) => Ok(target),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(target),
        Err(error) => Err(format!("저장 대상 확인 실패: {error}")),
    }
}

pub(crate) fn canonicalize_registered_native_save_target(
    access: &AllowedFileAccess,
    path: &str,
    save_token: &str,
) -> Result<ConsumedNativeSaveTarget, String> {
    access.reserve_native_save_target(
        Path::new(path.trim()),
        save_token,
        &NativeSaveTargetBinding::LegacyManual,
    )
}

#[cfg(test)]
pub(crate) fn validate_registered_native_save_target(
    access: &AllowedFileAccess,
    path: &str,
    save_token: &str,
    binding: &NativeSaveTargetBinding,
) -> Result<ConsumedNativeSaveTarget, String> {
    access.validate_native_save_target(Path::new(path.trim()), save_token, binding)
}

pub(crate) fn consume_registered_native_save_target(
    access: &AllowedFileAccess,
    path: &str,
    save_token: &str,
    binding: &NativeSaveTargetBinding,
) -> Result<ConsumedNativeSaveTarget, String> {
    access.reserve_native_save_target(Path::new(path.trim()), save_token, binding)
}

fn native_save_target_rejected() -> String {
    "저장 대상은 파일 선택기가 발급한 일회용 권한과 정확한 경로만 사용할 수 있습니다. 저장 다이얼로그를 다시 열어 주세요.".to_string()
}

fn native_save_target_token() -> Result<String, String> {
    let mut bytes = [0_u8; 16];
    getrandom::getrandom(&mut bytes)
        .map_err(|_| "저장 권한 토큰을 생성할 수 없습니다.".to_string())?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn generated_masked_text_name(path: &Path) -> bool {
    let name = path
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    name.contains(".masked.") || name.contains("_masked_") || name == "masked.txt"
}

pub(crate) fn canonicalize_registered_document(
    access: &AllowedFileAccess,
    path: &str,
    label: &str,
) -> Result<PathBuf, String> {
    let canonical = canonicalize_existing_file(Path::new(path.trim()), label)?;
    if !has_extension(&canonical, &["pdf"]) {
        return Err(format!("{label}은 PDF만 사용할 수 있습니다."));
    }
    if !access.document_is_allowed(&canonical) {
        return Err(format!(
            "{label}은 파일 선택기 또는 마스킹 산출물로 등록된 경로만 사용할 수 있습니다."
        ));
    }
    Ok(canonical)
}

pub(crate) fn canonicalize_registered_pdf(
    access: &AllowedFileAccess,
    path: &str,
    label: &str,
) -> Result<PathBuf, String> {
    let canonical = canonicalize_existing_file(Path::new(path.trim()), label)?;
    if !has_extension(&canonical, &["pdf"]) {
        return Err(format!("{label}은 PDF만 사용할 수 있습니다."));
    }
    if !access.pdf_is_allowed(&canonical) {
        return Err(format!(
            "{label}은 파일 선택기 또는 마스킹 산출물로 등록된 PDF만 사용할 수 있습니다."
        ));
    }
    Ok(canonical)
}

pub(crate) fn canonicalize_registered_artifact_dir(
    access: &AllowedFileAccess,
    path: &str,
) -> Result<PathBuf, String> {
    let canonical = canonicalize_existing_dir(Path::new(path.trim()))?;
    if !access.artifact_is_allowed(&canonical) {
        return Err(
            "출력 폴더는 폴더 선택기 또는 앱 작업폴더로 등록된 경로만 사용할 수 있습니다."
                .to_string(),
        );
    }
    Ok(canonical)
}

pub(crate) fn optional_registered_artifact(
    access: &AllowedFileAccess,
    path: Option<String>,
) -> Option<PathBuf> {
    let src = path?.trim().to_string();
    if src.is_empty() {
        return None;
    }
    let path_buf = canonicalize_existing_file(Path::new(&src), "부가 산출물").ok()?;
    if access.artifact_is_allowed(&path_buf) {
        Some(path_buf)
    } else {
        None
    }
}

pub(crate) fn optional_registered_masked_text_artifact(
    access: &AllowedFileAccess,
    path: Option<String>,
    preview_pdf: &Path,
) -> Option<PathBuf> {
    let src = path?.trim().to_string();
    if src.is_empty() {
        return None;
    }
    let path_buf = canonicalize_existing_file(Path::new(&src), "비식별 TXT").ok()?;
    access
        .masked_text_artifact_is_allowed_for_preview(&path_buf, preview_pdf)
        .then_some(path_buf)
}

pub(crate) fn optional_registered_report_artifact(
    access: &AllowedFileAccess,
    path: Option<String>,
) -> Option<PathBuf> {
    let src = path?.trim().to_string();
    if src.is_empty() {
        return None;
    }
    let path_buf = canonicalize_existing_file(Path::new(&src), "안전 리포트").ok()?;
    access
        .report_artifact_is_allowed(&path_buf)
        .then_some(path_buf)
}

pub(crate) fn remove_intermediate_file_if_outside_dir(
    access: &AllowedFileAccess,
    path: &Path,
    keep_dir: &Path,
) {
    if !path_is_within(path, keep_dir) && access.disposable_artifact_is_allowed(path) {
        let _ = std::fs::remove_file(path);
    }
}

struct PartialCopy {
    path: PathBuf,
    armed: bool,
}

impl PartialCopy {
    fn new(path: PathBuf) -> Self {
        Self { path, armed: true }
    }

    fn path(&self) -> &Path {
        &self.path
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for PartialCopy {
    fn drop(&mut self) {
        if self.armed {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

fn safe_name_component(value: &str) -> bool {
    let mut components = Path::new(value).components();
    matches!(components.next(), Some(Component::Normal(_))) && components.next().is_none()
}

fn destination_name(stem: &str, suffix: &str, ext: &str, timestamp: u64, index: u32) -> String {
    let counter = if index == 0 {
        String::new()
    } else {
        format!("_{index}")
    };
    if ext.is_empty() {
        format!("{stem}_{suffix}_{timestamp}{counter}")
    } else {
        format!("{stem}_{suffix}_{timestamp}{counter}.{ext}")
    }
}

fn create_partial_file(
    outdir: &Path,
    stem: &str,
    suffix: &str,
    timestamp: u64,
) -> Result<(PartialCopy, File), SaveError> {
    for _ in 0..MAX_TEMP_NAME_ATTEMPTS {
        let sequence = TEMP_FILE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let name = format!(
            ".{stem}_{suffix}_{timestamp}.{}.{}.part",
            std::process::id(),
            sequence
        );
        let path = outdir.join(name);
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((PartialCopy::new(path), file)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(_) => return Err(SaveError::Io),
        }
    }
    Err(SaveError::NameCollisionExhausted)
}

pub(crate) fn safe_copy_new(
    src: &Path,
    outdir: &Path,
    stem: &str,
    suffix: &str,
    ext: &str,
) -> Result<PathBuf, SaveError> {
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    safe_copy_new_at(src, outdir, stem, suffix, ext, timestamp)
}

pub(crate) fn safe_copy_new_at(
    src: &Path,
    outdir: &Path,
    stem: &str,
    suffix: &str,
    ext: &str,
    timestamp: u64,
) -> Result<PathBuf, SaveError> {
    safe_copy_new_at_with(src, outdir, stem, suffix, ext, timestamp, io::copy)
}

fn safe_copy_new_at_with<F>(
    src: &Path,
    outdir: &Path,
    stem: &str,
    suffix: &str,
    ext: &str,
    timestamp: u64,
    copy: F,
) -> Result<PathBuf, SaveError>
where
    F: FnOnce(&mut File, &mut File) -> io::Result<u64>,
{
    if !safe_name_component(stem)
        || !safe_name_component(suffix)
        || (!ext.is_empty() && !safe_name_component(ext))
    {
        return Err(SaveError::EscapesOutputDir);
    }

    let source =
        canonicalize_existing_file(src, "저장 원본").map_err(|_| SaveError::SourceRejected)?;
    let output_dir = canonicalize_existing_dir(outdir).map_err(|_| SaveError::OutputDirRejected)?;
    let mut source_file = File::open(&source).map_err(|_| SaveError::SourceRejected)?;
    let (mut partial, mut partial_file) =
        create_partial_file(&output_dir, stem, suffix, timestamp)?;
    copy(&mut source_file, &mut partial_file).map_err(|_| SaveError::Io)?;
    partial_file.sync_all().map_err(|_| SaveError::Io)?;
    drop(partial_file);

    for index in 0..MAX_NAME_COLLISIONS {
        let final_path = output_dir.join(destination_name(stem, suffix, ext, timestamp, index));
        match std::fs::hard_link(partial.path(), &final_path) {
            Ok(()) => {
                let final_abs = match final_path.canonicalize() {
                    Ok(path) => path,
                    Err(_) => {
                        let _ = std::fs::remove_file(&final_path);
                        return Err(SaveError::Io);
                    }
                };
                if !path_is_within(&final_abs, &output_dir) {
                    let _ = std::fs::remove_file(&final_path);
                    return Err(SaveError::EscapesOutputDir);
                }
                if std::fs::remove_file(partial.path()).is_err() {
                    let _ = std::fs::remove_file(&final_path);
                    return Err(SaveError::Io);
                }
                partial.disarm();
                return Ok(final_abs);
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(_) => return Err(SaveError::Io),
        }
    }
    Err(SaveError::NameCollisionExhausted)
}
#[derive(Debug)]
pub(crate) struct ExactOverwriteTransaction {
    final_path: PathBuf,
    backup_path: Option<PathBuf>,
}

impl ExactOverwriteTransaction {
    pub(crate) fn path(&self) -> &Path {
        &self.final_path
    }

    pub(crate) fn commit(self) -> Result<PathBuf, SaveError> {
        self.commit_with(
            |path| std::fs::remove_file(path),
            |path| std::fs::remove_file(path),
            |from, to| std::fs::rename(from, to),
        )
    }

    fn commit_with<R, D, M>(
        self,
        remove_backup: R,
        remove_target: D,
        restore_backup: M,
    ) -> Result<PathBuf, SaveError>
    where
        R: FnOnce(&Path) -> io::Result<()>,
        D: FnOnce(&Path) -> io::Result<()>,
        M: FnOnce(&Path, &Path) -> io::Result<()>,
    {
        let Some(backup_path) = self.backup_path else {
            return Ok(self.final_path);
        };
        if remove_backup(&backup_path).is_ok() {
            return Ok(self.final_path);
        }
        restore_overwritten_target(
            &self.final_path,
            &backup_path,
            remove_target,
            restore_backup,
        )?;
        Err(SaveError::Io)
    }

    pub(crate) fn rollback(self) -> Result<(), SaveError> {
        match self.backup_path {
            Some(backup_path) => restore_overwritten_target(
                &self.final_path,
                &backup_path,
                |path| std::fs::remove_file(path),
                |from, to| std::fs::rename(from, to),
            ),
            None => remove_file_if_present(&self.final_path).map_err(|_| SaveError::Io),
        }
    }
}

fn remove_file_if_present(path: &Path) -> io::Result<()> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn restore_overwritten_target<D, M>(
    final_path: &Path,
    backup_path: &Path,
    remove_target: D,
    restore_backup: M,
) -> Result<(), SaveError>
where
    D: FnOnce(&Path) -> io::Result<()>,
    M: FnOnce(&Path, &Path) -> io::Result<()>,
{
    let _ = remove_target;
    restore_backup(backup_path, final_path).map_err(|_| SaveError::RestoreFailed)
}

fn next_overwrite_backup_path(output_dir: &Path, file_name: &str) -> Result<PathBuf, SaveError> {
    for _ in 0..MAX_TEMP_NAME_ATTEMPTS {
        let sequence = TEMP_FILE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let candidate = output_dir.join(format!(
            ".{file_name}_backup.{}.{}.part",
            std::process::id(),
            sequence
        ));
        match std::fs::symlink_metadata(&candidate) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(candidate),
            Ok(_) => continue,
            Err(_) => return Err(SaveError::Io),
        }
    }
    Err(SaveError::NameCollisionExhausted)
}

pub(crate) fn stage_copy_overwrite_exact(
    src: &Path,
    target: &Path,
    overwrite_confirmed: bool,
) -> Result<ExactOverwriteTransaction, SaveError> {
    stage_copy_overwrite_exact_with(src, target, overwrite_confirmed, io::copy, |from, to| {
        std::fs::rename(from, to)
    })
}

pub(crate) fn stage_copy_overwrite_exact_from_file(
    source: &mut File,
    target: &Path,
    overwrite_confirmed: bool,
) -> Result<ExactOverwriteTransaction, SaveError> {
    stage_copy_overwrite_exact_file_with(
        source,
        target,
        overwrite_confirmed,
        io::copy,
        |from, to| std::fs::rename(from, to),
    )
}

fn stage_copy_overwrite_exact_with<C, M>(
    src: &Path,
    target: &Path,
    overwrite_confirmed: bool,
    copy_source: C,
    move_path: M,
) -> Result<ExactOverwriteTransaction, SaveError>
where
    C: FnOnce(&mut File, &mut File) -> io::Result<u64>,
    M: FnMut(&Path, &Path) -> io::Result<()>,
{
    let source =
        canonicalize_existing_file(src, "저장 원본").map_err(|_| SaveError::SourceRejected)?;
    let mut source_file = File::open(&source).map_err(|_| SaveError::SourceRejected)?;
    stage_copy_overwrite_exact_file_with(
        &mut source_file,
        target,
        overwrite_confirmed,
        copy_source,
        move_path,
    )
}

fn stage_copy_overwrite_exact_file_with<C, M>(
    source_file: &mut File,
    target: &Path,
    overwrite_confirmed: bool,
    copy_source: C,
    mut move_path: M,
) -> Result<ExactOverwriteTransaction, SaveError>
where
    C: FnOnce(&mut File, &mut File) -> io::Result<u64>,
    M: FnMut(&Path, &Path) -> io::Result<()>,
{
    let parent = target.parent().ok_or(SaveError::OutputDirRejected)?;
    let output_dir =
        canonicalize_existing_parent_dir(parent).map_err(|_| SaveError::OutputDirRejected)?;
    let file_name = target
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty())
        .ok_or(SaveError::EscapesOutputDir)?;
    let final_path = output_dir.join(file_name);
    let target_meta = std::fs::symlink_metadata(&final_path);
    let target_exists = target_meta.is_ok();
    if matches!(&target_meta, Ok(meta) if meta.file_type().is_symlink())
        || matches!(&target_meta, Ok(meta) if !meta.is_file())
    {
        return Err(SaveError::EscapesOutputDir);
    }
    if let Err(error) = target_meta {
        if error.kind() != io::ErrorKind::NotFound {
            return Err(SaveError::Io);
        }
    }
    if target_exists && !overwrite_confirmed {
        return Err(SaveError::OverwriteConfirmationRequired);
    }

    source_file
        .seek(std::io::SeekFrom::Start(0))
        .map_err(|_| SaveError::SourceRejected)?;
    let (mut partial, mut partial_file) =
        create_partial_file(&output_dir, file_name, "replace", 0)?;
    copy_source(source_file, &mut partial_file).map_err(|_| SaveError::Io)?;
    partial_file.sync_all().map_err(|_| SaveError::Io)?;
    drop(partial_file);
    let backup_path = if target_exists {
        let backup = next_overwrite_backup_path(&output_dir, file_name)?;
        // Keep the old destination linked until the new staged file atomically
        // replaces it. Moving the destination to a backup first creates an
        // observable/crash window where the destination does not exist.
        std::fs::hard_link(&final_path, &backup).map_err(|_| SaveError::Io)?;
        Some(backup)
    } else {
        None
    };
    if move_path(partial.path(), &final_path).is_err() {
        if let Some(backup) = backup_path.as_deref() {
            let _ = std::fs::remove_file(backup);
        }
        return Err(SaveError::Io);
    }
    partial.disarm();

    let final_path = final_path.canonicalize().map_err(|_| SaveError::Io)?;
    Ok(ExactOverwriteTransaction {
        final_path,
        backup_path,
    })
}

/// Copy an optional intermediate artifact into the output dir under a
/// `{stem}_{suffix}_{ts}` name. The caller owns source cleanup after the whole
/// finalize transaction commits, so a later copy failure remains retryable.
pub(crate) fn copy_optional_artifact(
    _access: &AllowedFileAccess,
    src_opt: Option<PathBuf>,
    suffix: &str,
    outdir: &Path,
    stem: &str,
    ts: u64,
) -> Result<Option<String>, SaveError> {
    let Some(src_path) = src_opt else {
        return Ok(None);
    };
    let ext = src_path.extension().and_then(|e| e.to_str()).unwrap_or("");
    let destination = safe_copy_new_at(&src_path, outdir, stem, suffix, ext, ts)?;
    Ok(Some(destination.display().to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io::{self, Write};

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
    fn registered_document_check_rejects_unselected_file() {
        let root = temp_security_root("unselected_doc");
        let document_path = root.join("sample.pdf");
        fs::write(&document_path, b"%PDF-1.4").expect("document");
        let access = AllowedFileAccess::default();

        let err =
            canonicalize_registered_document(&access, document_path.to_str().unwrap(), "입력 파일")
                .expect_err("unselected document should be blocked");

        assert!(err.contains("등록된 경로"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn registered_document_check_rejects_selected_hwpx() {
        let root = temp_security_root("selected_hwpx");
        let document_path = root.join("sample.hwpx");
        fs::write(&document_path, b"hwpx").expect("document");
        let access = AllowedFileAccess::default();
        access.allow_document_path(&document_path);

        let err =
            canonicalize_registered_document(&access, document_path.to_str().unwrap(), "입력 파일")
                .expect_err("selected hwpx should be blocked");

        assert!(err.contains("PDF만"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn output_dir_check_requires_registered_folder() {
        let root = temp_security_root("outdir");
        let access = AllowedFileAccess::default();

        let err = canonicalize_registered_artifact_dir(&access, root.to_str().unwrap())
            .expect_err("unselected outdir should be blocked");
        assert!(err.contains("등록된 경로"));

        access.allow_artifact_dir(&root);
        let allowed = canonicalize_registered_artifact_dir(&access, root.to_str().unwrap())
            .expect("selected outdir should pass");
        assert_eq!(allowed, root.canonicalize().expect("canonical"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn default_output_characterizes_existing_artifact_dir_registration() {
        let root = temp_security_root("default_output_artifact_registration");
        let artifact_dir = root.join("artifacts");
        let artifact_file = artifact_dir.join("preview.masked.pdf");
        fs::create_dir_all(&artifact_dir).expect("artifact dir");
        fs::write(&artifact_file, b"%PDF-1.4\n").expect("artifact file");
        let access = AllowedFileAccess::default();

        access.allow_artifact_dir(&artifact_dir);

        let canonical_dir = artifact_dir.canonicalize().expect("canonical artifact dir");
        let canonical_file = artifact_file.canonicalize().expect("canonical artifact");
        assert!(access.artifact_is_allowed(&canonical_dir));
        assert!(access.pdf_is_allowed(&canonical_file));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn intermediate_cleanup_removes_registered_temporary_file_outside_final_dir() {
        let root = temp_security_root("cleanup");
        let temp_dir = root.join("preview_runs");
        let final_dir = root.join("final");
        fs::create_dir_all(&temp_dir).expect("temp dir");
        fs::create_dir_all(&final_dir).expect("final dir");
        let intermediate = temp_dir.join("sample.masked.pdf");
        fs::write(&intermediate, b"%PDF-1.4\n").expect("intermediate");
        let access = AllowedFileAccess::default();
        access.allow_disposable_artifact_path(&intermediate);

        remove_intermediate_file_if_outside_dir(&access, &intermediate, &final_dir);

        assert!(!intermediate.exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn intermediate_cleanup_preserves_registered_user_source() {
        let root = temp_security_root("cleanup_selected_source");
        let source_dir = root.join("selected");
        let final_dir = root.join("final");
        fs::create_dir_all(&source_dir).expect("source dir");
        fs::create_dir_all(&final_dir).expect("final dir");
        let source = source_dir.join("source.pdf");
        fs::write(&source, b"%PDF-1.4\nselected").expect("source");
        let access = AllowedFileAccess::default();
        access.allow_pdf_path(&source);
        access.allow_disposable_artifact_path(&source);

        remove_intermediate_file_if_outside_dir(&access, &source, &final_dir);

        assert!(source.exists(), "selected input must never be deleted");
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn safe_copy_new_uses_counter_without_overwriting_existing_file() {
        let root = temp_security_root("safe_copy_collision");
        let source = root.join("source.pdf");
        let outdir = root.join("out");
        fs::create_dir_all(&outdir).expect("outdir");
        fs::write(&source, b"new-content").expect("source");
        let occupied = outdir.join("doc_final_masked_42.pdf");
        fs::write(&occupied, b"sentinel").expect("occupied");

        let copied = safe_copy_new_at(&source, &outdir, "doc", "final_masked", "pdf", 42)
            .expect("collision should use a counter");

        assert_eq!(
            copied.file_name().and_then(|name| name.to_str()),
            Some("doc_final_masked_42_1.pdf")
        );
        assert_eq!(fs::read(&occupied).expect("occupied bytes"), b"sentinel");
        assert_eq!(fs::read(&copied).expect("copied bytes"), b"new-content");
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn safe_copy_new_does_not_follow_existing_destination_symlink() {
        use std::os::unix::fs::symlink;

        let root = temp_security_root("safe_copy_symlink");
        let source = root.join("source.pdf");
        let outdir = root.join("out");
        let external = root.join("external.pdf");
        fs::create_dir_all(&outdir).expect("outdir");
        fs::write(&source, b"new-content").expect("source");
        fs::write(&external, b"sentinel").expect("external");
        symlink(&external, outdir.join("doc_final_masked_42.pdf")).expect("destination symlink");

        let copied = safe_copy_new_at(&source, &outdir, "doc", "final_masked", "pdf", 42)
            .expect("occupied symlink should be skipped safely");

        assert_eq!(
            copied.file_name().and_then(|name| name.to_str()),
            Some("doc_final_masked_42_1.pdf")
        );
        assert_eq!(fs::read(&external).expect("external bytes"), b"sentinel");
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn safe_copy_new_resolves_ancestor_alias_before_writing() {
        use std::os::unix::fs::symlink;

        let root = temp_security_root("safe_copy_ancestor_alias");
        let source = root.join("source.pdf");
        let physical = root.join("physical");
        let nested = physical.join("nested");
        let alias = root.join("alias");
        fs::create_dir_all(&nested).expect("nested output dir");
        symlink(&physical, &alias).expect("ancestor alias");
        fs::write(&source, b"new-content").expect("source");

        let copied = safe_copy_new_at(
            &source,
            &alias.join("nested"),
            "doc",
            "final_masked",
            "pdf",
            42,
        )
        .expect("an in-scope ancestor alias should be resolved");

        let canonical_nested = nested.canonicalize().expect("canonical nested output dir");
        assert!(copied.starts_with(&canonical_nested));
        assert_eq!(
            fs::read(canonical_nested.join(copied.file_name().unwrap())).unwrap(),
            b"new-content"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn exact_overwrite_resolves_symlinked_parent_before_writing() {
        use std::os::unix::fs::symlink;

        let root = temp_security_root("exact_overwrite_parent_alias");
        let source = root.join("source.pdf");
        let physical = root.join("physical");
        let alias = root.join("alias");
        fs::create_dir_all(&physical).expect("physical output dir");
        symlink(&physical, &alias).expect("parent alias");
        fs::write(&source, b"new snapshot").expect("source");

        let transaction = stage_copy_overwrite_exact(&source, &alias.join("selected.pdf"), false)
            .expect("an in-scope parent alias should be resolved");
        let published = transaction.commit().expect("publish");

        assert!(published.starts_with(&physical.canonicalize().unwrap()));
        assert_eq!(
            fs::read(physical.join("selected.pdf")).unwrap(),
            b"new snapshot"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn overwrite_copy_failure_before_swap_preserves_original() {
        let root = temp_security_root("overwrite_copy_failure")
            .canonicalize()
            .expect("canonical root");
        let source = root.join("source.pdf");
        let target = root.join("selected.pdf");
        fs::write(&source, b"new masked snapshot").expect("source");
        fs::write(&target, b"previous masked snapshot").expect("old target");

        let error = stage_copy_overwrite_exact_with(
            &source,
            &target,
            true,
            |_reader, writer| {
                writer.write_all(b"partial")?;
                Err(io::Error::other("injected copy failure"))
            },
            |from, to| fs::rename(from, to),
        )
        .expect_err("copy failure must abort before the original is moved");

        assert_eq!(error, SaveError::Io);
        assert_eq!(
            fs::read(&target).expect("unchanged target"),
            b"previous masked snapshot"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn overwrite_publish_failure_preserves_original_without_removal() {
        let root = temp_security_root("overwrite_publish_failure")
            .canonicalize()
            .expect("canonical root");
        let source = root.join("source.pdf");
        let target = root.join("selected.pdf");
        fs::write(&source, b"new masked snapshot").expect("source");
        fs::write(&target, b"previous masked snapshot").expect("old target");
        let mut rename_count = 0_u8;

        let error =
            stage_copy_overwrite_exact_with(&source, &target, true, io::copy, |_from, _to| {
                rename_count += 1;
                Err(io::Error::other("injected publish failure"))
            })
            .expect_err("publish failure must preserve the original");

        assert_eq!(error, SaveError::Io);
        assert_eq!(rename_count, 1, "only the atomic publish is attempted");
        assert_eq!(
            fs::read(&target).expect("preserved target"),
            b"previous masked snapshot"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn overwrite_commit_cleanup_failure_restores_original() {
        let root = temp_security_root("overwrite_commit_cleanup_failure")
            .canonicalize()
            .expect("canonical root");
        let source = root.join("source.pdf");
        let target = root.join("selected.pdf");
        fs::write(&source, b"new masked snapshot").expect("source");
        fs::write(&target, b"previous masked snapshot").expect("old target");
        let transaction = stage_copy_overwrite_exact(&source, &target, true)
            .expect("stage replacement transaction");

        let error = transaction
            .commit_with(
                |_backup| Err(io::Error::other("injected backup cleanup failure")),
                |path| fs::remove_file(path),
                |from, to| fs::rename(from, to),
            )
            .expect_err("commit cleanup failure must roll back the target");

        assert_eq!(error, SaveError::Io);
        assert_eq!(
            fs::read(&target).expect("restored target"),
            b"previous masked snapshot"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn safe_copy_failure_removes_partial_and_final_files() {
        let root = temp_security_root("safe_copy_interrupted");
        let source = root.join("source.pdf");
        let outdir = root.join("out");
        fs::create_dir_all(&outdir).expect("outdir");
        fs::write(&source, b"source-content").expect("source");

        let error = safe_copy_new_at_with(
            &source,
            &outdir,
            "doc",
            "final_masked",
            "pdf",
            42,
            |_reader, writer| {
                writer.write_all(b"partial")?;
                Err(io::Error::other("injected failure"))
            },
        )
        .expect_err("copy interruption must fail");

        assert_eq!(error, SaveError::Io);
        let names: Vec<String> = fs::read_dir(&outdir)
            .expect("outdir entries")
            .filter_map(Result::ok)
            .filter_map(|entry| entry.file_name().into_string().ok())
            .collect();
        assert!(
            names.is_empty(),
            "partial or final file remained: {names:?}"
        );
        assert_eq!(fs::read(&source).expect("source bytes"), b"source-content");
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn safe_copy_new_rejects_escape_components_without_writing() {
        let root = temp_security_root("safe_copy_escape");
        let source = root.join("source.pdf");
        let outdir = root.join("out");
        fs::create_dir_all(&outdir).expect("outdir");
        fs::write(&source, b"source-content").expect("source");

        let error = safe_copy_new_at(&source, &outdir, "../escape", "final_masked", "pdf", 42)
            .expect_err("unsafe stem must be rejected");

        assert_eq!(error, SaveError::EscapesOutputDir);
        assert!(
            fs::read_dir(&outdir)
                .expect("outdir entries")
                .next()
                .is_none(),
            "unsafe path must not create any file"
        );
        let _ = fs::remove_dir_all(root);
    }
    #[cfg(unix)]
    #[test]
    fn exact_overwrite_rejects_symlink_target_without_touching_external_file() {
        use std::os::unix::fs::symlink;

        let root = temp_security_root("exact_overwrite_symlink");
        let source = root.join("source.pdf");
        let external = root.join("external.pdf");
        let target = root.join("selected.pdf");
        fs::write(&source, b"new snapshot").expect("source");
        fs::write(&external, b"old external snapshot").expect("external");
        symlink(&external, &target).expect("target symlink");

        let error = stage_copy_overwrite_exact(&source, &target, true)
            .expect_err("symlink target must be rejected");
        assert_eq!(error, SaveError::EscapesOutputDir);
        assert_eq!(
            fs::read(&external).expect("external bytes"),
            b"old external snapshot"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn save_errors_never_include_document_names_or_paths() {
        let sensitive_fragments = ["person-name.pdf", "/private/source", "10,20,30,40"];

        for error in [
            SaveError::SourceRejected,
            SaveError::OutputDirRejected,
            SaveError::EscapesOutputDir,
            SaveError::Io,
            SaveError::NameCollisionExhausted,
            SaveError::OverwriteConfirmationRequired,
            SaveError::RestoreFailed,
        ] {
            let message = error.to_string();
            for fragment in sensitive_fragments {
                assert!(!message.contains(fragment));
            }
        }
    }
    #[test]
    fn native_save_target_uses_replaceable_one_shot_token() {
        let root = temp_security_root("native_save_target")
            .canonicalize()
            .expect("canonical root");
        let first = root.join("first.pdf");
        let selected = root.join("selected.pdf");
        let sibling = root.join("sibling.pdf");
        let access = AllowedFileAccess::default();

        assert_eq!(
            normalize_pdf_save_path(&root.join("report.txt")),
            root.join("report.txt.pdf"),
            "non-PDF extensions are appended rather than replaced"
        );
        assert_eq!(
            normalize_pdf_save_path(&root.join("report")),
            root.join("report.pdf"),
            "missing extensions are appended"
        );
        assert_eq!(
            normalize_pdf_save_path(&root.join("REPORT.PDF")),
            root.join("REPORT.PDF"),
            "PDF extensions are case-insensitive"
        );

        let first_registration = access
            .register_native_save_target(&first, NativeSaveTargetBinding::LegacyManual)
            .expect("first target must register");
        let registered = access
            .register_native_save_target(&selected, NativeSaveTargetBinding::LegacyManual)
            .expect("selected target must register");
        assert_eq!(registered.output_path, selected);
        assert_eq!(registered.save_token.len(), 32);
        assert!(registered
            .save_token
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')));
        assert_ne!(registered.save_token, first_registration.save_token);
        assert!(
            canonicalize_registered_native_save_target(
                &access,
                first_registration.output_path.to_str().unwrap(),
                &first_registration.save_token,
            )
            .is_err(),
            "a newer dialog selection must replace the old token"
        );
        let registered = access
            .register_native_save_target(&selected, NativeSaveTargetBinding::LegacyManual)
            .expect("selected target must register again after a consumed mismatch");
        assert_eq!(
            canonicalize_registered_native_save_target(
                &access,
                registered.output_path.to_str().unwrap(),
                &registered.save_token,
            )
            .expect("registered target must be consumable once")
            .output_path,
            registered.output_path,
        );
        assert!(
            canonicalize_registered_native_save_target(
                &access,
                registered.output_path.to_str().unwrap(),
                &registered.save_token,
            )
            .is_err(),
            "native save selection must not be reusable without reopening the dialog"
        );
        let sibling_registration = access
            .register_native_save_target(&selected, NativeSaveTargetBinding::LegacyManual)
            .expect("selected target must register for sibling rejection");
        assert!(
            canonicalize_registered_native_save_target(
                &access,
                sibling.to_str().unwrap(),
                &sibling_registration.save_token,
            )
            .is_err(),
            "a frontend-provided sibling was not selected natively"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn native_save_target_rejects_normalized_existing_file_without_os_confirmation() {
        let root = temp_security_root("native_save_normalized_collision")
            .canonicalize()
            .expect("canonical root");
        let selected = root.join("report.txt");
        fs::write(root.join("report.txt.pdf"), b"existing PDF").expect("existing normalized file");
        let access = AllowedFileAccess::default();

        let error = access
            .register_native_save_target(&selected, NativeSaveTargetBinding::LegacyManual)
            .expect_err("an unconfirmed normalized collision must be rejected");

        assert!(error.contains("SAVE_OVERWRITE_RECONFIRM_REQUIRED"));
        assert!(
            !error.contains("report.txt"),
            "the error must not expose the file name"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn clearing_native_save_target_invalidates_unconsumed_token() {
        let root = temp_security_root("native_save_clear")
            .canonicalize()
            .expect("canonical root");
        let selected = root.join("selected.pdf");
        let access = AllowedFileAccess::default();
        let registration = access
            .register_native_save_target(&selected, NativeSaveTargetBinding::LegacyManual)
            .expect("selected target must register");

        access
            .clear_native_save_target()
            .expect("clear registration");

        assert!(
            canonicalize_registered_native_save_target(
                &access,
                registration.output_path.to_str().unwrap(),
                &registration.save_token,
            )
            .is_err(),
            "cancel or replacement must invalidate the old token"
        );
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn public_native_save_target_requires_exact_tuple_and_consumes_only_after_match() {
        let root = temp_security_root("tuple_bound_native_save_target")
            .canonicalize()
            .expect("canonical root");
        let selected = root.join("selected.pdf");
        let access = AllowedFileAccess::default();
        let binding = NativeSaveTargetBinding::Public {
            run_id: "run-a".to_string(),
            analysis_revision: 7,
            manifest_hash: "manifest-a".to_string(),
        };
        let registration = access
            .register_native_save_target(&selected, binding.clone())
            .expect("register public target");

        let stale = NativeSaveTargetBinding::Public {
            run_id: "run-a".to_string(),
            analysis_revision: 6,
            manifest_hash: "manifest-a".to_string(),
        };
        assert!(validate_registered_native_save_target(
            &access,
            registration.output_path.to_str().unwrap(),
            &registration.save_token,
            &stale,
        )
        .is_err());
        validate_registered_native_save_target(
            &access,
            registration.output_path.to_str().unwrap(),
            &registration.save_token,
            &binding,
        )
        .expect("matching tuple must validate without consuming");
        consume_registered_native_save_target(
            &access,
            registration.output_path.to_str().unwrap(),
            &registration.save_token,
            &binding,
        )
        .expect("matching tuple consumes after commit");
        assert!(consume_registered_native_save_target(
            &access,
            registration.output_path.to_str().unwrap(),
            &registration.save_token,
            &binding,
        )
        .is_err());
        let _ = fs::remove_dir_all(root);
    }
}
