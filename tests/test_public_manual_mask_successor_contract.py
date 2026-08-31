from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from test_frontend_state_helpers import REPO_ROOT, canonical_review_manifest, run_node_helper


def public_manual_mask_manifest(**options: object) -> dict[str, object]:
    output = subprocess.check_output(
        [
            "node",
            "--input-type=module",
            "--eval",
            "import { publicManualMaskManifestForQa } from './scripts/qa_tauri_mock.mjs';"
            f" console.log(JSON.stringify(publicManualMaskManifestForQa({json.dumps(options)})));",
        ],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    return json.loads(output)


class PublicManualMaskSuccessorContractTests(unittest.TestCase):
    def test_second_mock_successor_revisions_all_manual_actions_before_frontend_adoption(self) -> None:
        # Given: the QA fixture has accepted one public scan mask action.
        first_action = {
            "actionId": "manual-2",
            "analysisRevision": 2,
            "page": 0,
            "rects": [{"x0": 12, "y0": 24, "x1": 48, "y1": 72}],
            "protectedNeighborRefs": [],
            "mode": "mask",
            "sourceKind": "scan",
            "linkedOccurrenceId": None,
            "expectedTextHash": None,
        }
        first_successor = public_manual_mask_manifest(
            revision=2,
            manifestHash="b" * 64,
            manualActions=[first_action],
        )
        # When: a second accepted action advances the same public review session.
        second_successor = public_manual_mask_manifest(
            revision=3,
            manifestHash="c" * 64,
            manualActions=[
                first_action,
                {**first_action, "actionId": "manual-3", "analysisRevision": 3, "rects": [{"x0": 52, "y0": 84, "x1": 88, "y1": 120}]},
            ],
        )
        # Then: both successors pass the exact parser used before controller adoption.
        first_result = run_node_helper(
            "src/state/maskingSession.ts",
            f"m.parseAnalysisManifestV1({json.dumps(first_successor)})",
        )
        second_result = run_node_helper(
            "src/state/maskingSession.ts",
            f"m.parseAnalysisManifestV1({json.dumps(second_successor)})",
        )

        self.assertTrue(first_result["ok"], first_result)
        self.assertTrue(second_result["ok"], second_result)
        self.assertEqual([3, 3], [action["analysisRevision"] for action in second_result["value"]["manualActions"]])

    def test_malformed_successor_reports_status_and_keeps_current_session(self) -> None:
        manifest = canonical_review_manifest()
        malformed_successor = {
            **manifest,
            "analysisRevision": manifest["analysisRevision"] + 1,
            "manifestHash": "c" * 64,
            "manualActions": [{
                "actionId": "manual-invalid",
                "analysisRevision": manifest["analysisRevision"],
                "page": 0,
                "rects": [{"x0": 12, "y0": 24, "x1": 48, "y1": 72}],
                "protectedNeighborRefs": [],
                "mode": "mask",
                "sourceKind": "scan",
                "linkedOccurrenceId": None,
                "expectedTextHash": None,
            }],
        }
        result = run_node_helper(
            "src/features/masking-run/maskingRunController.ts",
            "(async () => {"
            f"const manifest = {json.dumps(manifest)};"
            f"const malformedSuccessor = {json.dumps(malformed_successor)};"
            "const statuses = [];"
            "const state = {"
            " maskingRunning: false, savingInFlight: false, documentProvenance: { original: { path: '/docs/text.pdf', kind: 'pdf' } },"
            " latestReport: { product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, latestReportPath: '/work/report.json', activeRunKind: 'public',"
            " publicRunIdentity: { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile },"
            " latestExtractedPath: '', latestMaskedPath: '', latestMaskedTextPolicy: '', baseExtractedText: '', baseMaskedText: '', initialMaskingPreviewPdf: '', initialExtractedText: '', initialMaskedText: '', preManualPreviewPdf: '', preManualExtractedText: '', preManualMaskedText: '',"
            " boxes: [], geometryDraft: null, documentEditRevision: 0, selectedCanvasBoxIndex: -1, origDoc: null, currentOrigPage: 1, currentResultPage: 1, resultDoc: null, lastPreviewDiagnostics: ''"
            " };"
            "const controller = m.createMaskingRunController({ state, inputPathEl: { value: '' }, isPdfInput: () => true, isCustomRegionScope: () => false, getResultSourcePath: () => '', analyzeMaskingRun: async () => manifest, resolveMaskingReview: async () => malformedSuccessor, applyManualActionV1: async () => malformedSuccessor, readTextFile: async () => '', ensurePreviewWorkDir: async () => '/work', collectMaskingOptions: () => ({ profile: 'mixed', auto_mask_threshold: 0.85, review_threshold: 0.5 }), clampPage: (page) => page, loadPdfDoc: async () => null, loadResultPdf: async () => false, renderCompare: async () => {}, setCompareMode: () => {}, setStatus: (message) => statuses.push(message), setBaseMaskingProgress: () => {}, setTextCompareContents: () => {}, renderFinalState: () => {}, renderDocumentReviewSurfaces: () => {}, resetDerivedArtifacts: () => {}, updateWorkflowReadiness: () => {}, updateCanvasControls: () => {} });"
            "const applied = await controller.applyPublicManualMaskActions([{ page: 0, rects: [{ x0: 12, y0: 24, x1: 48, y1: 72 }], mode: 'mask' }]);"
            "return { applied, status: statuses.at(-1), revision: state.latestReport.analysisManifest.analysisRevision };"
            "})()",
        )

        self.assertFalse(result["applied"])
        self.assertEqual("공공 수동 보정 응답을 검증하지 못했습니다. 현재 검토 세션은 유지됩니다.", result["status"])
        self.assertEqual(manifest["analysisRevision"], result["revision"])
