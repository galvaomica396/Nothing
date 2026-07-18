import json
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def legacy_typescript_source() -> str:
    legacy_root = REPO_ROOT / "src" / "legacy"
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(legacy_root.rglob("*.ts"))
    )


def run_node_helper(module_path: str, expression: str):
    script = "\n".join(
        [
            'const fs = require("fs");',
            'const path = require("path");',
            'const ts = require("typescript");',
            'const vm = require("vm");',
            "const cache = new Map();",
            f"const sourcePath = path.resolve({json.dumps(str(REPO_ROOT))}, {json.dumps(module_path)});",
            "function resolveTsModule(basePath, specifier) {",
            "  const resolved = path.resolve(path.dirname(basePath), specifier);",
            "  const candidates = [resolved, `${resolved}.ts`, path.join(resolved, 'index.ts')];",
            "  for (const candidate of candidates) {",
            "    if (fs.existsSync(candidate)) return candidate;",
            "  }",
            "  return require.resolve(specifier, { paths: [path.dirname(basePath)] });",
            "}",
            "function loadModule(filePath) {",
            "  const absolutePath = path.resolve(filePath);",
            "  if (cache.has(absolutePath)) return cache.get(absolutePath).exports;",
            '  const source = fs.readFileSync(absolutePath, "utf8");',
            "  const js = ts.transpileModule(source, {",
            "    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }",
            "  }).outputText;",
            "  const module = { exports: {} };",
            "  cache.set(absolutePath, module);",
            "  function localRequire(specifier) {",
            "    if (specifier.startsWith('.')) return loadModule(resolveTsModule(absolutePath, specifier));",
            "    return require(specifier);",
            "  }",
            "  const sandbox = { module, exports: module.exports, require: localRequire, document: { querySelector: () => null } };",
            "  vm.runInNewContext(js, sandbox, { filename: absolutePath });",
            "  return module.exports;",
            "}",
            "const m = loadModule(sourcePath);",
            f"const result = {expression};",
            "Promise.resolve(result).then((value) => {",
            "  console.log(JSON.stringify(value));",
            "});",
        ]
    )
    output = subprocess.check_output(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
    )
    return json.loads(output)


