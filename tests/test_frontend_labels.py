import json
import struct
import unittest
from pathlib import Path

import document_masker_ocr_gui as masker


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
    # v4 P2 (문서 통합): 문서 관제(DocumentsWorkspace/DocumentStage/ReviewInspector)와
    # 수동 보정(WorkRail) 컴포넌트가 삭제되고 CanvasWorkspace 하나로 통합됐다.
    # 삭제된 파일을 참조하면 FileNotFoundError가 나므로 현존 컴포넌트만 나열한다.
    component_paths = [
        REPO_ROOT / "src" / "components" / "layout" / "AppShell.tsx",
        REPO_ROOT / "src" / "components" / "ui" / "Button.tsx",
        REPO_ROOT / "src" / "components" / "ui" / "Modal.tsx",
        REPO_ROOT / "src" / "components" / "ui" / "Toast.tsx",
        REPO_ROOT / "src" / "components" / "AppHeader.tsx",
        REPO_ROOT / "src" / "components" / "StatusRibbon.tsx",
        REPO_ROOT / "src" / "components" / "CanvasWorkspace.tsx",
        REPO_ROOT / "src" / "components" / "MaskingSettingsScreen.tsx",
        REPO_ROOT / "src" / "components" / "SettingsScreen.tsx",
        # v4 P3: MobileActionDock 삭제(좁은 폭에서 스테이지+검토 레일 세로 스택).
    ]
    component_sources = "\n".join(path.read_text(encoding="utf-8") for path in component_paths)
    return (REPO_ROOT / "index.html").read_text(encoding="utf-8") + "\n" + (REPO_ROOT / "src" / "App.tsx").read_text(encoding="utf-8") + "\n" + component_sources


def legacy_typescript_source() -> str:
    legacy_root = REPO_ROOT / "src" / "legacy"
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(legacy_root.rglob("*.ts"))
    )


def frontend_script():
    # The batch-queue render/lifecycle helpers (renderBatchQueue,
    # batchActionButton, openBatchPath, retryBatchItem) now live in the
    # document-batch feature controller; the single-document masking
    # orchestration (runMaskingForSelectedDocument) lives in the masking-run
    # controller; and the canvas rendering/interaction helpers
    # (getEmptyCanvasWidth, renderCompare, …) live in the canvas-workbench
    # controller. The legacy bootstrap is split across controller, DOM binding,
    # and wiring modules, so aggregate the complete legacy TypeScript source set
    # before adding feature controllers for behavioral assertions.
    return (
        (REPO_ROOT / "src" / "main.tsx").read_text(encoding="utf-8")
        + "\n"
        + legacy_typescript_source()
        + "\n"
        + (REPO_ROOT / "src" / "features" / "document-batch" / "batchQueueController.ts").read_text(encoding="utf-8")
        + "\n"
        + (REPO_ROOT / "src" / "features" / "masking-run" / "maskingRunController.ts").read_text(encoding="utf-8")
        + "\n"
        + (REPO_ROOT / "src" / "features" / "canvas-workbench" / "canvasRenderController.ts").read_text(encoding="utf-8")
        + "\n"
        + (REPO_ROOT / "src" / "features" / "document-session" / "documentSessionController.ts").read_text(encoding="utf-8")
        + "\n"
        + (REPO_ROOT / "src" / "features" / "manual-adjustment" / "manualAdjustmentController.ts").read_text(encoding="utf-8")
        + "\n"
        + (REPO_ROOT / "src" / "features" / "finalization" / "finalizationController.ts").read_text(encoding="utf-8")
    )


