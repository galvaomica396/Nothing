use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::{mpsc, Arc, Mutex, OnceLock};
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager, Runtime};

const QA_DRIVE_FLAG: &str = "--qa-drive-stdin";
// QA_DRIVE_OPEN_TIMEOUT is loaded from the canonical timeout contract below.

#[derive(Deserialize)]
struct QaDriveTimeoutConfig {
    startup_ms: u64,
    open_ms: u64,
    control_ms: u64,
    navigation_ms: u64,
    long_ms: u64,
}

fn timeout_config() -> &'static QaDriveTimeoutConfig {
    static CONFIG: OnceLock<QaDriveTimeoutConfig> = OnceLock::new();
    CONFIG.get_or_init(|| {
        serde_json::from_str(include_str!("../../contracts/qa-drive-timeouts.json"))
            .expect("QA drive timeout config must be valid")
    })
}

fn timeout_ms(value: u64) -> Duration {
    Duration::from_millis(value)
}

#[derive(Clone, Default)]
pub(crate) struct Bridge {
    responses: Arc<Mutex<HashMap<String, mpsc::Sender<Response>>>>,
    ready: Arc<Mutex<Option<mpsc::Sender<()>>>>,
}

#[derive(Deserialize)]
pub(crate) struct Response {
    id: String,
    ok: bool,
    #[serde(default)]
    state: serde_json::Value,
    #[serde(default)]
    error: String,
    #[serde(default)]
    trace: serde_json::Value,
}

impl Bridge {
    pub(crate) fn receive(&self, response: Response) -> Result<(), String> {
        if response.id == "ready" {
            let mut ready = self
                .ready
                .lock()
                .map_err(|_| "QA_DRIVE_STATE_UNAVAILABLE".to_string())?;
            if let Some(sender) = ready.take() {
                let _ = sender.send(());
            }
            return Ok(());
        }

        let mut responses = self
            .responses
            .lock()
            .map_err(|_| "QA_DRIVE_STATE_UNAVAILABLE".to_string())?;
        if let Some(sender) = responses.remove(&response.id) {
            let _ = sender.send(response);
        }
        Ok(())
    }

    fn forget(&self, id: &str) {
        if let Ok(mut responses) = self.responses.lock() {
            responses.remove(id);
        }
    }
}

#[derive(Clone, Serialize)]
struct Command<'a> {
    id: &'a str,
    command: &'a str,
}

#[derive(Serialize)]
struct Transcript<'a> {
    id: &'a str,
    command: &'a str,
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    state: Option<&'a serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    trace: Option<&'a serde_json::Value>,
}

pub(crate) fn is_enabled(
    args: impl IntoIterator<Item = impl AsRef<str>>,
    environment: Option<&str>,
) -> bool {
    environment == Some("1")
        && args
            .into_iter()
            .any(|argument| argument.as_ref() == QA_DRIVE_FLAG)
}

pub(crate) fn allowed_document_path(path: &str) -> Result<PathBuf, String> {
    let canonical = crate::canonicalize_existing_file(Path::new(path.trim()), "QA 입력 PDF")?;
    if !crate::has_extension(&canonical, &["pdf"]) {
        return Err("QA_DRIVE_DOCUMENT_INVALID".to_string());
    }
    let allowed = std::env::var_os("MASK_TOOL_ALLOWED_DIRS")
        .map(|value| std::env::split_paths(&value).collect::<Vec<_>>())
        .unwrap_or_default()
        .into_iter()
        .filter_map(|root| crate::canonicalize_existing_dir(&root).ok())
        .any(|root| canonical.starts_with(root));
    if !allowed {
        return Err("QA_DRIVE_DOCUMENT_OUTSIDE_ALLOWED_DIRS".to_string());
    }
    Ok(canonical)
}

fn command_kind(command: &str) -> &str {
    command.split_whitespace().next().unwrap_or("unknown")
}

fn command_timeout(command: &str) -> (Duration, &'static str) {
    match command_kind(command) {
        "open" => (timeout_ms(timeout_config().open_ms), "native_open_wait"),
        "apply-keyword" | "run-masking" | "wait-idle" | "resolve-review" | "apply-manual"
        | "confirm-save" | "wait-save" | "save-final" => {
            (timeout_ms(timeout_config().long_ms), "native_long_running")
        }
        "go-page" | "scroll-to" | "inspect-target" | "resolve-geometry" => (
            timeout_ms(timeout_config().navigation_ms),
            "native_navigation",
        ),
        "set-profile"
        | "set-tool"
        | "start-masking"
        | "render-probe"
        | "set-overlay"
        | "draw-box"
        | "drag-canvas"
        | "open-save-dialog"
        | "close-success-dialog"
        | "dump-state" => (timeout_ms(timeout_config().control_ms), "native_control"),
        _ => (timeout_ms(timeout_config().control_ms), "native_command"),
    }
}

fn native_timeout_error(command: &str, timeout_stage: &str) -> String {
    if command_kind(command) == "open" {
        format!(
            "QA_DRIVE_RENDER_UNAVAILABLE:stage={timeout_stage}:command={}",
            command_kind(command)
        )
    } else {
        format!(
            "QA_DRIVE_COMMAND_TIMEOUT:stage={timeout_stage}:command={}",
            command_kind(command)
        )
    }
}

pub(crate) fn start<R: Runtime>(app: AppHandle<R>, bridge: Bridge) {
    thread::spawn(move || dispatch_from_stdin(app, bridge));
}

