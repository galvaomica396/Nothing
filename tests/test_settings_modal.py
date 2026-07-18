import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def frontend_markup():
    # v4 P2: 문서 관제(WorkRail/DocumentStage/ReviewInspector)가 통합 "문서"
    # 화면(CanvasWorkspace)으로 흡수됐다.
    component_paths = [
        REPO_ROOT / "src" / "components" / "AppHeader.tsx",
        REPO_ROOT / "src" / "components" / "StatusRibbon.tsx",
        REPO_ROOT / "src" / "components" / "CanvasWorkspace.tsx",
        REPO_ROOT / "src" / "components" / "MaskingSettingsScreen.tsx",
        REPO_ROOT / "src" / "components" / "SettingsScreen.tsx",
    ]
    component_sources = "\n".join(path.read_text(encoding="utf-8") for path in component_paths)
    return (REPO_ROOT / "index.html").read_text(encoding="utf-8") + "\n" + (REPO_ROOT / "src" / "App.tsx").read_text(encoding="utf-8") + "\n" + component_sources


def frontend_script():
    paths = [
        REPO_ROOT / "src" / "main.tsx",
        *sorted((REPO_ROOT / "src" / "legacy").rglob("*.ts")),
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


class SettingsModalTests(unittest.TestCase):
    def test_left_rail_settings_screen_replaces_duplicate_settings_modal(self):
        html = frontend_markup()
        script = frontend_script()

        self.assertNotIn('id="btn-settings"', html)
        self.assertNotIn('id="settings-modal"', html)
        self.assertIn('id="settings-screen"', html)
        settings_screen = html[html.index('id="settings-screen"') :]
        self.assertNotIn('role="dialog"', settings_screen)
        self.assertNotIn('aria-modal="true"', settings_screen)
        self.assertIn('data-screen-panel="settings"', html)
        # v4: 좌측 레일 rail-settings 폐지 → 상단 바 우측 기어로 설정 진입.
        self.assertNotIn('id="rail-settings"', html)
        self.assertIn('className="dm-header__gear"', html)
        self.assertIn('data-screen-target="settings"', html)
        self.assertIn('aria-label="설정"', html)
        self.assertIn("function activateAppScreen", script)
        self.assertIn('button.dataset.screenTarget === screenName', script)

    def test_output_folder_picker_is_removed_from_all_screens(self):
        html = frontend_markup()

        documents_screen = html[html.index('data-screen-panel="documents"') : html.index('id="masking-settings-screen"')]
        masking_settings_screen = html[html.index('id="masking-settings-screen"') : html.index('id="settings-screen"')]
        settings_screen = html[html.index('id="settings-screen"') :]

        for screen in [documents_screen, masking_settings_screen, settings_screen]:
            self.assertNotIn('id="btn-pick-outdir"', screen)
            self.assertNotIn('id="outdir-path"', screen)
            self.assertNotIn('id="btn-open-output-dir"', screen)
        self.assertNotIn('id="btn-canvas-output-dir"', documents_screen)
        self.assertNotIn(">미선택<", html)

    def test_theme_choices_include_light_dark_and_system_preferences(self):
        html = frontend_markup()

        self.assertIn('name="settings-theme"', html)
        for theme in ["light", "dark", "system"]:
            self.assertIn(f'value: "{theme}"', html)
        for label in ["라이트 모드", "다크 모드", "시스템 설정 따름"]:
            self.assertIn(label, html)
        for removed in ["white", "blue", "purple", "brown", "black"]:
            self.assertNotIn(f'value: "{removed}"', html)

    def test_general_settings_exclude_masking_and_workflow_controls(self):
        html = frontend_markup()
        settings_screen = html[html.index('id="settings-screen"') :]

        self.assertIn('data-settings-panel="general"', settings_screen)
        for excluded in [
            "btn-run-masking",
            "btn-run-batch",
            "btn-manual-apply",
            "btn-save",
            "review-queue",
            "batch-queue",
            "profile",
            "engine",
            "output-artifacts",
            "region-scope",
            "rule-grid",
            "btn-pick-outdir",
        ]:
            self.assertNotIn(f'id="{excluded}"', settings_screen)

    def test_masking_settings_owns_detection_output_and_safety_preferences(self):
        html = frontend_markup()
        masking_settings_screen = html[html.index('id="masking-settings-screen"') : html.index('id="settings-screen"')]
        settings_screen = html[html.index('id="settings-screen"') :]

        self.assertIn('id="rule-grid"', masking_settings_screen)
        self.assertIn('id="profile"', masking_settings_screen)
        # v4.1: 사용자 대면 산출물 선택(output-artifacts)이 삭제됐다(리포트 내부화).
        # 프론트가 산출물을 내부 고정으로 전달하므로 어떤 화면에도 존재하면 안 된다.
        self.assertNotIn('id="output-artifacts"', html)
        self.assertIn('id="opt-pdf-redaction"', masking_settings_screen)
        self.assertIn('id="settings-export-masked-text"', masking_settings_screen)
        self.assertIn('id="region-scope"', masking_settings_screen)
        self.assertIn('id="settings-open-output-after-save"', settings_screen)

    def test_settings_screen_behavior_hooks_are_present(self):
        script = frontend_script()

        self.assertIn("activateAppScreen", script)
        self.assertIn("settingsSnapshot = collectSettings()", script)
        self.assertIn("btnSettingsSave.addEventListener", script)
        self.assertIn("applySettings(saved)", script)
        self.assertIn("saveSettings(collectSettings())", script)
        self.assertNotIn("trapSettingsFocus", script)
        self.assertNotIn("openSettingsModal", script)
        self.assertNotIn("closeSettingsModal", script)

    def test_settings_screen_has_explicit_close_buttons(self):
        html = frontend_markup()
        script = frontend_script()

        settings_screen = html[html.index('id="settings-screen"') :]
        self.assertIn('id="btn-settings-close"', settings_screen)
        self.assertIn(">닫기</button>", settings_screen)
        self.assertIn('id="btn-app-settings-close"', settings_screen)
        self.assertIn("function closeSettingsScreen", script)
        self.assertIn('activateAppScreen("documents")', script)
        self.assertIn("btnSettingsClose.addEventListener", script)
        self.assertIn("btnSettingsFooterClose.addEventListener", script)


if __name__ == "__main__":
    unittest.main()
