from __future__ import annotations

import re
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


def frontend_script() -> str:
    paths = [
        *sorted((REPO_ROOT / "src" / "legacy").rglob("*.ts")),
        REPO_ROOT / "src" / "features" / "save-gate" / "saveGate.ts",
        REPO_ROOT / "src" / "features" / "masking-run" / "maskingRunController.ts",
        REPO_ROOT / "src" / "features" / "document-session" / "documentSessionController.ts",
        REPO_ROOT / "src" / "features" / "manual-adjustment" / "manualAdjustmentController.ts",
        REPO_ROOT / "src" / "features" / "finalization" / "finalizationController.ts",
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def attr_value(attrs: str, name: str) -> str:
    match = re.search(rf'{re.escape(name)}="([^"]+)"', attrs)
    return match.group(1) if match else ""


class FrontendButtonContractTests(unittest.TestCase):
    def test_static_button_ids_are_unique_across_mounted_components(self) -> None:
        markup = frontend_markup()
        ids = re.findall(r'id="([^"]+)"', markup)
        duplicates = sorted({button_id for button_id in ids if ids.count(button_id) > 1})

        self.assertEqual([], duplicates)

    def test_every_static_button_has_routing_or_click_handler(self) -> None:
        markup = frontend_markup()
        script = frontend_script()
        id_to_var = {
            match.group("id"): match.group("var")
            for match in re.finditer(r'const\s+(?P<var>\w+)\s*=\s*\$\("#(?P<id>[^"]+)"\)', script)
        }
        # v4 P3: MobileActionDock 삭제 → data-mobile-panel-target 라우팅은 폐지됐다
        # (해당 querySelectorAll 핸들러도 legacy source set에서 제거됨). 죽은 참조를
        # 남기지 않도록 이 목록에서도 뺀다.
        generic_attrs = (
            "data-screen-target",
            "data-settings-tab",
            "data-canvas-tool",
        )
        generic_handlers = {
            "data-screen-target": 'document.querySelectorAll<HTMLButtonElement>("[data-screen-target]")',
            "data-settings-tab": 'document.querySelectorAll<HTMLButtonElement>("[data-settings-tab]")',
            "data-canvas-tool": "canvasEditorToolButtons",
        }

        dead_buttons: list[str] = []
        for match in re.finditer(r"<button\b(?P<attrs>[^>]*)>", markup, re.S):
            attrs = match.group("attrs")
            button_id = attr_value(attrs, "id")
            routed_attr = next((name for name in generic_attrs if attr_value(attrs, name)), "")
            if routed_attr:
                self.assertIn(generic_handlers[routed_attr], script)
                continue
            if not button_id:
                dead_buttons.append(attrs.strip())
                continue
            var_name = id_to_var.get(button_id)
            if not var_name:
                continue
            if f'{var_name}.addEventListener("click"' not in script:
                dead_buttons.append(button_id)

        self.assertEqual([], dead_buttons)

    def test_disabled_action_buttons_have_visible_readiness_contracts(self) -> None:
        markup = frontend_markup()
        script = frontend_script()

        run_button = re.search(r'<button[^>]+id="btn-run-masking"[^>]+>', markup)
        self.assertIsNotNone(run_button)
        self.assertRegex(run_button.group(0), r"\bdisabled\b")

        for button_id, reason_text in [
            ("btn-save", "final-save-readiness"),
            ("btn-canvas-apply", "canvas-tool-readiness"),
            ("btn-canvas-final-save", "canvas-tool-readiness"),
        ]:
            button = re.search(rf'<(?:button|Button)[^>]+id="{button_id}"[^>]+>', markup)
            self.assertIsNotNone(button, button_id)
            self.assertIn(f'aria-describedby="{reason_text}"', button.group(0))

        self.assertIn("btnRunMasking.title = readiness.baseMaskingReason", script)
        self.assertIn("btnManualApply.title = readiness.manualApplyReason", script)
        self.assertIn("btnSave.title =", script)
        self.assertIn("finalSaveReadinessEl.textContent", script)
        self.assertIn("canvasToolReadinessEl.textContent", script)

    def test_pdf_only_still_generates_internal_safe_report_without_copying_it(self) -> None:
        # v4.1: 안전 리포트는 내부 검증 장치로만 존재한다. 사용자 대면 산출물 선택
        # (output-artifacts)이 삭제되며 프론트는 산출물을 내부 고정값으로 전달하고,
        # finalize 는 항상 copyReport:false 로 호출한다(리포트가 사용자 산출 폴더에
        # 절대 복사되지 않음). 엔진은 여전히 리포트 JSON 을 내부 임시폴더에 생성한다.
        script = frontend_script()

        # 기본값은 PDF+내부 리포트이고, 비식별 TXT를 명시적으로 켠 경우에만
        # 옵션 헬퍼가 masked_txt 산출물을 추가한다.
        self.assertIn(
            "output_artifacts: maskingOutputArtifacts(settingsExportMaskedTextEl.checked)",
            script,
        )
        # finalize 리포트 복사는 어떤 경우에도 꺼져 있다(하드코딩 false).
        self.assertIn("copyReport: false", script)
        # 삭제된 리포트 복사 결정 함수/산출물 셀렉터가 되살아나지 않았는지 회귀 가드.
        self.assertNotIn("shouldCopyReportArtifact", script)
        self.assertNotIn("outputArtifactsEl", script)
        self.assertNotIn("selectedOutputArtifacts", script)

    def test_final_save_checklist_controls_are_removed_from_documents_screen(self) -> None:
        # v4 P2: 저장 전 확인은 한 곳(최종 저장 모달)뿐이다. 통합 화면 마크업에
        # 옛 체크리스트/하단 도크가 되살아나지 않았는지 확인한다(중간 요약 반복 금지).
        markup = frontend_markup()

        removed_checklist_class = "final-save-" + "checklist"
        self.assertNotIn(f'className="checklist {removed_checklist_class}"', markup)
        self.assertNotIn('className="stage-bottom-dock"', markup)

    def test_frontend_final_save_is_advisory_not_hard_blocking(self) -> None:
        # v4.2.0 정책 전환: 검증 결과는 저장을 "차단"하지 않는다. 최종 저장은 항상
        # 사용자 재량이며, save-gate 모듈은 더 이상 "차단 사유"를 만들지 않고 저장
        # 직전 확인 1회에 띄울 "권고형 경고 목록(finalSaveWarnings)"을 산출한다.
        # 폐기된 하드 차단 술어(reportFinalSaveBlockingReason/reportBlocker)가 되살아
        # 나지 않았는지도 함께 지킨다.
        script = frontend_script()
        legacy_controller = (REPO_ROOT / "src" / "legacy" / "legacyAppController.ts").read_text(encoding="utf-8")

        # 권고형 경고 산출기와 그 배선(currentFinalSaveWarnings)이 살아 있어야 한다.
        self.assertIn("export function finalSaveWarnings(", script)
        self.assertIn("function currentFinalSaveWarnings()", script)
        self.assertIn("export function finalSaveWarningPresentation(", script)
        self.assertIn("const presentation = finalSaveWarningPresentation(", legacy_controller)
        self.assertNotIn("residual_hits", legacy_controller)
        self.assertNotIn("missing_targets_count", legacy_controller)
        # 폐기된 하드 차단 술어/배선은 완전히 사라져야 한다(권고형 전환 회귀 가드).
        self.assertNotIn("reportFinalSaveBlockingReason", script)
        self.assertNotIn("reportBlocker", script)

    def test_final_save_dialog_uses_advisory_ids_and_confirm_reentry(self) -> None:
        # v4.2.0 저장 흐름 회귀 가드:
        #  (a) 삭제된 저장/검토 관련 DOM ID 6종이 마크업·컨트롤러 어디에도 없어야 한다.
        #  (b) "저장할 수 없습니다" 등 강제 차단 문구가 사용자 대면 마크업에 없어야 한다.
        markup = frontend_markup()
        script = frontend_script()

        removed_ids = [
            "btn-acknowledge-review",
            "btn-dialog-acknowledge-review",
            "dialog-review-state",
            "btn-open-final-save-dialog",
            "btn-dialog-open-output-folder",
            "btn-save-artifact-pdf",
        ]
        for removed_id in removed_ids:
            self.assertNotIn(f'id="{removed_id}"', markup, removed_id)
            self.assertNotIn(f'#{removed_id}', script, removed_id)

        # (b) 강제 차단 문구가 사용자 대면 마크업에 없어야 한다(권고형 전환).
        for forced_phrase in ["저장할 수 없습니다", "저장이 차단", "저장 불가"]:
            self.assertNotIn(forced_phrase, markup, forced_phrase)

        # 권고형 표현·버튼 배치 계약.
        self.assertIn('id="final-save-warning-list"', markup)
        self.assertIn('id="btn-dialog-cancel-save"', markup)
        self.assertIn('title="저장 전 확인"', markup)
        self.assertIn("추가 확인이 필요한 항목이 있습니다", markup)
        self.assertIn("저장 준비 완료", markup)
        self.assertIn("취소하고 검토하기", markup)
        self.assertIn("무시하고 그대로 저장", markup)
        self.assertIn('className="dm-savewarn__summary"', markup)
        self.assertIn('id="btn-dialog-cancel-save" className="dm-btn dm-btn--ghost"', markup)
        self.assertIn('id="btn-dialog-save-all" className="dm-btn dm-btn--primary"', markup)
        final_save_dialog = markup[markup.index('id="final-save-dialog"') :]
        self.assertNotIn("dm-btn--danger", final_save_dialog)

        # (c) saveFinalOutput 의 warningsConfirmed 재진입 구조.
        self.assertIn("async function saveFinalOutput({ warningsConfirmed = false }", script)
        self.assertIn("if (!warningsConfirmed) {", script)
        self.assertIn("openFinalSaveDialog();", script)
        self.assertIn("void saveFinalOutput({ warningsConfirmed: true });", script)
        self.assertIn("if (state.savingInFlight) return;", script)
        self.assertIn("state.savingInFlight = true;", script)
        self.assertIn("state.savingInFlight = false;", script)
        self.assertIn("state.maskingRunning || state.batchRunning || state.savingInFlight", script)

    def test_single_pdf_default_output_and_progress_are_engine_backed(self) -> None:
        script = frontend_script()
        default_output_service = (REPO_ROOT / "src" / "services" / "tauri" / "defaultOutputDir.ts").read_text(encoding="utf-8")

        self.assertIn("defaultOutputDirForSelection", script)
        self.assertIn('"default_output_dir_for_document"', default_output_service)
        self.assertIn('setBaseMaskingProgress({ status: "running", percent: 0', script)
        self.assertIn('setBaseMaskingProgress({ status: "complete", percent: 100', script)
        self.assertIn('setBaseMaskingProgress({ status: "failed", percent: 0', script)
        self.assertNotIn('return { percent: 25, label: "기본 마스킹 대기" };', script)
        self.assertNotIn('return { percent: 70, label: "검토 필요" };', script)
        self.assertNotIn('return { percent: 85, label: "보정 반영 대기" };', script)
        self.assertNotIn('return { percent: 90, label: "저장 조건 확인" };', script)
        self.assertNotIn('return { percent: 95, label: "최종 저장 가능" };', script)


if __name__ == "__main__":
    unittest.main()
