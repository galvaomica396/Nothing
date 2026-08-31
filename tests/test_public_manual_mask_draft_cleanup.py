from __future__ import annotations
import unittest


class PublicManualMaskDraftCleanupTests(unittest.TestCase):

    def test_apply_success_clears_draft_boxes_but_failure_keeps_them(self) -> None:
        from test_frontend_state_helpers import run_node_helper

        result = run_node_helper(
            "src/features/manual-adjustment/manualAdjustmentController.ts",
            "(async () => {"
            "const element = (dataset = {}) => ({ dataset, attrs: {}, classList: { toggle() {} }, style: {}, textContent: '', disabled: false, title: '', hidden: false, value: '', setAttribute(name, value) { this.attrs[name] = value; }, removeAttribute() {}, append() {}, replaceChildren() {}, addEventListener() {}, closest() { return null; }, getContext() { return { clearRect() {}, fillRect() {}, drawImage() {} }; } });"
            "const tools = ['mask', 'restore', 'select', 'delete', 'pan'].map((tool) => element({ canvasTool: tool })); let active = 'mask';"
            "const baseState = { documentProvenance: { original: { path: '/input.pdf', kind: 'pdf' }, generated: { path: '/result.pdf' }, manual: { path: '' }, final: { path: '' }, continuation: null }, outputDir: '/out', currentResultPage: 1, resultDoc: {}, scale: 1, boxes: [{ page: 0, x0: 1, y0: 2, x1: 3, y1: 4, mode: 'mask' }, { page: 0, x0: 5, y0: 6, x1: 7, y1: 8, mode: 'mask' }], geometryDraft: null, documentEditRevision: 3, mode: 'mask', selectedCanvasBoxIndex: 1, canvasMode: true, maskingRunning: false, batchRunning: false, savingInFlight: false, extractedText: '', maskedText: '', baseExtractedText: '', baseMaskedText: '', preManualPreviewPdf: '', preManualExtractedText: '', preManualMaskedText: '', latestReportPath: '/report.json', latestReport: null, activeRunKind: 'public', publicRunIdentity: null, latestMaskedPath: '', latestMaskedTextPolicy: 'token', lastPreviewDiagnostics: '', restoreRevalidationFailed: false };"
            "const make = (state, applyResult) => { const status = []; let redraws = 0, metas = 0; const deps = { state, invokeCommand: async () => ({}), modeMask: tools[0], modeRestore: tools[1], workspaceShellEl: element(), overlay: element(), canvasEditorToolButtons: tools, canvasActiveToolLabelEl: element(), canvasToolReadinessEl: element(), canvasBoxListEl: element(), canvasBoxPropertiesEl: element(), canvasBoxPropertyPageEl: element(), canvasBoxPropertyTypeEl: element(), canvasBoxPropertyCoordinatesEl: element(), canvasBoxPropertySizeEl: element(), canvasSummaryMaskCountEl: element(), canvasSummaryRestoreCountEl: element(), canvasSummaryKeywordCountEl: element(), canvasSummaryOutputStateEl: element(), btnCanvasZoomOut: element(), btnCanvasZoomIn: element(), btnCanvasUndo: element(), btnCanvasClear: element(), btnCanvasBoxDelete: element(), btnCanvasBoxConvertMask: element(), btnCanvasBoxConvertRestore: element(), isStandaloneCanvasWindow: false, isPdfInput: () => true, currentFinalDocumentPath: () => '/result.pdf', getActiveCanvasTool: () => active, setActiveCanvasToolState: (tool) => { active = tool; }, ensurePreviewWorkDir: async () => '/work', loadResultPdf: async () => true, redrawOverlay: () => { redraws += 1; }, updateMeta: () => { metas += 1; }, renderFinalState() {}, renderCompare: async () => {}, setTextCompareContents() {}, updateWorkflowReadiness() {}, updateStatusDetail() {}, setStatus: (message) => status.push(message), applyPublicManualMaskActions: async () => applyResult, renderDocumentReviewSurfaces() {} }; const controller = m.createManualAdjustmentController(deps); return { controller, state, status, redraws: () => redraws, metas: () => metas }; };"
            "const run = async (applyResult) => { const s = JSON.parse(JSON.stringify(baseState)); const { controller, state, status, redraws, metas } = make(s, applyResult); const outcome = await controller.applyPendingManualBoxes('test'); return { outcome, boxes: state.boxes.length, selected: state.selectedCanvasBoxIndex, revision: state.documentEditRevision, redraws: redraws(), metas: metas(), status }; };"
            "return { success: await run(true), failure: await run(false) };"
            "})()",
            browser_fixture=True,
        )
        success = result["success"]
        failure = result["failure"]

        self.assertIsNone(success["outcome"])
        self.assertEqual(success["boxes"], 0, "draft boxes cleared after successful public apply")
        self.assertEqual(success["selected"], -1, "selected canvas box index reset after successful apply")
        self.assertEqual(success["redraws"], 1)
        self.assertEqual(success["metas"], 1)
        self.assertEqual(success["revision"], 4)
        self.assertIn("공공 수동 보정 2건", success["status"][0])

        self.assertIsNone(failure["outcome"])
        self.assertEqual(failure["boxes"], 2, "draft boxes preserved when public apply fails")
        self.assertEqual(failure["selected"], 1, "selected canvas box index preserved on failure")
        self.assertEqual(failure["redraws"], 0, "no overlay redraw on failure")
        self.assertEqual(failure["metas"], 0, "no meta update on failure")


if __name__ == "__main__":
    unittest.main()