fn dispatch_from_stdin<R: Runtime>(app: AppHandle<R>, bridge: Bridge) {
    let (ready_sender, ready_receiver) = mpsc::channel();
    if let Ok(mut ready) = bridge.ready.lock() {
        *ready = Some(ready_sender);
    } else {
        write_startup_error("QA_DRIVE_STATE_UNAVAILABLE");
        return;
    }
    if ready_receiver
        .recv_timeout(timeout_ms(timeout_config().startup_ms))
        .is_err()
    {
        write_startup_error("QA_DRIVE_FRONTEND_NOT_READY");
        return;
    }
    write_transcript(
        "ready",
        &Response {
            id: "ready".to_string(),
            ok: true,
            state: serde_json::Value::Null,
            error: String::new(),
            trace: serde_json::Value::Null,
        },
    );

    for (sequence, line) in std::io::stdin().lock().lines().enumerate() {
        let Ok(command) = line else {
            write_startup_error("QA_DRIVE_STDIN_UNREADABLE");
            return;
        };
        let command = command.trim();
        if command.is_empty() {
            continue;
        }
        let id = format!("qa-{sequence}");
        let (sender, receiver) = mpsc::channel();
        if let Ok(mut responses) = bridge.responses.lock() {
            responses.insert(id.clone(), sender);
        } else {
            write_command_error_transcript(
                command,
                id,
                "QA_DRIVE_STATE_UNAVAILABLE:stage=native_response_registry",
            );
            return;
        }
        if app
            .emit("qa-drive-command", Command { id: &id, command })
            .is_err()
        {
            bridge.forget(&id);
            write_command_error_transcript(
                command,
                id,
                "QA_DRIVE_FRONTEND_UNAVAILABLE:stage=native_emit",
            );
            return;
        }
        let (timeout, timeout_stage) = command_timeout(command);
        match receiver.recv_timeout(timeout) {
            Ok(mut response) => {
                let command_timed_out = response.error.starts_with("QA_DRIVE_COMMAND_TIMEOUT:")
                    || response.error.starts_with("QA_DRIVE_RENDER_UNAVAILABLE:")
                    || response
                        .error
                        .starts_with("QA_DRIVE_RENDER_CANCEL_TIMEOUT:")
                    || response.error.starts_with("QA_DRIVE_COMMAND_CANCELLED:");
                add_main_window_bounds(&app, &mut response);
                write_transcript(command, &response);
                if command_timed_out {
                    return;
                }
            }
            Err(_) => {
                let _ = app.emit("qa-drive-cancel", Command { id: &id, command });
                bridge.forget(&id);
                write_command_error_transcript(
                    command,
                    id,
                    &native_timeout_error(command, timeout_stage),
                );
                return;
            }
        }
    }
}

fn add_main_window_bounds<R: Runtime>(app: &AppHandle<R>, response: &mut Response) {
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    let (Ok(position), Ok(size)) = (window.outer_position(), window.outer_size()) else {
        return;
    };
    let Some(state) = response.state.as_object_mut() else {
        return;
    };
    state.insert(
        "windowBounds".to_string(),
        serde_json::json!({
            "x": position.x,
            "y": position.y,
            "width": size.width,
            "height": size.height,
        }),
    );
}

fn write_transcript(command: &str, response: &Response) {
    let transcript = Transcript {
        id: &response.id,
        command,
        ok: response.ok,
        state: response.ok.then_some(&response.state),
        error: (!response.ok).then_some(response.error.as_str()),
        trace: (!response.trace.is_null()).then_some(&response.trace),
    };
    match serde_json::to_string(&transcript) {
        Ok(line) => {
            let _ = writeln!(std::io::stdout(), "{line}");
        }
        Err(_) => write_startup_error("QA_DRIVE_TRANSCRIPT_SERIALIZATION_FAILED"),
    }
}

fn write_command_error_transcript(command: &str, id: String, error: &str) {
    write_transcript(
        command,
        &Response {
            id,
            ok: false,
            state: serde_json::Value::Null,
            error: error.to_string(),
            trace: serde_json::Value::Null,
        },
    );
}

fn write_startup_error(code: &str) {
    let _ = writeln!(std::io::stderr(), "{code}");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_drive_command_class_has_a_finite_deadline() {
        let commands = [
            "open /tmp/input.pdf",
            "set-profile mixed",
            "apply-keyword token",
            "set-tool mask",
            "start-masking",
            "run-masking",
            "wait-idle",
            "render-probe on",
            "go-page 1",
            "scroll-to 1 0 0 10 10",
            "inspect-target 0 0 10 10",
            "set-overlay visible",
            "resolve-review review-1 mask",
            "resolve-geometry first 0 0 10 10",
            "draw-box 0 0 10 10",
            "drag-canvas 0 0 10 10",
            "apply-manual",
            "open-save-dialog",
            "confirm-save",
            "close-success-dialog",
            "save-final",
            "dump-state",
        ];
        for command in commands {
            let (timeout, stage) = command_timeout(command);
            assert!(!timeout.is_zero(), "{command} has no deadline");
            assert!(!stage.is_empty(), "{command} has no timeout stage");
        }
    }

    #[test]
    fn open_timeout_is_longer_than_control_timeout_and_has_a_stage() {
        let (open_timeout, open_stage) = command_timeout("open /tmp/input.pdf");
        let (control_timeout, control_stage) = command_timeout("dump-state");
        assert!(open_timeout > control_timeout);
        assert_eq!(open_stage, "native_open_wait");
        assert_eq!(control_stage, "native_control");
    }

    #[test]
    fn open_native_deadline_is_reported_as_render_unavailable() {
        let error = native_timeout_error("open /tmp/input.pdf", "native_open_wait");
        assert_eq!(
            error,
            "QA_DRIVE_RENDER_UNAVAILABLE:stage=native_open_wait:command=open"
        );
    }
}
