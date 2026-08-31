import json
import os
import subprocess
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
E2E_SCRIPT = REPOSITORY_ROOT / "scripts/e2e_real_app.mjs"
QA_DRIVE_SOURCE = REPOSITORY_ROOT / "src" / "app" / "qaDrive.ts"
CANVAS_RENDER_SOURCE = REPOSITORY_ROOT / "src" / "features" / "canvas-workbench" / "canvasRenderController.ts"
QA_MOCK_SOURCE = REPOSITORY_ROOT / "scripts" / "qa_tauri_mock.mjs"
CANVAS_QA_SOURCE = REPOSITORY_ROOT / "scripts" / "qa_canvas_interactions.mjs"
DOCUMENT_SESSION_SOURCE = REPOSITORY_ROOT / "src" / "features" / "document-session" / "documentSessionController.ts"
TAURI_SOURCE = REPOSITORY_ROOT / "src-tauri" / "src" / "lib.rs"


def test_geometry_fixture_is_kept_as_a_separate_scenario_from_the_real_document() -> None:
    # Given: the real-app scenario source that owns the QA-drive lifecycle.
    source = E2E_SCRIPT.read_text(encoding="utf-8")

    # When: the fixture geometry and real-document scenario boundaries are inspected.
    geometry_state_position = source.index("const geometryState")
    geometry_primary_rail_position = source.index('geometryState.reviewCardCount !== 1')
    geometry_advanced_position = source.index('geometryState.advancedGeometryCount !== 1')
    geometry_resolved_position = source.index("const resolvedGeometryState")
    geometry_resolved_advanced_position = source.index('resolvedGeometryState.advancedGeometryCount !== 0')
    geometry_separation_comment = source.index("T40 auto-confirmation no longer produces unresolved geometry")
    real_document_position = source.index("const realOpenedState")
    close_position = source.index("drive.close();")

    # Then: geometry coverage is resolved before the isolated real-document scenario.
    assert (
        geometry_state_position
        < geometry_primary_rail_position
        < geometry_advanced_position
        < geometry_resolved_position
        < geometry_resolved_advanced_position
        < geometry_separation_comment
        < real_document_position
        < close_position
    )


def test_qa_drive_snapshot_separates_primary_and_advanced_geometry_reviews() -> None:
    # Given: the QA-drive state snapshot implementation.
    source = QA_DRIVE_SOURCE.read_text(encoding="utf-8")

    # When: the two review projections are inspected.
    primary_projection = source.index('reviewCardCount: reviewItems.filter((item) => item.kind !== "region_geometry").length')
    advanced_projection = source.index('advancedGeometryCount: reviewItems.filter((item) => item.kind === "region_geometry" && item.status === "pending").length')

    # Then: primary cards exclude geometry while advanced count includes only pending geometry.
    assert primary_projection < advanced_projection


def test_qa_drive_contract_supports_manual_mask_and_restore_application() -> None:
    # Given: the real-app QA driver source.
    source = QA_DRIVE_SOURCE.read_text(encoding="utf-8")
    canvas_source = CANVAS_RENDER_SOURCE.read_text(encoding="utf-8")

    # When: the manual draw/apply commands and observable state are inspected.
    draw_command = source.index('kind === "draw-box"')
    apply_command = source.index('kind === "apply-manual"')
    production_tag = canvas_source.index('const tag = owner.draftOwner && owner.mode === "mask" ? owner.draftOwner : "MANUAL"')
    injected_tag = source.index('const tag = command.mode === "mask" && controller.state.geometryDraft')
    production_handler = source.index('controller.applyPendingManualBoxes("수동마스킹실행")')
    box_snapshot = source.index('boxes: controller.state.boxes.map((box) => ({ page: box.page, mode: box.mode, tag: box.tag ?? "MANUAL" }))')
    manual_action_snapshot = source.index('manualActionModes: workspace.report?.analysisManifest?.manualActions.map((action) => action.mode) ?? []')

    # Then: the driver reuses drag tagging semantics, calls the UI handler, and exposes both stages.
    assert draw_command < injected_tag
    assert apply_command < production_handler
    assert production_tag >= 0
    assert box_snapshot < manual_action_snapshot


