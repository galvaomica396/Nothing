use std::path::{Path, PathBuf};
use tauri::Manager;

#[derive(Debug, Clone)]
pub(crate) struct RuntimePaths {
    pub(crate) repo_root: PathBuf,
    pub(crate) pipeline_script: PathBuf,
    pub(crate) manual_boxes_script: PathBuf,
    pub(crate) masking_engine: Option<PathBuf>,
    pub(crate) development_discovery: bool,
}

fn masking_engine_for_root(root: &Path) -> Option<PathBuf> {
    let exe_name = if cfg!(windows) {
        "masking_engine.exe"
    } else {
        "masking_engine"
    };
    [
        root.join("bin").join(exe_name),
        root.join("masking_runtime/bin").join(exe_name),
        root.join("dist").join(exe_name),
        root.join("dist/masking_engine").join(exe_name),
        root.join("masking_engine.exe"),
    ]
    .into_iter()
    .find(|path| path.exists())
}

fn script_dir_for_root(root: &Path) -> Option<PathBuf> {
    [
        root.join("masking_runtime/tauri_frontend/scripts"),
        root.join("tauri_frontend/scripts"),
        root.join("scripts"),
    ]
    .into_iter()
    .find(|dir| {
        dir.join("run_masking_pipeline.py").exists() && dir.join("apply_manual_boxes.py").exists()
    })
}

fn runtime_paths_for_root(root: PathBuf, development_discovery: bool) -> RuntimePaths {
    let script_dir =
        script_dir_for_root(&root).unwrap_or_else(|| root.join("tauri_frontend/scripts"));
    RuntimePaths {
        pipeline_script: script_dir.join("run_masking_pipeline.py"),
        manual_boxes_script: script_dir.join("apply_manual_boxes.py"),
        masking_engine: masking_engine_for_root(&root),
        repo_root: root,
        development_discovery,
    }
}

fn runtime_root_candidates(cwd: Option<PathBuf>) -> Result<Vec<PathBuf>, String> {
    let mut candidates = Vec::new();

    let cwd = match cwd {
        Some(cwd) => Some(cwd),
        None => std::env::current_dir().ok(),
    };
    if let Some(cwd) = cwd {
        for p in [
            cwd.clone(),
            cwd.join(".."),
            cwd.join("../.."),
            cwd.join("../../.."),
        ] {
            candidates.push(p);
        }
    }

    let exe = std::env::current_exe().map_err(|e| format!("실행 파일 경로 확인 실패: {e}"))?;
    for anc in exe.ancestors() {
        candidates.push(anc.to_path_buf());
    }

    Ok(candidates)
}

fn has_runtime_files(root: &Path) -> bool {
    (root.join("document_masker_ocr_gui.py").exists() && script_dir_for_root(root).is_some())
        || masking_engine_for_root(root).is_some()
}

fn has_lifecycle_runtime_files(root: &Path) -> bool {
    root.join("document_masker_ocr_gui.py").exists() && script_dir_for_root(root).is_some()
}

fn resolve_runtime_paths_from(
    resource_dir: Option<PathBuf>,
    cwd: Option<PathBuf>,
) -> Result<RuntimePaths, String> {
    if let Some(resource_dir) = resource_dir {
        return Ok(runtime_paths_for_root(
            resource_dir.join("masking_runtime"),
            false,
        ));
    }
    let mut tried = Vec::new();
    if cfg!(debug_assertions) {
        for candidate in runtime_root_candidates(cwd)? {
            let normalized = candidate.canonicalize().unwrap_or(candidate);
            if tried.iter().any(|p: &PathBuf| p == &normalized) {
                continue;
            }
            tried.push(normalized.clone());
            if has_runtime_files(&normalized) {
                return Ok(runtime_paths_for_root(normalized, true));
            }
        }
    }

    let tried_msg = tried
        .into_iter()
        .map(|p| p.display().to_string())
        .collect::<Vec<_>>()
        .join(" | ");
    Err(format!(
        "마스킹 런타임을 찾지 못했습니다. 탐색경로: {tried_msg}"
    ))
}

