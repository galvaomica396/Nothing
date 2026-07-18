//! Retired coordinate-template IPC compatibility surface.
//!
//! The product feature was removed in v4.3.0. These commands deliberately keep
//! their existing names, parameters, and return shapes so older callers fail
//! closed with a stable error instead of observing a missing-command contract.

#![allow(dead_code)] // Compatibility DTO fields are deserialized but the feature never executes.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum CoordinateTemplateErrorCode {
    MalformedNormalizedRect,
    ForbiddenTemplateField,
    PageGeometryMismatch,
    TemplateSchemaMismatch,
    SymlinkRejected,
    UnsafeTemplateId,
    IoError,
    FeatureRetired,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct CoordinateTemplateError {
    pub(crate) code: CoordinateTemplateErrorCode,
    message: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct CoordinateTemplate {
    schema_version: u32,
    id: String,
    name: String,
    page_geometry: PageGeometry,
    rects: Vec<NormalizedRect>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct PageGeometry {
    page_count: u32,
    pages: Vec<PageGeometryEntry>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct PageGeometryEntry {
    page_index: u32,
    width: f64,
    height: f64,
    rotation: u16,
    crop_box: [f64; 4],
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct NormalizedRect {
    page_index: u32,
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
    tag: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CoordinateTemplateSummary {
    id: String,
    name: String,
    page_count: u32,
    rect_count: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CoordinateTemplateSaveResult {
    id: String,
    storage_key: String,
    saved: bool,
}

fn retired_error() -> CoordinateTemplateError {
    CoordinateTemplateError {
        code: CoordinateTemplateErrorCode::FeatureRetired,
        message: "좌표 템플릿 기능은 v4.3.0에서 제거되었습니다.".to_string(),
    }
}

#[tauri::command]
pub(crate) fn list_coordinate_templates(
    app: tauri::AppHandle,
) -> Result<Vec<CoordinateTemplateSummary>, CoordinateTemplateError> {
    let _ = app;
    Err(retired_error())
}

#[tauri::command]
pub(crate) fn load_coordinate_template(
    app: tauri::AppHandle,
    id: String,
) -> Result<CoordinateTemplate, CoordinateTemplateError> {
    let _ = (app, id);
    Err(retired_error())
}

#[tauri::command]
pub(crate) fn save_coordinate_template(
    app: tauri::AppHandle,
    template: serde_json::Value,
) -> Result<CoordinateTemplateSaveResult, CoordinateTemplateError> {
    let _ = (app, template);
    Err(retired_error())
}

#[tauri::command]
pub(crate) fn delete_coordinate_template(
    app: tauri::AppHandle,
    id: String,
) -> Result<(), CoordinateTemplateError> {
    let _ = (app, id);
    Err(retired_error())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn retired_template_wire_schema_remains_compatible() {
        let template = CoordinateTemplate {
            schema_version: 1,
            id: "template-1".to_string(),
            name: "template".to_string(),
            page_geometry: PageGeometry {
                page_count: 1,
                pages: vec![PageGeometryEntry {
                    page_index: 0,
                    width: 100.0,
                    height: 200.0,
                    rotation: 0,
                    crop_box: [0.0, 0.0, 100.0, 200.0],
                }],
            },
            rects: vec![NormalizedRect {
                page_index: 0,
                x0: 0.1,
                y0: 0.2,
                x1: 0.3,
                y1: 0.4,
                tag: Some("NAME".to_string()),
            }],
        };

        assert_eq!(
            serde_json::to_value(&template).expect("template serializes"),
            json!({
                "schemaVersion": 1,
                "id": "template-1",
                "name": "template",
                "pageGeometry": {
                    "pageCount": 1,
                    "pages": [{
                        "pageIndex": 0,
                        "width": 100.0,
                        "height": 200.0,
                        "rotation": 0,
                        "cropBox": [0.0, 0.0, 100.0, 200.0]
                    }]
                },
                "rects": [{
                    "pageIndex": 0,
                    "x0": 0.1,
                    "y0": 0.2,
                    "x1": 0.3,
                    "y1": 0.4,
                    "tag": "NAME"
                }]
            })
        );
        let with_unknown = json!({
            "schemaVersion": 1,
            "id": "template-1",
            "name": "template",
            "pageGeometry": { "pageCount": 0, "pages": [] },
            "rects": [],
            "unexpected": true
        });
        assert!(serde_json::from_value::<CoordinateTemplate>(with_unknown).is_err());
    }

    #[test]
    fn retired_template_summary_and_error_keys_remain_compatible() {
        let summary = CoordinateTemplateSummary {
            id: "template-1".to_string(),
            name: "template".to_string(),
            page_count: 1,
            rect_count: 2,
        };
        let save = CoordinateTemplateSaveResult {
            id: "template-1".to_string(),
            storage_key: "template-1.json".to_string(),
            saved: true,
        };

        assert_eq!(
            serde_json::to_value(summary).expect("summary serializes"),
            json!({ "id": "template-1", "name": "template", "pageCount": 1, "rectCount": 2 })
        );
        assert_eq!(
            serde_json::to_value(save).expect("save result serializes"),
            json!({ "id": "template-1", "storageKey": "template-1.json", "saved": true })
        );
        assert_eq!(
            serde_json::to_value(retired_error()).expect("error serializes")["code"],
            json!("feature_retired")
        );
    }
}
