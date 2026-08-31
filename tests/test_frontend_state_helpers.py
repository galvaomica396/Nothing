import json
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]



def run_node_helper(module_path: str, expression: str, *, default_storage: str = "absent", browser_fixture: bool = False):
    event_source = "class Event { constructor() {} }" if browser_fixture else "class Event { constructor() { throw new Error('unexpected browser access: Event'); } }"
    document_source = "browserDocument" if browser_fixture else "unexpectedBrowserAccess('document')"
    window_source = "{ location: { search: '' }, innerWidth: 800, listeners: {}, addEventListener(type, handler) { (this.listeners[type] ??= []).push(handler); }, removeEventListener() {}, dispatchEvent(event) { for (const handler of this.listeners[event.type] ?? []) handler(event); }, matchMedia() { return { matches: false, addEventListener() {}, removeEventListener() {} }; } }" if browser_fixture else "unexpectedBrowserAccess('window')"
    script = "\n".join(
        [
            'const fs = require("fs");',
            'const path = require("path");',
            'const ts = require("typescript");',
            'const vm = require("vm");',
            "const cache = new Map();",
            "const nativeCalls = [];",
            "let hostWindow;",
            f"const sourcePath = path.resolve({json.dumps(str(REPO_ROOT))}, {json.dumps(module_path)});",
            "function resolveTsModule(basePath, specifier) {",
            "  const resolved = path.resolve(path.dirname(basePath), specifier);",
            "  const candidates = [resolved, `${resolved}.ts`, `${resolved}.json`, path.join(resolved, 'index.ts')];",
            "  for (const candidate of candidates) {",
            "    if (fs.existsSync(candidate)) return candidate;",
            "  }",
            "  return require.resolve(specifier, { paths: [path.dirname(basePath)] });",
            "}",
            "function loadModule(filePath) {",
            "  const absolutePath = path.resolve(filePath);",
            "  if (cache.has(absolutePath)) return cache.get(absolutePath).exports;",
            "  if (absolutePath.endsWith('.json')) { const module = { exports: { default: JSON.parse(fs.readFileSync(absolutePath, 'utf8')) } }; cache.set(absolutePath, module); return module.exports; }",
            '  const source = fs.readFileSync(absolutePath, "utf8");',
            "  const js = ts.transpileModule(source, {",
            "    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }",
            "  }).outputText;",
            "  const module = { exports: {} };",
            "  cache.set(absolutePath, module);",
            "  function localRequire(specifier) {",
            "    if (specifier === '@tauri-apps/api/core') return { invoke(command, payload) { nativeCalls.push({ command, payload }); return Promise.resolve({ masked_path: '/tmp/masked.pdf', report_path: '/tmp/report.json', report: {}, extracted_text: '', masked_text: '' }); } };",
            "    if (specifier === '@tauri-apps/plugin-opener') return { openPath() { return Promise.resolve(); } };",
            "    if (specifier === 'react') return { useEffect(effect) { effect(); } };",
            "    if (specifier === 'pdfjs-dist') return { GlobalWorkerOptions: {}, getDocument() { throw new Error('pdfjs unavailable in controller unit test'); } };",
            "    if (specifier === 'pdfjs-dist/build/pdf.worker.min.mjs?url') return 'worker.js';",
            "    if (specifier.startsWith('.')) return loadModule(resolveTsModule(absolutePath, specifier));",
            "    return require(specifier);",
            "  }",
            "  const unexpectedBrowserAccess = (name) => new Proxy(Object.create(null), { get(_target, property) { throw new Error(`unexpected browser access: ${name}.${String(property)}`); }, set(_target, property) { throw new Error(`unexpected browser access: ${name}.${String(property)}`); } });",
            f"  const browserElement = {{ value: '', textContent: '', innerHTML: '', checked: false, disabled: false, hidden: false, tabIndex: 0, offsetParent: {{}}, dataset: {{}}, style: {{}}, classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }}, setAttribute() {{}}, removeAttribute() {{}}, addEventListener() {{}}, removeEventListener() {{}}, click() {{}}, matches() {{ return true; }}, closest() {{ return this; }}, querySelector() {{ return this; }}, querySelectorAll() {{ return []; }}, getClientRects() {{ return [{{}}]; }}, append() {{}}, appendChild() {{}}, replaceChildren() {{}}, focus() {{}}, getContext() {{ return {{ clearRect() {{}}, fillRect() {{}}, drawImage() {{}} }}; }}, getBoundingClientRect() {{ return {{ left: 0, top: 0, width: 1, height: 1 }}; }} }}; const browserDocument = {{ body: browserElement, documentElement: browserElement, querySelector() {{ return browserElement; }}, querySelectorAll() {{ return []; }}, createElement() {{ return {{ ...browserElement }}; }} }}; const browserWindow = {window_source}; if (!hostWindow) hostWindow = browserWindow; const sandbox = {{ module, exports: module.exports, require: localRequire, Event: {event_source}, HTMLElement: Object, URLSearchParams, document: {document_source}, window: browserWindow, globalThis: {{}} }};"
            f'  if ({json.dumps(default_storage)} === "throwing") Object.defineProperty(sandbox.globalThis, "localStorage", {{ get() {{ throw new Error("blocked default storage"); }} }});',
            "  vm.runInNewContext(js, sandbox, { filename: absolutePath });",
            "  return module.exports;",
            "}",
            "const m = loadModule(sourcePath);",
            f"if ({str(browser_fixture).lower()}) globalThis.window = hostWindow;",
            f"const result = {expression};",
            "Promise.resolve(result).then((value) => {",
            "  console.log(JSON.stringify(value));",
            "});",
        ]
    )
    try:
        output = subprocess.check_output(
            ["node", "-e", script],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            timeout=10,
            stderr=subprocess.STDOUT,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.output or ""
        raise AssertionError(f"Node helper timed out for {module_path}: {stdout}") from error
    except subprocess.CalledProcessError as error:
        raise AssertionError(f"Node helper failed for {module_path}: {error.output or ''}") from error
    return json.loads(output)


def geometry_review_mock_manifest(
    *,
    revision: int = 1,
    manifest_hash: str = "a" * 64,
    resolved_region_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    arguments = json.dumps(
        {
            "revision": revision,
            "manifestHash": manifest_hash,
            "resolvedRegionIds": list(resolved_region_ids),
        },
    )
    output = subprocess.check_output(
        [
            "node",
            "--input-type=module",
            "--eval",
            f"import {{ geometryReviewManifestForQa }} from './scripts/qa_tauri_mock.mjs'; console.log(JSON.stringify(geometryReviewManifestForQa({arguments})));",
        ],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    return json.loads(output)


def canonical_review_manifest(*, status: str = "pending", revision: int = 7) -> dict[str, object]:
    digest = "a" * 64
    segment = {
        "segmentId": "segment-1", "analysisRevision": revision, "pageStart": 0,
        "pageEnd": 0, "kind": "common", "state": "confirmed", "commonOnly": True, "source": "routing",
    }
    return {
        "manifestVersion": 1, "runId": "run-1", "originalDocumentHash": digest,
        "analysisRevision": revision, "manifestHash": "b" * 64, "profile": "mixed",
        "policyVersion": "policy-v1", "optionsVersion": "options-v1", "optionsHash": digest,
        "thresholdVersion": "thresholds-v2", "thresholdHash": digest,
        "thresholdArtifact": {
            "version": "thresholds-v2",
            "contentHash": digest,
            "autoMaskThreshold": 0.85,
            "reviewThreshold": 0.5,
        },
        "coordinateSpace": "pdf_points_top_left",
        "approvalCoverage": {
            "approval": "absent", "header_meta": "absent", "labeled_staff": "absent",
        },
        "requiredRegionCoverage": {
            "recipient_reference": "absent", "sender_institution": "absent",
            "approval_staff": "absent", "dispatch_metadata": "absent",
            "footer_contact": "absent",
        },
        "segments": [segment], "regions": [],
        "occurrences": [], "manualActions": [], "reviewItems": [{
            "reviewId": "review-1", "analysisRevision": revision, "kind": "boundary",
            "targetId": "segment-1", "pageStart": 0, "pageEnd": 0, "status": status,
            "reasonCodes": ["ambiguous_boundary"], "requiresAcknowledgment": True,
            "commonOnly": True, "provenance": "routing",
        }],
    }



class FrontendStateHelperTests(unittest.TestCase):
    def test_mask_counts_separate_automatic_manual_mask_and_restore_without_double_counting(self):
        manifest = canonical_review_manifest(status="resolved")
        manifest["occurrences"] = [{
            "occurrenceId": "occ_aaaaaaaaaaaaaaaaaaaaaaaa",
            "segmentId": "segment-1",
            "regionId": None,
            "analysisRevision": manifest["analysisRevision"],
            "page": 0,
            "rects": [{"x0": 10, "y0": 10, "x1": 20, "y1": 20}],
            "tag": "PHONE",
            "category": "phone",
            "valueHash": "c" * 64,
            "expectedTextHash": "d" * 64,
            "source": "qa",
            "policy": "mask",
            "proposedAction": "mask",
            "state": "confirmed",
            "provenance": "qa",
        }]
        manifest["manualActions"] = [
            {
                "actionId": "manual-mask",
                "analysisRevision": manifest["analysisRevision"],
                "page": 0,
                "rects": [{"x0": 30, "y0": 30, "x1": 40, "y1": 40}],
                "protectedNeighborRefs": [],
                "mode": "mask",
                "sourceKind": "scan",
                "linkedOccurrenceId": None,
                "expectedTextHash": None,
            },
            {
                "actionId": "manual-restore",
                "analysisRevision": manifest["analysisRevision"],
                "page": 0,
                "rects": [{"x0": 50, "y0": 50, "x1": 60, "y1": 60}],
                "protectedNeighborRefs": [],
                "mode": "restore",
                "sourceKind": "scan",
                "linkedOccurrenceId": None,
                "expectedTextHash": None,
            },
        ]
        identity = {key: manifest[key] for key in ("runId", "originalDocumentHash", "analysisRevision", "manifestHash", "profile")}
        result = run_node_helper(
            "src/state/maskingSession.ts",
            "(() => {"
            f"const manifest = {json.dumps(manifest)};"
            f"const identity = {json.dumps(identity)};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const dashboard = loadModule(path.resolve('src/dashboardSurfaceModels.ts'));"
            "const parsed = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity);"
            "if (!parsed.ok) return { parsed, counts: null, protocol: null, review: null, pages: null };"
            "return { parsed, counts: session.canonicalMaskCounts(parsed.value), protocol: session.canonicalMaskCount(parsed.value), review: dashboard.dashboardReviewSurfaceCounts(parsed.value), pages: dashboard.dashboardPageMaskCounts(parsed.value) };"
            "})()",
        )

        self.assertTrue(result["parsed"]["ok"], result)
        self.assertEqual(
            {
                "automaticMaskCount": 1,
                "manualMaskCount": 1,
                "manualRestoreCount": 1,
                "effectiveMaskCount": 2,
            },
            result["counts"]["value"],
        )
        self.assertEqual(3, result["protocol"]["value"])
        self.assertEqual({"autoMasked": 1, "pending": 0, "resolved": 1, "total": 1}, result["review"])
        self.assertEqual([{
            "page": 0,
            "automaticMaskCount": 1,
            "manualMaskCount": 1,
            "manualRestoreCount": 1,
            "effectiveMaskCount": 2,
        }], result["pages"])

    def test_first_masking_page_exposes_page_two_without_mutating_current_page(self):
        manifest = geometry_review_mock_manifest()
        identity = {key: manifest[key] for key in ("runId", "originalDocumentHash", "analysisRevision", "manifestHash", "profile")}
        result = run_node_helper(
            "src/dashboardSurfaceModels.ts",
            "(() => {"
            f"const manifest = {json.dumps(manifest)};"
            f"const identity = {json.dumps(identity)};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const dashboard = loadModule(path.resolve('src/dashboardSurfaceModels.ts'));"
            "const parsed = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity);"
            "return parsed.ok ? { first: dashboard.dashboardFirstMaskingPage(parsed.value), pages: dashboard.dashboardPageMaskCounts(parsed.value) } : { first: null, pages: [] };"
            "})()",
        )

        self.assertEqual(1, result["first"])
        self.assertEqual([1], [page["page"] for page in result["pages"]])
        self.assertEqual(2, result["pages"][0]["automaticMaskCount"])

    def test_masking_option_summary_uses_authoritative_runtime_profile(self):
        result = run_node_helper(
            "src/state/workspaceBoundary.ts",
            "(() => {"
            "const draft = { reviewId: 'review-1', segmentId: 'segment-1', pageStart: 3, pageEnd: 4, segmentKind: 'official_dispatch' };"
            "return { home: m.clampWorkspaceBoundaryDraft(draft, 0, 'start', 3, 5), end: m.clampWorkspaceBoundaryDraft(draft, 99, 'end', 3, 5), pointer: m.clampWorkspaceBoundaryDraft(draft, 1, 'start', 3, 5) };"
            "})()",
        )

        self.assertEqual({"pageStart": 3, "pageEnd": 4}, {key: result["home"][key] for key in ("pageStart", "pageEnd")})
        self.assertEqual({"pageStart": 3, "pageEnd": 5}, {key: result["end"][key] for key in ("pageStart", "pageEnd")})
        self.assertEqual({"pageStart": 3, "pageEnd": 4}, {key: result["pointer"][key] for key in ("pageStart", "pageEnd")})

    def test_suggested_geometry_confirmation_includes_uncovered_linked_detection_rects(self):
        manifest = canonical_review_manifest()
        region = {
            "regionId": "region-1", "segmentId": "segment-1", "analysisRevision": 7,
            "page": 0, "rects": [{"x0": 1, "y0": 1, "x1": 4, "y1": 4}],
            "kind": "approval", "state": "review_required", "confirmationSource": None,
            "reasonCodes": ["geometry_review"], "source": "routing",
        }
        occurrence = {
            "occurrenceId": "occ_000000000000000000000001", "segmentId": "segment-1", "regionId": "region-1",
            "analysisRevision": 7, "page": 0, "rects": [{"x0": 5, "y0": 1, "x1": 7, "y1": 3}],
            "tag": "NAME", "category": "person", "valueHash": "c" * 64, "expectedTextHash": "d" * 64,
            "source": "routing", "policy": "token", "proposedAction": "review", "state": "review_required", "provenance": "routing",
        }
        review = {**manifest["reviewItems"][0], "kind": "region_geometry", "targetId": "region-1", "reasonCodes": ["geometry_review"]}
        public_manifest = {**manifest, "regions": [region], "occurrences": [occurrence], "reviewItems": [review]}
        result = run_node_helper(
            "src/app/applicationController.ts",
            "(() => {"
            f"const manifest = {json.dumps(public_manifest)};"
            "return m.suggestedRegionGeometryRects(manifest, manifest.reviewItems[0]);"
            "})()",
        )
        self.assertEqual(
            [{"x0": 1, "y0": 1, "x1": 4, "y1": 4}, {"x0": 5, "y0": 1, "x1": 7, "y1": 3}],
            result,
        )

    def test_suggested_geometry_confirmation_covers_entire_grouped_card(self):
        manifest = canonical_review_manifest()
        regions = [
            {
                "regionId": "region-1", "segmentId": "segment-1", "analysisRevision": 7,
                "page": 0, "rects": [{"x0": 1, "y0": 1, "x1": 4, "y1": 4}],
                "kind": "approval", "state": "review_required", "confirmationSource": None,
                "reasonCodes": ["geometry_review"], "source": "routing",
            },
            {
                "regionId": "region-2", "segmentId": "segment-1", "analysisRevision": 7,
                "page": 0, "rects": [{"x0": 3, "y0": 2, "x1": 6, "y1": 5}],
                "kind": "approval", "state": "review_required", "confirmationSource": None,
                "reasonCodes": ["geometry_review"], "source": "routing",
            },
        ]
        occurrences = [
            {
                "occurrenceId": f"occ_{index:024d}", "segmentId": "segment-1", "regionId": region_id,
                "analysisRevision": 7, "page": 0, "rects": [rect], "tag": "NAME", "category": "person",
                "valueHash": "c" * 64, "expectedTextHash": "d" * 64, "source": "routing", "policy": "token",
                "proposedAction": "review", "state": "review_required", "provenance": "routing",
            }
            for index, (region_id, rect) in enumerate([
                ("region-1", {"x0": 7, "y0": 1, "x1": 8, "y1": 2}),
                ("region-2", {"x0": 7, "y0": 3, "x1": 8, "y1": 4}),
            ], start=1)
        ]
        reviews = [
            {
                **manifest["reviewItems"][0], "reviewId": f"review-{index}", "kind": "region_geometry",
                "targetId": region["regionId"], "reasonCodes": ["geometry_review"],
            }
            for index, region in enumerate(regions, start=1)
        ]
        public_manifest = {**manifest, "regions": regions, "occurrences": occurrences, "reviewItems": reviews}
        result = run_node_helper(
            "src/app/applicationController.ts",
            "(() => {"
            f"const manifest = {json.dumps(public_manifest)};"
            "return m.suggestedRegionGeometryRects(manifest, manifest.reviewItems[0]);"
            "})()",
        )
        self.assertEqual(
            [
                {"x0": 1, "y0": 1, "x1": 4, "y1": 4},
                {"x0": 3, "y0": 2, "x1": 6, "y1": 5},
                {"x0": 7, "y0": 1, "x1": 8, "y1": 2},
                {"x0": 7, "y0": 3, "x1": 8, "y1": 4},
            ],
            result,
        )

    def test_application_controller_runtime_survives_workspace_unregistration(self):
        result = run_node_helper(
            "src/state/appControllerRuntime.ts",
            "(() => {"
            "const deskController = { name: 'desk' };"
            "const workspaceController = { name: 'workspace' };"
            "m.registerApplicationController(deskController);"
            "const beforeUnrelatedClear = m.applicationController()?.name;"
            "m.clearApplicationController(workspaceController);"
            "const afterUnrelatedClear = m.applicationController()?.name;"
            "m.clearApplicationController(deskController);"
            "return { beforeUnrelatedClear, afterUnrelatedClear, afterOwnerClear: m.applicationController() };"
            "})()",
        )

        self.assertEqual("desk", result["beforeUnrelatedClear"])
        self.assertEqual("desk", result["afterUnrelatedClear"])
        self.assertIsNone(result["afterOwnerClear"])

    def test_session_document_store_keeps_distinct_saves_for_same_path_and_minute(self):
        result = run_node_helper(
            "src/state/sessionDocumentsStore.ts",
            "(() => {"
            "m.publishSessionDocuments({ documents: [], saves: ["
            "{ id: 'save-1', path: '/out/final.pdf', maskCount: 1, savedAt: '12:34' },"
            "{ id: 'save-2', path: '/out/final.pdf', maskCount: 1, savedAt: '12:34' }"
            "], profile: 'mixed' });"
            "return m.sessionDocumentsState().saves.map((save) => save.id);"
            "})()",
        )

        self.assertEqual(["save-1", "save-2"], result)

    def test_manual_actions_require_protected_neighbor_references(self):
        manifest = canonical_review_manifest(status="resolved")
        manifest["manualActions"] = [{
            "actionId": "manual-1",
            "analysisRevision": 7,
            "page": 0,
            "rects": [{"x0": 10, "y0": 10, "x1": 20, "y1": 20}],
            "protectedNeighborRefs": [{"x0": 25, "y0": 10, "x1": 35, "y1": 20}],
            "mode": "mask",
            "sourceKind": "text_pdf",
            "linkedOccurrenceId": None,
            "expectedTextHash": None,
        }]
        result = run_node_helper(
            "src/state/maskingSession.ts",
            "(() => {"
            f"const valid = {json.dumps(manifest)};"
            "const missing = structuredClone(valid); delete missing.manualActions[0].protectedNeighborRefs;"
            "const empty = structuredClone(valid); empty.manualActions[0].protectedNeighborRefs = [];"
            "const parsed = m.parseAnalysisManifestV1(valid);"
            "return {"
            "valid: parsed.ok,"
            "refs: parsed.ok ? parsed.value.manualActions[0].protectedNeighborRefs : null,"
            "missing: m.parseAnalysisManifestV1(missing).ok,"
            "empty: m.parseAnalysisManifestV1(empty).ok"
            "};"
            "})()",
        )

        self.assertTrue(result["valid"])
        self.assertEqual(
            [{"x0": 25, "y0": 10, "x1": 35, "y1": 20}],
            result["refs"],
        )
        self.assertFalse(result["missing"])
        self.assertFalse(result["empty"])
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


    def test_pdf_canvas_with_or_without_base_masking_can_apply_manual_boxes(self):
        result = run_node_helper(
            "src/workflowFlow.ts",
            "({"
            "withoutOutput: m.documentWorkflowReadiness({ documentKind: 'pdf', basePreviewPath: '', manualPreviewPath: '', safeReportPath: '', boxCount: 1, latestDocumentPath: '/tmp/original.pdf' }),"
            "withOutput: m.documentWorkflowReadiness({ documentKind: 'pdf', basePreviewPath: '/tmp/masked.pdf', manualPreviewPath: '', safeReportPath: '/tmp/safe.json', boxCount: 1, latestDocumentPath: '/tmp/masked.pdf' })"
            "})",
        )

        self.assertTrue(result["withoutOutput"]["canApplyManualPreview"])
        self.assertIn("수동 보정", result["withoutOutput"]["manualApplyReason"])
        self.assertTrue(result["withOutput"]["canApplyManualPreview"])
        self.assertEqual("수동 보정 대기", result["withoutOutput"]["phaseLabel"])
        self.assertEqual("수동 보정 대기", result["withOutput"]["phaseLabel"])

    def test_public_text_page_mask_box_can_apply_without_a_preview_pdf_path(self):
        result = run_node_helper(
            "src/workflowFlow.ts",
            "m.documentWorkflowReadiness({ documentKind: 'pdf', basePreviewPath: '', manualPreviewPath: '', safeReportPath: '', boxCount: 1, publicManualMaskEligible: true })",
        )

        self.assertTrue(result["canApplyManualPreview"])
        self.assertIn("공공", result["manualApplyReason"])
        self.assertNotIn("먼저 불러오", result["manualApplyReason"])

    def test_public_text_page_mask_box_enables_canvas_apply_button(self):
        manifest = canonical_review_manifest()
        result = run_node_helper(
            "src/features/finalization/finalizationController.ts",
            "(() => {"
            f"const manifest = {json.dumps(manifest)};"
            "const element = () => ({ disabled: false, title: '', tabIndex: 0, textContent: '', dataset: {}, classList: { toggle() {} }, setAttribute() {}, querySelector() { return null; }, nextElementSibling: null });"
            "const apply = element();"
            "const state = { documentProvenance: { original: { path: '/tmp/original.pdf', kind: 'pdf' }, generated: { path: '', artifactPath: '' }, manual: { path: '' }, final: { path: '' }, continuation: null }, resultDoc: {}, latestExtractedPath: '', latestMaskedPath: '', latestMaskedTextPolicy: '', latestReportPath: '/tmp/report.json', latestReport: { product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, activeRunKind: 'public', publicRunIdentity: { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile }, restoreRevalidationFailed: false, baseMaskingProgress: { displayMode: 'black' }, boxes: [{ page: 0, mode: 'mask', tag: 'manual' }], geometryDraft: null, documentEditRevision: 1, maskingRunning: false, batchRunning: false, savingInFlight: false, batchItems: [] };"
            "const controller = m.createFinalizationController({ state, btnSave: element(), btnCanvasFinalSave: element(), btnRunMasking: element(), btnManualApply: element(), btnCanvasApply: apply, btnNewDocument: element(), btnPickPdf: element(), btnPickBatch: element(), btnRunBatch: element(), btnClear: element(), finalSaveReadinessEl: element(), isPdfInput: () => true, renderDocumentReviewSurfaces() {} });"
            "controller.updateWorkflowReadiness();"
            "return { disabled: apply.disabled, title: apply.title };"
            "})()",
        )

        self.assertFalse(result["disabled"])
        self.assertIn("공공", result["title"])
        self.assertNotIn("먼저 불러오", result["title"])

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
            "for (let turns = 0; !resolveApply && turns < 100; turns += 1) await Promise.resolve(); if (!resolveApply) throw new Error('apply_manual_boxes was not invoked');"
            "state.documentProvenance = { ...original, original: { path: '/docs/replacement.pdf', kind: 'pdf' }, generated: { path: '', artifactPath: '' }, manual: { path: '' }, final: { path: '' } };"
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


    def test_manual_apply_discards_revalidation_after_document_session_changes(self):
        result = run_node_helper(
            "src/features/manual-adjustment/manualAdjustmentController.ts",
            "(async () => {"
            "const original = { original: { path: '/docs/original.pdf', kind: 'pdf' }, generated: { path: '/work/generated.pdf', artifactPath: '/work/generated.pdf' }, manual: { path: '' }, final: { path: '' } };"
            "const state = { documentProvenance: original, outputDir: '/out', currentResultPage: 1, resultDoc: {}, scale: 1, boxes: [{ page: 1, x0: 1, y0: 1, x1: 2, y1: 2, mode: 'restore' }], mode: 'restore', selectedCanvasBoxIndex: -1, canvasMode: true, maskingRunning: false, batchRunning: false, extractedText: 'old extracted', maskedText: 'old masked', baseExtractedText: '', baseMaskedText: '', preManualPreviewPdf: '', preManualExtractedText: '', preManualMaskedText: '', latestReportPath: '/work/old.safe_report.json', latestReport: { marker: 'old' }, lastPreviewDiagnostics: '', restoreRevalidationFailed: false };"
            "let resolveRead; let renderCount = 0; let compareCount = 0;"
            "const controller = m.createManualAdjustmentController({"
            "state, invokeCommand: (command) => command === 'apply_manual_boxes' ? Promise.resolve({ status: 'applied', output_file: '/work/manual.pdf', mask_count: 0, restore_count: 1, applied_count: 1, requires_revalidation: true, revalidation_status: 'passed', revalidation_report: '/work/manual.safe_report.json' }) : new Promise((resolve) => { resolveRead = resolve; }),"
            "displayModeEl: { value: 'black' }, isPdfInput: () => true, ensurePreviewWorkDir: async () => '/work', loadResultPdf: async () => true,"
            "updateWorkflowReadiness: () => {}, setStatus: () => {}, btnCanvasClear: { disabled: false }, updateMeta: () => {},"
            "renderFinalState: () => { renderCount += 1; }, setTextCompareContents: () => { compareCount += 1; }"
            "});"
            "const pending = controller.applyPendingManualBoxes('apply');"
            "for (let turns = 0; !resolveRead && turns < 100; turns += 1) await Promise.resolve(); if (!resolveRead) throw new Error('revalidation report read was not invoked');"
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

    def test_manual_nonpassed_revalidation_invalidates_prior_evidence(self):
        result = run_node_helper(
            "src/features/manual-adjustment/manualAdjustmentController.ts",
            "(async () => {"
            "const state = { documentProvenance: { original: { path: '/docs/original.pdf', kind: 'pdf' }, generated: { path: '/work/generated.pdf', artifactPath: '/work/generated.pdf' }, manual: { path: '' }, final: { path: '' } }, outputDir: '/out', currentResultPage: 1, resultDoc: {}, scale: 1, boxes: [{ page: 0, x0: 1, y0: 1, x1: 2, y1: 2, mode: 'restore' }], mode: 'restore', selectedCanvasBoxIndex: -1, canvasMode: true, maskingRunning: false, batchRunning: false, savingInFlight: false, extractedText: '', maskedText: '', baseExtractedText: '', baseMaskedText: '', preManualPreviewPdf: '', preManualExtractedText: '', preManualMaskedText: '', latestReportPath: '/work/prior.safe_report.json', latestReport: { product_checks: {}, analysisManifest: { stale: true } }, latestMaskedPath: '', latestMaskedTextPolicy: '', lastPreviewDiagnostics: '', restoreRevalidationFailed: false };"
            "const controller = m.createManualAdjustmentController({ state, invokeCommand: async () => ({ status: 'applied', output_file: '/work/manual.pdf', mask_count: 0, restore_count: 1, applied_count: 1, requires_revalidation: true, revalidation_status: 'failed' }), displayModeEl: { value: 'black' }, isPdfInput: () => true, ensurePreviewWorkDir: async () => '/work', loadResultPdf: async () => true, updateWorkflowReadiness: () => {}, setStatus: () => {}, btnCanvasClear: { disabled: false }, updateMeta: () => {}, renderFinalState: () => {}, setTextCompareContents: () => {} });"
            "const applied = await controller.applyPendingManualBoxes('apply');"
            "return { applied: Boolean(applied), report: state.latestReport, reportPath: state.latestReportPath, revalidationFailed: state.restoreRevalidationFailed, boxes: state.boxes.length };"
            "})()",
        )
        self.assertTrue(result["applied"])
        self.assertIsNone(result["report"])
        self.assertEqual(result["reportPath"], "")
        self.assertTrue(result["revalidationFailed"])
        self.assertEqual(result["boxes"], 0)
    def test_derived_reset_clears_generated_manual_and_final_artifacts(self):
        result = run_node_helper(
            "src/state/documentProvenance.ts",
            "(() => { const original = m.selectOriginalDocument(m.emptyDocumentProvenance(), '/docs/original.pdf', 'pdf'); const generated = m.adoptGeneratedPreview(original, '/work/generated.pdf', '/work/report.json'); const manual = m.adoptManualPreview(generated, '/work/manual.pdf'); return m.resetDerivedProvenance(manual); })()",
        )
        self.assertEqual(result["original"]["path"], "/docs/original.pdf")
        self.assertFalse(result["generated"]["path"])
        self.assertFalse(result["manual"]["path"])
        self.assertFalse(result["final"]["path"])

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
        manifest = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/dashboardSurfaceModels.ts",
            "(() => {"
            f"const base = {manifest};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = { runId: base.runId, originalDocumentHash: base.originalDocumentHash, analysisRevision: base.analysisRevision, manifestHash: base.manifestHash, profile: base.profile };"
            "const report = session.parseBoundSafeReport({ analysisManifest: base, reviewQueue: base.reviewItems, product_checks: { quality_gate_passed: false, needs_manual_review: true } }, identity).value;"
            "return m.buildDashboardSurfaceModel({ selectedPath: '/work/current.pdf', documentKind: 'pdf', batchItems: [], latestDocumentPath: '/out/current.final_masked.pdf', latestReportPath: '/out/current.safe_report.json', report });"
            "})()",
        )
        self.assertEqual(result["documentTitle"], "current.pdf")
        self.assertEqual(
            [
                {"label": "PDF", "state": "ok"},
                {"label": "위험 1", "state": "warn"},
                {"label": "결과 문서 있음", "state": "ok"},
                {"label": "안전 리포트 있음", "state": "ok"},
            ],
            result["health"],
        )

    def test_geometry_review_cards_merge_overlaps_and_present_region_reasons(self):
        manifest = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/dashboardSurfaceModels.ts",
            "(() => {"
            f"const manifest = {manifest};"
            "const rect = (x0, y0, x1, y1) => ({ x0, y0, x1, y1 });"
            "manifest.regions = ["
            "{ regionId: 'region-1', segmentId: 'segment-1', analysisRevision: 7, page: 0, rects: [rect(10, 10, 100, 100)], kind: 'approval', state: 'review_required', confirmationSource: null, reasonCodes: ['box_structure_missing'], source: 'layout' },"
            "{ regionId: 'region-2', segmentId: 'segment-1', analysisRevision: 7, page: 0, rects: [rect(50, 50, 140, 140)], kind: 'approval', state: 'review_required', confirmationSource: null, reasonCodes: ['geometry_review'], source: 'layout' },"
            "{ regionId: 'region-3', segmentId: 'segment-1', analysisRevision: 7, page: 0, rects: [rect(50, 50, 140, 140)], kind: 'header_meta', state: 'review_required', confirmationSource: null, reasonCodes: ['geometry_review'], source: 'layout' }"
            "];"
            "manifest.reviewItems = manifest.regions.map((region, index) => ({ reviewId: `geometry-review-${index + 1}`, analysisRevision: 7, kind: 'region_geometry', targetId: region.regionId, pageStart: 0, pageEnd: 0, status: 'pending', reasonCodes: ['geometry_review'], requiresAcknowledgment: false, commonOnly: false, provenance: 'layout' }));"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile };"
            "const report = session.parseBoundSafeReport({ analysisManifest: manifest, reviewQueue: manifest.reviewItems, product_checks: {} }, identity).value;"
            "return m.dashboardReviewState(report);"
            "})()",
        )

        self.assertEqual(result["status"], "valid")
        self.assertEqual(len(result["items"]), 2)
        merged, distinct_kind = result["items"]
        self.assertEqual(merged["kindLabel"], "결재선 영역 2건 통합")
        self.assertEqual(merged["reviewIds"], ["geometry-review-1", "geometry-review-2"])
        self.assertEqual(merged["mergedCount"], 2)
        self.assertIn("영역 테두리 구조를 확인할 수 없음", merged["detail"])
        self.assertEqual(distinct_kind["kindLabel"], "머리말 정보 영역")
        self.assertEqual(distinct_kind["mergedCount"], 1)

    def test_geometry_mock_manifest_auto_confirms_linked_occurrences_without_review_cards(self):
        pending_manifest = json.dumps(geometry_review_mock_manifest())
        pending_value = json.loads(pending_manifest)
        self.assertEqual([region["page"] for region in pending_value["regions"]], [1, 1])
        self.assertEqual([], pending_value["reviewItems"])
        first_rect = pending_value["regions"][0]["rects"][0]
        second_rect = pending_value["regions"][1]["rects"][0]
        self.assertLess(max(first_rect["x0"], second_rect["x0"]), min(first_rect["x1"], second_rect["x1"]))
        self.assertLess(max(first_rect["y0"], second_rect["y0"]), min(first_rect["y1"], second_rect["y1"]))
        self.assertEqual(
            [occurrence["regionId"] for occurrence in pending_value["occurrences"]],
            [region["regionId"] for region in pending_value["regions"]],
        )
        self.assertTrue(all(occurrence["state"] == "confirmed" for occurrence in pending_value["occurrences"]))
        self.assertTrue(all(region["confirmationSource"] == "automatic" for region in pending_value["regions"]))
        resolved_manifest = json.dumps(
            geometry_review_mock_manifest(
                revision=3,
                manifest_hash="d" * 64,
                resolved_region_ids=("geometry-region-1-r3", "geometry-region-2-r3"),
            ),
        )
        result = run_node_helper(
            "src/dashboardSurfaceModels.ts",
            "(() => {"
            f"const pendingManifest = {pending_manifest};"
            f"const resolvedManifest = {resolved_manifest};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const saveGate = loadModule(path.resolve('src/features/save-gate/saveGate.ts'));"
            "const report = (value) => { const identity = { runId: value.runId, originalDocumentHash: value.originalDocumentHash, analysisRevision: value.analysisRevision, manifestHash: value.manifestHash, profile: value.profile }; return session.parseBoundSafeReport({ analysisManifest: value, reviewQueue: value.reviewItems, product_checks: {} }, identity); };"
            "const pending = report(pendingManifest); const resolved = report(resolvedManifest);"
            "return { pendingParsed: session.parseAnalysisManifestV1(pendingManifest).ok, resolvedParsed: session.parseAnalysisManifestV1(resolvedManifest).ok, pending: pending.ok ? m.dashboardReviewState(pending.value) : null, pendingGate: saveGate.finalSaveGate({ report: pending.ok ? pending.value : null }), resolved: resolved.ok ? m.dashboardReviewState(resolved.value) : null, resolvedGate: saveGate.finalSaveGate({ report: resolved.ok ? resolved.value : null }) };"
            "})()",
        )

        self.assertTrue(result["pendingParsed"])
        self.assertTrue(result["resolvedParsed"])
        self.assertEqual(result["pending"]["status"], "valid")
        self.assertEqual([], result["pending"]["items"])
        self.assertTrue(result["pendingGate"]["eligible"])
        self.assertEqual([], result["resolved"]["items"])
        self.assertTrue(result["resolvedGate"]["eligible"])

    def test_zero_review_cards_cannot_turn_indeterminate_coverage_into_a_hard_block(self):
        manifest = canonical_review_manifest(status="resolved")
        manifest["reviewItems"] = []
        manifest["approvalCoverage"]["approval"] = "indeterminate"
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const manifest = {json.dumps(manifest)};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const dashboard = loadModule(path.resolve('src/dashboardSurfaceModels.ts'));"
            "const identity = { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile };"
            "const parsed = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity);"
            "if (!parsed.ok) return { parsed: false };"
            "return { parsed: true, review: dashboard.dashboardReviewState(parsed.value), gate: m.finalSaveGate({ report: parsed.value }) };"
            "})()",
        )
        self.assertTrue(result["parsed"])
        self.assertEqual([], result["review"]["items"])
        self.assertEqual("advisory", result["gate"]["state"])
        self.assertIn("indeterminate_coverage_requires_reanalysis", result["gate"]["reasonCodes"])

    def test_geometry_successor_review_counts_preserve_resolutions_across_two_steps(self):
        initial_manifest = json.dumps(geometry_review_mock_manifest())
        first_successor_manifest = json.dumps(
            geometry_review_mock_manifest(
                revision=2,
                manifest_hash="c" * 64,
                resolved_region_ids=("geometry-region-1-r2",),
            ),
        )
        completed_successor_manifest = json.dumps(
            geometry_review_mock_manifest(
                revision=3,
                manifest_hash="d" * 64,
                resolved_region_ids=("geometry-region-1-r3", "geometry-region-2-r3"),
            ),
        )
        result = run_node_helper(
            "src/dashboardSurfaceModels.ts",
            "(() => {"
            f"const manifests = [{initial_manifest}, {first_successor_manifest}, {completed_successor_manifest}];"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const surfaces = loadModule(path.resolve('src/dashboardSurfaces.ts'));"
            "const report = (value) => { const identity = { runId: value.runId, originalDocumentHash: value.originalDocumentHash, analysisRevision: value.analysisRevision, manifestHash: value.manifestHash, profile: value.profile }; return session.parseBoundSafeReport({ analysisManifest: value, reviewQueue: value.reviewItems, product_checks: {} }, identity).value; };"
            "return manifests.map((manifest) => { const bound = report(manifest); return { reviews: manifest.reviewItems.map((item) => item.status), counts: m.dashboardReviewSurfaceCounts(bound), sidebar: surfaces.reportSessionCounts(bound).pendingCount }; });"
            "})()",
        )

        self.assertEqual([entry["reviews"] for entry in result], [[], [], []])
        self.assertEqual([{"autoMasked": 2, "pending": 0, "resolved": 0, "total": 0}] * 3, [entry["counts"] for entry in result])
        self.assertEqual([entry["sidebar"] for entry in result], [0, 0, 0])

    def test_public_review_projection_and_save_gate_fail_closed(self):
        pending_manifest = json.dumps(canonical_review_manifest())
        resolved_manifest = json.dumps(canonical_review_manifest(status="resolved"))
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const pendingManifest = {pending_manifest};"
            f"const resolvedManifest = {resolved_manifest};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = (manifest) => ({ runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile });"
            "const report = (manifest) => session.parseBoundSafeReport({ product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity(manifest)).value;"
            "const pending = report(pendingManifest);"
            "return {"
            "missing: m.finalSaveGate({ report: null }),"
            "pending: m.finalSaveGate({ report: pending }),"
            "clone: m.finalSaveGate({ report: pending }), stale: m.finalSaveGate({ report: { ...pending, reviewQueue: pending.reviewQueue.map((item, index) => index ? item : ({ ...item, pageEnd: item.pageEnd + 1 })) } }),"
            "resolved: m.finalSaveGate({ report: report(resolvedManifest) }),"
            "restore: m.finalSaveGate({ report: report(resolvedManifest), restoreRevalidationFailed: true }),"
            "};"
            "})()",
        )

        self.assertFalse(result["missing"]["eligible"])
        self.assertEqual(result["missing"]["reasonCodes"], ["missing_current_session"])
        self.assertFalse(result["pending"]["eligible"])
        self.assertEqual(result["pending"]["reasonCodes"], ["ambiguous_boundary"])
        self.assertFalse(result["clone"]["eligible"])
        self.assertEqual(result["clone"]["reasonCodes"], ["ambiguous_boundary"])
        self.assertFalse(result["stale"]["eligible"])
        self.assertEqual(result["stale"]["reasonCodes"], ["missing_current_session"])
        self.assertTrue(result["resolved"]["eligible"])
        self.assertEqual(result["resolved"]["reasonCodes"], [])
        self.assertFalse(result["restore"]["eligible"])
        self.assertEqual(result["restore"]["reasonCodes"], ["restore_revalidation_failed"])

    def test_masking_failure_presents_only_safe_occurrence_diagnostics(self):
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            "const detail = 'MASKING_PIPELINE_TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED;diagnostics=' + JSON.stringify([{ kind: 'occurrence_failure', reason_code: 'expected_text_hash_mismatch', count: 1, occurrence_id: 'occ_' + 'a'.repeat(24), category: 'dispatch_metadata', page: 1, rect_fingerprint: 'b'.repeat(64), expected_text_hash: 'c'.repeat(64), observed_text_hash: 'd'.repeat(64) }]);"
            "const safe = m.presentMaskingFailure({ code: 'MASKING_PIPELINE_TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED', stage: 'pipeline_failure_code', detail });"
            "const unsafe = m.presentMaskingFailure({ code: 'MASKING_PIPELINE_TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED', stage: 'pipeline_failure_code', detail: detail.replace('dispatch_metadata', '기관명') });"
            "return { safe, unsafe };"
            "})()",
        )

        self.assertEqual(
            result["safe"]["diagnostics"],
            [{
                "kind": "occurrence_failure",
                "reasonCode": "expected_text_hash_mismatch",
                "count": 1,
                "occurrenceId": "occ_" + "a" * 24,
                "category": "dispatch_metadata",
                "page": 1,
                "rectFingerprint": "b" * 64,
                "expectedTextHash": "c" * 64,
                "observedTextHash": "d" * 64,
            }],
        )
        self.assertEqual([], result["unsafe"]["diagnostics"])

    def test_public_and_legal_save_presentations_use_distinct_policies(self):
        pending_manifest = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const manifest = {pending_manifest};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile };"
            "const publicReport = session.parseBoundSafeReport({ product_checks: { quality_gate_passed: false }, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity).value;"
            "const legalReport = { product_checks: { quality_gate_passed: false }, document_redaction: { verification: { residual_hits: 1 } } };"
            "return { public: m.publicFinalSavePresentation({ report: publicReport }), legal: m.finalSaveWarningPresentation({ hasReportPath: true, report: legalReport }), legalGate: m.legalCompatibilityFinalSaveGate({ hasReportPath: true, report: legalReport }) };"
            "})()",
        )

        self.assertEqual(result["public"]["stateName"], "review")
        self.assertEqual(result["public"]["title"], "사용자 확인 필요")
        self.assertEqual(result["legal"]["stateName"], "review")
        self.assertEqual(result["legal"]["detail"], "잔존 개인정보 후보 1건이 남아 있습니다. 보정 화면에서 확인하는 것을 권장합니다.")
        self.assertEqual(result["legal"]["warnings"][0], result["legal"]["detail"])
        self.assertEqual(result["legalGate"]["state"], "advisory")
        self.assertTrue(result["legalGate"]["eligible"])

    def test_review_resolution_rejects_non_authoritative_responses_without_state_changes(self):
        pending_manifest = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/features/masking-run/maskingRunController.ts",
            "(async () => {"
            f"const pending = {pending_manifest};"
            "const report = { product_checks: {}, analysisManifest: pending, reviewQueue: pending.reviewItems };"
            "const request = { runId: pending.runId, analysisRevision: pending.analysisRevision, manifestHash: pending.manifestHash, reviewId: 'review-1', resolution: { kind: 'boundary', pageStart: 0, pageEnd: 0, segmentKind: 'attachment' } };"
            "const resolved = { ...pending, analysisRevision: 8, manifestHash: 'f'.repeat(64), segments: pending.segments.map((item) => ({ ...item, segmentId: 'segment-2', analysisRevision: 8, kind: 'attachment', state: 'user_confirmed' })), reviewItems: pending.reviewItems.map((item) => ({ ...item, reviewId: 'review-2', targetId: 'segment-2', analysisRevision: 8, status: 'resolved' })) };"
            "const cases = { wrongRun: { ...resolved, runId: 'run-2' }, wrongRevision: { ...resolved, analysisRevision: 9, segments: resolved.segments.map((item) => ({ ...item, analysisRevision: 9 })), reviewItems: resolved.reviewItems.map((item) => ({ ...item, analysisRevision: 9 })) }, staleHash: { ...resolved, manifestHash: pending.manifestHash }, wrongProfile: { ...resolved, profile: 'official_dispatch' }, wrongOriginalHash: { ...resolved, originalDocumentHash: 'e'.repeat(64) }, wrongOptionsHash: { ...resolved, optionsHash: 'e'.repeat(64) }, missingQueue: { ...resolved, reviewItems: [] }, extraQueue: { ...resolved, reviewItems: [...resolved.reviewItems, { ...resolved.reviewItems[0], reviewId: 'extra' }] }, unresolvedRequest: { ...resolved, reviewItems: resolved.reviewItems.map((item) => ({ ...item, status: 'pending' })) }, malformed: { ...resolved, manifestHash: 'bad' } };"
            "const outcomes = {}; for (const [name, response] of Object.entries(cases)) { const state = { latestReport: report, latestReportPath: '/report.json', geometryDraft: null, savingInFlight: false, publicRunIdentity: { runId: pending.runId, originalDocumentHash: pending.originalDocumentHash, analysisRevision: pending.analysisRevision, manifestHash: pending.manifestHash, profile: pending.profile } }; const before = JSON.stringify(state.latestReport); let callbacks = 0; const controller = m.createMaskingRunController({ state, resolveMaskingReview: async () => response, renderFinalState: () => callbacks++, renderDocumentReviewSurfaces: () => callbacks++, updateWorkflowReadiness: () => callbacks++, setStatus: () => {} }); outcomes[name] = { accepted: await controller.resolveReview(request), unchanged: before === JSON.stringify(state.latestReport), reportPath: state.latestReportPath, callbacks }; }"
            "const state = { latestReport: report, latestReportPath: '/report.json', geometryDraft: null, savingInFlight: false, publicRunIdentity: { runId: pending.runId, originalDocumentHash: pending.originalDocumentHash, analysisRevision: pending.analysisRevision, manifestHash: pending.manifestHash, profile: pending.profile } }; let callbacks = 0; const controller = m.createMaskingRunController({ state, resolveMaskingReview: async () => resolved, renderFinalState: () => callbacks++, renderDocumentReviewSurfaces: () => callbacks++, updateWorkflowReadiness: () => callbacks++, setStatus: () => {} }); outcomes.valid = { accepted: await controller.resolveReview(request), revision: state.latestReport.analysisManifest.analysisRevision, hash: state.latestReport.analysisManifest.manifestHash, status: state.latestReport.reviewQueue[0].status, reportPath: state.latestReportPath, callbacks }; return outcomes;"
            "})()",
        )
        for name, outcome in result.items():
            if name == "valid":
                continue
            self.assertFalse(outcome["accepted"], name)
            self.assertTrue(outcome["unchanged"], name)
            self.assertEqual(outcome["reportPath"], "/report.json", name)
            self.assertEqual(outcome["callbacks"], 0, name)
        self.assertTrue(result["valid"]["accepted"])
        self.assertEqual(result["valid"]["revision"], 8)
        self.assertEqual(result["valid"]["hash"], "f" * 64)
        self.assertEqual(result["valid"]["status"], "resolved")
        self.assertEqual(result["valid"]["reportPath"], "")
        self.assertEqual(result["valid"]["callbacks"], 3)
    def test_manifest_segment_and_region_enums_fail_closed(self):
        manifest = canonical_review_manifest(status="resolved")
        manifest["regions"] = [{
            "regionId": "region-1", "segmentId": "segment-1", "analysisRevision": 7,
            "page": 0, "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}],
            "kind": "approval", "state": "confirmed", "confirmationSource": "automatic",
            "reasonCodes": [], "source": "layout",
        }]
        payload = json.dumps(manifest)
        result = run_node_helper(
            "src/state/maskingSession.ts",
            "(() => {"
            f"const valid = {payload};"
            "const parse = (value) => m.parseAnalysisManifestV1(value).ok;"
            "return { valid: parse(valid), badSegmentKind: parse({ ...valid, segments: valid.segments.map((item) => ({ ...item, kind: 'attacker' })) }), badSegmentState: parse({ ...valid, segments: valid.segments.map((item) => ({ ...item, state: 'approved' })) }), badRegionKind: parse({ ...valid, regions: valid.regions.map((item) => ({ ...item, kind: 'attacker' })) }), badRegionState: parse({ ...valid, regions: valid.regions.map((item) => ({ ...item, state: 'approved' })) }), badConfirmationSource: parse({ ...valid, regions: valid.regions.map((item) => ({ ...item, confirmationSource: 'server' })) }) };"
            "})()",
        )
        self.assertTrue(result["valid"])
        for name in ["badSegmentKind", "badSegmentState", "badRegionKind", "badRegionState", "badConfirmationSource"]:
            self.assertFalse(result[name], name)
    def test_review_resolution_revision_rules_and_review_target_bijection(self):
        pending_manifest = json.dumps(canonical_review_manifest())
        result = run_node_helper(
            "src/features/masking-run/maskingRunController.ts",
            "(async () => {"
            f"const seed = {pending_manifest};"
            "const rect = { x0: 1, y0: 1, x1: 2, y1: 2 };"
            "const make = (kind) => { const manifest = structuredClone(seed); const geometryReview = kind === 'region_geometry'; manifest.regions = [{ regionId: 'region-1', segmentId: 'segment-1', analysisRevision: 7, page: 0, rects: [rect], kind: 'approval', state: geometryReview ? 'unconfirmed' : 'confirmed', confirmationSource: geometryReview ? null : 'automatic', reasonCodes: geometryReview ? ['box_structure_missing'] : [], source: 'layout' }]; manifest.occurrences = [{ occurrenceId: 'occ_aaaaaaaaaaaaaaaaaaaaaaaa', segmentId: 'segment-1', regionId: 'region-1', analysisRevision: 7, page: 0, rects: [rect], tag: 'person', category: 'name', valueHash: 'c'.repeat(64), expectedTextHash: 'd'.repeat(64), source: 'ocr', policy: 'default', proposedAction: 'review', state: 'confirmed', provenance: 'ocr' }, { occurrenceId: 'occ_cccccccccccccccccccccccc', segmentId: 'segment-1', regionId: 'region-1', analysisRevision: 7, page: 0, rects: [rect], tag: 'person', category: 'name', valueHash: 'e'.repeat(64), expectedTextHash: 'f'.repeat(64), source: 'ocr', policy: 'default', proposedAction: 'review', state: 'confirmed', provenance: 'ocr' }]; manifest.manualActions = [{ actionId: 'manual-1', analysisRevision: 7, page: 0, rects: [rect], protectedNeighborRefs: [{ x0: 2, y0: 2, x1: 3, y1: 3 }], mode: 'mask', sourceKind: 'text_pdf', linkedOccurrenceId: manifest.occurrences[1].occurrenceId, expectedTextHash: 'f'.repeat(64) }]; const targetId = kind === 'name' || kind === 'institution' ? manifest.occurrences[0].occurrenceId : kind === 'region_geometry' ? manifest.regions[0].regionId : manifest.segments[0].segmentId; manifest.reviewItems = [{ ...manifest.reviewItems[0], kind, targetId, reasonCodes: geometryReview ? ['box_structure_missing'] : manifest.reviewItems[0].reasonCodes }, { ...manifest.reviewItems[0], reviewId: 'review-2', kind: 'name', targetId: manifest.occurrences[1].occurrenceId }]; return manifest; };"
            "const request = (manifest, kind) => ({ runId: manifest.runId, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, reviewId: 'review-1', resolution: kind === 'name' || kind === 'institution' ? { kind, action: kind === 'name' ? 'mask' : 'exclude' } : kind === 'acknowledge' ? { kind } : kind === 'boundary' ? { kind, pageStart: 0, pageEnd: 0, segmentKind: 'attachment' } : kind === 'region_geometry' ? { kind, rects: [{ x0: 3, y0: 3, x1: 4, y1: 4 }] } : { kind, accepted: true } });"
            "const reply = (current, kind) => { const next = structuredClone(current); const creates = kind === 'boundary' || kind === 'region_geometry'; next.analysisRevision += creates ? 1 : 0; next.manifestHash = 'f'.repeat(64); for (const key of ['segments', 'regions', 'occurrences', 'reviewItems', 'manualActions']) next[key] = next[key].map((item) => ({ ...item, analysisRevision: next.analysisRevision })); if (creates) { next.segments[0] = { ...next.segments[0], segmentId: 'segment-2' }; next.regions[0] = { ...next.regions[0], regionId: 'region-2', segmentId: 'segment-2' }; next.occurrences = next.occurrences.map((item, index) => ({ ...item, occurrenceId: index === 0 ? 'occ_bbbbbbbbbbbbbbbbbbbbbbbb' : 'occ_dddddddddddddddddddddddd', segmentId: 'segment-2', regionId: 'region-2' })); next.reviewItems = next.reviewItems.map((item, index) => ({ ...item, reviewId: index === 0 ? 'review-3' : 'review-4', targetId: index === 0 ? item.targetId : next.occurrences[1].occurrenceId })); next.manualActions[0] = { ...next.manualActions[0], actionId: 'manual-2', linkedOccurrenceId: next.occurrences[1].occurrenceId }; } const review = next.reviewItems[0]; review.status = 'resolved'; if (kind === 'name' || kind === 'institution') { next.occurrences[0] = { ...next.occurrences[0], occurrenceId: 'occ_bbbbbbbbbbbbbbbbbbbbbbbb', proposedAction: kind === 'name' ? 'mask' : 'exclude', state: 'confirmed' }; review.targetId = next.occurrences[0].occurrenceId; } else if (kind === 'acknowledge') { next.segments[0] = { ...next.segments[0], state: 'user_confirmed' }; } else if (kind === 'boundary') { next.segments[0] = { ...next.segments[0], kind: 'attachment', state: 'user_confirmed' }; review.targetId = next.segments[0].segmentId; } else if (kind === 'region_geometry') { next.regions[0] = { ...next.regions[0], rects: [{ x0: 3, y0: 3, x1: 4, y1: 4 }], state: 'user_confirmed', confirmationSource: 'user' }; review.targetId = next.regions[0].regionId; } return next; };"
            "const attempt = async (kind, mutate) => { const current = make(kind); const response = reply(current, kind); if (mutate) mutate(response); const report = { product_checks: {}, analysisManifest: current, reviewQueue: current.reviewItems }; const state = { latestReport: report, latestReportPath: '/report.json', geometryDraft: null, savingInFlight: false, publicRunIdentity: { runId: current.runId, originalDocumentHash: current.originalDocumentHash, analysisRevision: current.analysisRevision, manifestHash: current.manifestHash, profile: current.profile } }; const beforeReport = JSON.stringify(state.latestReport); const beforeIdentity = JSON.stringify(state.publicRunIdentity); let callbacks = 0; const controller = m.createMaskingRunController({ state, resolveMaskingReview: async () => response, renderFinalState: () => callbacks++, renderDocumentReviewSurfaces: () => callbacks++, updateWorkflowReadiness: () => callbacks++, setStatus: () => {} }); const accepted = await controller.resolveReview(request(current, kind)); return { accepted, unchanged: beforeReport === JSON.stringify(state.latestReport), identityUnchanged: beforeIdentity === JSON.stringify(state.publicRunIdentity), path: state.latestReportPath, callbacks, revision: state.latestReport.analysisManifest.analysisRevision }; };"
            "const kinds = ['name', 'institution', 'acknowledge', 'boundary', 'region_geometry']; const positive = {}; for (const kind of kinds) positive[kind] = await attempt(kind); const negative = { nameWrongAction: await attempt('name', (next) => { next.occurrences[0].proposedAction = 'exclude'; }), institutionWrongAction: await attempt('institution', (next) => { next.occurrences[0].proposedAction = 'mask'; }), acknowledgePending: await attempt('acknowledge', (next) => { next.segments[0].state = 'confirmed'; }), boundarySameRevision: await attempt('boundary', (next) => { next.analysisRevision = 7; for (const key of ['segments', 'regions', 'occurrences', 'reviewItems', 'manualActions']) next[key] = next[key].map((item) => ({ ...item, analysisRevision: 7 })); }), geometrySameRevision: await attempt('region_geometry', (next) => { next.analysisRevision = 7; for (const key of ['segments', 'regions', 'occurrences', 'reviewItems', 'manualActions']) next[key] = next[key].map((item) => ({ ...item, analysisRevision: 7 })); }), geometryWrongRects: await attempt('region_geometry', (next) => { next.regions[0].rects = [rect]; }), geometryNotUserConfirmed: await attempt('region_geometry', (next) => { next.regions[0].state = 'confirmed'; next.regions[0].confirmationSource = 'automatic'; }), sameRevisionReviewSubstitution: await attempt('name', (next) => { next.reviewItems[1].targetId = next.occurrences[0].occurrenceId; }), revisionReviewTargetSubstitution: await attempt('boundary', (next) => { next.reviewItems[1].targetId = next.occurrences[0].occurrenceId; }), staleRevisionId: await attempt('boundary', (next) => { next.segments[0].segmentId = 'segment-1'; next.regions[0].segmentId = 'segment-1'; next.occurrences = next.occurrences.map((item) => ({ ...item, segmentId: 'segment-1' })); next.reviewItems[0].targetId = 'segment-1'; }), manualActionMutation: await attempt('region_geometry', (next) => { next.manualActions[0].mode = 'mask'; next.manualActions[0].rects = [{ x0: 4, y0: 4, x1: 5, y1: 5 }]; }), sameRevisionManualActionMutation: await attempt('name', (next) => { next.manualActions[0].rects = [{ x0: 4, y0: 4, x1: 5, y1: 5 }]; }), ocr: await attempt('ocr') }; return { positive, negative };"
            "})()",
        )
        for kind, outcome in result["positive"].items():
            self.assertTrue(outcome["accepted"], kind)
            self.assertEqual(outcome["callbacks"], 3, kind)
            self.assertEqual(outcome["revision"], 8 if kind in ["boundary", "region_geometry"] else 7, kind)
        for name, outcome in result["negative"].items():
            self.assertFalse(outcome["accepted"], name)
            self.assertTrue(outcome["unchanged"], name)
            self.assertTrue(outcome["identityUnchanged"], name)
            self.assertEqual(outcome["path"], "/report.json", name)
            self.assertEqual(outcome["callbacks"], 0, name)
    def test_finalize_response_parser_rejects_partial_or_malformed_success(self):
        from test_frontend_state_helpers import canonical_review_manifest

        manifest = json.dumps(canonical_review_manifest(status="resolved"))
        result = run_node_helper(
            "src/services/tauri/maskingContracts.ts",
            f"(() => {{"
            f"const manifest = {manifest};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts')); const identity = { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile }; const report = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity).value;"
            "const request = { runId: 'run-1', analysisRevision: 7, manifestHash: 'b'.repeat(64), destination: '/tmp/final.pdf', saveToken: '0'.repeat(32), warningsConfirmed: false };"
            "const prepared = m.prepareFinalizeMaskingRun(request, report).value;"
            "const parse = (value) => m.parseFinalizeMaskingRunResult(value, prepared);"
            "const result = { runId: 'run-1', analysisRevision: 7, manifestHash: 'b'.repeat(64), finalPath: '/tmp/final.pdf', finalHash: 'a'.repeat(64), finalHashAttested: true, occurrenceCount: 0, appliedMaskCount: 0, manualMaskCount: 0, restoreCount: 0, effectiveMaskCount: 0, restoreAuthorization: { actionIdHash: '0'.repeat(64), targetOccurrenceIdHash: '0'.repeat(64), authorizationEvent: 'none' }, saveConfirmation: { status: 'not_required', unresolvedReviews: [] }, status: 'promoted' };"
            "return { valid: parse(result), missingPrepared: m.parseFinalizeMaskingRunResult(result, undefined), missingIdentity: parse({ ...result, manifestHash: 'c'.repeat(64) }), missingAttestation: parse({ ...result, finalHashAttested: false }), blankPath: parse({ ...result, finalPath: ' ' }), badHash: parse({ ...result, finalHash: 'nope' }), wrongCount: parse({ ...result, occurrenceCount: 1 }) };"
            "})()",
        )

        self.assertEqual(
            result["valid"],
            {"ok": True, "value": {"runId": "run-1", "analysisRevision": 7, "manifestHash": "b" * 64, "status": "promoted", "finalPath": "/tmp/final.pdf", "finalHash": "a" * 64, "finalHashAttested": True, "occurrenceCount": 0, "appliedMaskCount": 0, "manualMaskCount": 0, "restoreCount": 0, "effectiveMaskCount": 0, "restoreAuthorization": {"actionIdHash": "0" * 64, "targetOccurrenceIdHash": "0" * 64, "authorizationEvent": "none"}, "saveConfirmation": {"status": "not_required", "unresolvedReviews": []}}},
        )
        for name in ["missingPrepared", "missingIdentity", "missingAttestation", "blankPath", "badHash", "wrongCount"]:
            self.assertFalse(result[name]["ok"], name)

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

    def test_settings_persistence_round_trip_and_reports_stable_malformed_diagnostic(self):
        result = run_node_helper(
            "src/settingsState.ts",
            "(() => {"
            "  const storage = { value: null, getItem() { return this.value; }, setItem(_key, value) { this.value = value; }, removeItem() { this.value = null; } };"
            "  const saved = m.saveSettings({ theme: 'light', outputDir: '/tmp/out', profile: 'legal', engine: 'pymupdf', displayMode: 'label_ko', deidentificationMode: 'partial', regionScope: 'custom', customRegions: '서울 중구', customKeywords: '홍길동', pdfRedaction: false, exportMaskedText: true, openOutputAfterSave: true }, storage);"
            "  const loaded = m.loadSettings(storage);"
            "  storage.value = '{'; const malformed = m.loadSettings(storage);"
            "  const invalidThemes = ['purple', 'default'].map((value) => { try { m.themeAttribute(value); return false; } catch { return true; } });"
            "  return { saved, loaded, malformed, invalidThemes };"
            "})()",
        )
        self.assertEqual(result["saved"]["settings"]["theme"], "light")
        self.assertEqual(result["saved"]["diagnostic"], {"status": "saved"})
        self.assertEqual(result["loaded"]["settings"]["outputDir"], "")
        self.assertEqual(result["loaded"]["settings"]["displayMode"], "label_ko")
        self.assertEqual(result["loaded"]["settings"]["deidentificationMode"], "partial")
        self.assertFalse(result["loaded"]["settings"]["pdfRedaction"])
        self.assertTrue(result["loaded"]["settings"]["exportMaskedText"])
        self.assertTrue(result["loaded"]["settings"]["openOutputAfterSave"])
        self.assertEqual(result["malformed"]["settings"]["theme"], "light")
        self.assertEqual(
            {"status": "defaulted", "reason": "storage_parse_failed"},
            result["malformed"]["diagnostic"],
        )
        self.assertEqual(result["invalidThemes"], [True, True])

    def test_theme_defaults_distinguish_new_users_from_legacy_settings(self):
        result = run_node_helper(
            "src/settingsState.ts",
            "(() => {"
            "  const empty = { getItem() { return null; }, setItem() {}, removeItem() {} };"
            "  const legacy = { getItem() { return JSON.stringify({ profile: 'official' }); }, setItem() {}, removeItem() {} };"
            "  const malformed = { getItem() { return '{'; }, setItem() {}, removeItem() {} };"
            "  return {"
            "    newUser: m.loadSettings(empty).settings.theme,"
            "    legacyUser: m.loadSettings(legacy).settings.theme,"
            "    malformedLegacy: m.loadSettings(malformed).settings.theme,"
            "    light: m.resolveTheme('light', true),"
            "    dark: m.resolveTheme('dark', false),"
            "    systemLight: m.resolveTheme('system', false),"
            "    systemDark: m.resolveTheme('system', true),"
            "  };"
            "})()",
        )

        self.assertEqual(result["newUser"], "light")
        self.assertEqual(result["legacyUser"], "light")
        self.assertEqual(result["malformedLegacy"], "light")
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

        settings = result["settings"]
        self.assertEqual(settings["theme"], "light")
        self.assertEqual(settings["engine"], "auto")
        self.assertNotIn("outputArtifacts", settings)
        self.assertEqual(settings["displayMode"], "black")
        self.assertEqual(settings["deidentificationMode"], "token")
        self.assertEqual(settings["regionScope"], "national")
        self.assertNotIn("selectedInputPdf", settings)
        self.assertNotIn("boxes", settings)
        self.assertNotIn("batchItems", settings)
        self.assertEqual({"status": "defaulted", "reason": "invalid_payload"}, result["diagnostic"])

    def test_settings_unavailable_storage_is_stable_for_absent_throwing_default_and_explicit_null_paths(self):
        absent = run_node_helper(
            "src/settingsState.ts",
            "({ load: m.loadSettings(), save: m.saveSettings({ theme: 'light' }) })",
        )
        throwing = run_node_helper(
            "src/settingsState.ts",
            "({ load: m.loadSettings(), save: m.saveSettings({ theme: 'light' }) })",
            default_storage="throwing",
        )
        explicit_null = run_node_helper(
            "src/settingsState.ts",
            "({ load: m.loadSettings(null), save: m.saveSettings({ theme: 'light' }, null) })",
        )
        for outcome in (absent, throwing, explicit_null):
            self.assertEqual(
                {"status": "defaulted", "reason": "storage_unavailable"},
                outcome["load"]["diagnostic"],
            )
            self.assertEqual({"status": "failed", "reason": "storage_unavailable"}, outcome["save"]["diagnostic"])
    def test_all_settings_profiles_keep_mixed_as_the_default(self):
        result = run_node_helper(
            "src/settingsState.ts",
            "({ defaultProfile: m.mergeSettings({}).profile, profiles: ['internal_review', 'official_dispatch', 'mixed', 'legal'].map((profile) => m.mergeSettings({ profile }).profile) })",
        )
        self.assertEqual("mixed", result["defaultProfile"])
        self.assertEqual(["internal_review", "official_dispatch", "mixed", "legal"], result["profiles"])

    def test_document_session_busy_and_reset_paths_are_executable_and_cancel_geometry(self):
        result = run_node_helper(
            "src/features/document-session/documentSessionController.ts",
            "(async () => {"
            "const base = { documentProvenance: { original: { path: '/docs/a.pdf', kind: 'pdf' }, generated: { path: '/work/a.pdf', artifactPath: '/work/a.pdf' }, manual: { path: '' }, final: { path: '' } }, outputDir: '/out', previewWorkDir: '/work', currentOrigPage: 1, currentResultPage: 1, boxes: [{ tag: 'review:one' }, { tag: 'manual' }], geometryDraft: { owner: 'review:one' }, documentEditRevision: 4, selectedCanvasBoxIndex: 0, origDoc: {}, resultDoc: {}, extractedText: 'secret', maskedText: 'masked', baseExtractedText: '', baseMaskedText: '', initialMaskingPreviewPdf: '', initialExtractedText: '', initialMaskedText: '', preManualPreviewPdf: '', preManualExtractedText: '', preManualMaskedText: '', lastPreviewDiagnostics: '', latestExtractedPath: '', latestMaskedPath: '', latestMaskedTextPolicy: '', latestReportPath: '/report', latestReport: {}, activeRunKind: 'public', restoreRevalidationFailed: false, baseMaskingProgress: { status: 'idle', percent: 0, displayMode: 'black' }, mode: 'mask', batchItems: [], batchActiveIndex: -1, batchRunning: false };"
            "const make = (state, busy) => { const status = []; let cancellations = 0; const controller = m.createDocumentSessionController({ state, inputPathEl: { value: '' }, displayModeEl: { value: 'black' }, modeMask: { setAttribute() {} }, modeRestore: { setAttribute() {} }, invokeCommand: async () => null, hasTauriRuntime: () => false, clampPage: () => 1, renderCompare: async () => {}, renderDocumentReviewSurfaces: () => {}, setCompareMode: () => {}, setTextCompareContents: () => {}, setBaseMaskingProgress: () => {}, renderFinalState: () => {}, updateOutputDirectoryState: () => {}, updateWorkflowReadiness: () => {}, updateCanvasControls: () => {}, cancelGeometryDraft: () => { state.boxes = state.boxes.filter((box) => box.tag !== state.geometryDraft.owner); state.geometryDraft = null; state.documentEditRevision += 1; state.selectedCanvasBoxIndex = -1; cancellations++; return true; }, setCanvasMode: () => {}, redrawOverlay: () => {}, updateMeta: () => {}, resetLastSavedAt: () => {}, isBusy: () => busy, confirmDiscardCurrentWork: async () => true, resetCompareView: () => {}, renderBatchQueue: () => {}, closeTransientDialogs: () => {}, setStatus: (message) => status.push(message) }); return { controller, status, cancellations: () => cancellations }; };"
            "const busyState = JSON.parse(JSON.stringify(base)); const busy = make(busyState, true); const busyResult = await busy.controller.resetDocumentSession(); const resetState = JSON.parse(JSON.stringify(base)); const reset = make(resetState, false); const resetResult = await reset.controller.resetDocumentSession(); return { busyResult, busyStatus: busy.status, busyBoxes: busyState.boxes, resetResult, resetBoxes: resetState.boxes, revision: resetState.documentEditRevision, selected: resetState.selectedCanvasBoxIndex, cancellations: reset.cancellations(), report: resetState.latestReport };"
            "})()",
        )
        self.assertFalse(result["busyResult"])
        self.assertEqual(len(result["busyBoxes"]), 2)
        self.assertIn("실행 중", result["busyStatus"][0])
        self.assertTrue(result["resetResult"])
        self.assertEqual(result["resetBoxes"], [])
        self.assertEqual(result["revision"], 5)
        self.assertEqual(result["selected"], -1)
        self.assertEqual(result["cancellations"], 1)
        self.assertIsNone(result["report"])

    def test_canvas_workspace_load_failure_preserves_previous_document_session(self):
        result = run_node_helper(
            "src/features/document-session/documentSessionController.ts",
            "(async () => {"
            "const state = { documentProvenance: { original: { path: '/docs/previous.pdf', kind: 'pdf' }, generated: { path: '/work/previous.pdf', artifactPath: '/work/previous.pdf' }, manual: { path: '' }, final: { path: '' } }, outputDir: '/out', previewWorkDir: '/work', currentOrigPage: 2, currentResultPage: 3, boxes: [{ page: 0, x0: 1, y0: 2, x1: 3, y1: 4, mode: 'mask', tag: 'MANUAL' }], geometryDraft: null, documentEditRevision: 8, selectedCanvasBoxIndex: 0, origDoc: { id: 'previous-original' }, resultDoc: { id: 'previous-result' }, extractedText: 'previous', maskedText: 'masked', baseExtractedText: 'previous', baseMaskedText: 'masked', initialMaskingPreviewPdf: '/work/previous.pdf', initialExtractedText: 'previous', initialMaskedText: 'masked', preManualPreviewPdf: '/work/previous.pdf', preManualExtractedText: 'previous', preManualMaskedText: 'masked', lastPreviewDiagnostics: '', latestExtractedPath: '/work/previous.txt', latestMaskedPath: '/work/previous-masked.pdf', latestMaskedTextPolicy: 'token', latestReportPath: '/work/previous-report.json', latestReport: { id: 'previous-report' }, activeRunKind: 'public', restoreRevalidationFailed: false, baseMaskingProgress: { status: 'complete', percent: 100, displayMode: 'black' }, mode: 'mask', batchItems: [], batchActiveIndex: -1, batchRunning: false };"
            "const inputPathEl = { value: '/docs/previous.pdf' }; const controller = m.createDocumentSessionController({ state, inputPathEl, modeMask: { setAttribute() {} }, modeRestore: { setAttribute() {} }, invokeCommand: async () => { throw new Error('new document unreadable'); }, hasTauriRuntime: () => false, clampPage: () => 1, renderCompare: async () => {}, renderDocumentReviewSurfaces: () => {}, setCompareMode: () => {}, setTextCompareContents: () => {}, setBaseMaskingProgress: () => {}, renderFinalState: () => {}, updateOutputDirectoryState: () => {}, updateWorkflowReadiness: () => {}, updateCanvasControls: () => {}, cancelGeometryDraft: () => false, setCanvasMode: () => {}, redrawOverlay: () => {}, updateMeta: () => {}, resetLastSavedAt: () => {}, isBusy: () => false, confirmDiscardCurrentWork: async () => true, resetCompareView: () => {}, renderBatchQueue: () => {}, closeTransientDialogs: () => {}, setStatus: () => {} });"
            "let failure = ''; try { await controller.loadCanvasWorkspacePdf('/docs/new.pdf'); } catch (error) { failure = error instanceof Error ? error.message : String(error); } return { failure, path: state.documentProvenance.original.path, boxes: state.boxes, inputPath: inputPathEl.value, selected: state.selectedCanvasBoxIndex, report: state.latestReport?.id };"
            "})()",
        )

        self.assertEqual("new document unreadable", result["failure"])
        self.assertEqual("/docs/previous.pdf", result["path"])
        self.assertEqual("/docs/previous.pdf", result["inputPath"])
        self.assertEqual([{"page": 0, "x0": 1, "y0": 2, "x1": 3, "y1": 4, "mode": "mask", "tag": "MANUAL"}], result["boxes"])
        self.assertEqual(0, result["selected"])
        self.assertEqual("previous-report", result["report"])

    def test_production_document_replacement_waits_for_discard_modal_response(self):
        result = run_node_helper(
            "src/features/document-session/documentSessionController.ts",
            "(async () => {"
            "const state = { documentProvenance: { original: { path: '/docs/previous.pdf', kind: 'pdf' }, generated: { path: '', artifactPath: '' }, manual: { path: '' }, final: { path: '' } }, outputDir: '', previewWorkDir: '', currentOrigPage: 1, currentResultPage: 1, boxes: [], geometryDraft: null, documentEditRevision: 1, selectedCanvasBoxIndex: -1, origDoc: {}, resultDoc: {}, extractedText: '', maskedText: '', baseExtractedText: '', baseMaskedText: '', initialMaskingPreviewPdf: '', initialExtractedText: '', initialMaskedText: '', preManualPreviewPdf: '', preManualExtractedText: '', preManualMaskedText: '', lastPreviewDiagnostics: '', latestExtractedPath: '', latestMaskedPath: '', latestMaskedTextPolicy: '', latestReportPath: '', latestReport: null, activeRunKind: 'none', restoreRevalidationFailed: false, baseMaskingProgress: { status: 'idle', percent: 0, displayMode: 'black' }, mode: 'mask', batchItems: [], batchActiveIndex: -1, batchRunning: false };"
            "let resolveModal; let modalCalls = 0; let settled = false; const controller = m.createDocumentSessionController({ state, inputPathEl: { value: '' }, modeMask: { setAttribute() {} }, modeRestore: { setAttribute() {} }, invokeCommand: async () => null, hasTauriRuntime: () => false, clampPage: () => 1, renderCompare: async () => {}, renderDocumentReviewSurfaces: () => {}, setCompareMode: () => {}, setTextCompareContents: () => {}, setBaseMaskingProgress: () => {}, renderFinalState: () => {}, updateOutputDirectoryState: () => {}, updateWorkflowReadiness: () => {}, updateCanvasControls: () => {}, cancelGeometryDraft: () => false, setCanvasMode: () => {}, redrawOverlay: () => {}, updateMeta: () => {}, resetLastSavedAt: () => {}, isBusy: () => false, confirmDiscardCurrentWork: () => { modalCalls += 1; return new Promise((resolve) => { resolveModal = resolve; }); }, resetCompareView: () => {}, renderBatchQueue: () => {}, closeTransientDialogs: () => {}, setStatus: () => {} });"
            "const pending = controller.prepareForDocumentReplacement().then((value) => { settled = true; return value; }); await new Promise((resolve) => setTimeout(resolve, 0)); const waitingForModal = !settled; resolveModal(false); const result = await pending; return { waitingForModal, result, modalCalls, path: state.documentProvenance.original.path };"
            "})()",
        )

        self.assertTrue(result["waitingForModal"])
        self.assertFalse(result["result"])
        self.assertEqual(1, result["modalCalls"])
        self.assertEqual("/docs/previous.pdf", result["path"])
    def test_settings_storage_failures_are_observable_to_callers(self):
        result = run_node_helper(
            "src/settingsState.ts",
            "(() => {"
            "const good = { value: null, getItem() { return this.value; }, setItem(_key, value) { this.value = value; } };"
            "const blockedRead = { getItem() { throw new Error('blocked read'); }, setItem() {} };"
            "const blockedWrite = { getItem() { return null; }, setItem() { throw new Error('blocked write'); } };"
            "return { goodLoad: m.loadSettings(good), goodSave: m.saveSettings({ theme: 'light' }, good), readFailure: m.loadSettings(blockedRead), writeFailure: m.saveSettings({ theme: 'light' }, blockedWrite) };"
            "})()",
        )
        self.assertEqual(result["goodLoad"]["diagnostic"], {"status": "loaded"})
        self.assertEqual(result["goodSave"]["diagnostic"], {"status": "saved"})
        self.assertEqual(result["readFailure"]["settings"]["theme"], "light")
        self.assertEqual(result["readFailure"]["diagnostic"], {"status": "defaulted", "reason": "storage_read_failed"})
        self.assertEqual(result["writeFailure"]["settings"]["theme"], "light")
        self.assertEqual(result["writeFailure"]["diagnostic"], {"status": "failed", "reason": "write_failed"})


if __name__ == "__main__":
    unittest.main()
