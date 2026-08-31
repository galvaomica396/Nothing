import json
import unittest
from pathlib import Path

from test_frontend_state_helpers import canonical_review_manifest, run_node_helper


REPO_ROOT = Path(__file__).resolve().parents[1]


class TypedReviewQueueContractTests(unittest.TestCase):
    def test_public_authorized_restore_does_not_block_final_save(self):
        manifest = canonical_review_manifest(status="resolved")
        occurrence = {
            "occurrenceId": "occ_aaaaaaaaaaaaaaaaaaaaaaaa",
            "segmentId": "segment-1",
            "regionId": None,
            "analysisRevision": manifest["analysisRevision"],
            "page": 0,
            "rects": [{"x0": 10, "y0": 10, "x1": 20, "y1": 20}],
            "tag": "phone",
            "category": "phone",
            "valueHash": "a" * 64,
            "expectedTextHash": "b" * 64,
            "source": "common_detector",
            "policy": "masking-policy-v1",
            "proposedAction": "mask",
            "state": "confirmed",
            "provenance": "common_detector",
        }
        restore = {
            "actionId": "manual-restore-1",
            "analysisRevision": manifest["analysisRevision"],
            "page": 0,
            "rects": occurrence["rects"],
            "protectedNeighborRefs": [],
            "mode": "restore",
            "sourceKind": "text_pdf",
            "linkedOccurrenceId": occurrence["occurrenceId"],
            "expectedTextHash": occurrence["expectedTextHash"],
            "restoreAuthorizationHash": "c" * 64,
        }
        manifest.update({"occurrences": [occurrence], "manualActions": [restore]})
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const overlapping = {json.dumps(manifest)};"
            "const safe = structuredClone(overlapping); safe.manualActions[0].rects = [{ x0: 30, y0: 30, x1: 40, y1: 40 }];"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const report = (value) => { const identity = { runId: value.runId, originalDocumentHash: value.originalDocumentHash, analysisRevision: value.analysisRevision, manifestHash: value.manifestHash, profile: value.profile }; const parsed = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: value, reviewQueue: value.reviewItems }, identity); return parsed.ok ? parsed.value : null; };"
            "return { overlap: m.finalSaveGate({ report: report(overlapping) }), safe: m.finalSaveGate({ report: report(safe) }) };"
            "})()",
        )

        self.assertEqual({"eligible": True, "state": "eligible", "reasonCodes": []}, result["overlap"])
        self.assertEqual({"eligible": True, "state": "eligible", "reasonCodes": []}, result["safe"])

    def test_indeterminate_relevant_coverage_keeps_the_review_queue_available_for_confirm_save(self):
        manifest = canonical_review_manifest(status="resolved")
        manifest.update({
            "profile": "mixed",
            "approvalCoverage": {
                "approval": "absent",
                "header_meta": "indeterminate",
                "labeled_staff": "absent",
            },
            "requiredRegionCoverage": {
                "recipient_reference": "absent",
                "sender_institution": "absent",
                "approval_staff": "absent",
                "dispatch_metadata": "indeterminate",
                "footer_contact": "absent",
            },
            "regions": [
                {
                    "regionId": "region-header-meta",
                    "segmentId": "segment-1",
                    "analysisRevision": manifest["analysisRevision"],
                    "page": 0,
                    "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}],
                    "kind": "header_meta",
                    "state": "review_required",
                    "confirmationSource": None,
                    "reasonCodes": ["geometry_review"],
                    "source": "official_layout",
                },
                {
                    "regionId": "region-dispatch-metadata",
                    "segmentId": "segment-1",
                    "analysisRevision": manifest["analysisRevision"],
                    "page": 0,
                    "rects": [{"x0": 3, "y0": 3, "x1": 4, "y1": 4}],
                    "kind": "dispatch_metadata",
                    "state": "review_required",
                    "confirmationSource": None,
                    "reasonCodes": ["geometry_review"],
                    "source": "official_layout",
                },
            ],
            "reviewItems": [
                {
                    "reviewId": "review-header-meta",
                    "analysisRevision": manifest["analysisRevision"],
                    "kind": "region_geometry",
                    "targetId": "region-header-meta",
                    "pageStart": 0,
                    "pageEnd": 0,
                    "status": "pending",
                    "reasonCodes": ["geometry_review"],
                    "requiresAcknowledgment": False,
                    "commonOnly": False,
                    "provenance": "official_layout",
                },
                {
                    "reviewId": "review-dispatch-metadata",
                    "analysisRevision": manifest["analysisRevision"],
                    "kind": "region_geometry",
                    "targetId": "region-dispatch-metadata",
                    "pageStart": 0,
                    "pageEnd": 0,
                    "status": "pending",
                    "reasonCodes": ["geometry_review"],
                    "requiresAcknowledgment": False,
                    "commonOnly": False,
                    "provenance": "official_layout",
                },
            ],
        })
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const manifest = {json.dumps(manifest)};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile };"
            "const parsed = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity);"
            "const report = parsed.ok ? parsed.value : null;"
            "const noReviewManifest = { ...manifest, regions: [], reviewItems: [] };"
            "const noReviewReport = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: noReviewManifest, reviewQueue: noReviewManifest.reviewItems }, identity);"
            "const noReview = noReviewReport.ok ? noReviewReport.value : null;"
            "const dashboard = loadModule(path.resolve('src/dashboardSurfaceModels.ts'));"
            "return { parsed, queue: report && session.canonicalReviewQueue(report), count: report && session.canonicalMaskCount(report), gate: m.finalSaveGate({ report }), warnings: m.publicFinalSaveWarnings({ report }), noReviewCounts: dashboard.dashboardReviewSurfaceCounts(noReview), noReviewGate: m.finalSaveGate({ report: noReview }), noReviewWarnings: m.publicFinalSaveWarnings({ report: noReview }) };"
            "})()",
        )

        self.assertTrue(result["parsed"]["ok"])
        self.assertTrue(result["queue"]["ok"])
        self.assertTrue(result["count"]["ok"])
        self.assertFalse(result["gate"]["eligible"])
        self.assertEqual("advisory", result["gate"]["state"])
        self.assertEqual(["geometry_review"], result["gate"]["reasonCodes"])
        self.assertEqual(
            [
                "미가림 가능성: 머리말 정보 · 1쪽 — 결재란 영역 자동확인 미완료 — 확인하고 저장",
                "미가림 가능성: 시행 정보 · 1쪽 — 결재란 영역 자동확인 미완료 — 확인하고 저장",
            ],
            result["warnings"],
        )
        self.assertEqual(2, len(result["warnings"]))
        self.assertEqual(0, result["noReviewCounts"]["pending"])
        self.assertNotEqual("blocked", result["noReviewGate"]["state"])
        self.assertEqual(["indeterminate_coverage_requires_reanalysis"], result["noReviewGate"]["reasonCodes"])
        self.assertTrue(any("확인 후 저장할 수 있습니다." in warning for warning in result["noReviewWarnings"]))

    def test_geometry_reanalysis_can_resolve_only_its_indeterminate_coverage_kind(self):
        manifest = canonical_review_manifest(status="resolved")
        manifest.update({
            "profile": "mixed",
            "approvalCoverage": {
                "approval": "absent",
                "header_meta": "indeterminate",
                "labeled_staff": "absent",
            },
            "requiredRegionCoverage": {
                "recipient_reference": "absent",
                "sender_institution": "absent",
                "approval_staff": "absent",
                "dispatch_metadata": "absent",
                "footer_contact": "absent",
            },
            "regions": [{
                "regionId": "region-header-meta",
                "segmentId": "segment-1",
                "analysisRevision": manifest["analysisRevision"],
                "page": 0,
                "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}],
                "kind": "header_meta",
                "state": "review_required",
                "confirmationSource": None,
                "reasonCodes": ["geometry_review"],
                "source": "official_layout",
            }],
            "reviewItems": [{
                "reviewId": "review-header-meta",
                "analysisRevision": manifest["analysisRevision"],
                "kind": "region_geometry",
                "targetId": "region-header-meta",
                "pageStart": 0,
                "pageEnd": 0,
                "status": "pending",
                "reasonCodes": ["geometry_review"],
                "requiresAcknowledgment": False,
                "commonOnly": False,
                "provenance": "official_layout",
            }],
        })
        result = run_node_helper(
            "src/features/masking-run/maskingRunController.ts",
            "(async () => {"
            f"const current = {json.dumps(manifest)};"
            "const rects = [{ x0: 3, y0: 3, x1: 4, y1: 4 }];"
            "const next = { ...current, analysisRevision: current.analysisRevision + 1, manifestHash: 'f'.repeat(64), approvalCoverage: { ...current.approvalCoverage, header_meta: 'absent' }, segments: current.segments.map((item) => ({ ...item, segmentId: 'segment-2', analysisRevision: current.analysisRevision + 1 })), regions: current.regions.map((item) => ({ ...item, regionId: 'region-header-meta-2', segmentId: 'segment-2', analysisRevision: current.analysisRevision + 1, rects, state: 'user_confirmed', confirmationSource: 'user' })), reviewItems: current.reviewItems.map((item) => ({ ...item, reviewId: 'review-header-meta-2', targetId: 'region-header-meta-2', analysisRevision: current.analysisRevision + 1, status: 'resolved' })) };"
            "const state = { latestReport: { product_checks: {}, analysisManifest: current, reviewQueue: current.reviewItems }, latestReportPath: '/report.json', savingInFlight: false, publicRunIdentity: { runId: current.runId, originalDocumentHash: current.originalDocumentHash, analysisRevision: current.analysisRevision, manifestHash: current.manifestHash, profile: current.profile }, geometryDraft: null };"
            "const controller = m.createMaskingRunController({ state, resolveMaskingReview: async () => next, renderFinalState: () => {}, renderDocumentReviewSurfaces: () => {}, updateWorkflowReadiness: () => {}, setStatus: () => {} });"
            "const accepted = await controller.resolveReview({ runId: current.runId, analysisRevision: current.analysisRevision, manifestHash: current.manifestHash, reviewId: 'review-header-meta', resolution: { kind: 'region_geometry', rects } });"
            "return { accepted, coverage: state.latestReport.analysisManifest.approvalCoverage.header_meta, revision: state.latestReport.analysisManifest.analysisRevision };"
            "})()",
        )

        self.assertTrue(result["accepted"])
        self.assertEqual("absent", result["coverage"])
        self.assertEqual(manifest["analysisRevision"] + 1, result["revision"])

    def test_unconfirmed_region_requires_pending_geometry_review_and_blocks_final_save(self):
        manifest = canonical_review_manifest(status="resolved")
        region = {
            "regionId": "region-1",
            "segmentId": "segment-1",
            "analysisRevision": manifest["analysisRevision"],
            "page": 0,
            "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}],
            "kind": "approval",
            "state": "unconfirmed",
            "confirmationSource": None,
            "reasonCodes": ["layout_structure_missing", "label_evidence_missing"],
            "source": "official_layout",
        }
        review = {
            "reviewId": "review-region-1",
            "analysisRevision": manifest["analysisRevision"],
            "kind": "region_geometry",
            "targetId": "region-1",
            "pageStart": 0,
            "pageEnd": 0,
            "status": "pending",
            "reasonCodes": ["layout_structure_missing", "label_evidence_missing"],
            "requiresAcknowledgment": True,
            "commonOnly": False,
            "provenance": "official_layout",
        }
        incomplete = {**manifest, "regions": [region], "reviewItems": []}
        pending = {**manifest, "regions": [region], "reviewItems": [review]}
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const incomplete = {json.dumps(incomplete)};"
            f"const pending = {json.dumps(pending)};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = (value) => ({ runId: value.runId, originalDocumentHash: value.originalDocumentHash, analysisRevision: value.analysisRevision, manifestHash: value.manifestHash, profile: value.profile });"
            "const parsed = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: pending, reviewQueue: pending.reviewItems }, identity(pending));"
            "return { incomplete: session.parseAnalysisManifestV1(incomplete), parsed, gate: m.finalSaveGate({ report: parsed.ok ? parsed.value : null }) };"
            "})()",
        )

        self.assertFalse(result["incomplete"]["ok"])
        self.assertTrue(result["parsed"]["ok"])
        self.assertFalse(result["gate"]["eligible"])
        self.assertEqual("advisory", result["gate"]["state"])
        self.assertEqual(["geometry_review"], result["gate"]["reasonCodes"])

    def test_canonical_manifest_parser_rejects_mutated_sessions(self):
        manifest = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/state/maskingSession.ts",
            "(() => {"
            f"const manifest = {manifest};"
            "const mutation = (change) => ({ ...manifest, ...change }); const clonedQueue = manifest.reviewItems.map((item) => ({ ...item, reasonCodes: [...item.reasonCodes] }));"
            "return {"
            "valid: m.parseAnalysisManifestV1(manifest),"
            "extraField: m.parseAnalysisManifestV1(mutation({ unexpected: true })),"
            "staleReview: m.parseAnalysisManifestV1({ ...manifest, reviewItems: [{ ...manifest.reviewItems[0], analysisRevision: manifest.analysisRevision + 1 }] }),"
            "badReference: m.parseAnalysisManifestV1({ ...manifest, reviewItems: [{ ...manifest.reviewItems[0], targetId: 'unknown-target' }] }),"
            "crossNamespaceCollision: m.parseAnalysisManifestV1({ ...manifest, occurrences: [{ occurrenceId: manifest.segments[0].segmentId, segmentId: manifest.segments[0].segmentId, regionId: null, analysisRevision: manifest.analysisRevision, page: 0, rects: [{ x0: 0, y0: 0, x1: 1, y1: 1 }], tag: 'PHONE', category: 'phone', valueHash: 'b'.repeat(64), expectedTextHash: 'c'.repeat(64), source: 'routing', policy: 'token', proposedAction: 'mask', state: 'confirmed', provenance: 'routing' }] }),"
            "queueClone: m.canonicalReviewQueue({ analysisManifest: manifest, reviewQueue: clonedQueue }),"
            "missingCoverage: m.canonicalReviewQueue({ analysisManifest: { ...manifest, approvalCoverage: undefined }, reviewQueue: clonedQueue }),"
            "queueMutation: m.canonicalReviewQueue({ analysisManifest: manifest, reviewQueue: [{ ...clonedQueue[0], pageEnd: 1 }] }),"
            "badRect: m.parseAnalysisManifestV1({ ...manifest, regions: [{ regionId: 'region-1', segmentId: 'segment-1', analysisRevision: manifest.analysisRevision, page: 0, rects: [{ x0: 4, y0: 2, x1: 3, y1: 4 }], kind: 'approval', state: 'pending', confirmationSource: null, reasonCodes: [], source: 'routing' }] })"
            "};"
            "})()",
        )

        self.assertTrue(result["valid"]["ok"])
        for name in ("extraField", "staleReview", "badReference", "crossNamespaceCollision", "badRect"):
            self.assertFalse(result[name]["ok"], name)
        self.assertTrue(result["queueClone"]["ok"])
        self.assertFalse(result["missingCoverage"]["ok"])
        self.assertFalse(result["queueMutation"]["ok"])

    def test_review_queue_integrity_blocks_each_tamper_after_eligible_canonical_control(self):
        manifest = canonical_review_manifest(status="resolved")
        manifest["reviewItems"].append({**manifest["reviewItems"][0], "reviewId": "review-2"})
        manifest_json = json.dumps(manifest)
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            "const session = loadModule(path.resolve(path.dirname(sourcePath), '../../state/maskingSession.ts'));"
            f"const manifest = {manifest_json};"
            "const queue = manifest.reviewItems.map((item) => ({ ...item, reasonCodes: [...item.reasonCodes] }));"
            "const identity = { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile };"
            "const rawReport = (reviewQueue) => ({ product_checks: {}, analysisManifest: manifest, reviewQueue });"
            "const gate = (reviewQueue) => { const parsed = session.parseBoundSafeReport(rawReport(reviewQueue), identity); return { parsed: parsed.ok, decision: m.finalSaveGate({ report: parsed.ok ? parsed.value : null }) }; };"
            "const replaceFirst = (change) => queue.map((item, index) => index === 0 ? { ...item, ...change } : item);"
            "const stale = replaceFirst({ analysisRevision: manifest.analysisRevision + 1 });"
            "return {"
            "canonical: gate(queue),"
            "omitted: gate(queue.slice(1)),"
            "added: gate([...queue, { ...queue[0], reviewId: 'review-3' }]),"
            "duplicated: gate([...queue, { ...queue[0] }]),"
            "reordered: gate([...queue].reverse()),"
            "stale: gate(stale),"
            "mutated: gate(replaceFirst({ pageEnd: 1 })),"
            "recursiveCanary: gate(replaceFirst({ unexpected: { value: 'QUEUE_PII_CANARY_010-1234-5678', path: '/private/QUEUE_PATH_CANARY.pdf', error: 'QUEUE_ERROR_CANARY' } }))"
            "};"
            "})()",
        )

        self.assertTrue(result["canonical"]["parsed"])
        self.assertTrue(result["canonical"]["decision"]["eligible"])
        self.assertEqual("eligible", result["canonical"]["decision"]["state"])
        for name in ("omitted", "added", "duplicated", "reordered", "stale", "mutated", "recursiveCanary"):
            with self.subTest(queue=name):
                result_for_queue = result[name]
                self.assertFalse(result_for_queue["parsed"])
                decision = result_for_queue["decision"]
                self.assertFalse(decision["eligible"])
                self.assertEqual("blocked", decision["state"])
                self.assertEqual(["missing_current_session"], decision["reasonCodes"])
        encoded = json.dumps(result, ensure_ascii=False)
        for canary in ("홍길동", "서울시청", "QUEUE_PII_CANARY_010-1234-5678", "/private/QUEUE_PATH_CANARY.pdf", "QUEUE_ERROR_CANARY"):
            self.assertNotIn(canary, encoded)


if __name__ == "__main__":
    unittest.main()
