use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct ApplyResult {
    pub(crate) status: Option<String>,
    pub(crate) output_file: String,
    pub(crate) mask_count: usize,
    pub(crate) restore_count: usize,
    pub(crate) applied_count: Option<usize>,
    pub(crate) excluded_count: Option<usize>,
    pub(crate) mask_boxes_applied: Option<usize>,
    pub(crate) unmask_boxes_applied: Option<usize>,
    pub(crate) skipped_boxes: Option<usize>,
    pub(crate) warnings: Option<Vec<String>>,
    pub(crate) requires_revalidation: Option<bool>,
    pub(crate) display_mode: Option<String>,
    pub(crate) revalidation_report: Option<String>,
    pub(crate) revalidation_status: Option<String>,
}

/// 자문(권고) 분류기 — 더 이상 저장 게이트가 아니다.
///
/// 리포트가 깨끗하면(quality_gate_passed && missing_targets==0 && residual_hits==0,
/// 그리고 manual_revalidation 블록이 있으면 passed) `Ok(())`, 아니면 사람이 읽을 수
/// 있는 사유와 함께 `Err` 를 돌려준다.
///
/// v4.2.0 정책: 최종 저장은 항상 사용자 재량이다. 이 함수는 `finalize_manual_output`
/// 를 차단하지 않으며, 다음 두 자문 용도로만 쓰인다:
///
/// 1. `apply_manual_boxes` 가 복원 재검증 리포트의 passed 여부를 계산할 때, 기준
///    리포트가 이미 깨끗했는지 판정하는 기준.
/// 2. 프론트가 경고를 산출할 때 참고하는 "권고" 신호.
///
/// 반환하는 `Err` 는 저장 금지가 아니라 "리포트가 깨끗하지 않음(경고 필요)" 을 뜻한다.
pub(crate) fn report_allows_final_save(report_path: Option<&Path>) -> Result<(), String> {
    let Some(path) = report_path else {
        return Err("safe_report 확인 후에만 최종 저장할 수 있습니다.".to_string());
    };
    let report_text = std::fs::read_to_string(path)
        .map_err(|_| "SAFE_REPORT_READ_FAILED: 안전 리포트를 읽을 수 없습니다.".to_string())?;
    let report: serde_json::Value = serde_json::from_str(&report_text).map_err(|_| {
        "SAFE_REPORT_PARSE_FAILED: 안전 리포트 형식이 올바르지 않습니다.".to_string()
    })?;
    let checks = report
        .get("product_checks")
        .unwrap_or(&serde_json::Value::Null);
    let quality_gate_passed = checks
        .get("quality_gate_passed")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    let active_redaction = report
        .get("document_redaction")
        .or_else(|| report.get("pdf_redaction"))
        .unwrap_or(&serde_json::Value::Null);
    let missing_targets = report
        .get("missing_targets_count")
        .or_else(|| active_redaction.get("missing_targets_count"))
        .and_then(serde_json::Value::as_i64)
        .unwrap_or(1);
    let residual_hits = report
        .get("document_redaction")
        .or_else(|| report.get("pdf_redaction"))
        .and_then(|redaction| redaction.get("verification"))
        .and_then(|verification| verification.get("residual_hits"))
        .and_then(serde_json::Value::as_i64)
        .unwrap_or(1);
    let manual_revalidation = report
        .get("manual_revalidation")
        .unwrap_or(&serde_json::Value::Null);
    let manual_revalidation_present = manual_revalidation.is_object();
    let manual_revalidation_passed = manual_revalidation
        .get("status")
        .and_then(serde_json::Value::as_str)
        == Some("passed")
        && manual_revalidation
            .get("verified")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);

    if manual_revalidation_present && !manual_revalidation_passed {
        return Err("자동 검증을 통과한 문서만 최종 저장할 수 있습니다. 검토 큐와 안전 리포트를 먼저 확인하세요.".to_string());
    }

    // needs_manual_review는 하드 차단이 아니라 자문(권고) 플래그다. 엔진(masking_rules.py)은
    // NAME/ADDRESS 등 민감 태그가 탐지되면 레닥션이 완전히 성공해도 이 플래그를 켜지만,
    // 스스로 final_submission_allowed = quality_gate_passed 로 제출을 허용한다. 프론트
    // 게이트도 이를 자문으로 취급한다. 따라서 저장을 실제로 막는 하드 차단은
    // quality_gate_passed=false / missing_targets>0 / residual_hits>0 세 가지뿐이며,
    // needs_manual_review는 이 판정에서 제외해 엔진·프론트와 정합을 맞춘다.
    if quality_gate_passed && missing_targets == 0 && residual_hits == 0 {
        return Ok(());
    }

    Err("자동 검증을 통과한 문서만 최종 저장할 수 있습니다. 검토 큐와 안전 리포트를 먼저 확인하세요.".to_string())
}