class FrontendLabelsTests(unittest.TestCase):
    def test_index_uses_nothing_ui_brand_with_korean_user_facing_labels(self):
        html = frontend_markup()

        self.assertIn("<title>Nothing</title>", html)
        self.assertIn("Nothing", html)
        # 좌표 템플릿 퇴역 후 상단 바는 문서 홈과 설정 진입점만 제공한다.
        self.assertIn('className="dm-header__home"', html)
        self.assertIn('className="dm-header__gear"', html)
        self.assertNotIn('data-screen-target="coordinate-template"', html)
        self.assertNotIn('className="rail-brand-card"', html)
        self.assertNotIn('className="workspace-command-strip"', html)
        self.assertIn("현재 문서", html)
        self.assertIn("문서를 선택하세요", html)
        self.assertIn("PDF 열기", html)
        self.assertIn("추출 결과", html)
        self.assertIn("마스킹 결과", html)
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
        header = (REPO_ROOT / "src" / "components" / "AppHeader.tsx").read_text(encoding="utf-8")

        self.assertIn('src="/favicon.png"', header)
        self.assertIn('className="dm-header__logo"', header)
        self.assertIn('alt=""', header)
        self.assertIn('aria-hidden="true"', header)
        self.assertNotIn('<SymbolIcon name="policy"', header)
        self.assertIn('data-screen-target="documents"', header)
        self.assertIn('aria-label="문서 홈"', header)

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

    def test_custom_region_input_is_explicitly_controlled_by_region_scope(self):
        html = frontend_markup()
        script = frontend_script()

        self.assertIn('option value="custom">사용자 지정 지역</option>', html)
        self.assertIn('id="deidentification-policy"', html)
        self.assertIn('option value="partial">부분 마스킹 · 일부 형식 유지</option>', html)
        self.assertIn('option value="pseudonym">일관 가명 · 같은 값은 같은 가명</option>', html)
        self.assertIn('id="custom-regions"', html)
        self.assertIn("사용자 지정 지역을 선택했으면 지역명을 입력하세요.", script)
        self.assertIn("function updateRegionScopeControls()", script)
        self.assertIn('regionScopeEl.value === "custom"', script)
        self.assertIn("custom_regions: isCustomRegionScope() ? customRegionsEl.value.trim() : \"\"", script)

    def test_python_gui_option_labels_are_korean_display_values(self):
        self.assertEqual(["공공문서", "법률문서"], list(masker.PROFILE_DISPLAY_TO_VALUE))
        self.assertIn("자동 선택", masker.ENGINE_DISPLAY_TO_VALUE)
        self.assertIn("사용자 지정 지역", masker.REGION_SCOPE_DISPLAY_TO_VALUE)
        self.assertIn("PDF만 저장", masker.OUTPUT_ARTIFACT_LABELS)
        self.assertNotIn("official", masker.PROFILE_DISPLAY_TO_VALUE)
        self.assertNotIn("pdf_only", masker.OUTPUT_ARTIFACT_LABELS)

        self.assertEqual("legal", masker._profile_value("법률문서"))
        self.assertEqual("pypdf", masker._engine_value("간단 텍스트 추출"))
        self.assertEqual("custom", masker._region_scope_value("사용자 지정 지역"))
        self.assertEqual("pdf_only", masker._output_artifact_value("PDF만 저장"))
        self.assertEqual({"pdf"}, masker.resolve_output_artifacts({"output_artifacts": "PDF만 저장"}))

    def test_frontend_deidentification_policy_is_wired_to_backend_options(self):
        html = frontend_markup()
        script = frontend_script()
        rust = (REPO_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

        self.assertIn('id="deidentification-policy"', html)
        self.assertIn("TXT 비식별 변환", html)
        self.assertIn("비식별 TXT 함께 저장", html)
        self.assertIn("PDF 가림 모양", html)
        self.assertIn("TXT 저장 시에만 적용하며 PDF 가림 모양과 독립적으로 선택합니다.", html)
        for card_id in [
            "btn-display-mode-black",
            "btn-display-mode-label-en",
            "btn-display-mode-label-ko",
            "btn-display-mode-pseudonym",
            "btn-policy-token",
            "btn-policy-partial",
            "btn-policy-pseudonym",
        ]:
            self.assertIn(f'id="{card_id}"', html)
        for preview in [
            "[NAME]",
            "[PHONE]",
            "홍길동",
            "홍OO",
            "010-****-5678",
            "hong@test.com",
            "h***@test.com",
            "박지훈",
            "010-0000-1199",
            "[전화번호]",
        ]:
            self.assertIn(preview, html)
        self.assertIn('aria-pressed="true"', html)
        self.assertIn("function syncSettingCards(", html)
        self.assertIn("deidentification_policy: deidentificationPolicyEl.value", script)
        self.assertIn("deidentification_policy: String", rust)

    def test_final_save_summary_marks_deidentification_as_text_only(self):
        flow = (REPO_ROOT / "src" / "workflowFlow.ts").read_text(encoding="utf-8")

        self.assertIn("비식별 TXT: 완전 치환(유형 토큰)", flow)
        self.assertIn("비식별 TXT: 부분 마스킹", flow)
        self.assertIn("비식별 TXT: 일관 가명", flow)

    def test_masking_settings_controls_feed_runtime_options(self):
        html = frontend_markup()
        script = frontend_script()

        # 원문 TXT 선택지는 없고, 비식별 TXT 추가 저장 여부만 명시적으로 제공한다.
        for element_id in [
            "profile",
            "engine",
            "display-mode",
            "deidentification-policy",
            "region-scope",
            "custom-regions",
            "opt-pdf-redaction",
            "settings-export-masked-text",
        ]:
            self.assertIn(f'id="{element_id}"', html)
        self.assertNotIn('id="output-artifacts"', html)
        for option_line in [
            "extract_engine: engineEl.value",
            "profile: profileEl.value",
            "output_artifacts: maskingOutputArtifacts(settingsExportMaskedTextEl.checked)",
            "display_mode: displayModeEl.value",
            "deidentification_policy: deidentificationPolicyEl.value",
            "region_scope: regionScopeEl.value",
            "custom_regions: isCustomRegionScope() ? customRegionsEl.value.trim() : \"\"",
            "pdf_redaction: ($(\"#opt-pdf-redaction\") as HTMLInputElement).checked",
            "return_text_preview: false",
        ]:
            self.assertIn(option_line, script)

    def test_review_queue_workspace_is_removed_from_live_frontend(self):
        html = frontend_markup()
        script = frontend_script()
        self.assertFalse((REPO_ROOT / "src" / "reviewQueue.ts").exists())
        self.assertNotIn('id="review-screen"', html)
        self.assertNotIn('id="review-queue"', html)
        self.assertNotIn('data-screen-target="review"', html)
        self.assertNotIn("createReviewQueueRows", script)
        self.assertNotIn("activeReviewQueueIndex", script)
        self.assertNotIn("queue-jump", script)
        self.assertNotIn("queue-actions", script)

    def test_manual_no_effect_status_is_visible_in_frontend_contract(self):
        script = frontend_script()
        rust = (REPO_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        manual_revalidation_rust = (
            REPO_ROOT / "src-tauri" / "src" / "manual_revalidation.rs"
        ).read_text(encoding="utf-8")

        self.assertIn("status?: string;", script)
        self.assertIn('result.status === "no_effect"', script)
        self.assertIn("변경 없음", script)
        self.assertIn("status: Option<String>", rust + manual_revalidation_rust)

    def test_manual_apply_passes_display_mode_to_tauri_and_helper(self):
        script = frontend_script()
        rust = (REPO_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

        self.assertIn("displayMode: displayModeEl.value", script)
        self.assertIn("display_mode: String", rust)
        self.assertIn('.arg("--display-mode")', rust)

    def test_pdf_only_document_flow_removes_hwpx_surface(self):
        script = frontend_script()
        rust = (REPO_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

        self.assertIn("documentProvenance.original.kind", script)
        self.assertIn("pick_input_document", script)
        self.assertNotIn("finalize_document_output", script)
        self.assertNotIn("HWPX는 수동 박스 보정 대상이 아닙니다.", script)
        self.assertNotIn("hwpx", script.lower())
        self.assertIn('add_filter("PDF", &["pdf"])', rust)
        self.assertNotIn('add_filter("HWPX"', rust)
        self.assertNotIn('"지원 문서", &["pdf", "hwpx"]', rust)
        # 호환 커맨드는 유지하고 네이티브 저장 경로용 신규 커맨드를 별도로 등록한다.
        self.assertNotIn("fn finalize_document_output", rust)
        self.assertIn("fn finalize_manual_output(", rust)
        self.assertIn("fn choose_final_pdf_path(", rust)
        self.assertIn("fn finalize_manual_output_to_selected_path(", rust)

    def test_batch_document_queue_and_canvas_workbench_are_exposed(self):
        html = frontend_markup()
        script = frontend_script()
        rust = (REPO_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

        self.assertIn("여러 PDF", html)
        self.assertIn("여러 문서 일괄 처리", html)
        self.assertIn('id="batch-queue"', html)
        self.assertIn("문서 마스킹 작업", html)
        header = (REPO_ROOT / "src" / "components" / "AppHeader.tsx").read_text(encoding="utf-8")
        self.assertNotIn('id="btn-run-batch"', header)
        self.assertIn('id="btn-run-batch"', html)
        self.assertIn("대기 N개 모두 마스킹", html)
        self.assertIn("pick_input_documents", script)
        self.assertIn("runMaskingForSelectedDocument", script)
        self.assertIn("renderBatchQueue", script)
        self.assertIn("batchActionButton", script)
        self.assertIn("openBatchPath", script)
        self.assertIn("retryBatchItem", script)
        self.assertIn("불러오기", script)
        self.assertIn("재실행", script)
        self.assertIn("결과 열기", script)
        # v4.1: 리포트 내부화로 배치 행의 "리포트 열기" 액션이 삭제됐다(사용자
        # 산출 폴더에 리포트 파일이 없으므로 열 대상이 없다). 회귀 가드로 부재 단언.
        self.assertNotIn("리포트 열기", script)
        self.assertIn("setCanvasMode", script)
        self.assertIn("openCanvasDesktopWindow", script)
        self.assertIn('btnMaskCanvas.addEventListener("click", () => {', script)
        self.assertIn("void openCanvasDesktopWindow();", script)
        self.assertIn("function getEmptyCanvasWidth()", script)
        self.assertIn("Math.min(700, Math.max(320, Math.floor(window.innerWidth - 18)))", script)
        self.assertNotIn("HWPX는 캔버스 직접 보정 대상이 아닙니다.", script)
        self.assertIn("fn pick_input_documents", rust)
        self.assertIn("pick_files()", rust)

    def test_mobile_action_dock_is_removed_and_review_rail_reachable(self):
        # v4 P3 (§0 진짜 삭제): 통합 문서 화면에서 work/stage/review 패널 개념이
        # 하나로 재편되며 모바일 독은 완전 삭제됐다. 좁은 폭에서는 스테이지+검토
        # 레일이 세로로 스택되어 독 없이 스크롤로 레일에 접근한다. 독 마크업·전환
        # 타깃·컨트롤러 코드가 잔재 없이 사라졌는지 검증한다.
        html = frontend_markup()
        script = frontend_script()

        self.assertNotIn("MobileActionDock", html)
        self.assertNotIn('className="mobile-action-dock"', html)
        self.assertNotIn('id="mobile-panel-work"', html)
        self.assertNotIn('id="mobile-panel-stage"', html)
        self.assertNotIn("data-mobile-panel-target", html)
        self.assertNotIn("data-mobile-panel", html)
        self.assertNotIn('data-screen-target="review"', html)
        # 컨트롤러의 모바일 패널 스위처도 함께 삭제됐다.
        self.assertNotIn("function activateMobilePanel", script)
        self.assertNotIn("data-mobile-panel-target", script)
        # 검토·저장 레일 자체는 통합 문서 화면에 그대로 살아 있다(스크롤로 접근).
        self.assertIn('id="side-panel"', html)
        self.assertIn("검토·저장", html)

    def test_canvas_window_can_open_as_desktop_window_and_load_pdf(self):
        html = frontend_markup()
        script = frontend_script()
        rust = (REPO_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        capability = (REPO_ROOT / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8")

        self.assertIn("PDF 작업창 열기", html)
        self.assertNotIn("브라우저 미리보기용 임시 캔버스", html)
        self.assertIn("PDF 열기", html)
        self.assertIn("btn-canvas-load-pdf", html)
        self.assertIn("canvasStatusText", script)
        self.assertIn("대상", script)
        self.assertIn("현재 도구", script)
        self.assertIn("박스", script)
        self.assertIn("독립 창", script)
        self.assertNotIn("sessionStorage", script)
        self.assertIn("create_canvas_launch_token", script)
        self.assertIn("take_canvas_launch_payload", script)
        self.assertIn("hydrateStandaloneCanvasWindow", script)
        self.assertIn("createCanvasWindowLaunchToken", script)
        self.assertIn("open_mask_canvas_window", script)
        self.assertIn("targetPath", script)
        self.assertIn("pickCanvasPdf", script)
        # v4 P2(문서 통합): showCanvasWorkspace는 폐지되고 독립 캔버스 창 부팅이
        # hydrateStandaloneCanvasWindow(setCanvasMode 경유)로 일원화됐다. 같은 의도
        # (독립 창으로 열려 PDF를 적재)를 현존 함수로 재조준한다.
        self.assertIn("async function hydrateStandaloneCanvasWindow()", script)
        self.assertIn("standalone: deps.isStandaloneCanvasWindow", script)
        self.assertIn("setStatus(readiness.reason)", script)
        self.assertIn('get("mode") === "canvas"', script)
        self.assertIn("fn create_canvas_launch_token", rust)
        self.assertIn("fn take_canvas_launch_payload", rust)
        self.assertIn("fn open_mask_canvas_window", rust)
        self.assertIn("target_path: Option<String>", rust)
        self.assertIn("mask_canvas", rust)
        self.assertIn('"mask_canvas"', capability)

    def test_canvas_editor_tools_are_exposed_without_production_fixture_path(self):
        html = frontend_markup()
        script = frontend_script()

        self.assertNotIn("QA 샘플 PDF", html)
        self.assertNotIn('id="btn-canvas-fixture-pdf"', html)
        self.assertNotIn('id="btn-canvas-output-dir"', html)
        self.assertIn('id="btn-canvas-zoom-out"', html)
        self.assertIn('id="btn-canvas-zoom-in"', html)
        self.assertIn('id="btn-canvas-delete-box"', html)
        self.assertIn('id="canvas-box-list"', html)
        self.assertIn("현재 페이지 박스", html)
        self.assertIn("선택 삭제", html)
        self.assertNotIn("출력 폴더", html)
        self.assertIn("createCanvasBoxRows", script)
        self.assertIn("renderCanvasBoxList", script)
        self.assertNotIn("loadPhase6FixturePdf", script)
        self.assertNotIn("pickOutputDir", script)
        self.assertNotIn("load_phase6_fixture_pdf", script)
        # v4 P4: 캔버스 편집 도구의 브라우저 무결성은 게이트 qa_canvas_interactions.mjs
        # 가 실제 상호작용으로 검증한다(구 ui_risk_check.mjs 는 v3 레일/스테이지
        # 구조를 조회해 폐지·삭제됨).

    def test_remaining_ux_contract_exposes_editor_workflow_and_save_confirmation(self):
        html = frontend_markup()
        script = frontend_script()

        # v4 P2(문서 통합): 워크플로 스테퍼(dm-stepper/workflow-step-*/workflow-progress-*)와
        # 메인 빈 드롭존(main-empty-dropzone)은 통합 문서 화면에서 폐지됐다. 삭제된
        # 표면이 되살아나지 않도록 부재를 단언한다.
        self.assertNotIn('className="dm-stepper dm-card"', html)
        self.assertNotIn('id="workflow-step-add-document"', html)
        self.assertNotIn('id="workflow-step-final-review"', html)
        self.assertNotIn('id="main-empty-dropzone"', html)
        self.assertNotIn('className="dm-stepper__progress"', html)
        self.assertNotIn('id="workflow-progress-percent"', html)
        self.assertNotIn('id="workflow-progress-fill"', html)
        # 4단계 마스킹 흐름 중 통합 화면에 남은 라벨(기본 마스킹/최종 저장)은 유지된다.
        self.assertIn("기본 마스킹", html)
        self.assertIn("최종 저장", html)
        # 저장 전 확인 모달은 권고 경고만 담당한다. 저장 위치와 파일명은 계속 진행한 뒤
        # OS 네이티브 저장 창에서 선택한다.
        self.assertIn('id="final-save-dialog"', html)
        self.assertNotIn("안전 리포트", html)
        self.assertIn("저장 전 확인 (권고사항)", html)
        self.assertIn('id="final-save-warning-list"', html)
        self.assertIn('id="btn-dialog-cancel-save"', html)
        self.assertIn("취소하고 검토하기", html)
        self.assertIn("무시하고 그대로 저장", html)
        self.assertIn("저장 위치와 파일명", html)
        self.assertNotIn("출력 폴더", html)
        # 권고형 전환: 강제 차단 문구가 사용자 대면 모달에 남으면 안 된다.
        self.assertNotIn("저장할 수 없습니다", html)
        # renderMainWorkflowState/renderBaseMaskingProgress(DOM 렌더)는 제거됐고,
        # 상태 소스인 setBaseMaskingProgress만 남아 maskingRunController가 호출한다.
        self.assertNotIn("function renderMainWorkflowState()", script)
        self.assertNotIn("function renderBaseMaskingProgress()", script)
        self.assertIn("function setBaseMaskingProgress(", script)
        self.assertNotIn("workflowProgressForPhase", script)
        self.assertIn("function renderFinalSaveConfirmation()", script)
        self.assertIn("reviewSummaryMaskCountEl.textContent", script)
        self.assertIn("renderFinalSaveDialogSummary()", script)

    def test_canvas_properties_allow_type_switch_and_toolbar_uses_tool_buttons(self):
        html = frontend_markup()
        script = frontend_script()

        self.assertIn('className="tool-button is-active"', html)
        self.assertIn('id="btn-canvas-box-convert-mask"', html)
        self.assertIn('id="btn-canvas-box-convert-restore"', html)
        self.assertIn("마스킹으로 전환", html)
        self.assertIn("복원으로 전환", html)
        self.assertIn("convertCanvasSelectedBox", script)
        self.assertIn("btnCanvasBoxConvertMask", script)
        self.assertIn("btnCanvasBoxConvertRestore", script)

    def test_masking_settings_separates_app_defaults_from_current_document_values(self):
        html = frontend_markup()
        script = frontend_script()
        masking_settings_screen = html[html.index('id="masking-settings-screen"') : html.index('id="settings-screen"')]

        self.assertIn("앱 기본값", masking_settings_screen)
        self.assertIn("현재 문서 작업값", masking_settings_screen)
        self.assertIn('id="settings-apply-scope-status"', masking_settings_screen)
        self.assertIn("저장하면 현재 작업에도 적용됩니다", masking_settings_screen)
        self.assertNotIn('id="output-path-preview"', masking_settings_screen)
        self.assertNotIn("예상 저장 경로", masking_settings_screen)
        self.assertIn("function renderSettingsScopeStatus()", script)
        self.assertNotIn("function renderOutputPathPreview()", script)

    def test_canvas_workflow_gates_manual_apply_and_final_save_controls(self):
        html = frontend_markup()
        script = frontend_script()

        canvas_screen = html[html.index('id="canvas-workspace-screen"') :]
        self.assertIn('id="canvas-tool-readiness"', canvas_screen)
        self.assertIn('id="btn-canvas-apply"', canvas_screen)
        self.assertIn('id="btn-canvas-final-save"', canvas_screen)
        self.assertIn('aria-describedby="canvas-tool-readiness"', canvas_screen)
        self.assertIn("documentWorkflowReadiness", script)
        self.assertIn("updateWorkflowReadiness", script)
        self.assertIn("btnCanvasApply.disabled", script)
        self.assertIn("btnCanvasFinalSave.disabled", script)

    def test_final_save_opens_native_save_dialog_before_exact_path_finalize(self):
        script = frontend_script()
        controller = script[
            script.index("async function saveFinalOutput")
            : script.index("return {", script.index("async function saveFinalOutput"))
        ]

        self.assertIn('"choose_final_pdf_path"', controller)
        self.assertIn("defaultFileName: finalSaveDefaultFileName", controller)
        self.assertIn('"finalize_manual_output_to_selected_path"', controller)
        self.assertLess(
            controller.index('"choose_final_pdf_path"'),
            controller.index('"finalize_manual_output_to_selected_path"'),
        )
        self.assertIn("if (!saveTarget) return;", controller)
        self.assertIn("outputPath: saveTarget.outputPath", controller)
        self.assertIn("saveToken: saveTarget.saveToken", controller)

    def test_original_pdf_load_does_not_count_as_base_masking_preview(self):
        script = frontend_script()
        load_original = script[script.index("async function loadOriginalPdf") : script.index("async function loadCanvasWorkspacePdf")]

        self.assertIn("selectOriginalDocument", load_original)
        self.assertIn('resetDerivedArtifacts("new-document");', load_original)
        self.assertLess(load_original.index("selectOriginalDocument"), load_original.index('resetDerivedArtifacts("new-document");'))
        self.assertNotIn("adoptGeneratedPreview", load_original)

    def test_canvas_workspace_has_complete_correction_and_final_save_tools(self):
        html = frontend_markup()
        script = frontend_script()

        canvas_screen = html[html.index('id="canvas-workspace-screen"') :]
        # v4 P2(문서 통합): 캔버스 화면이 통합 "문서" 화면이 되며 패널 키가
        # canvas → documents로 바뀌었다.
        self.assertIn('data-screen-panel="documents"', canvas_screen)
        self.assertIn("마스킹 미리보기", canvas_screen)
        self.assertIn("원문 대조", canvas_screen)
        self.assertIn('id="toggle-original-compare"', canvas_screen)
        self.assertIn('id="custom-keywords"', canvas_screen)
        self.assertIn('<textarea id="custom-keywords"', canvas_screen)
        self.assertNotIn('id="btn-run-keywords"', canvas_screen)
        self.assertIn('id="btn-open-keyword-dialog"', canvas_screen)
        self.assertIn("키워드 관리", canvas_screen)
        self.assertNotIn('id="btn-canvas-mask"', canvas_screen)
        self.assertNotIn('id="btn-canvas-restore"', canvas_screen)
        self.assertIn("마스킹 박스", canvas_screen)
        self.assertIn("복원 박스", canvas_screen)
        self.assertIn('id="btn-canvas-apply"', canvas_screen)
        self.assertIn('id="btn-canvas-final-save"', canvas_screen)
        self.assertNotIn('id="btn-canvas-output-dir"', canvas_screen)
        self.assertIn("btnCanvasFinalSave.addEventListener", script)
        self.assertIn("btnCanvasFinalSave", script)
        self.assertIn("btnCanvasFinalSave.disabled", script)
        self.assertNotIn("btnRunKeywords.addEventListener", script)

    def test_new_work_reset_keyword_consolidation_and_fixture_removal_contract(self):
        html = frontend_markup()
        script = frontend_script()
        rust = (REPO_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

        self.assertIn('id="btn-new-document"', html)
        self.assertIn("새 작업 시작", html)
        self.assertIn('id="new-document-dialog"', html)
        self.assertIn("진행 중인 작업이 있습니다", html)
        self.assertIn("저장하지 않고 새 작업을 시작하시겠습니까? 기존 작업 내역은 사라집니다.", html)
        self.assertIn("resetDocumentSession", script)
        self.assertIn("btnPickBatch.disabled = busy", script)
        self.assertNotIn('id="btn-canvas-fixture-pdf"', html)
        self.assertNotIn("load_phase6_fixture_pdf", script)
        self.assertNotIn("load_phase6_fixture_pdf", rust)
        self.assertNotIn("미리보기만 반영", html)
        self.assertNotIn(">보기<", html)
        self.assertNotIn(">검토<", html)
        self.assertIn("키워드 적용 후 다시 마스킹", html)
        self.assertIn('data-screen-target="masking-settings"', html)

    def test_canvas_workspace_exposes_editor_palette_and_active_tool_state(self):
        html = frontend_markup()
        script = frontend_script()

        canvas_screen = html[html.index('id="canvas-workspace-screen"') :]
        self.assertIn("canvas-editor-palette", canvas_screen)
        self.assertIn('id="canvas-active-tool-label"', canvas_screen)
        self.assertIn('id="canvas-tool-readiness"', canvas_screen)
        self.assertIn('id="btn-canvas-tool-select"', canvas_screen)
        self.assertIn('id="btn-canvas-tool-mask"', canvas_screen)
        self.assertIn('id="btn-canvas-tool-restore"', canvas_screen)
        self.assertIn('id="btn-canvas-tool-pan"', canvas_screen)
        self.assertIn('id="btn-canvas-tool-delete"', canvas_screen)
        self.assertIn('data-canvas-tool="mask"', canvas_screen)
        self.assertIn('aria-pressed="true"', canvas_screen)
        self.assertIn('aria-label="보정 도구"', canvas_screen)
        self.assertIn('aria-label="보기"', canvas_screen)
        self.assertIn('aria-label="반영·저장"', canvas_screen)
        self.assertIn('className="dm-canvas__flow"', canvas_screen)
        self.assertIn("PDF 열기", canvas_screen)
        self.assertIn("자동 마스킹", canvas_screen)
        self.assertIn("수동 보정 및 저장", canvas_screen)
        self.assertIn("syncCanvasToolPalette", script)
        self.assertIn("canvasToolReadinessText", script)
        self.assertIn("needsEditablePdf && (editsLocked() || (!canEdit && !deps.isStandaloneCanvasWindow))", script)
        self.assertIn("state.savingInFlight", script)

    def test_canvas_workspace_has_box_property_panel_for_selection(self):
        html = frontend_markup()
        script = frontend_script()

        canvas_screen = html[html.index('id="canvas-workspace-screen"') :]
        self.assertIn('id="canvas-box-properties"', canvas_screen)
        self.assertIn('id="canvas-box-property-page"', canvas_screen)
        self.assertIn('id="canvas-box-property-type"', canvas_screen)
        self.assertIn('id="canvas-box-property-coordinates"', canvas_screen)
        self.assertIn('id="canvas-box-property-size"', canvas_screen)
        # v4 P2(문서 통합): 속성 패널이 우측 검토 레일의 "현재 페이지 박스" 카드로 이식됨.
        self.assertIn("현재 페이지 박스", canvas_screen)
        self.assertIn("renderCanvasBoxProperties", script)


if __name__ == "__main__":
    unittest.main()