def test_qa_drive_open_uses_transactional_workspace_load_without_touching_common_replacement_gate() -> None:
    # Given: the QA-drive and application-controller document-open sources.
    drive_source = QA_DRIVE_SOURCE.read_text(encoding="utf-8")
    app_source = (REPOSITORY_ROOT / "src" / "app" / "applicationController.ts").read_text(encoding="utf-8")

    # Then: only the drive open branch uses the transactional workspace loader, while the common gate remains modal-backed.
    drive_open = drive_source.index('case "open":')
    drive_load = drive_source.index("controller.loadCanvasWorkspacePdf(path", drive_open)
    drive_legacy = drive_source.find("controller.openQaDocument(path)", drive_open)
    drive_failure = drive_source.index("QA_DRIVE_DOCUMENT_LOAD_FAILED", drive_open)
    common_gate = app_source.index("async function openQaDocument")
    modal_gate = app_source.index("prepareForDocumentReplacement()", common_gate)
    session_source = DOCUMENT_SESSION_SOURCE.read_text(encoding="utf-8")
    modal_confirm = session_source.index("deps.confirmDiscardCurrentWork()", session_source.index("async function prepareForDocumentReplacement"))
    assert drive_load < drive_failure
    assert drive_legacy == -1
    assert modal_gate >= 0
    assert modal_confirm >= 0


def test_qa_drive_open_has_a_stage_labelled_deadline_and_protocol_id_matching() -> None:
    # Given: the frontend timeout guard, native bridge, and packaged-app clients.
    drive_source = QA_DRIVE_SOURCE.read_text(encoding="utf-8")
    protocol_source = (REPOSITORY_ROOT / "src" / "app" / "qaDriveProtocol.ts").read_text(encoding="utf-8")
    native_source = (REPOSITORY_ROOT / "src-tauri" / "src" / "qa_drive.rs").read_text(encoding="utf-8")
    acceptance_source = (REPOSITORY_ROOT / "scripts" / "acceptance_real_app.mjs").read_text(encoding="utf-8")
    e2e_source = E2E_SCRIPT.read_text(encoding="utf-8")

    # Then: an open cannot wait forever, timeout diagnostics identify the stage,
    # and a late response cannot be consumed by a later command.
    assert "QA_DRIVE_OPEN_TIMEOUT_MS = 180_000" in protocol_source
    assert "QA_DRIVE_COMMAND_TIMEOUT:stage=" in protocol_source
    assert "loadCanvasWorkspacePdf(path" in drive_source
    assert "stateSnapshot" in drive_source
    assert '"inspect-target"' in protocol_source
    assert 'case "inspect-target":' in drive_source
    assert 'final-save-readiness' in drive_source
    assert "QA_DRIVE_SAVE_FINAL_REQUIRES_CONFIRM_SAVE" in drive_source
    assert 'saveFinalOutput({ warningsConfirmed: true })' in drive_source
    assert "QA_DRIVE_OPEN_TIMEOUT" in native_source
    assert "QA_DRIVE_COMMAND_TIMEOUT:stage={timeout_stage}:command=" in native_source
    assert "bridge.forget(&id)" in native_source
    assert "pending.get(transcript.id)" in acceptance_source
    assert "pending.get(transcript.id)" in e2e_source
    assert "OPEN_COMMAND_TIMEOUT = 30_000" not in acceptance_source


