use super::*;
use std::fs;

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

fn write_report(path: &Path, quality_gate_passed: bool, needs_manual_review: bool) {
    let report = serde_json::json!({
        "product_checks": {
            "quality_gate_passed": quality_gate_passed,
            "needs_manual_review": needs_manual_review
        },
        "missing_targets_count": 0,
        "document_redaction": {
            "verification": {
                "residual_hits": 0
            }
        }
    });
    fs::write(path, report.to_string()).expect("report");
}

// NOTE: 아래 report_allows_final_save 테스트들은 v4.2.0 정책상 "저장 차단"이 아니라
// 자문(권고) 분류기의 판정을 검증한다. `Err` = "리포트가 깨끗하지 않음(경고 필요)"이며
// finalize 를 막지 않는다. finalize 가 리포트 내용과 무관하게 저장을 진행한다는 회귀
// 가드는 lib.rs 의 finalize_manual_output_path 테스트가 담당한다.

#[test]
fn advisory_classifier_flags_manual_review_report_as_not_clean() {
    let root = temp_security_root("advisory_not_clean");
    let report_path = root.join("sample.safe_report.json");
    write_report(&report_path, false, true);

    let err = report_allows_final_save(Some(&report_path))
        .expect_err("dirty report should classify as not clean (advisory)");

    assert!(err.contains("자동 검증을 통과한 문서만"));
    let _ = fs::remove_dir_all(root);
}

#[test]
fn advisory_classifier_marks_clean_report_as_clean() {
    let root = temp_security_root("advisory_clean");
    let report_path = root.join("sample.safe_report.json");
    write_report(&report_path, true, false);

    report_allows_final_save(Some(&report_path)).expect("clean report should classify clean");

    let _ = fs::remove_dir_all(root);
}

#[test]
fn advisory_classifier_treats_manual_review_as_clean_when_gate_passed() {
    // needs_manual_review 는 자문 플래그다. quality_gate_passed=true, residual_hits=0,
    // missing_targets=0 이면 깨끗한 것으로 분류된다.
    let root = temp_security_root("advisory_review_clean");
    let report_path = root.join("sample.safe_report.json");
    write_report(&report_path, true, true);

    report_allows_final_save(Some(&report_path))
        .expect("advisory manual-review report should classify clean");

    let _ = fs::remove_dir_all(root);
}

#[test]
fn advisory_classifier_flags_residual_hits_even_with_advisory_review() {
    // 자문 플래그(needs_manual_review=true)가 residual_hits>0 을 덮지 못한다:
    // residual_hits>0 이면 quality_gate_passed=true 라도 "깨끗하지 않음"으로 분류된다.
    let root = temp_security_root("advisory_residual");
    let report_path = root.join("sample.safe_report.json");
    let report = serde_json::json!({
        "product_checks": {
            "quality_gate_passed": true,
            "needs_manual_review": true
        },
        "missing_targets_count": 0,
        "document_redaction": {
            "verification": {
                "residual_hits": 1
            }
        }
    });
    fs::write(&report_path, report.to_string()).expect("report");

    let err = report_allows_final_save(Some(&report_path))
        .expect_err("residual hits must classify as not clean");

    assert!(err.contains("자동 검증을 통과한 문서만"));
    let _ = fs::remove_dir_all(root);
}

#[test]
fn advisory_classifier_reads_missing_targets_inside_redaction_block() {
    let root = temp_security_root("advisory_nested_missing");
    let report_path = root.join("sample.safe_report.json");
    let report = serde_json::json!({
        "product_checks": {
            "quality_gate_passed": true,
            "needs_manual_review": false
        },
        "document_redaction": {
            "missing_targets_count": 0,
            "verification": {
                "residual_hits": 0
            }
        }
    });
    fs::write(&report_path, report.to_string()).expect("report");

    report_allows_final_save(Some(&report_path))
        .expect("nested missing count should classify clean");

    let _ = fs::remove_dir_all(root);
}

#[test]
fn advisory_classifier_does_not_trust_synthetic_manual_revalidation() {
    // manual_revalidation.passed=true 만으로는 제품 게이트(quality_gate_passed=false)를
    // 덮지 못한다 — 자문 분류는 여전히 "깨끗하지 않음"이다.
    let root = temp_security_root("advisory_synthetic_reval");
    let report_path = root.join("sample.safe_report.json");
    let report = serde_json::json!({
        "product_checks": {
            "quality_gate_passed": false,
            "needs_manual_review": true
        },
        "manual_revalidation": {
            "status": "passed",
            "verified": true
        },
        "document_redaction": {
            "missing_targets_count": 0,
            "verification": {
                "residual_hits": 0
            }
        }
    });
    fs::write(&report_path, report.to_string()).expect("report");

    let err = report_allows_final_save(Some(&report_path))
        .expect_err("synthetic manual revalidation should not classify clean");

    assert!(err.contains("자동 검증을 통과한 문서만"));
    let _ = fs::remove_dir_all(root);
}