/// One restore box's page-local rectangle (PDF points, top-left origin), matching
/// the coordinate space of both the manual-box application and the base safe
/// report's redaction bboxes.
#[derive(Debug, Clone, Copy)]
pub(crate) struct RestoreRect {
    pub(crate) page: u32,
    pub(crate) x0: f64,
    pub(crate) y0: f64,
    pub(crate) x1: f64,
    pub(crate) y1: f64,
}

fn rects_intersect(a: (f64, f64, f64, f64), b: (f64, f64, f64, f64)) -> bool {
    let (ax0, ax1) = (a.0.min(a.2), a.0.max(a.2));
    let (ay0, ay1) = (a.1.min(a.3), a.1.max(a.3));
    let (bx0, bx1) = (b.0.min(b.2), b.0.max(b.2));
    let (by0, by1) = (b.1.min(b.3), b.1.max(b.3));
    ax0 < bx1 && bx0 < ax1 && ay0 < by1 && by0 < ay1
}

/// A base-report review item that represents a region the base masking actually
/// redacted (has a bbox on a concrete page). Residual/missing entries carry no
/// applied rectangle, so they are excluded.
fn base_redaction_rects(report: &serde_json::Value) -> Vec<(u32, f64, f64, f64, f64)> {
    let mut rects = Vec::new();
    let candidates = report
        .get("review_items")
        .and_then(serde_json::Value::as_array)
        .into_iter()
        .flatten()
        .chain(
            report
                .get("document_redaction")
                .or_else(|| report.get("pdf_redaction"))
                .and_then(|redaction| redaction.get("review_items"))
                .and_then(serde_json::Value::as_array)
                .into_iter()
                .flatten(),
        );
    for item in candidates {
        // Only regions that were actually redacted (applied or flagged for review
        // but present) re-expose PII when restored. Residual/missing markers have
        // no covering rectangle.
        let status = item
            .get("status")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("");
        if matches!(status, "residual_found" | "missing_pdf_rect") {
            continue;
        }
        let Some(page) = item.get("page").and_then(serde_json::Value::as_u64) else {
            continue;
        };
        let Some(bbox) = item.get("bbox").filter(|value| value.is_object()) else {
            continue;
        };
        let x = bbox.get("x").and_then(serde_json::Value::as_f64);
        let y = bbox.get("y").and_then(serde_json::Value::as_f64);
        let w = bbox.get("width").and_then(serde_json::Value::as_f64);
        let h = bbox.get("height").and_then(serde_json::Value::as_f64);
        if let (Some(x), Some(y), Some(w), Some(h)) = (x, y, w, h) {
            rects.push((page as u32, x, y, x + w, y + h));
        }
    }
    rects
}

