import json
import struct
import unittest
from pathlib import Path



REPO_ROOT = Path(__file__).resolve().parents[1]


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise AssertionError(f"invalid PNG header: {path}")
    return struct.unpack(">II", header[16:24])


def ico_sizes(path: Path) -> set[tuple[int, int]]:
    data = path.read_bytes()
    if len(data) < 6:
        raise AssertionError(f"truncated ICO header: {path}")
    reserved, image_type, count = struct.unpack_from("<HHH", data)
    directory_size = 6 + count * 16
    if reserved != 0 or image_type != 1 or count == 0 or len(data) < directory_size:
        raise AssertionError(f"invalid ICO directory: {path}")

    sizes: set[tuple[int, int]] = set()
    for index in range(count):
        entry_offset = 6 + index * 16
        width = data[entry_offset] or 256
        height = data[entry_offset + 1] or 256
        image_size, image_offset = struct.unpack_from("<II", data, entry_offset + 8)
        if image_size == 0 or image_offset < directory_size or image_offset + image_size > len(data):
            raise AssertionError(f"invalid ICO image entry: {path}")
        sizes.add((width, height))
    return sizes


def icns_size(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 8 or data[:4] != b"icns":
        raise AssertionError(f"invalid ICNS header: {path}")
    declared_size = struct.unpack(">I", data[4:8])[0]
    if declared_size != len(data):
        raise AssertionError(f"invalid ICNS size: {path}")
    return declared_size


def frontend_markup():
    # 2026-08 Pencil replacement: include the live left rail, desk, storage,
    # and document screens so label tests target the current user-facing shell.
    component_paths = [
        REPO_ROOT / "src" / "components" / "layout" / "AppShell.tsx",
        REPO_ROOT / "src" / "components" / "layout" / "Sidebar.tsx",
        REPO_ROOT / "src" / "components" / "ui" / "Button.tsx",
        REPO_ROOT / "src" / "components" / "ui" / "Modal.tsx",
        REPO_ROOT / "src" / "components" / "ui" / "Toast.tsx",
        REPO_ROOT / "src" / "components" / "AppHeader.tsx",
        REPO_ROOT / "src" / "components" / "StatusRibbon.tsx",
        REPO_ROOT / "src" / "components" / "DocumentDeskScreen.tsx",
        REPO_ROOT / "src" / "components" / "CanvasWorkspace.tsx",
        REPO_ROOT / "src" / "components" / "MaskingSettingsScreen.tsx",
        REPO_ROOT / "src" / "components" / "SettingsScreen.tsx",
        REPO_ROOT / "src" / "components" / "StorageScreen.tsx",
    ]
    component_sources = "\n".join(path.read_text(encoding="utf-8") for path in component_paths)
    return (REPO_ROOT / "index.html").read_text(encoding="utf-8") + "\n" + (REPO_ROOT / "src" / "App.tsx").read_text(encoding="utf-8") + "\n" + component_sources




class FrontendLabelsTests(unittest.TestCase):
    def test_index_uses_nothing_ui_brand_with_korean_user_facing_labels(self):
        html = frontend_markup()

        self.assertIn("<title>Nothing</title>", html)
        self.assertIn("Nothing", html)
        self.assertIn('className="dm-header__home"', html)
        self.assertIn('className="dm-sidebar__item"', html)
        self.assertIn("문서 데스크", html)
        self.assertIn("마스킹 작업", html)
        self.assertIn("검토 대기", html)
        self.assertIn("저장함", html)
        self.assertIn("설정", html)
        self.assertNotIn('data-screen-target="coordinate-template"', html)
        self.assertNotIn('className="rail-brand-card"', html)
        self.assertNotIn('className="workspace-command-strip"', html)
        self.assertIn("현재 문서", html)
        self.assertIn("문서를 선택하세요", html)
        self.assertIn("PDF 열기", html)
        self.assertIn("문서명·유형으로 검색", html)
        self.assertIn("저장 문서 검색", html)
        self.assertIn("마스킹 미리보기", html)
        self.assertNotIn("HWPX 검토", html)
        self.assertNotIn("문서 마스킹 도구", html)
        self.assertNotIn("Document Masker", html)
        self.assertNotIn("FIX1515", html)
        self.assertNotIn("[PATCH", html)
        self.assertNotIn("extracted_text", html)
        self.assertNotIn("masked_text", html)

    def test_frontend_markup_does_not_ship_static_document_examples_as_live_state(self):
        html = frontend_markup()
        banned_live_samples = [
            "마스킹 대기 문서",
            "간담회 비용 지급",
            "전화 <kbd>PHONE</kbd>",
            "주소 <kbd>ADDRESS</kbd>",
            "사건번호 2024가단",
            "예산담당관",
            "비영리법인 설립",
            "검토 큐 2건",
            "현재 3개",
            "서울특별시/자치구",
            "2023_재무제표_최종본.pdf",
            "고객데이터_추출_0912.pdf",
            "계약서_모음_zip_extracted.pdf",
            "인사기록카드_스캔본_A동.pdf",
            "회의록_2023_하반기.pdf",
            "프로젝트_기획안_v2.pdf",
            "총 128개 항목",
            "3개 파일 선택됨",
            "42건",
            "18건",
            "66건",
        ]

        for sample in banned_live_samples:
            self.assertNotIn(sample, html)

        # v4 P2(문서 통합): 라이브보드(stage-summary-*/stage-alert-list/stage-document-title),
        # 문서 테이블(obsidian-document-rows), 저장 게이트 카드(dm-savegate)는 통합 화면에서
        # 폐지됐다. 정적 예시가 이 표면들을 통해 재유입되지 않도록 부재를 단언한다.
        self.assertNotIn('id="stage-document-title"', html)
        self.assertNotIn('id="stage-summary-risk-count"', html)
        self.assertNotIn('id="obsidian-document-rows"', html)
        self.assertNotIn('className="dm-savegate dm-card"', html)
        self.assertNotIn('id="stage-alert-list"', html)
        # 통합 화면에 살아있는 라이브 상태 앵커는 그대로 존재해야 한다.
        self.assertIn('id="obsidian-target-summary"', html)
        self.assertIn('id="obsidian-detection-list"', html)
        # v4.1: 안전 리포트 요약 표면이 내부화되며 검토 레일의 탐지 카드는
        # dm-detect__list 로 통일됐다(구 dm-report__guide 폐지).
        self.assertIn('className="dm-detect__list"', html)
        self.assertNotIn('id="keyword-result-preview"', html)
        self.assertIn('id="app-health-strip"', html)
        self.assertNotIn('id="stage-preview-output"', html)
        self.assertNotIn('className="document-preview-workbench"', html)
        self.assertNotIn('className="mock-document-page"', html)

    def test_no_user_facing_safe_report_wording_in_ui(self):
        # v4.1 신규 가드 (REDESIGN_V4_DARK §"가르치지 말고 치워라"): 안전 리포트는
        # 내부 검증 장치로만 존재한다. 사용자 대면 마크업(상단 바·통합 문서 화면·
        # 마스킹 설정·설정 화면·저장 모달) 어디에도 "안전 리포트"/"리포트" 사용자
        # 문구가 남으면 안 된다. 내부 코드 식별자(safeReportPath, reportBasename,
        # report_path 등)는 대상이 아니며, 여기서는 렌더되는 한글 문구 리터럴만 본다.
        html = frontend_markup()

        self.assertNotIn("안전 리포트", html)
        self.assertNotIn("리포트", html)

    def test_tauri_window_title_uses_nothing_brand(self):
        config = json.loads((REPO_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))

        self.assertEqual("Nothing", config["productName"])
        self.assertEqual("Nothing", config["app"]["windows"][0]["title"])
        self.assertEqual("io.github.galvaomica.nothing", config["identifier"])
        self.assertEqual("AGPL-3.0", config["bundle"]["license"])

        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        cargo = (REPO_ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
        self.assertEqual("AGPL-3.0", package["license"])
        self.assertIn('license = "AGPL-3.0"', cargo)

    def test_index_declares_raster_brand_favicon(self):
        html = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
        favicon = REPO_ROOT / "public" / "favicon.png"

        self.assertIn('rel="icon"', html)
        self.assertIn('/favicon.png', html)
        self.assertIn('type="image/png"', html)
        self.assertTrue(favicon.exists())
        self.assertLess(favicon.stat().st_size, 200_000)
        self.assertEqual((256, 256), png_size(favicon))

    def test_header_uses_raster_brand_mark_instead_of_generic_policy_symbol(self):
        sidebar = (REPO_ROOT / "src" / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
        header = (REPO_ROOT / "src" / "components" / "AppHeader.tsx").read_text(encoding="utf-8")

        self.assertIn('src="/favicon.png"', sidebar)
        self.assertIn('className="dm-sidebar__logo"', sidebar)
        self.assertIn('alt=""', sidebar)
        self.assertIn('aria-hidden="true"', sidebar)
        self.assertNotIn('<SymbolIcon name="policy"', header)
        self.assertIn('data-screen-target="documents"', sidebar)
        self.assertIn('data-screen-target="masking-settings"', header)
        self.assertIn('aria-label="문서 홈"', sidebar)

    def test_nothing_icon_assets_use_canonical_brand_raster(self):
        config = json.loads((REPO_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        icon_dir = REPO_ROOT / "src-tauri" / "icons"
        source = icon_dir / "nothing-icon-1024.png"
        generator = (REPO_ROOT / "scripts" / "generate_documasker_icons.py").read_text(encoding="utf-8")

        self.assertEqual(
            [
                "icons/32x32.png",
                "icons/128x128.png",
                "icons/128x128@2x.png",
                "icons/icon.icns",
                "icons/icon.ico",
            ],
            config["bundle"]["icon"],
        )
        self.assertTrue(source.exists())
        self.assertEqual((1024, 1024), png_size(source))
        self.assertEqual(
            {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)},
            ico_sizes(icon_dir / "icon.ico"),
        )
        self.assertGreater(icns_size(icon_dir / "icon.icns"), 8)
        self.assertIn("SOURCE_ICON", generator)
        self.assertIn('ICON_DIR / "nothing-icon-1024.png"', generator)
        self.assertNotIn("SVG_SOURCE", generator)
        self.assertNotIn("draw_nothing_document_icon", generator)
        self.assertFalse((icon_dir / "nothing.svg").exists())

    def test_review_queue_projection_and_action_inputs_are_complete_per_kind(self):
        from test_frontend_state_helpers import canonical_review_manifest, run_node_helper

        manifest = json.dumps({
            **canonical_review_manifest(),
            "approvalCoverage": {
                "approval": "absent",
                "header_meta": "absent",
                "labeled_staff": "absent",
            },
            "requiredRegionCoverage": {
                "recipient_reference": "absent",
                "sender_institution": "absent",
                "approval_staff": "absent",
                "dispatch_metadata": "absent",
                "footer_contact": "absent",
            },
        })
        result = run_node_helper(
            "src/services/tauri/maskingContracts.ts",
            "(() => {"
            f"const manifest = {manifest};"
            "const kinds = ['name', 'institution', 'acknowledge', 'boundary', 'region_geometry', 'ocr'];"
            "const resolutions = { name: { kind: 'name', action: 'mask' }, institution: { kind: 'institution', action: 'exclude' }, acknowledge: { kind: 'acknowledge', acknowledged: true }, boundary: { kind: 'boundary', pageStart: 0, pageEnd: 0, segmentKind: 'attachment' }, region_geometry: { kind: 'region_geometry', rects: [{ x0: 1, y0: 2, x1: 3, y1: 4 }] }, ocr: { kind: 'ocr', accepted: true } };"
            "const segment = { ...manifest.segments[0], pageEnd: 5 };"
            "const region = { regionId: 'region-1', segmentId: 'segment-1', analysisRevision: 7, page: 4, rects: [{ x0: 1, y0: 2, x1: 3, y1: 4 }], kind: 'approval', state: 'confirmed', confirmationSource: null, reasonCodes: [], source: 'routing' };"
            "const occurrence = (occurrenceId, page) => ({ occurrenceId, segmentId: 'segment-1', regionId: null, analysisRevision: 7, page, rects: [{ x0: 1, y0: 2, x1: 3, y1: 4 }], tag: 'candidate', category: 'person', valueHash: 'a'.repeat(64), expectedTextHash: 'b'.repeat(64), source: 'routing', policy: 'default', proposedAction: 'review', state: 'review_required', provenance: 'routing' });"
            "const occurrences = [occurrence('occ_000000000000000000000001', 0), occurrence('occ_000000000000000000000002', 1)];"
            "const targets = { name: occurrences[0].occurrenceId, institution: occurrences[1].occurrenceId, acknowledge: segment.segmentId, boundary: segment.segmentId, region_geometry: region.regionId, ocr: segment.segmentId };"
            "const items = kinds.map((kind, index) => ({ ...manifest.reviewItems[0], reviewId: `review-${kind}`, targetId: targets[kind], kind, pageStart: index, pageEnd: index, status: 'pending' }));"
            "const safeManifest = { ...manifest, segments: [segment], regions: [region], occurrences, reviewItems: items };"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = { runId: safeManifest.runId, originalDocumentHash: safeManifest.originalDocumentHash, analysisRevision: safeManifest.analysisRevision, manifestHash: safeManifest.manifestHash, profile: safeManifest.profile };"
            "const report = session.parseBoundSafeReport({ analysisManifest: safeManifest, reviewQueue: items, product_checks: {} }, identity).value;"
            "const request = (item, resolution) => ({ runId: safeManifest.runId, analysisRevision: safeManifest.analysisRevision, manifestHash: safeManifest.manifestHash, reviewId: item.reviewId, resolution });"
            "const boundaryKinds = ['internal_review', 'official_dispatch', 'attachment', 'legal']; const invalidBoundaryKinds = ['mixed', 'common', 'unknown', 'arbitrary']; const boundaryItem = items.find((item) => item.kind === 'boundary'); return { valid: Object.fromEntries(items.map((item) => [item.kind, m.prepareResolveMaskingReview(request(item, resolutions[item.kind]), report)])), boundaryKinds: Object.fromEntries(boundaryKinds.map((segmentKind) => [segmentKind, m.prepareResolveMaskingReview(request(boundaryItem, { kind: 'boundary', pageStart: 0, pageEnd: 0, segmentKind }), report)])), invalidBoundaryKinds: Object.fromEntries(invalidBoundaryKinds.map((segmentKind) => [segmentKind, m.prepareResolveMaskingReview(request(boundaryItem, { kind: 'boundary', pageStart: 0, pageEnd: 0, segmentKind }), report)])), incomplete: Object.fromEntries(items.map((item) => [item.kind, m.prepareResolveMaskingReview(request(item, { kind: item.kind }), report)])), mismatched: Object.fromEntries(items.map((item, index) => [item.kind, m.prepareResolveMaskingReview(request(item, resolutions[kinds[(index + 1) % kinds.length]]), report)])), malformedAcknowledge: m.prepareResolveMaskingReview(request(items[2], { kind: 'acknowledge', acknowledged: 'true' }), report), emptyRegionGeometry: m.prepareResolveMaskingReview(request(items[4], { kind: 'region_geometry', rects: [] }), report) };"
            "})()",
        )
        self.assertEqual({kind: True for kind in ("name", "institution", "acknowledge", "boundary", "region_geometry", "ocr")}, {kind: item["ok"] for kind, item in result["valid"].items()})
        self.assertEqual(
            {kind: item["value"]["resolution"] for kind, item in result["valid"].items()},
            {
                "name": {"kind": "name", "action": "mask"},
                "institution": {"kind": "institution", "action": "exclude"},
                "acknowledge": {"kind": "acknowledge", "acknowledged": True},
                "boundary": {"kind": "boundary", "pageStart": 0, "pageEnd": 0, "segmentKind": "attachment"},
                "region_geometry": {"kind": "region_geometry", "rects": [{"x0": 1, "y0": 2, "x1": 3, "y1": 4}]},
                "ocr": {"kind": "ocr", "accepted": True},
            },
        )
        self.assertTrue(all(item["ok"] for item in result["boundaryKinds"].values()))
        self.assertTrue(all(not item["ok"] for item in result["invalidBoundaryKinds"].values()))
        for cases in (result["incomplete"], result["mismatched"]):
            for kind, invalid in cases.items():
                with self.subTest(case=kind, rejected=cases is result["mismatched"]):
                    self.assertEqual(
                        {"ok": False, "errors": [{"code": "invalid_status", "field": "review_request.resolution"}]},
                        invalid,
                    )
        self.assertEqual(
            {"ok": False, "errors": [{"code": "invalid_status", "field": "review_request.resolution"}]},
            result["malformedAcknowledge"],
        )
        self.assertEqual(
            {"ok": False, "errors": [{"code": "invalid_status", "field": "review_request.resolution"}]},
            result["emptyRegionGeometry"],
        )

    def test_review_cancellation_targets_only_the_owned_draft_and_sends_the_complete_request(self):
        from test_frontend_state_helpers import canonical_review_manifest, run_node_helper

        manifest = json.dumps({
            **canonical_review_manifest(),
            "approvalCoverage": {
                "approval": "absent",
                "header_meta": "absent",
                "labeled_staff": "absent",
            },
            "requiredRegionCoverage": {
                "recipient_reference": "absent",
                "sender_institution": "absent",
                "approval_staff": "absent",
                "dispatch_metadata": "absent",
                "footer_contact": "absent",
            },
        })
        result = run_node_helper(
            "src/features/masking-run/maskingRunController.ts",
            "(async () => {"
            f"const manifest = {manifest};"
            "const report = { analysisManifest: manifest, reviewQueue: manifest.reviewItems, product_checks: {} };"
            "const request = { runId: manifest.runId, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, reviewId: 'review-1', resolution: { kind: 'boundary', pageStart: 0, pageEnd: 0, segmentKind: 'attachment' } };"
            "const run = async (owner, accepted) => { const state = { latestReport: report, latestReportPath: '/report.json', savingInFlight: false, publicRunIdentity: { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile }, geometryDraft: owner ? { owner, reviewId: owner, targetId: 'segment-1' } : null, boxes: [{ page: 0, tag: 'review-1', mode: 'mask' }, { page: 0, tag: 'review-2', mode: 'mask' }, { page: 0, tag: 'manual', mode: 'mask' }], documentEditRevision: 2, selectedCanvasBoxIndex: 2 }; const calls = [], cleanupCalls = []; const controller = m.createMaskingRunController({ state, resolveMaskingReview: async (value) => { calls.push(value); if (!accepted) throw new Error('rejected'); return { ...manifest, analysisRevision: 8, manifestHash: 'f'.repeat(64), segments: manifest.segments.map((item) => ({ ...item, segmentId: 'segment-2', analysisRevision: 8, kind: 'attachment', state: 'user_confirmed' })), reviewItems: manifest.reviewItems.map((item) => ({ ...item, reviewId: 'review-2', targetId: 'segment-2', analysisRevision: 8, status: 'resolved' })) }; }, cancelGeometryDraft: () => { cleanupCalls.push(state.geometryDraft?.owner ?? null); return true; }, renderFinalState: () => {}, renderDocumentReviewSurfaces: () => {}, updateWorkflowReadiness: () => {}, setStatus: () => {} }); const resolved = await controller.resolveReview(request); return { resolved, calls, cleanupCalls, tags: state.boxes.map((box) => box.tag), draft: state.geometryDraft?.owner ?? null, revision: state.documentEditRevision, selected: state.selectedCanvasBoxIndex }; }; return { ownedRejected: await run('review-1', false), adjacentRejected: await run('review-2', false), manualRejected: await run('manual', false), ownedAccepted: await run('review-1', true), adjacentAccepted: await run('review-2', true), manualAccepted: await run('manual', true) };"
            "})()",
        )
        expected_request = {
            "runId": "run-1",
            "analysisRevision": 7,
            "manifestHash": "b" * 64,
            "reviewId": "review-1",
            "resolution": {"kind": "boundary", "pageStart": 0, "pageEnd": 0, "segmentKind": "attachment"},
        }
        expected_rejected = {
            "ownedRejected": (False, ["review-1", "review-2", "manual"], "review-1", 2, 2, ["review-1"]),
            "adjacentRejected": (False, ["review-1", "review-2", "manual"], "review-2", 2, 2, []),
            "manualRejected": (False, ["review-1", "review-2", "manual"], "manual", 2, 2, []),
        }
        for name, (resolved, tags, draft, revision, selected, cleanup_calls) in expected_rejected.items():
            with self.subTest(outcome=name):
                case = result[name]
                self.assertEqual(resolved, case["resolved"])
                self.assertEqual([expected_request], case["calls"])
                self.assertEqual(tags, case["tags"])
                self.assertEqual(draft, case["draft"])
                self.assertEqual(revision, case["revision"])
                self.assertEqual(selected, case["selected"])
                self.assertEqual(cleanup_calls, case["cleanupCalls"])
        for name in ("ownedAccepted", "adjacentAccepted", "manualAccepted"):
            with self.subTest(outcome=name):
                case = result[name]
                self.assertTrue(case["resolved"])
                self.assertEqual([expected_request], case["calls"])
                self.assertEqual(2, case["revision"])
                self.assertEqual(2, case["selected"])
                self.assertEqual([], case["cleanupCalls"])
if __name__ == "__main__":
    unittest.main()