class FrontendStateHelperTests(unittest.TestCase):
    def test_document_provenance_transitions_preserve_roles(self):
        result = run_node_helper(
            "src/state/documentProvenance.ts",
            "(() => {"
            "const selected = m.selectOriginalDocument(m.emptyDocumentProvenance(), '/docs/original.pdf', 'pdf');"
            "const generated = m.adoptGeneratedPreview(selected, '/work/generated.pdf', '/work/reported.pdf');"
            "const manual = m.adoptManualPreview(generated, '/work/manual.pdf');"
            "const verified = m.adoptLoadVerifiedFinalContinuation(manual, '/out/final.pdf');"
            "const continued = m.adoptManualPreview(verified, '/work/continued.pdf');"
            "const unavailable = m.adoptUnavailableFinalContinuation(manual, '/out/unavailable.pdf');"
            "const remasked = m.adoptGeneratedPreview(m.resetDerivedProvenance(verified), '/work/remasked.pdf', '/work/remasked.pdf');"
            "return { selected, generated, manual, verified, continued, unavailable, remasked, cleared: m.resetDerivedProvenance(verified) };"
            "})()",
        )

        self.assertEqual(result["selected"]["original"], {"path": "/docs/original.pdf", "kind": "pdf"})
        self.assertIsNone(result["selected"]["continuation"])
        for role in ["generated", "manual", "final"]:
            self.assertFalse(result["selected"][role]["path"])
        self.assertEqual(result["generated"]["generated"]["path"], "/work/generated.pdf")
        self.assertEqual(result["generated"]["generated"]["artifactPath"], "/work/reported.pdf")
        self.assertEqual(result["manual"]["manual"]["path"], "/work/manual.pdf")
        self.assertEqual(result["verified"]["final"]["path"], "/out/final.pdf")
        self.assertEqual(result["verified"]["continuation"], {"state": "ready", "path": "/out/final.pdf"})
        self.assertFalse(result["verified"]["generated"]["path"])
        self.assertFalse(result["verified"]["manual"]["path"])
        self.assertEqual(result["continued"]["manual"]["path"], "/work/continued.pdf")
        self.assertEqual(result["continued"]["final"]["path"], "/out/final.pdf")
        self.assertEqual(result["continued"]["continuation"], {"state": "ready", "path": "/out/final.pdf"})
        self.assertEqual(result["unavailable"]["final"]["path"], "/out/unavailable.pdf")
        self.assertEqual(result["unavailable"]["continuation"], {"state": "unavailable", "path": "/out/unavailable.pdf"})
        self.assertFalse(result["unavailable"]["generated"]["path"])
        self.assertFalse(result["unavailable"]["manual"]["path"])
        self.assertEqual(result["remasked"]["original"], result["selected"]["original"])
        self.assertEqual(result["remasked"]["generated"]["path"], "/work/remasked.pdf")
        self.assertFalse(result["remasked"]["manual"]["path"])
        self.assertFalse(result["remasked"]["final"]["path"])
        self.assertIsNone(result["remasked"]["continuation"])
        self.assertEqual(result["cleared"]["original"], result["selected"]["original"])
        self.assertFalse(result["cleared"]["generated"]["path"])
        self.assertFalse(result["cleared"]["generated"]["artifactPath"])
        self.assertFalse(result["cleared"]["manual"]["path"])
        self.assertFalse(result["cleared"]["final"]["path"])
        self.assertIsNone(result["cleared"]["continuation"])

    def test_document_provenance_selectors_prioritize_final_continuation(self):
        result = run_node_helper(
            "src/state/documentProvenance.ts",
            "(() => {"
            "const original = m.selectOriginalDocument(m.emptyDocumentProvenance(), '/docs/original.pdf', 'pdf');"
            "const generated = m.adoptGeneratedPreview(original, '/work/generated.pdf', '/work/reported.pdf');"
            "const manual = m.adoptManualPreview(generated, '/work/manual.pdf');"
            "const verified = m.adoptLoadVerifiedFinalContinuation(manual, '/out/final.pdf');"
            "const unavailable = m.adoptUnavailableFinalContinuation(manual, '/out/unavailable.pdf');"
            "const continued = m.adoptManualPreview(verified, '/work/continued.pdf');"
            "return [m.emptyDocumentProvenance(), original, generated, manual, verified, unavailable, continued].map((p) => ({"
            "result: m.resultSourcePath(p), save: m.finalSaveSourcePath(p),"
            "target: m.canvasWindowTargetPath(p), candidates: m.canvasWindowTargetCandidates(p),"
            "masked: m.hasMaskedArtifact(p), status: m.statusSourcePath(p), latest: m.latestGeneratedPath(p)"
            "}));"
            "})()",
        )

        self.assertEqual(result[0], {"result": "", "save": "", "target": "", "candidates": [], "masked": False, "status": "", "latest": ""})
        self.assertEqual(result[1]["target"], "/docs/original.pdf")
        self.assertEqual(result[1]["candidates"], ["/docs/original.pdf"])
        self.assertFalse(result[1]["masked"])
        self.assertEqual(result[2]["result"], "/work/generated.pdf")
        self.assertEqual(result[2]["save"], "/work/generated.pdf")
        self.assertEqual(result[2]["status"], "/work/generated.pdf")
        self.assertEqual(result[3]["result"], "/work/manual.pdf")
        self.assertEqual(result[3]["candidates"], ["/work/manual.pdf", "/work/generated.pdf", "/docs/original.pdf"])
        self.assertEqual(result[4]["result"], "/out/final.pdf")
        self.assertEqual(result[4]["save"], "/out/final.pdf")
        self.assertEqual(result[4]["candidates"], ["/out/final.pdf"])
        self.assertTrue(result[4]["masked"])
        self.assertEqual(result[5]["result"], "")
        self.assertEqual(result[5]["save"], "")
        self.assertEqual(result[5]["target"], "/out/unavailable.pdf")
        self.assertEqual(result[5]["candidates"], ["/out/unavailable.pdf"])
        self.assertFalse(result[5]["masked"])
        self.assertEqual(result[5]["status"], "/out/unavailable.pdf")
        self.assertEqual(result[6]["result"], "/work/continued.pdf")
        self.assertEqual(result[6]["save"], "/work/continued.pdf")
        self.assertEqual(result[6]["target"], "/work/continued.pdf")
        self.assertEqual(result[6]["candidates"], ["/work/continued.pdf"])

    def test_final_save_confirmation_summary_keeps_missing_report_advisory(self):
        result = run_node_helper(
            "src/workflowFlow.ts",
            "m.finalSaveConfirmationSummary({"
            "maskBoxes: 3,"
            "restoreBoxes: 1,"
            "keywords: '홍길동, 서울시청',"
            "outputFileName: 'fixture_masked.pdf',"
            "pdfRedaction: true,"
            "maskedTxtExport: false,"
            "maskedTxtRequested: false,"
            "deidentificationMode: 'token',"
            "safeReportPath: ''"
            "})",
        )

        self.assertTrue(result["canConfirm"])
        self.assertEqual(result["outputFileLabel"], "fixture_masked.pdf")
        self.assertEqual(result["maskCountLabel"], "마스킹 박스 수 3개")
        self.assertEqual(result["restoreCountLabel"], "복원 박스 수 1개")
        self.assertEqual(result["keywordCountLabel"], "키워드 수 2개")
        self.assertEqual(result["blockingReason"], "")
        self.assertIn("자동 검증 전", result["securityLabels"])

    def test_workflow_screen_contract_rejects_modal_screen(self):
        result = run_node_helper(
            "src/workflowFlow.ts",
            "({ screens: m.WORKFLOW_SCREENS, modal: m.isWorkflowScreen('settingsModal'), review: m.isWorkflowScreen('review'), settings: m.isWorkflowScreen('settings') })",
        )

        self.assertIn("settings", result["screens"])
        self.assertIn("documents", result["screens"])
        # v4 P2: canvas 화면이 통합 "문서" 화면으로 흡수되어 1급 화면 목록에서 빠졌다.
        self.assertNotIn("canvas", result["screens"])
        self.assertNotIn("review", result["screens"])
        self.assertNotIn("hwpx", result["screens"])
        self.assertFalse(result["modal"])
        self.assertFalse(result["review"])
        self.assertTrue(result["settings"])

    def test_final_save_is_user_discretionary_and_only_requires_masked_document(self):
        result = run_node_helper(
            "src/workflowFlow.ts",
            "({"
            "ready: m.finalSaveReadiness({ finalDocumentPath: '/tmp/doc.pdf', safeReportPath: '/tmp/report.json' }),"
            "missingDocument: m.finalSaveReadiness({ finalDocumentPath: '', safeReportPath: '/tmp/report.json' }),"
            "missingReport: m.finalSaveReadiness({ finalDocumentPath: '/tmp/doc.pdf', safeReportPath: '' }),"
            "defaultName: m.finalSaveDefaultFileName('/tmp/기밀문서.pdf'),"
            "windowsName: m.finalSaveDefaultFileName('C:\\\\docs\\\\contract.final.PDF')"
            "})",
        )

        # 저장할 마스킹본이 존재하면 저장할 수 있고, 저장 위치는 준비 상태에 포함하지 않는다.
        self.assertTrue(result["ready"]["canSave"])
        # 마스킹본 부재만이 저장의 물리적 전제 미충족(유일한 canSave=false 사유).
        self.assertFalse(result["missingDocument"]["canSave"])
        self.assertIn("마스킹본", result["missingDocument"]["reason"])
        self.assertEqual(result["defaultName"], "기밀문서_masked")
        self.assertEqual(result["windowsName"], "contract.final_masked")
        self.assertTrue(result["missingReport"]["canSave"])
        self.assertIn("자동 검증", result["missingReport"]["reason"])

    def test_document_workflow_readiness_tracks_base_manual_and_final_save(self):
        result = run_node_helper(
            "src/workflowFlow.ts",
            "({"
            "empty: m.documentWorkflowReadiness({ documentKind: '', outputDir: '', basePreviewPath: '', manualPreviewPath: '', safeReportPath: '', boxCount: 0 }),"
            "basePdf: m.documentWorkflowReadiness({ documentKind: 'pdf', outputDir: '/tmp/out', basePreviewPath: '/tmp/masked.pdf', manualPreviewPath: '', safeReportPath: '/tmp/report.json', boxCount: 0 }),"
            "withBoxes: m.documentWorkflowReadiness({ documentKind: 'pdf', outputDir: '/tmp/out', basePreviewPath: '/tmp/masked.pdf', manualPreviewPath: '', safeReportPath: '/tmp/report.json', boxCount: 2 }),"
            "manualReady: m.documentWorkflowReadiness({ documentKind: 'pdf', outputDir: '/tmp/out', basePreviewPath: '/tmp/masked.pdf', manualPreviewPath: '/tmp/manual.pdf', safeReportPath: '/tmp/report.json', boxCount: 0 }),"
            "manualNoReport: m.documentWorkflowReadiness({ documentKind: 'pdf', outputDir: '/tmp/out', basePreviewPath: '/tmp/masked.pdf', manualPreviewPath: '/tmp/manual.pdf', safeReportPath: '', boxCount: 0 }),"
            "unavailable: m.documentWorkflowReadiness({ documentKind: 'pdf', outputDir: '/tmp/out', basePreviewPath: '', manualPreviewPath: '', safeReportPath: '/tmp/report.json', boxCount: 0, continuationUnavailable: true })"
            "})",
        )

        self.assertFalse(result["empty"]["canRunBaseMasking"])
        self.assertIn("문서", result["empty"]["baseMaskingReason"])
        self.assertTrue(result["basePdf"]["canRunBaseMasking"])
        self.assertFalse(result["basePdf"]["canApplyManualPreview"])
        self.assertIn("박스", result["basePdf"]["manualApplyReason"])
        self.assertTrue(result["withBoxes"]["canApplyManualPreview"])
        self.assertTrue(result["withBoxes"]["canFinalSave"])
        self.assertIn("자동 반영", result["withBoxes"]["finalSaveReason"])
        self.assertTrue(result["manualReady"]["canFinalSave"])
        self.assertEqual(result["manualReady"]["phaseLabel"], "최종 저장 준비")
        # v4.2.0: 리포트(자동 검증) 전이어도 마스킹본이 있으면 최종 저장 가능(사용자
        # 재량). 하드 차단이 아니라 "자동 검증 전" 안내만 노출한다.
        self.assertTrue(result["manualNoReport"]["canFinalSave"])
        self.assertIn("자동 검증", result["manualNoReport"]["finalSaveReason"])
        self.assertFalse(result["unavailable"]["canRunBaseMasking"])
        self.assertFalse(result["unavailable"]["canApplyManualPreview"])
        self.assertFalse(result["unavailable"]["canFinalSave"])
        self.assertIn("다시", result["unavailable"]["baseMaskingReason"])

    def test_masking_run_rejects_unavailable_final_without_original_fallback(self):
        result = run_node_helper(
            "src/features/masking-run/maskingRunController.ts",
            "(async () => {"
            "const statuses = []; let runCount = 0; let resetCount = 0;"
            "const state = { maskingRunning: false, savingInFlight: false, documentProvenance: {"
            "original: { path: '/docs/original.pdf', kind: 'pdf' }, generated: { path: '', artifactPath: '' },"
            "manual: { path: '' }, final: { path: '/out/final.pdf' }, continuation: { state: 'unavailable', path: '/out/final.pdf' }"
            "} };"
            "const controller = m.createMaskingRunController({"
            "state, setStatus: (value) => statuses.push(value), isCustomRegionScope: () => false,"
            "runMaskingPipeline: async () => { runCount += 1; return {}; },"
            "resetDerivedArtifacts: () => { resetCount += 1; }"
            "});"
            "const value = await controller.runMaskingForSelectedDocument();"
            "return { value, runCount, resetCount, statuses };"
            "})()",
        )

        self.assertIsNone(result["value"])
        self.assertEqual(result["runCount"], 0)
        self.assertEqual(result["resetCount"], 0)
        self.assertIn("다시", result["statuses"][-1])

    def test_manual_apply_rejects_unavailable_final_without_original_fallback(self):
        result = run_node_helper(
            "src/features/manual-adjustment/manualAdjustmentController.ts",
            "(async () => {"
            "const statuses = []; let invokeCount = 0;"
            "const state = { documentProvenance: {"
            "original: { path: '/docs/original.pdf', kind: 'pdf' }, generated: { path: '', artifactPath: '' },"
            "manual: { path: '' }, final: { path: '/out/final.pdf' }, continuation: { state: 'unavailable', path: '/out/final.pdf' }"
            "}, maskingRunning: false, batchRunning: false, savingInFlight: false };"
            "const controller = m.createManualAdjustmentController({"
            "state, isPdfInput: () => true, setStatus: (value) => statuses.push(value),"
            "invokeCommand: async () => { invokeCount += 1; return {}; }"
            "});"
            "const value = await controller.applyPendingManualBoxes('apply');"
            "return { value, invokeCount, statuses };"
            "})()",
        )

        self.assertIsNone(result["value"])
        self.assertEqual(result["invokeCount"], 0)
        self.assertIn("다시", result["statuses"][-1])


    def test_pdf_canvas_loaded_without_base_masking_can_apply_manual_boxes(self):
        result = run_node_helper(
            "src/workflowFlow.ts",
            "({"
            "withoutOutput: m.documentWorkflowReadiness({ documentKind: 'pdf', basePreviewPath: '', manualPreviewPath: '', safeReportPath: '', boxCount: 1, latestDocumentPath: '/tmp/original.pdf' }),"
            "withOutput: m.documentWorkflowReadiness({ documentKind: 'pdf', basePreviewPath: '', manualPreviewPath: '', safeReportPath: '', boxCount: 1, latestDocumentPath: '/tmp/original.pdf' })"
            "})",
        )

        self.assertTrue(result["withoutOutput"]["canApplyManualPreview"])
        self.assertIn("수동 보정", result["withoutOutput"]["manualApplyReason"])
        self.assertEqual(result["withoutOutput"], result["withOutput"])

    def test_default_output_dir_selection_only_resolves_for_single_pdf_without_existing_output(self):
        result = run_node_helper(
            "src/services/tauri/defaultOutputDir.ts",
            "({"
            "empty: m.planDefaultOutputDirSelection({ currentOutputDir: '', selectedDocumentPaths: [] }),"
            "multiple: m.planDefaultOutputDirSelection({ currentOutputDir: '', selectedDocumentPaths: ['/docs/a.pdf', '/other/b.pdf'] }),"
            "existing: m.planDefaultOutputDirSelection({ currentOutputDir: '/kept/out', selectedDocumentPaths: ['/docs/a.pdf'] }),"
            "single: m.planDefaultOutputDirSelection({ currentOutputDir: '', selectedDocumentPaths: ['/docs/a.pdf'] })"
            "})",
        )

        self.assertEqual(result["empty"], {"kind": "preserve", "outputDir": "", "reason": "empty_selection"})
        self.assertEqual(result["multiple"], {"kind": "preserve", "outputDir": "", "reason": "multiple_selection"})
        self.assertEqual(result["existing"], {"kind": "preserve", "outputDir": "/kept/out", "reason": "existing_output_dir"})
        self.assertEqual(result["single"], {"kind": "resolve", "outputDir": "", "documentPath": "/docs/a.pdf", "reason": "single_selection"})

    def test_task1_single_pdf_default_output_dir_uses_tauri_result_contract(self):
        print("OK test_task1_single_pdf_default_output_dir_uses_tauri_result_contract")
        print("OK test_task1_masking_session_reducers_preserve_base_masking_percent")
        result = run_node_helper(
            "src/services/tauri/defaultOutputDir.ts",
            "(async () => {"
            "const calls = [];"
            "const resolved = await m.defaultOutputDirForSelection("
            "  async (command, payload) => {"
            "    calls.push({ command, payload });"
            "    return { status: 'ok', outputDir: '/tmp/rust-default' };"
            "  },"
            "  { currentOutputDir: '', selectedDocumentPaths: ['/docs/a.pdf'] }"
            ");"
            "return { resolved, calls };"
            "})()",
        )

        self.assertEqual(result["resolved"], "/tmp/rust-default")
        self.assertEqual(
            result["calls"],
            [{"command": "default_output_dir_for_document", "payload": {"documentPath": "/docs/a.pdf"}}],
        )

    def test_legacy_final_save_auto_applies_pending_manual_boxes(self):
        legacy_source = legacy_typescript_source()
        manual = (REPO_ROOT / "src" / "features" / "manual-adjustment" / "manualAdjustmentController.ts").read_text(encoding="utf-8")
        finalization = (REPO_ROOT / "src" / "features" / "finalization" / "finalizationController.ts").read_text(encoding="utf-8")

        self.assertIn("async function applyPendingManualBoxes", manual)
        self.assertIn('await applyPendingManualBoxes("수동마스킹실행")', legacy_source)
        self.assertIn('await deps.applyPendingManualBoxes("최종 저장 전 수동 보정 자동 반영")', finalization)
        self.assertIn("reportPath: previousReportPath", manual)
        self.assertIn("result.revalidation_report", manual)
        self.assertIn('deps.invokeCommand<string>("read_text_file", { path: result.revalidation_report })', manual)
        self.assertLess(
            finalization.index('await deps.applyPendingManualBoxes("최종 저장 전 수동 보정 자동 반영")'),
            finalization.index('await deps.invokeCommand<FinalizeResult>("finalize_manual_output_to_selected_path"'),
        )
        self.assertLess(
            manual.index(
                "await deps.loadResultPdf(result.output_file, previousPreview, sessionIsCurrent)"
            ),
            manual.index("adoptManualPreview(previousProvenance, result.output_file)"),
        )
        self.assertIn("adoptLoadVerifiedFinalContinuation", finalization)
        self.assertIn("adoptUnavailableFinalContinuation", finalization)
        self.assertIn("loadResultPdf,", legacy_source)
        self.assertIn("updateCanvasControls,", legacy_source)
        self.assertIn("cancelCanvasInteraction: cancelActiveInteraction", legacy_source)
        self.assertIn("documentEditRevision", finalization)
        self.assertIn("state.documentEditRevision === finalizationEditRevision", finalization)
        self.assertLess(
            finalization.index("deps.cancelCanvasInteraction()"),
            finalization.index('await deps.invokeCommand<FinalizeResult>("finalize_manual_output_to_selected_path"'),
        )
        finalize_call = finalization.index('const result = await deps.invokeCommand<FinalizeResult>("finalize_manual_output_to_selected_path"')
        masked_path_invalidated = finalization.index('state.latestMaskedPath = "";', finalize_call)
        masked_policy_invalidated = finalization.index('state.latestMaskedTextPolicy = "";', finalize_call)
        final_load = finalization.index('await deps.loadResultPdf(result.final_output_file, "", finalizationIsCurrent)')
        self.assertLess(finalize_call, masked_path_invalidated)
        self.assertLess(masked_path_invalidated, final_load)
        self.assertLess(masked_policy_invalidated, final_load)
        self.assertLess(
            finalization.index('await deps.loadResultPdf(result.final_output_file, "", finalizationIsCurrent)'),
            finalization.index("state.documentProvenance = adoptLoadVerifiedFinalContinuation"),
        )
        post_load = finalization[final_load:]
        self.assertLess(
            post_load.index("if (!sessionIsCurrent()) return;"),
            post_load.index(
                'markContinuationUnavailable("파일은 저장되었으나 저장 중 문서 변경으로 무결성 확인을 완료하지 못했습니다.'
            ),
        )
        self.assertLess(
            post_load.index(
                'markContinuationUnavailable("파일은 저장되었으나 저장 중 문서 변경으로 무결성 확인을 완료하지 못했습니다.'
            ),
            post_load.index("state.documentProvenance = adoptLoadVerifiedFinalContinuation"),
        )
        self.assertLess(
            final_load,
            finalization.index(
                'markContinuationUnavailable("파일은 저장되었으나 무결성 확인에 실패했습니다.'
            ),
        )
        verified_adoption = finalization.index("state.documentProvenance = adoptLoadVerifiedFinalContinuation")
        saved_at = finalization.index("deps.recordSavedAt(", verified_adoption)
        self.assertLess(final_load, verified_adoption)
        self.assertLess(verified_adoption, saved_at)

    def test_manual_apply_discards_completion_after_document_session_changes(self):
        result = run_node_helper(
            "src/features/manual-adjustment/manualAdjustmentController.ts",
            "(async () => {"
            "const original = { original: { path: '/docs/original.pdf', kind: 'pdf' }, generated: { path: '/work/generated.pdf', artifactPath: '/work/generated.pdf' }, manual: { path: '' }, final: { path: '' } };"
            "const state = { documentProvenance: original, outputDir: '/out', currentResultPage: 1, resultDoc: {}, scale: 1, boxes: [{ page: 1, x0: 1, y0: 1, x1: 2, y1: 2, mode: 'mask' }], mode: 'mask', selectedCanvasBoxIndex: -1, canvasMode: true, maskingRunning: false, batchRunning: false, extractedText: '', maskedText: '', baseExtractedText: '', baseMaskedText: '', preManualPreviewPdf: '', preManualExtractedText: '', preManualMaskedText: '', latestReportPath: '', latestReport: null, lastPreviewDiagnostics: '', restoreRevalidationFailed: false };"
            "let resolveApply; let loadCount = 0; const statuses = [];"
            "const controller = m.createManualAdjustmentController({"
            "state, invokeCommand: () => new Promise((resolve) => { resolveApply = resolve; }),"
            "displayModeEl: { value: 'black' }, isPdfInput: () => true, ensurePreviewWorkDir: async () => '/work',"
            "loadResultPdf: async () => { loadCount += 1; }, updateWorkflowReadiness: () => {}, setStatus: (value) => statuses.push(value),"
            "btnCanvasClear: { disabled: false }, renderFinalState: () => {}, setTextCompareContents: () => {}"
            "});"
            "const pending = controller.applyPendingManualBoxes('apply');"
            "while (!resolveApply) await Promise.resolve();"
            "state.documentProvenance = { ...original, generated: { path: '', artifactPath: '' }, manual: { path: '' }, final: { path: '' } };"
            "resolveApply({ status: 'applied', output_file: '/work/manual.pdf', mask_count: 1, restore_count: 0, applied_count: 1 });"
            "const applied = await pending;"
            "return { applied, loadCount, provenance: state.documentProvenance, boxCount: state.boxes.length, statuses };"
            "})()",
        )

        self.assertIsNone(result["applied"])
        self.assertEqual(result["loadCount"], 0)
        self.assertFalse(result["provenance"]["generated"]["path"])
        self.assertFalse(result["provenance"]["manual"]["path"])
        self.assertEqual(result["boxCount"], 1)

        session = (REPO_ROOT / "src" / "features" / "document-session" / "documentSessionController.ts").read_text(encoding="utf-8")
        manual = (REPO_ROOT / "src" / "features" / "manual-adjustment" / "manualAdjustmentController.ts").read_text(encoding="utf-8")
        finalization = (REPO_ROOT / "src" / "features" / "finalization" / "finalizationController.ts").read_text(encoding="utf-8")
        self.assertIn("if (deps.isBusy())", session)
        self.assertIn("deps.btnCanvasClear.disabled = !state.documentProvenance.original.path || busy", manual)
        self.assertIn("deps.btnClear.disabled = !state.documentProvenance.original.path || busy", finalization)

    def test_manual_apply_discards_revalidation_after_document_session_changes(self):
        result = run_node_helper(
            "src/features/manual-adjustment/manualAdjustmentController.ts",
            "(async () => {"
            "const original = { original: { path: '/docs/original.pdf', kind: 'pdf' }, generated: { path: '/work/generated.pdf', artifactPath: '/work/generated.pdf' }, manual: { path: '' }, final: { path: '' } };"
            "const state = { documentProvenance: original, outputDir: '/out', currentResultPage: 1, resultDoc: {}, scale: 1, boxes: [{ page: 1, x0: 1, y0: 1, x1: 2, y1: 2, mode: 'restore' }], mode: 'restore', selectedCanvasBoxIndex: -1, canvasMode: true, maskingRunning: false, batchRunning: false, extractedText: 'old extracted', maskedText: 'old masked', baseExtractedText: '', baseMaskedText: '', preManualPreviewPdf: '', preManualExtractedText: '', preManualMaskedText: '', latestReportPath: '/work/old.safe_report.json', latestReport: { marker: 'old' }, lastPreviewDiagnostics: '', restoreRevalidationFailed: false };"
            "let resolveRead; let renderCount = 0; let compareCount = 0;"
            "const controller = m.createManualAdjustmentController({"
            "state, invokeCommand: (command) => command === 'apply_manual_boxes' ? Promise.resolve({ status: 'applied', output_file: '/work/manual.pdf', mask_count: 0, restore_count: 1, applied_count: 1, requires_revalidation: true, revalidation_status: 'passed', revalidation_report: '/work/manual.safe_report.json' }) : new Promise((resolve) => { resolveRead = resolve; }),"
            "displayModeEl: { value: 'black' }, isPdfInput: () => true, ensurePreviewWorkDir: async () => '/work', loadResultPdf: async () => {},"
            "updateWorkflowReadiness: () => {}, setStatus: () => {}, btnCanvasClear: { disabled: false }, updateMeta: () => {},"
            "renderFinalState: () => { renderCount += 1; }, setTextCompareContents: () => { compareCount += 1; }"
            "});"
            "const pending = controller.applyPendingManualBoxes('apply');"
            "while (!resolveRead) await Promise.resolve();"
            "state.documentProvenance = { original: { path: '/docs/new.pdf', kind: 'pdf' }, generated: { path: '', artifactPath: '' }, manual: { path: '' }, final: { path: '' } };"
            "state.latestReport = { marker: 'new' }; state.latestReportPath = '/work/new.safe_report.json';"
            "resolveRead(JSON.stringify({ product_checks: { quality_gate_passed: false }, document_redaction: { status: 'failed' }, review_items: [] }));"
            "const applied = await pending;"
            "return { applied, provenance: state.documentProvenance, report: state.latestReport, reportPath: state.latestReportPath, boxCount: state.boxes.length, renderCount, compareCount };"
            "})()",
        )

        self.assertIsNone(result["applied"])
        self.assertEqual(result["provenance"]["original"]["path"], "/docs/new.pdf")
        self.assertEqual(result["report"]["marker"], "new")
        self.assertEqual(result["reportPath"], "/work/new.safe_report.json")
        self.assertEqual(result["boxCount"], 1)
        self.assertEqual(result["renderCount"], 0)
        self.assertEqual(result["compareCount"], 0)

    def test_shared_derived_reset_is_used_by_original_canvas_clear_and_remask(self):
        legacy_source = legacy_typescript_source()
        session = (REPO_ROOT / "src" / "features" / "document-session" / "documentSessionController.ts").read_text(encoding="utf-8")
        masking = (REPO_ROOT / "src" / "features" / "masking-run" / "maskingRunController.ts").read_text(encoding="utf-8")
        load_original = session[session.index("async function loadOriginalPdf") : session.index("async function loadCanvasWorkspacePdf")]
        load_canvas = session[session.index("async function loadCanvasWorkspacePdf") : session.index("async function readCanvasWindowLaunchState")]
        clear_flow = session[session.index("async function clearDerivedArtifacts") : session.index("return {", session.index("async function clearDerivedArtifacts"))]

        self.assertIn('resetDerivedArtifacts("new-document");', load_original)
        self.assertIn('resetDerivedArtifacts("canvas-hydrate", targetPath);', load_canvas)
        self.assertIn('resetDerivedArtifacts("clear");', clear_flow)
        self.assertIn("void clearDerivedArtifacts();", legacy_source)
        self.assertIn("deps.resetDerivedArtifacts();", masking)
        self.assertLess(masking.index("deps.resetDerivedArtifacts();"), masking.index("deps.runMaskingPipeline({"))
        self.assertIn("const sessionIsCurrent = () => state.documentProvenance === runProvenance;", masking)
        self.assertIn("deps.loadResultPdf(previewCandidate, inputPdfForRun, sessionIsCurrent);", masking)

    def test_canvas_entry_readiness_allows_pdf_and_empty_standalone_only(self):
        result = run_node_helper(
            "src/workflowFlow.ts",
            "({"
            "pdf: m.canvasEntryReadiness({ documentKind: 'pdf', standalone: false }),"
            "emptyStandalone: m.canvasEntryReadiness({ documentKind: '', standalone: true }),"
            "emptyMain: m.canvasEntryReadiness({ documentKind: '', standalone: false })"
            "})",
        )

        self.assertTrue(result["pdf"]["canEnter"])
        self.assertTrue(result["emptyStandalone"]["canEnter"])
        self.assertFalse(result["emptyMain"]["canEnter"])

    def test_canvas_tool_readiness_explains_disabled_editing_and_save(self):
        result = run_node_helper(
            "src/canvasToolUx.ts",
            "({"
            "empty: m.canvasToolReadinessText({ hasPdf: false, hasResultDoc: false, hasFinalDocument: false }),"
            "loaded: m.canvasToolReadinessText({ hasPdf: true, hasResultDoc: true, hasFinalDocument: true }),"
            "summary: m.canvasFinalSaveSummary({ maskBoxes: 2, restoreBoxes: 1, keywords: '홍길동, 서초구청', hasFinalDocument: false })"
            "})",
        )

        self.assertIn("PDF", result["empty"]["editReason"])
        self.assertFalse(result["empty"]["canEdit"])
        self.assertIn("미리보기 문서", result["empty"]["saveReason"])
        self.assertTrue(result["loaded"]["canEdit"])
        self.assertTrue(result["loaded"]["canSave"])
        self.assertEqual(result["summary"]["maskLabel"], "마스킹 박스 2개")
        self.assertEqual(result["summary"]["restoreLabel"], "복원 박스 1개")
        self.assertEqual(result["summary"]["keywordLabel"], "키워드 2개")
        self.assertIn("최종 저장 전", result["summary"]["saveLabel"])

    def test_canvas_box_delete_keeps_next_selection_on_same_page(self):
        result = run_node_helper(
            "src/canvasWorkbench.ts",
            "(() => {"
            "const rowsBefore = m.createCanvasBoxRows(["
            "{ page: 0, x0: 10, y0: 10, x1: 40, y1: 30, mode: 'mask' },"
            "{ page: 0, x0: 50, y0: 10, x1: 90, y1: 35, mode: 'restore' },"
            "{ page: 1, x0: 15, y0: 15, x1: 25, y1: 25, mode: 'mask' }"
            "], 0, 0);"
            "const deleted = m.deleteCanvasBoxAtIndex(["
            "{ page: 0, x0: 10, y0: 10, x1: 40, y1: 30, mode: 'mask' },"
            "{ page: 0, x0: 50, y0: 10, x1: 90, y1: 35, mode: 'restore' },"
            "{ page: 1, x0: 15, y0: 15, x1: 25, y1: 25, mode: 'mask' }"
            "], 0);"
            "return { rowsBefore, deleted };"
            "})()",
        )

        self.assertEqual(len(result["rowsBefore"]), 2)
        # UX_SIMPLICITY_V3_4 §2: 박스 행 라벨은 유형만 노출 (n번 식별자·px 크기·
        # 페이지 수치 제거). 내부 필드 localNumber/pageLabel/modeLabel 은 유지된다.
        self.assertEqual(result["rowsBefore"][0]["label"], "마스킹")
        self.assertEqual(result["rowsBefore"][1]["label"], "복원")
        self.assertEqual(result["rowsBefore"][0]["localNumber"], 1)
        self.assertEqual(result["rowsBefore"][0]["pageLabel"], "1쪽")
        self.assertEqual(result["deleted"]["selectedBoxIndex"], 0)
        self.assertEqual(len(result["deleted"]["boxes"]), 2)
        self.assertEqual(result["deleted"]["boxes"][0]["mode"], "restore")

    def test_default_output_dir_contract_validates_payloads_and_results(self):
        result = run_node_helper(
            "src/services/tauri/contracts.ts",
            "({"
            "defaultOutputPayload: m.serializeDefaultOutputDirForDocumentPayload({ documentPath: '/tmp/in.pdf' }),"
            "invalidDefaultOutputPayload: m.serializeDefaultOutputDirForDocumentPayload({ documentPath: '' }),"
            "defaultOutputResult: m.parseDefaultOutputDirForDocumentResult({ status: 'ok', outputDir: '/tmp/out' }),"
            "invalidDefaultOutputResult: m.parseDefaultOutputDirForDocumentResult({ status: 'ok', outputDir: '' }),"
            "})",
        )

        self.assertTrue(result["defaultOutputPayload"]["ok"])
        self.assertEqual(result["defaultOutputPayload"]["value"]["documentPath"], "/tmp/in.pdf")
        self.assertFalse(result["invalidDefaultOutputPayload"]["ok"])
        self.assertEqual(result["invalidDefaultOutputPayload"]["errors"][0]["field"], "documentPath")
        self.assertTrue(result["defaultOutputResult"]["ok"])
        self.assertEqual(result["defaultOutputResult"]["value"]["outputDir"], "/tmp/out")
        self.assertFalse(result["invalidDefaultOutputResult"]["ok"])
        self.assertEqual(result["invalidDefaultOutputResult"]["errors"][0]["field"], "outputDir")

    def test_final_save_confirmation_summary_reports_security_and_post_save_actions(self):
        result = run_node_helper(
            "src/workflowFlow.ts",
            "({"
            "ready: m.finalSaveConfirmationSummary({ maskBoxes: 3, restoreBoxes: 1, keywords: '홍길동,서초구청', outputFileName: 'masked.pdf', pdfRedaction: true, displayMode: 'pseudonym', maskedTxtExport: false, maskedTxtRequested: false, deidentificationMode: 'token', safeReportPath: '/tmp/report.json' }),"
            "unsafe: m.finalSaveConfirmationSummary({ maskBoxes: 3, restoreBoxes: 1, keywords: '홍길동', outputFileName: 'masked.pdf', pdfRedaction: false, maskedTxtExport: true, maskedTxtRequested: true, deidentificationMode: 'partial', safeReportPath: '/tmp/report.json' }),"
            "unchecked: m.finalSaveConfirmationSummary({ maskBoxes: 0, restoreBoxes: 0, keywords: '', outputFileName: 'masked.pdf', pdfRedaction: true, maskedTxtExport: false, maskedTxtRequested: false, safeReportPath: '/tmp/report.json' }),"
            "blocked: m.finalSaveConfirmationSummary({ maskBoxes: 0, restoreBoxes: 0, keywords: '', outputFileName: '', pdfRedaction: false, maskedTxtExport: false, maskedTxtRequested: true, safeReportPath: '' })"
            "})",
        )

        self.assertTrue(result["ready"]["canConfirm"])
        self.assertEqual(result["ready"]["maskCountLabel"], "마스킹 박스 수 3개")
        self.assertEqual(result["ready"]["restoreCountLabel"], "복원 박스 수 1개")
        self.assertEqual(result["ready"]["keywordCountLabel"], "키워드 수 2개")
        self.assertIn("PDF 레닥션 적용됨", result["ready"]["securityLabels"])
        self.assertEqual("PDF: 가명 표시", result["ready"]["pdfPolicyLabel"])
        self.assertEqual("TXT: 저장 안 함", result["ready"]["txtPolicyLabel"])
        self.assertIn("비식별 TXT 저장 안 함", result["ready"]["securityLabels"])
        # v4.1: 리포트 내부화로 저장 후 액션에서 "리포트 보기"가 제거됐다(사용자
        # 산출 폴더에 리포트 파일이 생기지 않으므로 열어 볼 대상이 없다).
        self.assertEqual(result["ready"]["postSaveActions"], ["결과 열기", "폴더 열기"])
        self.assertNotIn("리포트 보기", result["ready"]["postSaveActions"])
        self.assertTrue(result["unsafe"]["canConfirm"])
        self.assertEqual("", result["unsafe"]["blockingReason"])
        self.assertEqual("PDF: 레닥션 꺼짐", result["unsafe"]["pdfPolicyLabel"])
        self.assertIn("비식별 TXT: 부분 마스킹", result["unsafe"]["securityLabels"])
        self.assertTrue(result["unchecked"]["canConfirm"])
        self.assertEqual("", result["unchecked"]["blockingReason"])
        self.assertFalse(result["blocked"]["canConfirm"])
        self.assertIn("파일명", result["blocked"]["blockingReason"])

    def test_settings_scope_status_distinguishes_app_defaults_and_current_work(self):
        result = run_node_helper(
            "src/workflowFlow.ts",
            "({"
            "current: m.settingsScopeStatus({ selectedDocumentPath: '/tmp/doc.pdf', currentDocumentName: 'doc.pdf' }),"
            "defaults: m.settingsScopeStatus({ selectedDocumentPath: '', currentDocumentName: '' }),"
            "defaultName: m.finalSaveDefaultFileName('/tmp/source.pdf')"
            "})",
        )

        self.assertIn("현재 작업에도 적용됨", result["current"]["applyLabel"])
        self.assertIn("앱 기본값", result["defaults"]["applyLabel"])
        self.assertEqual(result["defaultName"], "source_masked")

    def test_batch_document_append_filters_duplicates_and_unsupported_files(self):
        result = run_node_helper(
            "src/batchQueue.ts",
            "m.appendBatchDocuments("
            '[{ id: "1", path: "/tmp/a.pdf", basename: "a.pdf", kind: "pdf", status: "대기" }],'
            '["/tmp/a.pdf", "/tmp/b.hwpx", "/tmp/c.txt", "/tmp/d.PDF"],'
            "100"
            ")",
        )

        self.assertEqual([item["path"] for item in result], ["/tmp/a.pdf", "/tmp/d.PDF"])
        self.assertEqual(result[1]["kind"], "pdf")

    def test_batch_summary_and_action_state_are_behavioral(self):
        result = run_node_helper(
            "src/batchQueue.ts",
            "({"
            "summary: m.summarizeBatchItems(["
            '{ id: "1", path: "a.pdf", basename: "a.pdf", kind: "pdf", status: "대기" },'
            '{ id: "2", path: "b.pdf", basename: "b.pdf", kind: "pdf", status: "완료", outputPath: "/tmp/b.pdf", reportPath: "/tmp/b.json" },'
            '{ id: "3", path: "c.pdf", basename: "c.pdf", kind: "pdf", status: "실패", error: "실패" }'
            "]),"
            'failedActions: m.batchActionState({ id: "3", path: "c.pdf", basename: "c.pdf", kind: "pdf", status: "실패", error: "실패" }, false),'
            'runningActions: m.batchActionState({ id: "3", path: "c.pdf", basename: "c.pdf", kind: "pdf", status: "실패", error: "실패" }, true),'
            'doneActions: m.batchActionState({ id: "2", path: "b.pdf", basename: "b.pdf", kind: "pdf", status: "완료", outputPath: "/tmp/b.pdf", reportPath: "/tmp/b.json" }, true)'
            "})",
        )

        self.assertEqual(result["summary"], {"total": 3, "pending": 1, "done": 1, "failed": 1})
        self.assertTrue(result["failedActions"]["canRetry"])
        self.assertFalse(result["runningActions"]["canRetry"])
        self.assertTrue(result["doneActions"]["canOpenOutput"])
        self.assertTrue(result["doneActions"]["canOpenReport"])

    def test_canvas_box_rows_filter_current_page_and_action_state(self):
        result = run_node_helper(
            "src/canvasWorkbench.ts",
            "({"
            "rows: m.createCanvasBoxRows(["
            '{ page: 0, x0: 10.2, y0: 20.8, x1: 110.2, y1: 70.8, mode: "mask" },'
            '{ page: 1, x0: 1, y0: 2, x1: 3, y1: 4, mode: "restore" },'
            '{ page: 0, x0: 30, y0: 40, x1: 80, y1: 90, mode: "restore" }'
            "], 0, 2),"
            "actions: m.canvasBoxActionState({ boxes: ["
            '{ page: 0, x0: 10, y0: 20, x1: 110, y1: 70, mode: "mask" }'
            "], currentPage: 0, selectedBoxIndex: 0, hasResultDoc: true })"
            "})",
        )

        self.assertEqual([row["globalIndex"] for row in result["rows"]], [0, 2])
        # UX_SIMPLICITY_V3_4 §2: 유형만 노출 (수치·식별자 제거).
        self.assertEqual(result["rows"][0]["label"], "마스킹")
        self.assertEqual(result["rows"][1]["label"], "복원")
        self.assertTrue(result["actions"]["canDeleteSelected"])
        self.assertTrue(result["actions"]["canApply"])
        self.assertFalse(result["actions"]["emptyCurrentPage"])

    def test_dashboard_surface_model_uses_runtime_state_not_static_counts(self):
        result = run_node_helper(
            "src/dashboardSurfaceModels.ts",
            "m.buildDashboardSurfaceModel({"
            "selectedPath: '/work/current.pdf',"
            "documentKind: 'pdf',"
            "latestDocumentPath: '/out/current.final_masked.pdf',"
            "latestReportPath: '/out/current.safe_report.json',"
            "keywordCount: 2,"
            "maskBoxCount: 1,"
            "restoreBoxCount: 1,"
            "report: { counts: { PHONE: 2, ADDRESS: 1 }, product_checks: { quality_gate_passed: false, needs_manual_review: true }, review_items: [{ tag: 'PHONE', display_token: '[PHONE]', status: 'applied', count: 2 }, { tag: 'ADDRESS', display_token: '[ADDRESS]', status: 'needs_review', count: 1 }] }"
            "})",
        )

        self.assertEqual(result["documentTitle"], "current.pdf")
        self.assertEqual(result["summary"]["maskCount"], "4건")
        self.assertEqual(result["summary"]["restoreCount"], "1건")
        self.assertEqual(result["summary"]["keywordCount"], "2개")
        self.assertEqual(result["summary"]["riskCount"], "2건")
        self.assertNotIn("previewLabel", result["summary"])
        self.assertEqual(result["reportActions"][0]["badge"], "2건")
        self.assertNotIn("keywordActions", result)

    def test_canvas_delete_selection_reindexes_after_removal(self):
        result = run_node_helper(
            "src/canvasWorkbench.ts",
            "m.deleteCanvasBoxAtIndex(["
            '{ page: 0, x0: 0, y0: 0, x1: 10, y1: 10, mode: "mask" },'
            '{ page: 0, x0: 20, y0: 20, x1: 40, y1: 40, mode: "restore" },'
            '{ page: 0, x0: 50, y0: 50, x1: 90, y1: 90, mode: "mask" }'
            "], 1)",
        )

        self.assertEqual(len(result["boxes"]), 2)
        self.assertEqual(result["selectedBoxIndex"], 1)
        self.assertEqual(result["boxes"][1]["x0"], 50)

    def test_canvas_zoom_action_state_uses_existing_bounds(self):
        result = run_node_helper(
            "src/canvasWorkbench.ts",
            "({ min: m.canvasZoomActionState(0.5), mid: m.canvasZoomActionState(1.2), max: m.canvasZoomActionState(2.5) })",
        )

        self.assertFalse(result["min"]["canZoomOut"])
        self.assertTrue(result["min"]["canZoomIn"])
        self.assertTrue(result["mid"]["canZoomOut"])
        self.assertTrue(result["mid"]["canZoomIn"])
        self.assertTrue(result["max"]["canZoomOut"])
        self.assertFalse(result["max"]["canZoomIn"])

    def test_settings_persistence_round_trip_and_sanitizes_invalid_theme(self):
        result = run_node_helper(
            "src/settingsState.ts",
            "(() => {"
            "  const storage = { value: null, getItem() { return this.value; }, setItem(_key, value) { this.value = value; }, removeItem() { this.value = null; } };"
            "  const saved = m.saveSettings({ theme: 'light', outputDir: '/tmp/out', profile: 'legal', engine: 'pymupdf', displayMode: 'label_ko', deidentificationMode: 'partial', regionScope: 'custom', customRegions: '서울 중구', customKeywords: '홍길동', pdfRedaction: false, exportMaskedText: true, openOutputAfterSave: true }, storage);"
            "  const loaded = m.loadSettings(storage);"
            "  storage.value = JSON.stringify({ theme: 'orange', outputDir: 123, profile: 'bad' });"
            "  const sanitized = m.loadSettings(storage);"
            "  return { saved, loaded, sanitized, attr: m.themeAttribute('purple'), legacy: m.themeAttribute('default') };"
            "})()",
        )

        self.assertEqual(result["saved"]["theme"], "light")
        self.assertEqual(result["saved"]["outputDir"], "/tmp/out")
        self.assertEqual(result["loaded"]["outputDir"], "")
        self.assertEqual(result["loaded"]["displayMode"], "label_ko")
        self.assertEqual(result["loaded"]["deidentificationMode"], "partial")
        self.assertFalse(result["loaded"]["pdfRedaction"])
        self.assertTrue(result["loaded"]["exportMaskedText"])
        self.assertTrue(result["loaded"]["openOutputAfterSave"])
        self.assertEqual(result["loaded"]["customRegions"], "")
        self.assertEqual(result["loaded"]["customKeywords"], "")
        self.assertEqual(result["sanitized"]["theme"], "dark")
        self.assertEqual(result["sanitized"]["outputDir"], "")
        self.assertEqual(result["sanitized"]["profile"], "official")
        self.assertEqual(result["attr"], "dark")
        self.assertEqual(result["legacy"], "dark")

    def test_theme_defaults_distinguish_new_users_from_legacy_settings(self):
        result = run_node_helper(
            "src/settingsState.ts",
            "(() => {"
            "  const empty = { getItem() { return null; }, setItem() {}, removeItem() {} };"
            "  const legacy = { getItem() { return JSON.stringify({ profile: 'official' }); }, setItem() {}, removeItem() {} };"
            "  const malformed = { getItem() { return '{'; }, setItem() {}, removeItem() {} };"
            "  return {"
            "    newUser: m.loadSettings(empty).theme,"
            "    legacyUser: m.loadSettings(legacy).theme,"
            "    malformedLegacy: m.loadSettings(malformed).theme,"
            "    light: m.resolveTheme('light', true),"
            "    dark: m.resolveTheme('dark', false),"
            "    systemLight: m.resolveTheme('system', false),"
            "    systemDark: m.resolveTheme('system', true),"
            "  };"
            "})()",
        )

        self.assertEqual(result["newUser"], "system")
        self.assertEqual(result["legacyUser"], "dark")
        self.assertEqual(result["malformedLegacy"], "dark")
        self.assertEqual(result["light"], "light")
        self.assertEqual(result["dark"], "dark")
        self.assertEqual(result["systemLight"], "light")
        self.assertEqual(result["systemDark"], "dark")

    def test_settings_reject_invalid_enums_and_transient_fields(self):
        result = run_node_helper(
            "src/settingsState.ts",
            "m.loadSettings({"
            "getItem() { return JSON.stringify({ theme: 'orange', engine: 'bad', outputArtifacts: 'docx', displayMode: 'emoji', deidentificationMode: 'unsafe', regionScope: 'mars', selectedInputPdf: '/tmp/private.pdf', boxes: [{ x: 1 }], batchItems: [{ id: 1 }] }); },"
            "setItem() {},"
            "removeItem() {}"
            "})",
        )

        self.assertEqual(result["theme"], "dark")
        self.assertEqual(result["engine"], "auto")
        # v4.1 회귀 가드: settingsState 에서 outputArtifacts 영속화가 삭제됐다. 사용자
        # 대면 산출물 선택이 사라지고 프론트가 내부 고정("pdf_safe_report")으로
        # 전달하므로, 저장된 설정에는 outputArtifacts 키 자체가 존재하면 안 된다.
        self.assertNotIn("outputArtifacts", result)
        self.assertEqual(result["displayMode"], "black")
        self.assertEqual(result["deidentificationMode"], "token")
        self.assertEqual(result["regionScope"], "national")
        self.assertNotIn("selectedInputPdf", result)
        self.assertNotIn("boxes", result)
        self.assertNotIn("batchItems", result)

    def test_settings_storage_failures_fall_back_to_defaults(self):
        result = run_node_helper(
            "src/settingsState.ts",
            "(() => {"
            "  const throwing = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };"
            "  return { loaded: m.loadSettings(throwing), saved: m.saveSettings({ theme: 'light' }, throwing) };"
            "})()",
        )

        self.assertEqual(result["loaded"]["theme"], "system")
        self.assertEqual(result["saved"]["theme"], "light")


if __name__ == "__main__":
    unittest.main()