def test_real_acceptance_restarts_the_drive_for_one_render_retry_and_checks_frame_stability() -> None:
    # Given: the real-app acceptance lifecycle and its external measurement boundary.
    source = (REPOSITORY_ROOT / "scripts" / "acceptance_real_app.mjs").read_text(encoding="utf-8")

    # Then: only the labelled render-unavailable path is retried in a fresh drive,
    # and layout mismatches are observed rather than hidden by a second scroll.
    assert "async function stableMeasurementFrame" in source
    assert "frame-stability:" in source
    assert "if (code !== \"QA_DRIVE_RENDER_UNAVAILABLE\" || openRetryUsed)" in source
    assert "await terminateChild(child, drive)" in source
    assert "openRetryUsed ? \"PASS_AFTER_RETRY\" : \"PASS\"" in source
    assert "attempts: openAttempts" in source
    assert '.filter((field) => field !== "scrollOffsets")' not in source


def test_gate_parser_uses_acceptance_headers_and_preserves_final_pdf_failure() -> None:
    # Given: a current acceptance table where keyword passes but final PDF fails.
    report = "\n".join([
        "| alias | sha256 | open render attempts | 자동 확정 마스킹 화면 | 검토 대기 표시 | 검토 행 제외 | 다른 페이지 안내 | 수동 실제 텍스트 | 복원/잔존 마스크 PDF | 키워드 픽셀 변화 | 저장 PDF 가림 (자동/수동/복원/키워드) | 문서 판정 | 증거 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        f"| doc-01 | {'a' * 64} | PASS: #1/PASS/OK/open/1ms | PASS | PASS | PASS | PASS | PASS | PASS | PASS | FAIL — auto PASS; keyword FAIL | **FAIL** | — |",
    ])
    with tempfile.TemporaryDirectory() as directory:
        report_path = Path(directory) / "acceptance.md"
        report_path.write_text(report, encoding="utf-8")
        completed = subprocess.run(
            [
                "node",
                "--input-type=module",
                "--eval",
                "import { resultsPerDocument } from './scripts/gate_complete.mjs';"
                "console.log(JSON.stringify(resultsPerDocument()));",
            ],
            cwd=REPOSITORY_ROOT,
            env={**os.environ, "T53R_REPORT_PATH": str(report_path)},
            text=True,
            capture_output=True,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)[0]
    assert result["status"] == "FAIL"
    assert result["checks"]["keyword"] == "PASS"
    assert result["checks"]["finalPdf"] == "FAIL"


def test_real_document_e2e_requires_an_isolated_scratch_alias_and_never_uses_source_filename() -> None:
    # Given: the packaged-app real-document harness.
    source = E2E_SCRIPT.read_text(encoding="utf-8")

    # Then: source input is copied to a fixed alias and only scratch is granted to the masking engine.
    assert 'const realDocumentPath = process.env.E2E_REAL_DOC?.trim() ?? "";' in source
    assert 'E2E_REAL_DOC_MISSING' in source
    assert 'const realInput = join(scratch, "real-document-input.pdf");' in source
    assert 'MASK_TOOL_ALLOWED_DIRS: scratch' in source
    assert 'basename(realDocumentPath)' not in source
    assert 'real-app-real-document-before-masking.png' in source
    assert 'real-app-real-document-after-masking.png' in source


def test_real_document_scenario_reports_six_evidence_steps_and_save_gate_readiness() -> None:
    # Given: the isolated real-document scenario.
    source = E2E_SCRIPT.read_text(encoding="utf-8")

    # Then: the six observable milestones are emitted after real detection and manual mask/restore application.
    markers = [source.index(f"[real-doc][{index}]") for index in range(1, 7)]
    assert markers == sorted(markers)
    assert source.index("overlayPaintedPixelCount <= 0") < markers[1]
    assert source.index("manualActionModes") < markers[4]
    assert source.index('saveGateState !== "ready"') < markers[4]