/// Real restore revalidation: does any restore box re-expose a region that the
/// base masking redacted? A restore copies original content back, so overlapping
/// an applied redaction re-exposes the PII that was masked there. Restoring a
/// never-masked region changes nothing sensitive.
///
/// Safety-conservative: if the base report is absent or unparseable we cannot
/// prove the restore is safe, so we treat it as a re-exposure (block).
pub(crate) fn restore_reexposes_masked_region(
    base_report_path: Option<&Path>,
    restore_rects: &[RestoreRect],
) -> bool {
    if restore_rects.is_empty() {
        return false;
    }
    let Some(path) = base_report_path else {
        return true;
    };
    let Ok(text) = std::fs::read_to_string(path) else {
        return true;
    };
    let Ok(report) = serde_json::from_str::<serde_json::Value>(&text) else {
        return true;
    };
    let redaction_rects = base_redaction_rects(&report);
    if redaction_rects.is_empty() {
        return true;
    }
    for restore in restore_rects {
        for (page, mx0, my0, mx1, my1) in &redaction_rects {
            if *page != restore.page {
                continue;
            }
            if rects_intersect(
                (restore.x0, restore.y0, restore.x1, restore.y1),
                (*mx0, *my0, *mx1, *my1),
            ) {
                return true;
            }
        }
    }
    false
}

/// Writes the manual-revalidation safe report for a restore-bearing apply. When
/// `passed` the report classifies the restore as clean (re-exposed no masked
/// region); otherwise it classifies it as a re-exposure. v4.2.0 정책상 이 리포트는
/// 저장을 막지 않는다 — 프론트가 경고를 보여주는 자문 신호이며, 사용자가 "그대로
/// 저장"을 확정하면 finalize 는 그대로 진행된다. 복원이 적용된 apply 에서만 생성되고,
/// 마스킹만 추가한 보정은 기준 리포트를 건드리지 않는다.
pub(crate) fn write_manual_revalidation_report(
    output_dir: &Path,
    manual_result: &ApplyResult,
    passed: bool,
) -> Result<PathBuf, String> {
    let report_path = output_dir.join("manual_revalidation.safe_report.json");
    let report_abs = report_path
        .parent()
        .and_then(|parent| parent.canonicalize().ok())
        .map(|parent| parent.join(report_path.file_name().unwrap_or_default()))
        .unwrap_or_else(|| report_path.clone());
    if !report_abs.starts_with(output_dir) {
        return Err("수동 재검증 리포트 경로 검증 실패".to_string());
    }

    let mask_applied = manual_result
        .mask_boxes_applied
        .or(manual_result.applied_count)
        .unwrap_or(0);
    let restore_applied = manual_result.unmask_boxes_applied.unwrap_or(0);
    let skipped_boxes = manual_result.skipped_boxes.unwrap_or(0);
    let manual_status = if passed { "passed" } else { "failed" };
    let redaction_status = if passed {
        "manual_revalidated"
    } else {
        "manual_revalidation_failed"
    };
    let verification_reason_code = if passed {
        "manual_restore_verified"
    } else {
        "manual_restore_review_required"
    };
    let warning_count = manual_result.warnings.as_ref().map(Vec::len).unwrap_or(0);
    let report = serde_json::json!({
        "raw_values_saved": false,
        "raw_text_returned": false,
        "manual_revalidation": {
            "status": manual_status,
            "verified": passed,
            "output_file_saved_in_report": false,
            "mask_boxes_applied": mask_applied,
            "restore_boxes_applied": restore_applied,
            "skipped_boxes": skipped_boxes,
            "warning_count": warning_count
        },
        "document_redaction": {
            "status": redaction_status,
            "missing_targets_count": if passed { 0 } else { 1 },
            "verification": {
                "verified": passed,
                "residual_hits": if passed { 0 } else { 1 },
                "reason_code": verification_reason_code
            }
        },
        "product_checks": {
            "quality_gate_passed": passed,
            "needs_manual_review": !passed,
            "final_submission_allowed": passed
        },
        "review_items": if passed {
            serde_json::json!([])
        } else {
            serde_json::json!([{
                "page": null,
                "tag": "MANUAL",
                "display_token": "[MASK]",
                "status": "needs_review",
                "count": 1,
                "raw_value_saved": false
            }])
        }
    });
    std::fs::write(&report_path, report.to_string()).map_err(|_| {
        "MANUAL_REVALIDATION_WRITE_FAILED: 재검증 리포트를 저장할 수 없습니다.".to_string()
    })?;
    Ok(report_path)
}

#[cfg(test)]
mod tests;