pub(crate) fn resolve_runtime_paths(app: &tauri::AppHandle) -> Result<RuntimePaths, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .ok()
        .filter(|dir| has_runtime_files(&dir.join("masking_runtime")));
    resolve_runtime_paths_from(resource_dir, None)
}

fn path_is_safe_within(root: &Path, path: &Path) -> bool {
    let Ok(root) = root.canonicalize() else {
        return false;
    };
    let Ok(relative) = path.strip_prefix(&root) else {
        return false;
    };
    let mut current = root.clone();
    if std::fs::symlink_metadata(&current)
        .map(|metadata| metadata.file_type().is_symlink())
        .unwrap_or(true)
    {
        return false;
    }
    for component in relative.components() {
        current.push(component);
        if std::fs::symlink_metadata(&current)
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(true)
        {
            return false;
        }
    }
    path.canonicalize()
        .map(|canonical| canonical.starts_with(&root))
        .unwrap_or(false)
}

fn lifecycle_runtime_is_safe(root: &Path) -> bool {
    if root.is_symlink() || !has_lifecycle_runtime_files(root) {
        return false;
    }
    let Ok(root) = root.canonicalize() else {
        return false;
    };
    let Some(script_dir) = script_dir_for_root(&root) else {
        return false;
    };
    [
        root.join("document_masker_ocr_gui.py"),
        script_dir.join("run_masking_pipeline.py"),
        script_dir.join("apply_manual_boxes.py"),
    ]
    .iter()
    .all(|path| path.is_file() && path_is_safe_within(&root, path))
}

fn resolve_lifecycle_runtime_paths_from(
    resource_dir: Option<PathBuf>,
    cwd: Option<PathBuf>,
) -> Result<RuntimePaths, String> {
    if let Some(resource_dir) = resource_dir.filter(|dir| lifecycle_runtime_is_safe(dir)) {
        let root = resource_dir
            .canonicalize()
            .map_err(|_| "MASKING_SESSION_ANALYZER_UNAVAILABLE".to_string())?;
        return Ok(runtime_paths_for_root(root, false));
    }

    // Repository/ancestor discovery is development-only. Production execution
    // never falls back to cwd-controlled code.
    if cfg!(debug_assertions) {
        for candidate in runtime_root_candidates(cwd)? {
            if lifecycle_runtime_is_safe(&candidate) {
                let root = candidate
                    .canonicalize()
                    .map_err(|_| "MASKING_SESSION_ANALYZER_UNAVAILABLE".to_string())?;
                return Ok(runtime_paths_for_root(root, true));
            }
        }
    }
    Err("MASKING_SESSION_ANALYZER_UNAVAILABLE".to_string())
}

pub(crate) fn resolve_lifecycle_runtime_paths(
    app: &tauri::AppHandle,
) -> Result<RuntimePaths, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .ok()
        .map(|dir| dir.join("masking_runtime"));
    resolve_lifecycle_runtime_paths_from(resource_dir, None)
}

fn python_candidates(root: &Path) -> Vec<(PathBuf, Vec<String>)> {
    vec![
        (root.join(".venv/bin/python"), vec![]),
        (root.join(".venv/Scripts/python.exe"), vec![]),
        (PathBuf::from("/opt/homebrew/bin/python3"), vec![]),
        (PathBuf::from("/usr/local/bin/python3"), vec![]),
        (PathBuf::from("python3"), vec![]),
        (PathBuf::from("python.exe"), vec![]),
        (PathBuf::from("py"), vec!["-3".to_string()]),
    ]
}
pub(crate) fn lifecycle_command(
    runtime: &RuntimePaths,
    mode: &str,
) -> Result<std::process::Command, String> {
    if !matches!(mode, "analyze" | "trusted-finalize")
        || !path_is_safe_within(&runtime.repo_root, &runtime.pipeline_script)
    {
        return Err("MASKING_SESSION_ANALYZER_UNAVAILABLE".to_string());
    }
    if !runtime.development_discovery {
        let engine = runtime
            .masking_engine
            .as_ref()
            .ok_or_else(|| "MASKING_SESSION_ANALYZER_UNAVAILABLE".to_string())?;
        if !engine.is_file() || !path_is_safe_within(&runtime.repo_root, engine) {
            return Err("MASKING_SESSION_ANALYZER_UNAVAILABLE".to_string());
        }
        let mut command = std::process::Command::new(engine);
        command
            .arg("--repo-root")
            .arg(&runtime.repo_root)
            .arg("--mode")
            .arg(mode);
        return Ok(command);
    }
    let (python, args) = resolve_python(&runtime.repo_root)
        .map_err(|_| "MASKING_SESSION_ANALYZER_UNAVAILABLE".to_string())?;
    let mut command = std::process::Command::new(python);
    command
        .args(args)
        .arg(&runtime.pipeline_script)
        .arg("--repo-root")
        .arg(&runtime.repo_root)
        .arg("--mode")
        .arg(mode);
    Ok(command)
}