def test_qa_drive_contract_exposes_real_canvas_drag_and_last_rejection() -> None:
    # Given: the QA driver and canvas interaction sources.
    drive_source = QA_DRIVE_SOURCE.read_text(encoding="utf-8")
    canvas_source = CANVAS_RENDER_SOURCE.read_text(encoding="utf-8")

    # When: the real DOM event path and structured rejection telemetry are inspected.
    drag_command = drive_source.index('kind === "drag-canvas"')
    mouse_event = drive_source.index('new MouseEvent(type')
    mouse_down = drive_source.index('dispatchCanvasMouseEvent(overlay, "mousedown"')
    mouse_move = drive_source.index('dispatchCanvasMouseEvent(\n      overlay,\n      "mousemove"')
    mouse_up = drive_source.index('dispatchCanvasMouseEvent(overlay, "mouseup"')
    snapshot_rejection = drive_source.index('lastDragRejection: controller.state.lastDragRejection')
    rejection_field = canvas_source.index('lastDragRejection')
    rejection_reasons = [
        canvas_source.index(f'reason: "{reason}"')
        for reason in (
            "resultDocChanged",
            "pageChanged",
            "scaleChanged",
            "modeChanged",
            "draftOwnerChanged",
            "tooSmall",
        )
    ]

    # Then: the driver dispatches the complete event sequence and dump-state exposes the telemetry.
    assert drag_command < mouse_event
    assert mouse_down < mouse_move < mouse_up
    assert snapshot_rejection >= 0
    assert all(index > rejection_field for index in rejection_reasons)


def test_production_save_negative_path_keeps_chooser_and_token_guard_when_qa_is_off() -> None:
    # Given: the native chooser command with its QA-only output shortcut.
    source = TAURI_SOURCE.read_text(encoding="utf-8")
    chooser = source.index("fn choose_final_pdf_path(")
    qa_branch = source.index("if qa_drive.0", chooser)
    qa_target = source.index("MASK_TOOL_QA_FINAL_OUTPUT_PATH", qa_branch)
    qa_return = source.index("return Ok(Some(FinalPdfSaveTarget", qa_target)
    production_reset = source.index("access.clear_native_save_target()?", qa_return)
    production_dialog = source.index("FileDialog::new()", production_reset)
    production_token = source.index("register_native_save_target_core(&access, &path, binding)", production_dialog)

    # Then: the configured target is unreachable without QA mode, while production
    # still clears stale authority, opens the native chooser, and issues a fresh token.
    assert qa_branch < qa_target < qa_return < production_reset < production_dialog < production_token
    assert source.index("MASK_TOOL_QA_FINAL_OUTPUT_PATH", qa_branch, production_reset) == qa_target
    assert source.index(
        "access.clear_native_save_target()?",
        chooser,
        production_reset + len("access.clear_native_save_target()?"),
    ) == production_reset


def test_acceptance_measurement_separates_visibility_sessions_and_save_status() -> None:
    # Given: the corrected real-app acceptance and QA-drive sources.
    acceptance_source = (REPOSITORY_ROOT / "scripts" / "acceptance_real_app.mjs").read_text(encoding="utf-8")
    drive_source = QA_DRIVE_SOURCE.read_text(encoding="utf-8")

    # Then: target visibility, display scale, independent sessions, and save outcomes
    # are observable without the old zero-detection OCR shortcut.
    assert "scroll-to" in acceptance_source
    assert "ACTIVE_DISPLAY_INFO_SWIFT" in acceptance_source
    assert "SCREEN_DISPLAY_MISMATCH" in acceptance_source
    assert "captureOrigin" in acceptance_source
    assert "pendingReview" in acceptance_source
    assert "finalManifestTargetGroups" in acceptance_source
    assert "saveOutcomeSummary" in acceptance_source
    assert "hasZeroDetectionText" not in acceptance_source
    assert "scrollableAncestors" in drive_source
    assert "documentScroll" in drive_source
    assert "getImageData" in drive_source


