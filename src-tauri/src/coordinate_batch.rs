//! Retired coordinate-batch IPC compatibility surface.
//!
//! The executable batch pipeline was removed in v4.3.0. Commands remain
//! registered with their original signatures and always return FeatureRetired.

#![allow(dead_code)] // Compatibility DTO fields are deserialized but the feature never executes.

use serde::{Deserialize, Serialize};

#[derive(Default)]
pub(crate) struct CoordinateBatchRegistry;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum CoordinateBatchErrorCode {
    ForbiddenPath,
    SymlinkRejected,
    TemplateInvalid,
    IoError,
    BatchAlreadyRunning,
    BatchNotFound,
    ExecutionFailed,
    FeatureRetired,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct CoordinateBatchError {
    pub(crate) code: CoordinateBatchErrorCode,
    message: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CoordinateBatchTargetListing {
    target_count: usize,
    targets: Vec<CoordinateBatchTarget>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CoordinateBatchTarget {
    id: String,
    name: String,
    size_bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum CoordinatePreflightStatus {
    Compatible,
    IncompatiblePageCount,
    IncompatiblePageSize,
    IncompatibleRotation,
    Encrypted,
    InvalidPdf,
    BoxOutOfBounds,
    OutputConflict,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CoordinateBatchPreflightTarget {
    id: String,
    name: String,
    page_count: Option<u32>,
    width: Option<f64>,
    height: Option<f64>,
    rotation: Option<u16>,
    encrypted: Option<bool>,
    invalid_pdf: Option<bool>,
    box_out_of_bounds: Option<bool>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CoordinateBatchPreflightItem {
    id: String,
    basename: String,
    status: CoordinatePreflightStatus,
    output_basename: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CoordinateBatchPreflightReport {
    status: CoordinatePreflightStatus,
    compatible_count: usize,
    blocked_count: usize,
    compatible_only: bool,
    targets: Vec<CoordinateBatchPreflightItem>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CoordinateBatchStartRequest {
    output_dir: String,
    template: serde_json::Value,
    display_mode: Option<String>,
    targets: Vec<CoordinateBatchExecutionTarget>,
    target_ids: Option<Vec<String>>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CoordinateBatchExecutionTarget {
    id: String,
    name: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CoordinateBatchCancelRequest {
    #[serde(alias = "runId")]
    session_id: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CoordinateBatchCancelResult {
    session_id: String,
    cancelled: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct CoordinateBatchFileResult {
    id: String,
    input_basename: String,
    output_basename: String,
    status: String,
    error_code: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CoordinateBatchRunResult {
    #[serde(rename = "sessionId", alias = "runId")]
    session_id: String,
    status: String,
    total: usize,
    completed: usize,
    failed: usize,
    cancelled: usize,
    result_basename: String,
    event_basename: String,
    files: Vec<CoordinateBatchFileResult>,
}

// Keep the original Rust type paths visible in the command signatures. The
// executable modules were retired, but callers and source-level contract checks
// still observe `preflight::...` / `execution::...` as part of the frozen IPC
// surface.
mod preflight {
    pub(crate) type CoordinateBatchTargetListing = super::CoordinateBatchTargetListing;
    pub(crate) type CoordinateBatchPreflightTarget = super::CoordinateBatchPreflightTarget;
    pub(crate) type CoordinateBatchPreflightReport = super::CoordinateBatchPreflightReport;
}

mod execution {
    pub(crate) type CoordinateBatchStartRequest = super::CoordinateBatchStartRequest;
    pub(crate) type CoordinateBatchCancelRequest = super::CoordinateBatchCancelRequest;
    pub(crate) type CoordinateBatchCancelResult = super::CoordinateBatchCancelResult;
    pub(crate) type CoordinateBatchRunResult = super::CoordinateBatchRunResult;
}

fn retired_error() -> CoordinateBatchError {
    CoordinateBatchError {
        code: CoordinateBatchErrorCode::FeatureRetired,
        message: "좌표 템플릿 기능은 v4.3.0에서 제거되었습니다.".to_string(),
    }
}

#[tauri::command]
pub(crate) fn enumerate_coordinate_batch_targets(
    access: tauri::State<'_, crate::AllowedFileAccess>,
    target_folder: String,
) -> Result<preflight::CoordinateBatchTargetListing, CoordinateBatchError> {
    let _ = (access, target_folder);
    Err(retired_error())
}

#[tauri::command]
pub(crate) fn preflight_coordinate_batch(
    access: tauri::State<'_, crate::AllowedFileAccess>,
    template: serde_json::Value,
    targets: Vec<preflight::CoordinateBatchPreflightTarget>,
    output_dir: String,
    compatible_only: Option<bool>,
) -> Result<preflight::CoordinateBatchPreflightReport, CoordinateBatchError> {
    let _ = (access, template, targets, output_dir, compatible_only);
    Err(retired_error())
}

#[tauri::command]
pub(crate) fn start_coordinate_batch(
    app: tauri::AppHandle,
    access: tauri::State<'_, crate::AllowedFileAccess>,
    registry: tauri::State<'_, CoordinateBatchRegistry>,
    request: execution::CoordinateBatchStartRequest,
) -> Result<execution::CoordinateBatchRunResult, CoordinateBatchError> {
    let _ = (app, access, registry, request);
    Err(retired_error())
}

#[tauri::command]
pub(crate) fn cancel_coordinate_batch(
    registry: tauri::State<'_, CoordinateBatchRegistry>,
    request: execution::CoordinateBatchCancelRequest,
) -> Result<execution::CoordinateBatchCancelResult, CoordinateBatchError> {
    let _ = (registry, request);
    Err(retired_error())
}

#[tauri::command]
pub(crate) fn retry_coordinate_batch(
    app: tauri::AppHandle,
    access: tauri::State<'_, crate::AllowedFileAccess>,
    registry: tauri::State<'_, CoordinateBatchRegistry>,
    request: execution::CoordinateBatchStartRequest,
) -> Result<execution::CoordinateBatchRunResult, CoordinateBatchError> {
    let _ = (app, access, registry, request);
    Err(retired_error())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn retired_preflight_wire_schema_remains_compatible() {
        let statuses = [
            (CoordinatePreflightStatus::Compatible, "compatible"),
            (
                CoordinatePreflightStatus::IncompatiblePageCount,
                "incompatible_page_count",
            ),
            (
                CoordinatePreflightStatus::IncompatiblePageSize,
                "incompatible_page_size",
            ),
            (
                CoordinatePreflightStatus::IncompatibleRotation,
                "incompatible_rotation",
            ),
            (CoordinatePreflightStatus::Encrypted, "encrypted"),
            (CoordinatePreflightStatus::InvalidPdf, "invalid_pdf"),
            (
                CoordinatePreflightStatus::BoxOutOfBounds,
                "box_out_of_bounds",
            ),
            (CoordinatePreflightStatus::OutputConflict, "output_conflict"),
        ];
        for (status, expected) in statuses {
            assert_eq!(
                serde_json::to_value(status).expect("status serializes"),
                json!(expected)
            );
        }

        let listing = CoordinateBatchTargetListing {
            target_count: 1,
            targets: vec![CoordinateBatchTarget {
                id: "target-1".to_string(),
                name: "target.pdf".to_string(),
                size_bytes: 42,
            }],
        };
        assert_eq!(
            serde_json::to_value(listing).expect("listing serializes"),
            json!({
                "targetCount": 1,
                "targets": [{ "id": "target-1", "name": "target.pdf", "sizeBytes": 42 }]
            })
        );

        let target: CoordinateBatchPreflightTarget = serde_json::from_value(json!({
            "id": "target-1",
            "name": "target.pdf",
            "pageCount": 1,
            "width": 100.0,
            "height": 200.0,
            "rotation": 0,
            "encrypted": false,
            "invalidPdf": false,
            "boxOutOfBounds": false
        }))
        .expect("preflight target deserializes");
        assert_eq!(target.id, "target-1");
        assert_eq!(target.page_count, Some(1));
        assert_eq!(target.box_out_of_bounds, Some(false));

        let report = CoordinateBatchPreflightReport {
            status: CoordinatePreflightStatus::Compatible,
            compatible_count: 1,
            blocked_count: 0,
            compatible_only: true,
            targets: vec![CoordinateBatchPreflightItem {
                id: "target-1".to_string(),
                basename: "target.pdf".to_string(),
                status: CoordinatePreflightStatus::Compatible,
                output_basename: "target_masked.pdf".to_string(),
            }],
        };
        assert_eq!(
            serde_json::to_value(report).expect("report serializes"),
            json!({
                "status": "compatible",
                "compatibleCount": 1,
                "blockedCount": 0,
                "compatibleOnly": true,
                "targets": [{
                    "id": "target-1",
                    "basename": "target.pdf",
                    "status": "compatible",
                    "outputBasename": "target_masked.pdf"
                }]
            })
        );
    }

    #[test]
    fn retired_execution_wire_schema_remains_compatible() {
        let request: CoordinateBatchStartRequest = serde_json::from_value(json!({
            "outputDir": "/registered-output",
            "template": { "schemaVersion": 1 },
            "displayMode": "black",
            "targets": [{ "id": "target-1", "name": "target.pdf" }],
            "targetIds": ["target-1"]
        }))
        .expect("start request deserializes");
        assert_eq!(request.output_dir, "/registered-output");
        assert_eq!(request.display_mode.as_deref(), Some("black"));
        assert_eq!(request.targets[0].id, "target-1");
        assert_eq!(request.target_ids, Some(vec!["target-1".to_string()]));

        for key in ["sessionId", "runId"] {
            let cancel: CoordinateBatchCancelRequest =
                serde_json::from_value(json!({ key: "run-1" }))
                    .expect("cancel request deserializes");
            assert_eq!(cancel.session_id, "run-1");
        }
        let cancel_result = CoordinateBatchCancelResult {
            session_id: "run-1".to_string(),
            cancelled: true,
        };
        assert_eq!(
            serde_json::to_value(cancel_result).expect("cancel result serializes"),
            json!({ "sessionId": "run-1", "cancelled": true })
        );

        let run_result = CoordinateBatchRunResult {
            session_id: "run-1".to_string(),
            status: "completed".to_string(),
            total: 1,
            completed: 1,
            failed: 0,
            cancelled: 0,
            result_basename: "results.json".to_string(),
            event_basename: "events.jsonl".to_string(),
            files: vec![CoordinateBatchFileResult {
                id: "target-1".to_string(),
                input_basename: "target.pdf".to_string(),
                output_basename: "target_masked.pdf".to_string(),
                status: "completed".to_string(),
                error_code: None,
            }],
        };
        assert_eq!(
            serde_json::to_value(run_result).expect("run result serializes"),
            json!({
                "sessionId": "run-1",
                "status": "completed",
                "total": 1,
                "completed": 1,
                "failed": 0,
                "cancelled": 0,
                "resultBasename": "results.json",
                "eventBasename": "events.jsonl",
                "files": [{
                    "id": "target-1",
                    "inputBasename": "target.pdf",
                    "outputBasename": "target_masked.pdf",
                    "status": "completed",
                    "errorCode": null
                }]
            })
        );
        assert_eq!(
            serde_json::to_value(retired_error()).expect("error serializes")["code"],
            json!("feature_retired")
        );
    }
}