#[test]
fn advisory_classifier_flags_failed_manual_revalidation() {
    let root = temp_security_root("advisory_failed_reval");
    let report_path = root.join("sample.safe_report.json");
    let report = serde_json::json!({
        "product_checks": {
            "quality_gate_passed": true,
            "needs_manual_review": false
        },
        "manual_revalidation": {
            "status": "failed",
            "verified": false
        },
        "document_redaction": {
            "missing_targets_count": 0,
            "verification": {
                "residual_hits": 0
            }
        }
    });
    fs::write(&report_path, report.to_string()).expect("report");

    let err = report_allows_final_save(Some(&report_path))
        .expect_err("failed manual revalidation should classify as not clean");

    assert!(err.contains("자동 검증을 통과한 문서만"));
    let _ = fs::remove_dir_all(root);
}

fn sample_apply_result(output_file: &Path) -> ApplyResult {
    ApplyResult {
        status: Some("applied".to_string()),
        output_file: output_file.display().to_string(),
        mask_count: 1,
        restore_count: 1,
        applied_count: Some(2),
        excluded_count: Some(0),
        mask_boxes_applied: Some(1),
        unmask_boxes_applied: Some(1),
        skipped_boxes: Some(0),
        warnings: Some(vec![]),
        requires_revalidation: Some(true),
        display_mode: Some("black".to_string()),
        revalidation_report: None,
        revalidation_status: None,
    }
}

fn write_base_report_with_applied_bbox(path: &Path) {
    // A base report whose masking redacted one region on page 0 at (72,60)-(200,78).
    let report = serde_json::json!({
        "product_checks": { "quality_gate_passed": true, "needs_manual_review": false },
        "missing_targets_count": 0,
        "document_redaction": { "verification": { "residual_hits": 0 } },
        "review_items": [{
            "tag": "KEYWORD",
            "status": "applied",
            "page": 0,
            "bbox": { "x": 72.0, "y": 60.0, "width": 128.0, "height": 18.0 }
        }]
    });
    fs::write(path, report.to_string()).expect("base report");
}

#[test]
fn restore_over_masked_region_is_flagged_as_reexposure() {
    let root = temp_security_root("restore_reexpose");
    let report_path = root.join("base.safe_report.json");
    write_base_report_with_applied_bbox(&report_path);

    // A restore box overlapping the redacted region re-exposes masked PII.
    let overlapping = [RestoreRect {
        page: 0,
        x0: 80.0,
        y0: 62.0,
        x1: 150.0,
        y1: 74.0,
    }];
    assert!(restore_reexposes_masked_region(
        Some(&report_path),
        &overlapping
    ));

    // A restore box elsewhere on the page re-exposes nothing.
    let disjoint = [RestoreRect {
        page: 0,
        x0: 300.0,
        y0: 400.0,
        x1: 360.0,
        y1: 420.0,
    }];
    assert!(!restore_reexposes_masked_region(
        Some(&report_path),
        &disjoint
    ));

    // A restore box on a different page cannot overlap the page-0 redaction.
    let other_page = [RestoreRect {
        page: 1,
        x0: 80.0,
        y0: 62.0,
        x1: 150.0,
        y1: 74.0,
    }];
    assert!(!restore_reexposes_masked_region(
        Some(&report_path),
        &other_page
    ));

    let _ = fs::remove_dir_all(root);
}

#[test]
fn restore_without_base_report_is_treated_as_unsafe() {
    let restore = [RestoreRect {
        page: 0,
        x0: 10.0,
        y0: 10.0,
        x1: 20.0,
        y1: 20.0,
    }];
    // No base report to prove safety -> conservatively unsafe (re-exposure).
    assert!(restore_reexposes_masked_region(None, &restore));
    // No restore rects at all -> nothing to re-expose.
    assert!(!restore_reexposes_masked_region(None, &[]));
}

#[test]
fn passing_manual_revalidation_report_is_generated_and_classifies_clean() {
    // 재검증 리포트는 계속 생성·저장된다(프론트 경고 산출용). passed=true 리포트는
    // 자문 분류기에서 깨끗한 것으로 판정된다.
    let root = temp_security_root("manual_reval_pass")
        .canonicalize()
        .expect("canon");
    let output_pdf = root.join("manual_preview.pdf");
    let result = sample_apply_result(&output_pdf);

    let report_path = write_manual_revalidation_report(&root, &result, true).expect("write report");
    assert!(report_path.exists(), "revalidation report must be written");
    report_allows_final_save(Some(&report_path)).expect("passing report should classify clean");

    let _ = fs::remove_dir_all(root);
}

#[test]
fn failing_manual_revalidation_report_is_generated_and_classifies_not_clean() {
    // 재검증 실패 리포트도 정상적으로 생성·저장된다(프론트가 경고를 띄우게). 자문
    // 분류기에서는 "깨끗하지 않음"으로 판정되지만, 이는 저장을 막지 않는다.
    let root = temp_security_root("manual_reval_fail")
        .canonicalize()
        .expect("canon");
    let output_pdf = root.join("manual_preview.pdf");
    let result = sample_apply_result(&output_pdf);

    let report_path =
        write_manual_revalidation_report(&root, &result, false).expect("write report");
    assert!(
        report_path.exists(),
        "revalidation report must be written even when failed"
    );
    let err = report_allows_final_save(Some(&report_path))
        .expect_err("failing report should classify as not clean");
    assert!(err.contains("자동 검증을 통과한 문서만"));

    let _ = fs::remove_dir_all(root);
}