def test_real_drag_regression_scenarios_use_drag_canvas_and_verify_box_cleanup() -> None:
    # Given: the packaged-app and browser canvas QA scenario sources.
    e2e_source = E2E_SCRIPT.read_text(encoding="utf-8")
    canvas_qa_source = CANVAS_QA_SOURCE.read_text(encoding="utf-8")

    # When: their manual-drag paths are inspected.
    e2e_start = e2e_source.index('start-masking')
    e2e_running_drag = e2e_source.index('drag-canvas " + Object.values(canvasRect(realOpenedState', e2e_start)
    e2e_running_status = e2e_source.index('마스킹 실행 중에는 박스를 그릴 수 없습니다.', e2e_running_drag)
    e2e_wait = e2e_source.index('wait-idle', e2e_running_drag)
    e2e_mask_drag = e2e_source.index('drag-canvas " + Object.values(realMaskRect', e2e_wait)
    e2e_mask_apply = e2e_source.index('apply-manual', e2e_mask_drag)
    e2e_restore_drag = e2e_source.index('drag-canvas " + Object.values(restoreRect', e2e_mask_apply)
    e2e_restore_apply = e2e_source.index('apply-manual', e2e_restore_drag)
    e2e_cleanup = e2e_source.index('boxes?.length !== 0', e2e_restore_apply)
    qa_drag_helper = canvas_qa_source.index("async function dragCanvas")
    qa_real_dispatch = canvas_qa_source.index('new MouseEvent(type', qa_drag_helper)
    qa_cleanup = canvas_qa_source.index("draft boxes cleared")

    # Then: both gates exercise the real pointer path and assert the post-apply cleanup.
    assert e2e_start < e2e_running_drag < e2e_running_status < e2e_wait < e2e_mask_drag < e2e_mask_apply < e2e_restore_drag < e2e_restore_apply < e2e_cleanup
    assert qa_drag_helper < qa_real_dispatch < qa_cleanup


def test_canvas_controls_disable_tools_and_apply_during_masking() -> None:
    # Given: the manual adjustment control wiring and its shared run state.
    source = (REPOSITORY_ROOT / "src" / "features" / "finalization" / "finalizationController.ts").read_text(encoding="utf-8")

    # Then: every drawing/action control uses the masking-running busy gate.
    busy = source.index("const busy = state.maskingRunning || state.batchRunning || state.savingInFlight;")
    apply = source.index("deps.btnCanvasApply.disabled = !readiness.canApplyManualPreview || busy;", busy)
    assert busy < apply


def test_real_app_e2e_exercises_mixed_profile_manual_mask_restore_and_save_gate() -> None:
    # Given: the packaged-app E2E driver.
    source = E2E_SCRIPT.read_text(encoding="utf-8")

    # When: the manual-application scenario is inspected.
    profile = source.index('set-profile mixed', source.index('const realOpenedState'))
    mask_draw = source.index('maskedRealState', profile)
    mask_apply = source.index('apply-manual', mask_draw)
    restore_draw = source.index('restoreRect', mask_apply)
    manual_modes = source.index('restoredManualState?.manualActionModes')
    save_gate = source.index('saveGateState !== "ready"', restore_draw)

    # Then: all public manual modes reach the same real-app apply handler in order.
    assert profile < mask_draw < mask_apply < restore_draw < manual_modes < save_gate


def test_internal_review_mock_analysis_unlocks_the_headless_manual_apply_chain() -> None:
    # Given: the public-session mock and its browser interaction scenario.
    mock_source = QA_MOCK_SOURCE.read_text(encoding="utf-8")
    canvas_qa_source = CANVAS_QA_SOURCE.read_text(encoding="utf-8")

    # When: analysis and the manual action gate are inspected.
    supported_profiles = mock_source.index('["internal_review", "official_dispatch", "mixed"].includes(request?.profile)')
    analyzed = mock_source.index("publicSession.analyzed = true", supported_profiles)
    profile_binding = mock_source.index("publicSession.profile = request.profile", supported_profiles)
    action_gate = mock_source.index("!publicSession.analyzed")
    internal_review_scenario = canvas_qa_source.index('}, "internal_review");')
    trust_boundary_assertion = canvas_qa_source.index("syntheticRestore.applyCount === 1")

    # Then: every public profile receives an analyzed session before the shared action gate,
    # and the browser QA proves synthetic restore never reaches the native action endpoint.
    assert supported_profiles < profile_binding < analyzed
    assert action_gate >= 0
    assert internal_review_scenario < trust_boundary_assertion