pub(crate) fn resolve_python(root: &Path) -> Result<(PathBuf, Vec<String>), String> {
    python_candidates(root)
        .into_iter()
        .find(|(path, _args)| {
            if path.components().count() == 1 {
                true
            } else {
                path.exists()
            }
        })
        .ok_or_else(|| "python 실행파일을 찾지 못했습니다.".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_runtime_root(name: &str, script_subdir: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "makiiing_runtime_test_{}_{}",
            name,
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join(script_subdir)).expect("script dir");
        fs::write(root.join("document_masker_ocr_gui.py"), "").expect("engine");
        fs::write(root.join(script_subdir).join("run_masking_pipeline.py"), "").expect("pipeline");
        fs::write(root.join(script_subdir).join("apply_manual_boxes.py"), "").expect("manual");
        root
    }

    #[test]
    fn resolves_bundled_resources_runtime() {
        let resource_dir =
            std::env::temp_dir().join(format!("makiiing_resources_test_{}", std::process::id()));
        let _ = fs::remove_dir_all(&resource_dir);
        fs::create_dir_all(resource_dir.join("masking_runtime/tauri_frontend/scripts"))
            .expect("script dir");
        fs::write(
            resource_dir.join("masking_runtime/document_masker_ocr_gui.py"),
            "",
        )
        .expect("engine");
        fs::write(
            resource_dir.join("masking_runtime/tauri_frontend/scripts/run_masking_pipeline.py"),
            "",
        )
        .expect("pipeline");
        fs::write(
            resource_dir.join("masking_runtime/tauri_frontend/scripts/apply_manual_boxes.py"),
            "",
        )
        .expect("manual");
        let runtime = resolve_runtime_paths_from(Some(resource_dir), None).expect("runtime paths");

        assert!(runtime.repo_root.ends_with("masking_runtime"));
        assert!(runtime
            .pipeline_script
            .ends_with("tauri_frontend/scripts/run_masking_pipeline.py"));
        let _ = fs::remove_dir_all(runtime.repo_root.parent().unwrap_or(&runtime.repo_root));
    }

    #[cfg(debug_assertions)]
    #[test]
    fn resolves_development_scripts_runtime() {
        let root = temp_runtime_root("dev", "scripts");
        let runtime = resolve_runtime_paths_from(None, Some(root.clone())).expect("runtime paths");
        assert_eq!(
            runtime.repo_root,
            root.canonicalize().expect("canonical root")
        );
        assert!(runtime
            .pipeline_script
            .ends_with("scripts/run_masking_pipeline.py"));
        let _ = fs::remove_dir_all(runtime.repo_root);
    }

    #[cfg(not(debug_assertions))]
    #[test]
    fn release_runtime_resolution_does_not_discover_cwd() {
        let root = temp_runtime_root("release", "scripts");
        let result = resolve_runtime_paths_from(None, Some(root.clone()));
        assert!(result.is_err());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn python_candidates_include_windows_launchers() {
        let root = PathBuf::from("C:/app");
        let candidates = python_candidates(&root);
        assert!(candidates
            .iter()
            .any(|(path, _)| path.ends_with(".venv/Scripts/python.exe")));
        assert!(candidates
            .iter()
            .any(|(path, _)| path == Path::new("python.exe")));
        assert!(candidates
            .iter()
            .any(|(path, args)| path == Path::new("py") && args == &vec!["-3".to_string()]));
    }

    #[test]
    fn packaged_engine_can_satisfy_runtime_files() {
        let root = std::env::temp_dir().join(format!(
            "makiiing_engine_runtime_test_{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("root");
        fs::write(root.join("masking_engine.exe"), "").expect("engine");
        assert!(has_runtime_files(&root));
        let runtime = runtime_paths_for_root(root.clone(), true);
        assert_eq!(
            runtime.masking_engine,
            Some(root.join("masking_engine.exe"))
        );
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn engine_only_runtime_rejects_authoritative_lifecycle_modes() {
        let root = std::env::temp_dir().join(format!(
            "masking_engine_lifecycle_test_{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("root");
        fs::write(root.join("masking_engine.exe"), "").expect("engine");
        let runtime = runtime_paths_for_root(root.clone(), true);
        assert!(lifecycle_command(&runtime, "analyze").is_err());
        assert!(lifecycle_command(&runtime, "trusted-finalize").is_err());
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn packaged_engine_owns_authoritative_lifecycle_modes_when_scripts_are_present() {
        let root = temp_runtime_root("packaged_lifecycle", "tauri_frontend/scripts");
        fs::create_dir_all(root.join("bin")).expect("engine dir");
        let engine = root.join("bin").join(if cfg!(windows) {
            "masking_engine.exe"
        } else {
            "masking_engine"
        });
        fs::write(&engine, "").expect("packaged engine");
        let runtime =
            runtime_paths_for_root(root.canonicalize().expect("canonical runtime root"), false);

        let command = lifecycle_command(&runtime, "analyze").expect("packaged lifecycle command");
        assert_eq!(
            command.get_program(),
            runtime.masking_engine.as_ref().expect("engine").as_os_str()
        );
        assert!(!command
            .get_args()
            .any(|arg| arg == runtime.pipeline_script.as_os_str()));
        assert!(command.get_args().any(|arg| arg == "--mode"));
        assert!(command.get_args().any(|arg| arg == "analyze"));
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn development_lifecycle_prefers_live_script_over_stale_engine() {
        let root = temp_runtime_root("live_lifecycle", "scripts");
        fs::create_dir_all(root.join("bin")).expect("engine dir");
        fs::create_dir_all(root.join(".venv/bin")).expect("python dir");
        fs::write(
            root.join("bin").join(if cfg!(windows) {
                "masking_engine.exe"
            } else {
                "masking_engine"
            }),
            "",
        )
        .expect("stale engine");
        fs::write(root.join(".venv/bin/python"), "").expect("python");
        let runtime =
            runtime_paths_for_root(root.canonicalize().expect("canonical runtime root"), true);

        let command = lifecycle_command(&runtime, "analyze").expect("development command");
        assert_eq!(
            command.get_program(),
            runtime.repo_root.join(".venv/bin/python").as_os_str()
        );
        assert!(command
            .get_args()
            .any(|arg| arg == runtime.pipeline_script.as_os_str()));
        let _ = fs::remove_dir_all(root);
    }
    #[cfg(debug_assertions)]
    #[test]
    fn lifecycle_resolution_skips_engine_only_root_for_packaged_scripts() {
        let root = temp_runtime_root("lifecycle", "scripts");
        let engine_only = root.join("engine-only");
        fs::create_dir_all(&engine_only).expect("engine root");
        fs::write(engine_only.join("masking_engine.exe"), "").expect("engine");
        fs::create_dir_all(root.join(".venv/bin")).expect("python dir");
        let python = root.join(".venv/bin/python");
        fs::write(&python, "").expect("python");
        let runtime = resolve_lifecycle_runtime_paths_from(None, Some(engine_only))
            .expect("lifecycle runtime");

        assert_eq!(
            runtime.repo_root,
            root.canonicalize().expect("canonical root")
        );
        let command = lifecycle_command(&runtime, "trusted-finalize").expect("lifecycle command");
        assert_eq!(
            command.get_program(),
            python.canonicalize().expect("canonical python").as_os_str()
        );
        assert!(command
            .get_args()
            .any(|arg| arg == runtime.pipeline_script.as_os_str()));
        let _ = fs::remove_dir_all(root);
    }
}
