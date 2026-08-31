from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
# v4 P2 (REDESIGN_V4_DARK §1): 문서 관제(WorkRail/DocumentStage/ReviewInspector)가
# 통합 "문서" 화면(CanvasWorkspace, data-screen-panel="documents")으로 흡수됐다.
# 이식된 검토 레일·저장 게이트 모달·실행 프록시는 CanvasWorkspace 안에 있다.
COMPONENT_PATHS = [
    REPO_ROOT / "src" / "components" / "AppHeader.tsx",
    REPO_ROOT / "src" / "components" / "CanvasWorkspace.tsx",
    REPO_ROOT / "src" / "components" / "MaskingSettingsScreen.tsx",
    REPO_ROOT / "src" / "components" / "SettingsScreen.tsx",
    # v4 P3: MobileActionDock 삭제(좁은 폭에서 스테이지+검토 레일 세로 스택).
]


def frontend_markup() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in COMPONENT_PATHS)






class FrontendButtonContractTests(unittest.TestCase):
    def test_visible_toggle_controls_update_exclusive_aria_state(self) -> None:
        from test_frontend_state_helpers import run_node_helper

        result = run_node_helper(
            "src/features/manual-adjustment/manualAdjustmentController.ts",
            "(() => {"
            "const element = (dataset = {}) => ({ dataset, attrs: {}, classList: { toggle() {} }, style: {}, textContent: '', disabled: false, title: '', hidden: false, value: '', setAttribute(name, value) { this.attrs[name] = value; }, removeAttribute() {}, append() {}, replaceChildren() {}, addEventListener() {}, closest() { return null; }, getContext() { return { clearRect() {}, fillRect() {}, drawImage() {} }; } });"
            "const tools = ['mask', 'restore', 'select', 'delete', 'pan'].map((tool) => element({ canvasTool: tool })); let active = 'mask';"
            "const state = { documentProvenance: { original: { path: '/input.pdf' }, generated: { path: '/result.pdf' }, manual: { path: '' }, final: { path: '' }, continuation: null }, boxes: [], geometryDraft: null, documentEditRevision: 0, mode: 'mask', selectedCanvasBoxIndex: -1, canvasMode: true, maskingRunning: false, batchRunning: false, savingInFlight: false, extractedText: '', maskedText: '', baseExtractedText: '', baseMaskedText: '', preManualPreviewPdf: '', preManualExtractedText: '', preManualMaskedText: '', latestReportPath: '', latestReport: null, latestMaskedPath: '', latestMaskedTextPolicy: 'token', lastPreviewDiagnostics: '', restoreRevalidationFailed: false, scale: 1, resultDoc: {} };"
            "const controller = m.createManualAdjustmentController({ state, invokeCommand: async () => ({}), displayModeEl: element(), customKeywordsEl: element(), modeMask: tools[0], modeRestore: tools[1], workspaceShellEl: element(), overlay: element(), canvasEditorToolButtons: tools, canvasActiveToolLabelEl: element(), canvasToolReadinessEl: element(), canvasBoxListEl: element(), canvasBoxPropertiesEl: element(), canvasBoxPropertyPageEl: element(), canvasBoxPropertyTypeEl: element(), canvasBoxPropertyCoordinatesEl: element(), canvasBoxPropertySizeEl: element(), canvasSummaryMaskCountEl: element(), canvasSummaryRestoreCountEl: element(), canvasSummaryKeywordCountEl: element(), canvasSummaryOutputStateEl: element(), btnCanvasZoomOut: element(), btnCanvasZoomIn: element(), btnCanvasUndo: element(), btnCanvasClear: element(), btnCanvasBoxDelete: element(), btnCanvasBoxConvertMask: element(), btnCanvasBoxConvertRestore: element(), isStandaloneCanvasWindow: false, isPdfInput: () => true, currentFinalDocumentPath: () => '/result.pdf', getActiveCanvasTool: () => active, setActiveCanvasToolState: (tool) => { active = tool; }, ensurePreviewWorkDir: async () => '/work', loadResultPdf: async () => true, redrawOverlay() {}, updateMeta() {}, renderFinalState() {}, renderCompare: async () => {}, setTextCompareContents() {}, updateWorkflowReadiness() {}, updateStatusDetail() {}, setStatus() {} });"
            "return Object.fromEntries(tools.map((button) => { controller.setActiveCanvasTool(button.dataset.canvasTool); return [button.dataset.canvasTool, tools.map((candidate) => candidate.attrs['aria-pressed'])]; }));"
            "})()",
            browser_fixture=True,
        )
        for active, states in result.items():
            with self.subTest(active=active):
                self.assertEqual(
                    ["true" if tool == active else "false" for tool in ("mask", "restore", "select", "delete", "pan")],
                    states,
                )

    def test_masking_output_artifacts_keep_reports_internal(self) -> None:
        from test_frontend_state_helpers import run_node_helper

        result = run_node_helper(
            "src/settingsState.ts",
            "({ pdfOnly: m.maskingOutputArtifacts(false), withMaskedText: m.maskingOutputArtifacts(true) })",
        )
        self.assertEqual(result["pdfOnly"], "pdf_safe_report")
        self.assertEqual(result["withMaskedText"], "pdf_masked_txt_safe_report")

    def test_final_save_checklist_controls_are_removed_from_documents_screen(self) -> None:
        # v4 P2: 저장 전 확인은 한 곳(최종 저장 모달)뿐이다. 통합 화면 마크업에
        # 옛 체크리스트/하단 도크가 되살아나지 않았는지 확인한다(중간 요약 반복 금지).
        markup = frontend_markup()

        removed_checklist_class = "final-save-" + "checklist"
        self.assertNotIn(f'className="checklist {removed_checklist_class}"', markup)
        self.assertNotIn('className="stage-bottom-dock"', markup)

    def test_public_save_gate_warns_for_pending_reviews_while_legal_is_advisory(self) -> None:
        from test_frontend_state_helpers import canonical_review_manifest, run_node_helper

        pending = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const manifest = {pending};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = (manifest) => ({ runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile });"
            "const publicReport = session.parseBoundSafeReport({ analysisManifest: manifest, reviewQueue: manifest.reviewItems, product_checks: {} }, identity(manifest)).value;"
            "const legalReport = { product_checks: { quality_gate_passed: false }, document_redaction: { verification: { residual_hits: 1 } } };"
            "return { public: m.finalSaveGate({ report: publicReport }), legal: m.legalCompatibilityFinalSaveGate({ hasReportPath: true, report: legalReport }) };"
            "})()",
        )
        self.assertFalse(result["public"]["eligible"])
        self.assertEqual(result["public"]["state"], "advisory")
        self.assertEqual(result["public"]["reasonCodes"], ["ambiguous_boundary"])
        self.assertTrue(result["legal"]["eligible"])
        self.assertEqual(result["legal"]["state"], "advisory")

    def test_excluded_only_manifest_does_not_require_partial_save_confirmation(self) -> None:
        from test_frontend_state_helpers import canonical_review_manifest, run_node_helper

        manifest = canonical_review_manifest(status="resolved")
        manifest["reviewItems"] = []
        manifest["occurrences"] = [{
            "occurrenceId": "occ_aaaaaaaaaaaaaaaaaaaaaaaa",
            "segmentId": "segment-1",
            "regionId": None,
            "analysisRevision": 7,
            "page": 0,
            "rects": [{"x0": 10, "y0": 10, "x1": 20, "y1": 20}],
            "tag": "NAME",
            "category": "name",
            "valueHash": "c" * 64,
            "expectedTextHash": "d" * 64,
            "source": "text_pdf",
            "policy": "masking-policy-v1",
            "proposedAction": "exclude",
            "state": "confirmed",
            "provenance": "qa",
        }]
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            f"(() => {{ const manifest = {json.dumps(manifest)}; const session = loadModule(path.resolve('src/state/maskingSession.ts')); const identity = {{ runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile }}; const report = session.parseBoundSafeReport({{ product_checks: {{}}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }}, identity).value; return {{ gate: m.finalSaveGate({{ report }}), warnings: m.publicFinalSaveWarnings({{ report }}) }}; }})()",
        )
        self.assertEqual("eligible", result["gate"]["state"])
        self.assertEqual([], result["warnings"])

    def test_indeterminate_coverage_warning_list_stays_confirm_save_eligible(self) -> None:
        from test_frontend_state_helpers import canonical_review_manifest, run_node_helper

        base_manifest = json.dumps(canonical_review_manifest(status="resolved"))
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const base = {base_manifest};"
            "const manifest = { ...base, profile: 'internal_review', approvalCoverage: { approval: 'absent', header_meta: 'indeterminate', labeled_staff: 'absent' }, regions: [{ regionId: 'region-header-meta', segmentId: base.segments[0].segmentId, analysisRevision: base.analysisRevision, page: 0, rects: [{ x0: 1, y0: 1, x1: 2, y1: 2 }], kind: 'header_meta', state: 'review_required', confirmationSource: null, reasonCodes: ['geometry_review'], source: 'official_layout' }], reviewItems: [{ reviewId: 'review-header-meta', analysisRevision: base.analysisRevision, kind: 'region_geometry', targetId: 'region-header-meta', pageStart: 0, pageEnd: 0, status: 'pending', reasonCodes: ['geometry_review'], requiresAcknowledgment: false, commonOnly: false, provenance: 'official_layout' }] };"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const dashboard = loadModule(path.resolve('src/dashboardSurfaceModels.ts'));"
            "const identity = { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile };"
            "const parsed = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity);"
            "const report = parsed.ok ? parsed.value : null;"
            "return { parsed: parsed.ok, gate: m.finalSaveGate({ report }), warnings: m.publicFinalSaveWarnings({ report }), dashboard: dashboard.dashboardReviewState(report) };"
            "})()",
        )
        self.assertTrue(result["parsed"])
        self.assertEqual("advisory", result["gate"]["state"])
        self.assertEqual(
            ["미가림 가능성: 머리말 정보 · 1쪽 — 결재란 영역 자동확인 미완료 — 확인하고 저장"],
            result["warnings"],
        )

    def test_scanned_geometry_warning_is_advisory_and_identifies_the_page(self) -> None:
        from test_frontend_state_helpers import canonical_review_manifest, run_node_helper

        base_manifest = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const base = {base_manifest};"
            "const segment = { ...base.segments[0], kind: 'unknown', state: 'review_required', commonOnly: false, source: 'scanned_geometry_unavailable', pageStart: 1, pageEnd: 2 };"
            "const review = { ...base.reviewItems[0], kind: 'acknowledge', targetId: segment.segmentId, pageStart: 1, pageEnd: 2, status: 'pending', reasonCodes: ['scanned_geometry_unavailable'], requiresAcknowledgment: true, commonOnly: false, provenance: 'extraction_evidence' };"
            "const manifest = { ...base, segments: [segment], reviewItems: [review] };"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const dashboard = loadModule(path.resolve('src/dashboardSurfaceModels.ts'));"
            "const identity = { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile };"
            "const parsed = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity);"
            "const report = parsed.ok ? parsed.value : null;"
            "return { parsed: parsed.ok, gate: m.finalSaveGate({ report }), warnings: m.publicFinalSaveWarnings({ report }), dashboard: dashboard.dashboardReviewState(report) };"
            "})()",
        )
        self.assertTrue(result["parsed"])
        self.assertEqual("advisory", result["gate"]["state"])
        self.assertEqual(
            ["미가림 가능성: 검토 항목 · 2–3쪽 — 자동 탐지가 되지 않아 수동 확인이 필요합니다."],
            result["warnings"],
        )
        card = result["dashboard"]["items"][0]
        self.assertEqual(1, card["pageStart"])
        self.assertTrue(card["scannedGeometryUnavailable"])
        self.assertEqual("스캔 페이지 2–3쪽: 자동 탐지 불가 — 수동 마스킹으로 가린 뒤 확인하세요.", card["detail"])

    def test_each_pending_scanned_range_is_listed_in_the_save_warning(self) -> None:
        from test_frontend_state_helpers import canonical_review_manifest, run_node_helper

        base_manifest = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const base = {base_manifest};"
            "const first = { ...base.segments[0], kind: 'unknown', state: 'review_required', commonOnly: false, source: 'scanned_geometry_unavailable', pageStart: 1, pageEnd: 1 };"
            "const second = { ...first, segmentId: 'segment-2', pageStart: 4, pageEnd: 5 };"
            "const firstReview = { ...base.reviewItems[0], kind: 'acknowledge', targetId: first.segmentId, pageStart: 1, pageEnd: 1, status: 'pending', reasonCodes: ['scanned_geometry_unavailable'], requiresAcknowledgment: true, commonOnly: false, provenance: 'extraction_evidence' };"
            "const secondReview = { ...firstReview, reviewId: 'review-2', targetId: second.segmentId, pageStart: 4, pageEnd: 5 };"
            "const manifest = { ...base, segments: [first, second], reviewItems: [firstReview, secondReview] };"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile };"
            "const parsed = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity);"
            "return { parsed: parsed.ok, warnings: m.publicFinalSaveWarnings({ report: parsed.ok ? parsed.value : null }) };"
            "})()",
        )
        self.assertTrue(result["parsed"])
        self.assertEqual(
            [
                "미가림 가능성: 검토 항목 · 2쪽 — 자동 탐지가 되지 않아 수동 확인이 필요합니다.",
                "미가림 가능성: 검토 항목 · 5–6쪽 — 자동 탐지가 되지 않아 수동 확인이 필요합니다.",
            ],
            result["warnings"],
        )

    def test_public_confirmed_warning_prepares_save_but_integrity_failure_does_not(self) -> None:
        from test_frontend_state_helpers import canonical_review_manifest, run_node_helper

        manifest = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/services/tauri/maskingContracts.ts",
            "(() => {"
            f"const manifest = {manifest};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile };"
            "const report = session.parseBoundSafeReport({ analysisManifest: manifest, reviewQueue: manifest.reviewItems, product_checks: {} }, identity).value;"
            "const request = (warningsConfirmed) => ({ runId: manifest.runId, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, destination: '/out/final.pdf', saveToken: '0'.repeat(32), warningsConfirmed });"
            "return { unconfirmed: m.prepareFinalizeMaskingRun(request(false), report), confirmed: m.prepareFinalizeMaskingRun(request(true), report), badToken: m.prepareFinalizeMaskingRun({ ...request(true), saveToken: 'token-1' }, report), integrity: m.prepareFinalizeMaskingRun(request(true), null) };"
            "})()",
        )
        self.assertFalse(result["unconfirmed"]["ok"])
        self.assertTrue(result["confirmed"]["ok"])
        self.assertEqual(
            [{"code": "invalid_status", "field": "finalize_request.warningsConfirmed"}],
            result["unconfirmed"]["errors"],
        )
        self.assertEqual(
            [{"code": "invalid_status", "field": "finalize_request.saveToken"}],
            result["badToken"]["errors"],
        )
        self.assertFalse(result["integrity"]["ok"])

    def test_unmapped_review_reason_codes_warn_by_review_kind_without_raw_code(self) -> None:
        from test_frontend_state_helpers import canonical_review_manifest, run_node_helper

        base_manifest = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const base = {base_manifest};"
            "const occurrence = { occurrenceId: 'occ_000000000000000000000001', segmentId: base.segments[0].segmentId, regionId: null, analysisRevision: base.analysisRevision, page: 0, rects: [{ x0: 72, y0: 60, x1: 200, y1: 78 }], tag: 'NAME', category: 'name', valueHash: 'c'.repeat(64), expectedTextHash: 'd'.repeat(64), source: 'text_pdf', policy: 'mask', proposedAction: 'review', state: 'review_required', provenance: 'qa' };"
            "const nameManifest = { ...base, occurrences: [occurrence], reviewItems: [{ reviewId: 'review-name', analysisRevision: base.analysisRevision, kind: 'name', targetId: occurrence.occurrenceId, pageStart: 0, pageEnd: 0, status: 'pending', reasonCodes: ['requires_review'], requiresAcknowledgment: false, commonOnly: false, provenance: 'qa' }] };"
            "const region = { regionId: 'region-1', segmentId: base.segments[0].segmentId, analysisRevision: base.analysisRevision, page: 0, rects: [{ x0: 1, y0: 1, x1: 2, y1: 2 }], kind: 'approval', state: 'unconfirmed', confirmationSource: null, reasonCodes: ['layout_structure_missing'], source: 'official_layout' };"
            "const geometryManifest = { ...base, regions: [region], reviewItems: [{ reviewId: 'review-region', analysisRevision: base.analysisRevision, kind: 'region_geometry', targetId: 'region-1', pageStart: 0, pageEnd: 0, status: 'pending', reasonCodes: ['layout_structure_missing'], requiresAcknowledgment: true, commonOnly: false, provenance: 'official_layout' }] };"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = (manifest) => ({ runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile });"
            "const nameReport = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: nameManifest, reviewQueue: nameManifest.reviewItems }, identity(nameManifest)).value;"
            "const geometryReport = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: geometryManifest, reviewQueue: geometryManifest.reviewItems }, identity(geometryManifest)).value;"
            "return { name: { gate: m.finalSaveGate({ report: nameReport }), warnings: m.publicFinalSaveWarnings({ report: nameReport }) }, geometry: { gate: m.finalSaveGate({ report: geometryReport }), warnings: m.publicFinalSaveWarnings({ report: geometryReport }) } };"
            "})()",
        )
        self.assertEqual("advisory", result["name"]["gate"]["state"])
        self.assertEqual(["requires_review"], result["name"]["gate"]["reasonCodes"])
        self.assertEqual(["미가림 가능성: 이름 · 1쪽 — 이름 또는 기관 탐지 결과를 확인해야 합니다."], result["name"]["warnings"])
        self.assertEqual("advisory", result["geometry"]["gate"]["state"])
        self.assertEqual(["geometry_review"], result["geometry"]["gate"]["reasonCodes"])
        self.assertEqual(["미가림 가능성: 결재선 · 1쪽 — 고정 영역의 박스 구조를 확인해야 합니다."], result["geometry"]["warnings"])
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("(requires_review)", encoded)
        self.assertNotIn("(layout_structure_missing)", encoded)

    def test_default_output_native_success_parser_rejects_malformed_payload_with_typed_diagnostic(self) -> None:
        from test_frontend_state_helpers import run_node_helper

        result = run_node_helper(
            "src/services/tauri/contracts.ts",
            "({ success: m.parseDefaultOutputDirForDocumentResult({ status: 'ok', outputDir: '/tmp/out' }), missing: m.parseDefaultOutputDirForDocumentResult({ status: 'ok' }), wrongStatus: m.parseDefaultOutputDirForDocumentResult({ status: 'error', outputDir: '/tmp/out' }), blank: m.parseDefaultOutputDirForDocumentResult({ status: 'ok', outputDir: ' ' }) })",
        )
        self.assertEqual({"ok": True, "value": {"status": "ok", "outputDir": "/tmp/out"}}, result["success"])
        self.assertEqual({"ok": False, "errors": [{"code": "missing_outputDir", "field": "outputDir"}]}, result["missing"])
        self.assertEqual({"ok": False, "errors": [{"code": "invalid_status", "field": "status"}]}, result["wrongStatus"])
        self.assertEqual({"ok": False, "errors": [{"code": "missing_outputDir", "field": "outputDir"}]}, result["blank"])


    def test_masking_native_invoke_wrappers_propagate_each_rejection_with_exact_payload(self) -> None:
        from test_frontend_state_helpers import canonical_review_manifest, run_node_helper

        manifest = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/services/tauri/maskingContracts.ts",
            f"(async () => {{ const base = {manifest}; const finalizedManifest = {{ ...base, reviewItems: base.reviewItems.map((item) => ({{ ...item, status: 'resolved' }})) }}; const session = loadModule(path.resolve('src/state/maskingSession.ts')); const identity = (manifest) => ({{ runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile }}); const reviewReport = session.parseBoundSafeReport({{ product_checks: {{}}, analysisManifest: base, reviewQueue: base.reviewItems }}, identity(base)).value; const finalizedReport = session.parseBoundSafeReport({{ product_checks: {{}}, analysisManifest: finalizedManifest, reviewQueue: finalizedManifest.reviewItems }}, identity(finalizedManifest)).value; const request = {{ runId: 'run-1', analysisRevision: 7, manifestHash: 'b'.repeat(64), destination: '/out/final.pdf', saveToken: '0'.repeat(32), warningsConfirmed: false }}; const prepared = m.prepareFinalizeMaskingRun(request, finalizedReport); const review = {{ runId: 'run-1', analysisRevision: 7, manifestHash: 'b'.repeat(64), reviewId: 'review-1', resolution: {{ kind: 'boundary', pageStart: 0, pageEnd: 0, segmentKind: 'attachment' }} }}; const cases = [ ['state', (invoke) => m.getMaskingRunState(invoke, {{ runId: 'run-1' }})], ['review', (invoke) => m.resolveMaskingReview(invoke, review, reviewReport)], ['finalize', (invoke) => m.finalizeMaskingRun(invoke, prepared.value)] ]; const results = []; for (const [name, call] of cases) {{ const calls = []; const error = new Error(`${{name}} rejected`); try {{ await call(async (command, payload) => {{ calls.push({{ command, payload }}); throw error; }}); }} catch (caught) {{ results.push({{ name, calls, sameError: caught === error, message: caught.message }}); }} }} return results; }})()"
        )
        self.assertEqual(
            result,
            [
                {"name": "state", "calls": [{"command": "get_masking_run_state", "payload": {"runId": "run-1"}}], "sameError": True, "message": "state rejected"},
                {"name": "review", "calls": [{"command": "resolve_masking_review", "payload": {"request": {"runId": "run-1", "analysisRevision": 7, "manifestHash": "b" * 64, "reviewId": "review-1", "resolution": {"kind": "boundary", "pageStart": 0, "pageEnd": 0, "segmentKind": "attachment"}}}}], "sameError": True, "message": "review rejected"},
                {"name": "finalize", "calls": [{"command": "finalize_masking_run", "payload": {"request": {"runId": "run-1", "analysisRevision": 7, "manifestHash": "b" * 64, "destination": "/out/final.pdf", "saveToken": "0" * 32, "warningsConfirmed": False}}}], "sameError": True, "message": "finalize rejected"},
            ],
        )
    def test_public_save_presentation_transitions_pending_ready_and_blocked(self) -> None:
        from test_frontend_state_helpers import canonical_review_manifest, run_node_helper

        pending = json.dumps(canonical_review_manifest())
        resolved = json.dumps(canonical_review_manifest(status="resolved"))
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const pending = {pending}; const resolved = {resolved};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = (manifest) => ({ runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile });"
            "const report = (manifest) => session.parseBoundSafeReport({ product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity(manifest)).value;"
            "return { pending: m.publicFinalSavePresentation({ report: report(pending) }), ready: m.publicFinalSavePresentation({ report: report(resolved) }), blocked: m.publicFinalSavePresentation({ report: null }), blockedGate: m.finalSaveGate({ report: null }) };"
            "})()",
        )
        self.assertEqual("review", result["pending"]["stateName"])
        self.assertEqual("사용자 확인 필요", result["pending"]["title"])
        self.assertEqual("pass", result["ready"]["stateName"])
        self.assertEqual("서버 검토 완료", result["ready"]["title"])
        self.assertEqual("fail", result["blocked"]["stateName"])
        self.assertEqual("최종 저장 차단", result["blocked"]["title"])
        self.assertEqual("현재 서버 검토 세션이 없습니다. 문서를 다시 분석하세요.", result["blocked"]["detail"])
        self.assertEqual(["missing_current_session"], result["blockedGate"]["reasonCodes"])
if __name__ == "__main__":
    unittest.main()
