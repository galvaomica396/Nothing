from __future__ import annotations

import json
import unittest

from test_frontend_state_helpers import canonical_review_manifest, run_node_helper


class MaskingRunSessionGuardTests(unittest.TestCase):
    def test_masking_running_blocks_mousedown_mousemove_and_mouseup_box_creation(self) -> None:
        result = run_node_helper(
            "src/features/canvas-workbench/canvasRenderController.ts",
            "(() => {"
            "const target = () => { const listeners = {}; return { width: 100, height: 100, parentElement: null, classList: { add() {}, remove() {} }, addEventListener(type, handler) { (listeners[type] ??= []).push(handler); }, dispatchEvent(event) { for (const handler of listeners[event.type] ?? []) handler(event); }, closest() { return null; }, getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; } }; };"
            "const overlay = target(); const state = { scale: 1, boxes: [], geometryDraft: null, lastDragRejection: null, documentEditRevision: 0, mode: 'mask', currentOrigPage: 1, currentResultPage: 1, origDoc: { numPages: 1 }, resultDoc: {}, selectedCanvasBoxIndex: -1, lastPreviewDiagnostics: '', syncPages: true, activeRunKind: 'public', savingInFlight: false, maskingRunning: true };"
            "const statuses = []; const context = { clearRect() {}, fillRect() {}, drawImage() {} };"
            "m.createCanvasRenderController({ state, origCanvas: target(), resultCanvas: target(), overlay, origWrap: target(), resultWrap: target(), pdfCompareView: target(), origCtx: context, resultCtx: context, octx: context, clampPage: (page) => page, updateMeta() {}, getActiveCanvasTool: () => 'mask', setStatus: (message) => statuses.push(message), getPublicDetectionOverlay: () => null, publishPageThumbnails() {} });"
            "overlay.dispatchEvent({ type: 'mousedown', clientX: 10, clientY: 10 }); overlay.dispatchEvent({ type: 'mousemove', clientX: 70, clientY: 70 }); window.dispatchEvent({ type: 'mouseup' });"
            "return { boxCount: state.boxes.length, statuses, revision: state.documentEditRevision, selection: state.selectedCanvasBoxIndex };"
            "})()",
            browser_fixture=True,
        )

        self.assertEqual(0, result["boxCount"])
        self.assertEqual(0, result["revision"])
        self.assertEqual(-1, result["selection"])
        self.assertEqual(
            ["마스킹 실행 중에는 박스를 그릴 수 없습니다. 완료 후 그려 주세요."] * 3,
            result["statuses"],
        )

    def test_successful_run_clears_manual_boxes_and_selection(self) -> None:
        result = run_node_helper(
            "src/features/masking-run/maskingRunController.ts",
            "(async () => {"
            "const state = { maskingRunning: false, savingInFlight: false, documentProvenance: { original: { path: '/docs/original.pdf', kind: 'pdf' }, generated: { path: '', artifactPath: '' }, manual: { path: '' }, final: { path: '' }, continuation: null }, latestReport: null, latestReportPath: '', activeRunKind: 'none', publicRunIdentity: null, latestExtractedPath: '', latestMaskedPath: '', latestMaskedTextPolicy: '', baseExtractedText: '', baseMaskedText: '', initialMaskingPreviewPdf: '', initialExtractedText: '', initialMaskedText: '', preManualPreviewPdf: '', preManualExtractedText: '', preManualMaskedText: '', boxes: [{ page: 0, x0: 12, y0: 24, x1: 48, y1: 72, mode: 'mask' }], geometryDraft: null, documentEditRevision: 0, selectedCanvasBoxIndex: 0, origDoc: { numPages: 2 }, currentOrigPage: 1, currentResultPage: 1, resultDoc: null, lastPreviewDiagnostics: '' };"
            "  const statuses = [];"
            "  const controller = m.createMaskingRunController({ state, inputPathEl: { value: '' }, isPdfInput: () => true, isCustomRegionScope: () => false, getResultSourcePath: () => '', analyzeMaskingRun: async () => ({}), resolveMaskingReview: async () => ({}), applyManualActionV1: async () => ({}), readTextFile: async () => '', ensurePreviewWorkDir: async () => '/work', collectMaskingOptions: () => ({ profile: 'legal', deidentification_policy: 'token', auto_mask_threshold: 0.85, review_threshold: 0.5 }), clampPage: (page) => page, loadPdfDoc: async () => null, loadResultPdf: async () => true, renderCompare: async () => {}, setCompareMode: () => {}, setStatus: (message) => statuses.push(message), setBaseMaskingProgress: () => {}, setTextCompareContents: () => {}, renderFinalState: () => {}, renderDocumentReviewSurfaces: () => {}, resetDerivedArtifacts: () => {}, updateWorkflowReadiness: () => {}, updateCanvasControls: () => {}, runMaskingPipeline: async () => ({ report: { product_checks: {}, pdf_redaction: {}, outputs: { preview_pdf_source_file: '/work/preview.pdf' } }, extracted_text: '', masked_text: '' }) });"
            "  await controller.runMaskingForSelectedDocument();"
            "  return { boxes: state.boxes, revision: state.documentEditRevision, selected: state.selectedCanvasBoxIndex, status: statuses.at(-1), allStatuses: statuses };"
            "})()",
        )

        self.assertEqual([], result["boxes"])
        self.assertEqual(1, result["revision"])
        self.assertEqual(-1, result["selected"])
        self.assertNotIn("유지했습니다", " ".join(result["allStatuses"]))
        self.assertNotIn("기존 수동 박스", " ".join(result["allStatuses"]))

    def test_public_text_page_restore_box_builds_authorized_occurrence_request_while_geometry_draft_exists(self) -> None:
        manifest = canonical_review_manifest()
        target_rect = {"x0": 12, "y0": 24, "x1": 48, "y1": 72}
        target = {
            "occurrenceId": "occ_aaaaaaaaaaaaaaaaaaaaaaaa",
            "segmentId": "segment-1",
            "regionId": None,
            "analysisRevision": manifest["analysisRevision"],
            "page": 0,
            "rects": [target_rect],
            "tag": "PHONE",
            "category": "phone",
            "valueHash": "c" * 64,
            "expectedTextHash": "d" * 64,
            "source": "text_pdf",
            "policy": "masking-policy-v1",
            "proposedAction": "mask",
            "state": "confirmed",
            "provenance": "text_pdf",
        }
        manifest["occurrences"] = [target]
        next_manifest = {
            **manifest,
            "analysisRevision": manifest["analysisRevision"] + 1,
            "manifestHash": "c" * 64,
            "segments": [{**manifest["segments"][0], "analysisRevision": manifest["analysisRevision"] + 1}],
            "reviewItems": [{**manifest["reviewItems"][0], "analysisRevision": manifest["analysisRevision"] + 1}],
            "occurrences": [{**target, "analysisRevision": manifest["analysisRevision"] + 1}],
            "manualActions": [{
                "actionId": "manual-8",
                "analysisRevision": manifest["analysisRevision"] + 1,
                "page": 0,
                "rects": [target_rect],
                "protectedNeighborRefs": [],
                "mode": "restore",
                "sourceKind": "text_pdf",
                "linkedOccurrenceId": target["occurrenceId"],
                "expectedTextHash": target["expectedTextHash"],
                "restoreAuthorizationHash": "a" * 64,
            }],
        }
        result = run_node_helper(
            "src/features/masking-run/maskingRunController.ts",
            "(async () => {"
            f"const manifest = {json.dumps(manifest)};"
            f"const nextManifest = {json.dumps(next_manifest)};"
            "const requests = [];"
            "const state = {"
            " maskingRunning: false, savingInFlight: false, documentProvenance: { original: { path: '/docs/text.pdf', kind: 'pdf' } },"
            " latestReport: { product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, latestReportPath: '/work/report.json', activeRunKind: 'public',"
            " publicRunIdentity: { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile },"
            " latestExtractedPath: '', latestMaskedPath: '', latestMaskedTextPolicy: '', baseExtractedText: '', baseMaskedText: '', initialMaskingPreviewPdf: '', initialExtractedText: '', initialMaskedText: '', preManualPreviewPdf: '', preManualExtractedText: '', preManualMaskedText: '',"
            " boxes: [{ page: 0, x0: 12, y0: 24, x1: 48, y1: 72, mode: 'mask' }], geometryDraft: { owner: 'review-geometry' }, documentEditRevision: 0, selectedCanvasBoxIndex: 0, origDoc: null, currentOrigPage: 1, currentResultPage: 1, resultDoc: null, lastPreviewDiagnostics: ''"
            " };"
            "const controller = m.createMaskingRunController({ state, inputPathEl: { value: '' }, isPdfInput: () => true, isCustomRegionScope: () => false, getResultSourcePath: () => '', analyzeMaskingRun: async () => manifest, resolveMaskingReview: async () => nextManifest, applyManualActionV1: async (request) => { requests.push(request); return nextManifest; }, issueRestoreCapability: async () => ({ capability: 'b'.repeat(64) }), readTextFile: async () => '', ensurePreviewWorkDir: async () => '/work', collectMaskingOptions: () => ({ profile: 'mixed', auto_mask_threshold: 0.85, review_threshold: 0.5 }), clampPage: (page) => page, loadPdfDoc: async () => null, loadResultPdf: async () => false, renderCompare: async () => {}, setCompareMode: () => {}, setStatus: () => {}, setBaseMaskingProgress: () => {}, setTextCompareContents: () => {}, renderFinalState: () => {}, renderDocumentReviewSurfaces: () => {}, resetDerivedArtifacts: () => {}, updateWorkflowReadiness: () => {}, updateCanvasControls: () => {} });"
            "const applied = await controller.applyPublicManualMaskActions([{ page: 0, rects: [{ x0: 12, y0: 24, x1: 48, y1: 72 }], mode: 'restore', gestureTrusted: true }]);"
            "return { applied, request: requests[0] ?? null, geometryDraft: state.geometryDraft };"
            "})()",
        )

        self.assertTrue(result["applied"])
        self.assertEqual(
            {
                "runId": manifest["runId"],
                "analysisRevision": manifest["analysisRevision"],
                "manifestHash": manifest["manifestHash"],
                "page": 0,
                "rects": [{"x0": 12, "y0": 24, "x1": 48, "y1": 72}],
                "mode": "restore",
                "sourceKind": "text_pdf",
                "linkedOccurrenceId": "occ_aaaaaaaaaaaaaaaaaaaaaaaa",
                "targetRegionId": None,
                "expectedTextHash": "d" * 64,
                "protectedNeighborRefs": [],
                "restoreCapability": "b" * 64,
            },
            result["request"],
        )
        self.assertEqual({"owner": "review-geometry"}, result["geometryDraft"])

    def test_late_public_and_legal_failure_restore_complete_session_snapshot(self) -> None:
        manifest = canonical_review_manifest()
        manifest["thresholdHash"] = "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e"
        manifest["thresholdArtifact"]["contentHash"] = manifest["thresholdHash"]
        public_manifest = json.dumps(manifest)
        scenario = f"""
(async () => {{
  const publicManifest = {public_manifest};
  const hostile = 'HOSTILE_ERROR_CANARY /private/HOSTILE_PATH_CANARY.pdf';
  const run = async (kind) => {{
    const origDoc = {{ id: 'orig' }}, resultDoc = {{ id: 'result' }};
    const state = {{
      maskingRunning: false, savingInFlight: false,
      documentProvenance: {{ original: {{ path: '/docs/original.pdf', kind: 'pdf' }}, generated: {{ path: '/work/generated.pdf' }}, manual: {{ path: '/work/manual.pdf' }}, final: {{ path: '/out/final.pdf' }}, continuation: {{ state: 'ready', path: '/out/final.pdf' }} }},
      latestReport: {{ stable: kind }}, latestReportPath: `/${{kind}}.json`, activeRunKind: kind,
      latestExtractedPath: `/${{kind}}.txt`, latestMaskedPath: `/${{kind}}.masked.txt`, latestMaskedTextPolicy: 'token',
      baseExtractedText: `${{kind}} extracted`, baseMaskedText: `${{kind}} masked`, initialMaskingPreviewPdf: `/${{kind}}.preview.pdf`, initialExtractedText: `${{kind}} initial extracted`, initialMaskedText: `${{kind}} initial masked`, preManualPreviewPdf: `/${{kind}}.pre-manual.pdf`, preManualExtractedText: `${{kind}} pre-manual extracted`, preManualMaskedText: `${{kind}} pre-manual masked`,
      boxes: [{{ id: kind }}], geometryDraft: {{ id: kind }}, documentEditRevision: 4, selectedCanvasBoxIndex: 0,
      origDoc, currentOrigPage: 2, currentResultPage: 3, resultDoc, lastPreviewDiagnostics: 'prior diagnostic'
    }};
    const snapshot = structuredClone(state);
    const identities = {{ provenance: state.documentProvenance, report: state.latestReport, boxes: state.boxes, geometry: state.geometryDraft, origDoc, resultDoc }};
    const calls = {{ analyze: 0, pipeline: 0, reset: 0, late: 0 }}, statuses = [];
    const lateFailure = () => {{ calls.late += 1; throw new Error(hostile); }};
    const controller = m.createMaskingRunController({{
      state, customRegionsEl: {{ value: '', focus() {{}} }}, displayModeEl: {{ value: 'black' }}, inputPathEl: {{ value: '' }},
      isCustomRegionScope: () => false, isPdfInput: () => true, getResultSourcePath: () => '',
      collectMaskingOptions: () => ({{ profile: kind === 'public' ? 'mixed' : 'legal', deidentification_policy: 'token', auto_mask_threshold: 0.85, review_threshold: 0.5 }}),
      analyzeMaskingRun: async () => {{ calls.analyze += 1; return publicManifest; }},
      runMaskingPipeline: async () => {{ calls.pipeline += 1; return {{ report: {{}}, extracted_text: 'new', masked_text: 'new' }}; }},
      resetDerivedArtifacts: () => {{ calls.reset += 1; }},
      setStatus: (message) => statuses.push(message), setBaseMaskingProgress: () => {{}}, updateWorkflowReadiness: () => {{}}, updateCanvasControls: () => {{}},
      renderFinalState: kind === 'public' ? lateFailure : () => {{}}, renderDocumentReviewSurfaces: kind === 'legal' ? lateFailure : () => {{}},
      ensurePreviewWorkDir: async () => '/work', readTextFile: async () => '', setTextCompareContents: () => {{}}, clampPage: (page) => page,
      loadPdfDoc: async () => null, loadResultPdf: async () => false, renderCompare: async () => {{}}, setCompareMode: () => {{}}, resolveMaskingReview: async () => ({{}})
    }});
    const outcome = await controller.runMaskingForSelectedDocument();
    return {{ outcome, calls, snapshot, after: structuredClone(state), sameProvenance: state.documentProvenance === identities.provenance, sameReport: state.latestReport === identities.report, sameBoxes: state.boxes === identities.boxes, sameGeometry: state.geometryDraft === identities.geometry, sameOrigDoc: state.origDoc === identities.origDoc, sameResultDoc: state.resultDoc === identities.resultDoc, status: statuses.at(-1) }};
  }};
  return {{ public: await run('public'), legal: await run('legal') }};
}})()
"""
        result = run_node_helper("src/features/masking-run/maskingRunController.ts", scenario)
        for kind, outcome in result.items():
            self.assertIsNone(outcome["outcome"], kind)
            self.assertEqual(1, outcome["calls"]["reset"], kind)
            self.assertEqual(1, outcome["calls"]["late"], kind)
            self.assertEqual(1, outcome["calls"]["analyze"] if kind == "public" else outcome["calls"]["pipeline"], kind)
            self.assertEqual(0, outcome["calls"]["pipeline"] if kind == "public" else outcome["calls"]["analyze"], kind)
            self.assertEqual(outcome["snapshot"], outcome["after"], kind)
            for identity in ("sameProvenance", "sameReport", "sameBoxes", "sameGeometry", "sameOrigDoc", "sameResultDoc"):
                self.assertTrue(outcome[identity], f"{{kind}}: {{identity}}")
            self.assertIn("실패", outcome["status"], kind)
            self.assertNotIn("HOSTILE_ERROR_CANARY", outcome["status"], kind)
            self.assertNotIn("HOSTILE_PATH_CANARY", outcome["status"], kind)

    def test_deferred_stale_public_and_legal_completion_cannot_replace_new_provenance(self) -> None:
        manifest = canonical_review_manifest()
        manifest["thresholdHash"] = "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e"
        manifest["thresholdArtifact"]["contentHash"] = manifest["thresholdHash"]
        result = run_node_helper(
            "src/features/masking-run/maskingRunController.ts",
            "(() => {"
            f"const manifest = {json.dumps(manifest)};"
            "const deferred = () => { let resolve; return { promise: new Promise((done) => { resolve = done; }), resolve }; };"
            "const run = async (profile) => { const pending = deferred(); const replacement = { original: { path: '/replacement.pdf', kind: 'pdf' }, generated: { path: '/replacement-generated.pdf' }, manual: { path: '' }, final: { path: '' }, continuation: null }; const state = { maskingRunning: false, savingInFlight: false, batchRunning: false, documentProvenance: { original: { path: '/old.pdf', kind: 'pdf' }, generated: { path: '' }, manual: { path: '' }, final: { path: '' }, continuation: null }, latestReport: { owner: 'old' }, latestReportPath: '/old.json', latestExtractedPath: '/old.txt', latestMaskedPath: '/old.masked.txt', latestMaskedTextPolicy: 'token', baseExtractedText: 'old', baseMaskedText: 'old', boxes: [], geometryDraft: null, documentEditRevision: 0, selectedCanvasBoxIndex: -1, currentOrigPage: 1, currentResultPage: 1 }; const calls = { reset: 0, rendered: 0, status: [] }; const controller = m.createMaskingRunController({ state, customRegionsEl: { value: '', focus() {} }, displayModeEl: { value: 'black' }, inputPathEl: { value: '' }, isCustomRegionScope: () => false, isPdfInput: () => true, getResultSourcePath: () => '', collectMaskingOptions: () => ({ profile, deidentification_policy: 'token', auto_mask_threshold: 0.85, review_threshold: 0.5 }), analyzeMaskingRun: () => pending.promise, runMaskingPipeline: () => pending.promise, resetDerivedArtifacts: () => { calls.reset += 1; }, setStatus: (value) => calls.status.push(value), setBaseMaskingProgress() {}, updateWorkflowReadiness() {}, updateCanvasControls() {}, renderFinalState() { calls.rendered += 1; }, renderDocumentReviewSurfaces() { calls.rendered += 1; }, ensurePreviewWorkDir: async () => '/work', readTextFile: async () => '', setTextCompareContents() {}, clampPage: (page) => page, loadPdfDoc: async () => null, loadResultPdf: async () => true, renderCompare: async () => {}, setCompareMode() {}, resolveMaskingReview: async () => ({}) }); const work = controller.runMaskingForSelectedDocument(); state.documentProvenance = replacement; state.latestReport = { owner: 'replacement' }; state.latestReportPath = '/replacement.json'; if (profile === 'legal') pending.resolve({ report: {}, extracted_text: 'stale', masked_text: 'stale' }); else pending.resolve(manifest); const outcome = await work; return { outcome, state, replacement, calls }; }; return Promise.all([run('mixed'), run('legal')]);"
            "})()",
        )
        for outcome in result:
            self.assertIsNone(outcome["outcome"])
            self.assertEqual(outcome["replacement"], outcome["state"]["documentProvenance"])
            self.assertEqual({"owner": "replacement"}, outcome["state"]["latestReport"])
            self.assertEqual("/replacement.json", outcome["state"]["latestReportPath"])
            self.assertEqual(0, outcome["calls"]["reset"])
            self.assertEqual(0, outcome["calls"]["rendered"])
            self.assertNotIn("실패", outcome["calls"]["status"])

if __name__ == "__main__":
    unittest.main()
