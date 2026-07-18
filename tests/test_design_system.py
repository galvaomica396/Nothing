import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

PUBLIC_REQUIRED_DOCS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "DESIGN.md",
    REPO_ROOT / "PRODUCT.md",
    REPO_ROOT / "docs" / "ARCHITECTURE.md",
    REPO_ROOT / "docs" / "DEIDENTIFICATION_POLICY.md",
    REPO_ROOT / "docs" / "MACOS_INSTALL.md",
    REPO_ROOT / "docs" / "RUNTIME_CONTRACT.md",
    REPO_ROOT / "docs" / "SECURITY_AND_PRIVACY.md",
    REPO_ROOT / "docs" / "WINDOWS_RELEASE_TEST.md",
)


def read_optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def public_doc_corpus() -> str:
    return "\n".join(read_optional_text(path) for path in PUBLIC_REQUIRED_DOCS)


def frontend_markup():
    # v4 P2 (문서 통합): 문서 관제(DocumentsWorkspace/DocumentStage/WorkRail/
    # ReviewInspector)와 수동 보정(캔버스) 화면이 하나의 "문서" 화면
    # (CanvasWorkspace, data-screen-panel="documents")으로 병합됐다. 삭제된
    # DocumentsWorkspace/WorkRail/DocumentStage/ReviewInspector 는 마크업 코퍼스에서
    # 제거하고, 이식된 검토·저장 레일/최종 저장 게이트는 CanvasWorkspace 로 읽는다.
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
    return (
        (REPO_ROOT / "src" / "main.tsx").read_text(encoding="utf-8")
        + "\n"
        + legacy_typescript_source()
    )


def refinement_css_files():
    return sorted(path.relative_to(REPO_ROOT).as_posix() for path in (REPO_ROOT / "src").glob("*Refinement.css"))


# The redesign (REDESIGN_SPEC_V2_2 "Trust Desk") splits the single styles.css
# into focused files under src/styles/. Design-system tests read these instead.
# v4 P2 (문서 통합): screen-documents.css 는 삭제됐다. 여전히 필요한 규칙(검토·저장
# 레일 + 최종 저장 게이트)은 #canvas-workspace-screen 스코프로 screen-canvas.css 에
# 이관됐고, main.tsx 도 더 이상 screen-documents.css 를 import 하지 않는다.
NEW_STYLE_FILES = (
    "variables.css",
    "base.css",
    "components.css",
    "shell.css",
    "screen-canvas.css",
    "screen-settings.css",
    "themes.css",
)


def style_css(name: str) -> str:
    return (REPO_ROOT / "src" / "styles" / name).read_text(encoding="utf-8")


def all_style_css() -> str:
    return "\n".join(style_css(name) for name in NEW_STYLE_FILES)


class DesignSystemTests(unittest.TestCase):
    def test_public_docs_include_runtime_and_safety_guides(self):
        for path in PUBLIC_REQUIRED_DOCS:
            self.assertTrue(
                path.exists(),
                f"Public product documentation is missing: {path.relative_to(REPO_ROOT)}",
            )

    def test_public_docs_describe_normal_workflow(self):
        docs = public_doc_corpus()

        self.assertIn("PDF 추가 -> 기본 마스킹 -> 검토/보정 -> 저장", docs)

    def test_public_docs_do_not_overclaim_privacy_safety(self):
        docs = public_doc_corpus()

        self.assertNotIn("개인정보 전체 검증 완료", docs)
        self.assertNotIn("완전 안전", docs)
        self.assertNotIn("모든 개인정보 제거 완료", docs)

    def test_design_md_locks_document_security_studio_direction(self):
        design = (REPO_ROOT / "DESIGN.md").read_text(encoding="utf-8")

        # DESIGN.md itself is the v4.3 authority. The old two-screen spec is
        # historical because coordinate templates were retired.
        self.assertIn("One Document Flow, One Accent", design)
        self.assertIn("single current authority", design)
        self.assertIn("exclude the coordinate-template product flow", design)
        self.assertNotIn("Those specs are the single authority", design)
        self.assertIn("single blue action accent", design)
        self.assertIn("--dm-accent: #2f81f7", design)
        self.assertIn("--dm-inspector-w: 320px", design)
        # The v4 shell has no left rail width token.
        self.assertNotIn("--dm-rail-w: 224px", design)
        self.assertIn("shell.css", design)
        self.assertIn("screen-canvas.css", design)
        # screen-documents.css was deleted when 문서 관제 merged into the canvas;
        # DESIGN.md must document its removal, not list it as a live style file.
        self.assertIn("There is no `screen-documents.css`", design)
        self.assertIn("data-theme", design)
        self.assertIn("`light` / `dark` / `system`", design)
        self.assertIn("data-screen-target", design)
        self.assertIn("btn-run-masking", design)
        self.assertIn("docs/RUNTIME_CONTRACT.md", design)
        self.assertIn("Do not reintroduce the removed review queue screen", design)
        self.assertIn("The accent must appear in at most one or two places on a screen.", design)
        self.assertIn("박스 크기, 버튼 높이, 경계선 두께가 흔들리면 안 된다", design)
        self.assertIn("React 19 + TypeScript components", design)
        self.assertIn("plain-CSS token system", design)
        self.assertIn("Tailwind utilities are not used in JSX", design)
        self.assertIn("immutable exported snapshot", design)
        self.assertIn("sole continuation baseline", design)
        self.assertIn("must never silently fall back", design)
        self.assertIn("editing and re-saving stay disabled", design)
        # The retired Obsidian identity must not be reasserted as the default.
        self.assertNotIn("dark Korean administrative workbench", design)
        self.assertNotIn("Primary violet: `#8b5cf6`", design)

    def test_frontend_stack_is_react_typescript_tailwind(self):
        package_json = (REPO_ROOT / "package.json").read_text(encoding="utf-8")
        vite_config = (REPO_ROOT / "vite.config.ts").read_text(encoding="utf-8")
        tsconfig = (REPO_ROOT / "tsconfig.json").read_text(encoding="utf-8")
        index = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
        entry = (REPO_ROOT / "src" / "main.tsx").read_text(encoding="utf-8")
        app = (REPO_ROOT / "src" / "App.tsx").read_text(encoding="utf-8")
        legacy_entrypoint = (REPO_ROOT / "src" / "legacy" / "startLegacyApp.ts").read_text(encoding="utf-8")

        self.assertIn('"react": "^19.', package_json)
        self.assertIn('"react-dom": "^19.', package_json)
        self.assertIn('"tailwindcss": "^4.', package_json)
        self.assertIn('"@tailwindcss/vite": "^4.', package_json)
        self.assertIn("@vitejs/plugin-react", package_json)
        self.assertIn("react()", vite_config)
        # The Vite Tailwind plugin is removed (REDESIGN_SPEC_V2_2 §3, Task 7):
        # JSX never used Tailwind utilities and no stylesheet imports Tailwind,
        # so the plugin was dead weight. The devDependencies stay in
        # package.json to keep the lockfile stable.
        self.assertNotIn("tailwindcss()", vite_config)
        self.assertNotIn("@tailwindcss/vite", vite_config)
        self.assertIn('"jsx": "react-jsx"', tsconfig)
        self.assertIn('id="root"', index)
        self.assertIn('/src/main.tsx', index)
        self.assertIn('createRoot(rootElement).render', entry)
        self.assertIn("<App />", entry)
        self.assertNotIn("startLegacyApp()", entry)
        self.assertIn("<LegacyBootstrap />", app)
        self.assertIn("export function App()", app)
        self.assertIn("export function startLegacyApp(): void", legacy_entrypoint)
        # styles.css is retired; the redesign loads the --dm-* token root first.
        self.assertFalse((REPO_ROOT / "src" / "styles.css").exists())
        self.assertIn('import "./styles/variables.css";', entry)
        self.assertNotIn('@import "tailwindcss";', all_style_css())

    def test_react_shell_is_split_into_design_components(self):
        app = (REPO_ROOT / "src" / "App.tsx").read_text(encoding="utf-8")
        # v4 P2 (문서 통합): WorkRail/DocumentStage/ReviewInspector 와 그 소유자
        # DocumentsWorkspace 가 삭제되고, 그 내용은 통합 문서 화면 CanvasWorkspace
        # 로 인라인됐다. 남은 1급 화면 컴포넌트는 셸(AppShell)이 소유하는
        # AppHeader/StatusRibbon 과 App 이 직접 렌더하는 4개 화면뿐이다.
        components = {
            "AppHeader": REPO_ROOT / "src" / "components" / "AppHeader.tsx",
            "StatusRibbon": REPO_ROOT / "src" / "components" / "StatusRibbon.tsx",
            "CanvasWorkspace": REPO_ROOT / "src" / "components" / "CanvasWorkspace.tsx",
            "MaskingSettingsScreen": REPO_ROOT / "src" / "components" / "MaskingSettingsScreen.tsx",
            "SettingsScreen": REPO_ROOT / "src" / "components" / "SettingsScreen.tsx",
        }

        owners = {
            "AppHeader": REPO_ROOT / "src" / "components" / "layout" / "AppShell.tsx",
            "StatusRibbon": REPO_ROOT / "src" / "components" / "layout" / "AppShell.tsx",
            "CanvasWorkspace": REPO_ROOT / "src" / "App.tsx",
            "MaskingSettingsScreen": REPO_ROOT / "src" / "App.tsx",
            "SettingsScreen": REPO_ROOT / "src" / "App.tsx",
        }

        # 삭제된 문서 관제 컴포넌트/소유자는 진짜 사라져야 한다(숨김 잔존 금지).
        self.assertFalse((REPO_ROOT / "src" / "components" / "workspaces" / "DocumentsWorkspace.tsx").exists())
        self.assertFalse((REPO_ROOT / "src" / "components" / "WorkRail.tsx").exists())
        self.assertFalse((REPO_ROOT / "src" / "components" / "DocumentStage.tsx").exists())
        self.assertFalse((REPO_ROOT / "src" / "components" / "ReviewInspector.tsx").exists())

        for component_name, component_path in components.items():
            self.assertTrue(component_path.exists(), f"{component_name} component file should exist")
            source = component_path.read_text(encoding="utf-8")
            self.assertIn(f"export function {component_name}()", source)
            owner_source = owners[component_name].read_text(encoding="utf-8")
            self.assertIn(f"import {{ {component_name} }}", owner_source)
            self.assertIn(f"<{component_name} />", owner_source)

        self.assertLess(len(app.splitlines()), 80)

    def test_design_system_layers_are_real_imported_modules(self):
        # v4: 좌측 레일(WorkspaceSidebar/WorkspaceNavigationContext/useWorkspaceNavigation)
        # 이 폐지되고 상단 바(AppHeader) 2탭+기어가 화면 전환을 소유한다.
        entry = (REPO_ROOT / "src" / "main.tsx").read_text(encoding="utf-8")
        app_shell = (REPO_ROOT / "src" / "components" / "layout" / "AppShell.tsx").read_text(encoding="utf-8")
        header = (REPO_ROOT / "src" / "components" / "AppHeader.tsx").read_text(encoding="utf-8")
        ui_paths = [
            REPO_ROOT / "src" / "components" / "ui" / "Button.tsx",
            REPO_ROOT / "src" / "components" / "ui" / "Modal.tsx",
            REPO_ROOT / "src" / "components" / "ui" / "Toast.tsx",
        ]
        style_paths = [
            REPO_ROOT / "src" / "styles" / "variables.css",
            REPO_ROOT / "src" / "styles" / "base.css",
            REPO_ROOT / "src" / "styles" / "components.css",
            REPO_ROOT / "src" / "styles" / "shell.css",
        ]
        # v4 P2 (문서 통합): WorkRail/DocumentStage 삭제 → 통합 문서 화면
        # CanvasWorkspace 가 그 소비 표면을 대신한다.
        consumers = "\n".join(
            (REPO_ROOT / "src" / "components" / path).read_text(encoding="utf-8")
            for path in [
                "AppHeader.tsx",
                "StatusRibbon.tsx",
                "CanvasWorkspace.tsx",
                "MaskingSettingsScreen.tsx",
            ]
        )

        for path in ui_paths:
            self.assertTrue(path.exists(), f"{path.relative_to(REPO_ROOT)} should exist")
            self.assertIn("export function", path.read_text(encoding="utf-8"))

        for path in style_paths:
            self.assertTrue(path.exists(), f"{path.relative_to(REPO_ROOT)} should exist")
            self.assertIn(f'import "./styles/{path.name}";', entry)

        # The removed rail modules must be truly gone (no hidden residue).
        self.assertFalse((REPO_ROOT / "src" / "components" / "AppRail.tsx").exists())
        self.assertFalse((REPO_ROOT / "src" / "components" / "sidebar" / "WorkspaceSidebar.tsx").exists())
        self.assertFalse((REPO_ROOT / "src" / "contexts" / "WorkspaceNavigationContext.tsx").exists())
        self.assertFalse((REPO_ROOT / "src" / "hooks" / "useWorkspaceNavigation.ts").exists())
        self.assertNotIn("WorkspaceNavigationProvider", app_shell)
        self.assertNotIn("AppRail", app_shell)
        # The top bar owns screen switching via the document home and settings gears.
        self.assertIn('data-screen-target="settings"', header)
        self.assertIn('data-screen-target="documents"', header)
        self.assertNotIn('data-screen-target="coordinate-template"', header)
        # Single --dm-* token root (the --nothing-*/--stitch-* systems are gone).
        self.assertIn("--dm-accent:", style_css("variables.css"))
        self.assertIn(".ui-button", style_css("components.css"))

    def test_settings_and_masking_settings_have_separate_ownership(self):
        settings = (REPO_ROOT / "src" / "components" / "SettingsScreen.tsx").read_text(encoding="utf-8")
        masking_settings = (REPO_ROOT / "src" / "components" / "MaskingSettingsScreen.tsx").read_text(encoding="utf-8")
        app = (REPO_ROOT / "src" / "App.tsx").read_text(encoding="utf-8")

        self.assertIn('id="settings-screen"', settings)
        self.assertIn('data-screen-panel="settings"', settings)
        self.assertIn('name="settings-theme"', settings)
        self.assertIn('id="settings-open-output-after-save"', settings)
        self.assertNotIn('id="btn-pick-outdir"', settings)
        self.assertNotIn('id="profile"', settings)
        self.assertIn('id="masking-settings-screen"', masking_settings)
        self.assertIn('data-screen-panel="masking-settings"', masking_settings)
        self.assertIn('id="rule-grid"', masking_settings)
        self.assertIn('id="profile"', masking_settings)
        self.assertNotIn('id="btn-pick-outdir"', masking_settings)
        self.assertNotIn('id="outdir-path"', masking_settings)
        self.assertNotIn('id="settings-modal"', app)
        self.assertNotIn('id="btn-pick-outdir"', app)

    def test_settings_theme_picker_exposes_requested_base_palettes(self):
        settings = (REPO_ROOT / "src" / "components" / "SettingsScreen.tsx").read_text(encoding="utf-8")
        settings_state = (REPO_ROOT / "src" / "settingsState.ts").read_text(encoding="utf-8")
        entry = (REPO_ROOT / "src" / "main.tsx").read_text(encoding="utf-8")
        themes = (REPO_ROOT / "src" / "styles" / "themes.css").read_text(encoding="utf-8")

        for theme_value, theme_label in [
            ('value: "light"', "라이트 모드"),
            ('value: "dark"', "다크 모드"),
            ('value: "system"', "시스템 설정 따름"),
        ]:
            self.assertIn(theme_value, settings)
            self.assertIn(theme_label, settings)

        # Removed presets must not linger in any layer.
        for removed in ["white", "blue", "purple", "brown", "black"]:
            self.assertNotIn(f'value: "{removed}"', settings)
            self.assertNotIn(f':root[data-theme="{removed}"]', themes)
        self.assertIn('const SETTINGS_THEMES = ["light", "dark", "system"] as const;', settings_state)
        self.assertIn('import "./styles/themes.css";', entry)
        self.assertIn(':root[data-theme="dark"]', themes)
        self.assertIn(':root[data-theme="light"]', themes)

    def test_legacy_bootstrap_preserves_masking_ipc_contract(self):
        entry = (REPO_ROOT / "src" / "main.tsx").read_text(encoding="utf-8")
        legacy_start = REPO_ROOT / "src" / "legacy" / "startLegacyApp.ts"
        legacy_bootstrap = REPO_ROOT / "src" / "legacy" / "LegacyBootstrap.tsx"
        document_session = REPO_ROOT / "src" / "features" / "document-session" / "documentSessionController.ts"
        manual_adjustment = REPO_ROOT / "src" / "features" / "manual-adjustment" / "manualAdjustmentController.ts"
        app = (REPO_ROOT / "src" / "App.tsx").read_text(encoding="utf-8")

        self.assertTrue(legacy_start.exists(), "legacy app bootstrap should live under src/legacy")
        self.assertTrue(legacy_bootstrap.exists(), "legacy bootstrap should be mounted by React after shell commit")
        legacy_source = legacy_typescript_source()
        legacy_bootstrap_source = legacy_bootstrap.read_text(encoding="utf-8")
        document_session_source = document_session.read_text(encoding="utf-8")
        manual_adjustment_source = manual_adjustment.read_text(encoding="utf-8")
        self.assertIn('import { LegacyBootstrap } from "./legacy/LegacyBootstrap";', app)
        self.assertIn("<LegacyBootstrap />", app)
        self.assertNotIn("startLegacyApp();", entry)
        self.assertIn("useEffect", legacy_bootstrap_source)
        self.assertIn("startLegacyApp();", legacy_bootstrap_source)
        removed_bridge_name = "withLegacy" + "BridgeElements"
        self.assertNotIn(removed_bridge_name, legacy_bootstrap_source)
        self.assertIn('invoke<MaskingResult>("run_masking_pipeline"', legacy_source)
        self.assertIn('invokeCommand<ApplyResult>("apply_manual_boxes"', manual_adjustment_source)
        self.assertIn('invokeCommand("open_mask_canvas_window"', document_session_source)
        self.assertNotIn('invoke<MaskingResult>("run_masking_pipeline"', app)

    def test_css_uses_refined_document_studio_tokens(self):
        # Trust Desk: a single --dm-* light token root (REDESIGN_SPEC_V2_2 §2).
        css = style_css("variables.css")

        # v4: :root 기본이 다크로 전환됨 (near-black + 단일 신뢰-블루 액센트).
        self.assertIn("--dm-bg: #0e1116;", css)
        self.assertIn("--dm-surface: #161b22;", css)
        self.assertIn("--dm-text: #e8edf4;", css)
        self.assertIn("--dm-accent: #2f81f7;", css)
        self.assertIn("--dm-mask:", css)
        self.assertIn("--dm-radius-sm: 8px;", css)
        self.assertIn("--dm-radius: 8px;", css)
        self.assertIn("--dm-radius-lg: 12px;", css)
        self.assertIn("--dm-radius-modal: 16px;", css)
        self.assertIn("--dm-radius-pill: 9999px;", css)
        for spacing in ("4px", "8px", "12px", "16px", "24px", "32px"):
            self.assertIn(spacing, css)
        for type_size in ("12px", "14px", "15px", "16px", "20px", "22px", "26px"):
            self.assertIn(type_size, css)
        self.assertIn("--dm-hairline: 1px;", css)
        self.assertIn("Segoe UI", css)
        self.assertIn("--dm-header-h: 48px;", css)
        self.assertIn("--dm-statusbar-h: 28px;", css)
        # 라이트 값은 themes.css light 프리셋으로 이관됨 (다크 기본에는 없다).
        themes = style_css("themes.css")
        self.assertIn("--dm-bg: #f6f5f4;", themes)
        self.assertIn("--dm-border: #e6e6e6;", themes)
        self.assertIn("--dm-text: #37352f;", themes)
        self.assertIn("--dm-accent: #0075de;", themes)
        # 좌측 레일 폐지 → 레일 폭 토큰도 제거됨.
        self.assertNotIn("--dm-rail-w", css)
        # The retired violet/obsidian and --surface-*/--nothing-* systems are gone.
        self.assertNotIn("--surface-app:", css)
        self.assertNotIn("--nothing-ink", css)
        self.assertNotIn("#8b5cf6", css)

    def test_document_canvas_layout_and_themes_are_bounded(self):
        shell = style_css("shell.css")
        themes = style_css("themes.css")
        base = style_css("base.css")
        canvas = style_css("screen-canvas.css")

        # 문서 work rail 은 통합 캔버스 툴바로, 검토·저장 레일은 inspector로 유지된다.
        self.assertIn(".dm-canvas__toolbar", style_css("screen-canvas.css"))
        self.assertIn(".dm-inspector", style_css("screen-canvas.css"))
        self.assertIn(".dm-statusbar", shell)
        self.assertIn("word-break: keep-all;", base)
        self.assertIn(':root[data-theme="dark"]', themes)
        self.assertIn(':root[data-theme="light"]', themes)
        self.assertNotIn(':root[data-theme="green"]', themes)
        self.assertNotIn(':root[data-theme="blue"]', themes)
        self.assertIn("--dm-document-paper: #ffffff;", style_css("variables.css"))
        self.assertIn("background: var(--dm-document-paper);", canvas)
        self.assertNotIn("filter: invert", canvas)

    def test_theme_bootstrap_runs_before_the_application_module(self):
        index = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
        bootstrap = (REPO_ROOT / "public" / "theme-bootstrap.js").read_text(encoding="utf-8")

        self.assertLess(index.index('/theme-bootstrap.js'), index.index('/src/main.tsx'))
        self.assertIn('data-theme-preference', bootstrap)
        self.assertIn('data-theme', bootstrap)
        self.assertIn('makiiing-v2-settings', bootstrap)
        self.assertIn('prefers-color-scheme: dark', bootstrap)
        self.assertNotRegex(index, r'<script(?![^>]*\bsrc=)[^>]*>')

    def test_settings_modal_responsive_layout_is_bounded(self):
        css = style_css("screen-settings.css")

        self.assertIn(".dm-settings-grid", css)
        self.assertIn(".dm-theme-grid", css)
        self.assertNotIn(".dm-output-picker-row", css)
        self.assertIn(".dm-rule-grid", css)
        mobile = css[css.index("@media (max-width: 640px)") :]
        self.assertIn(".dm-theme-grid", mobile)
        self.assertNotIn(".dm-output-picker-row", mobile)
        self.assertIn(".dm-rule-grid", mobile)

    def test_main_control_center_flow_keeps_workflow_controls_in_correct_surfaces(self):
        html = frontend_markup()
        shell = style_css("shell.css")
        # v4 P2 (문서 통합): screen-documents.css 삭제 → 검토·저장 레일 규칙은
        # #canvas-workspace-screen 스코프로 screen-canvas.css 에 이관.
        canvas_css = style_css("screen-canvas.css")

        # 상단 문서 홈 + main + inspector + status bar (좌측 레일 폐지).
        self.assertIn('className="dm-shell"', html)
        self.assertNotIn('className="dm-rail"', html)
        self.assertIn('className="dm-header__home"', html)
        self.assertIn('id="workspace-shell"', html)
        # v4 P2: 문서 work rail(.dm-workrail) 은 통합 문서 화면으로 병합되며 제거됐다.
        self.assertNotIn('className="dm-workrail"', html)
        self.assertIn('className="dm-stage"', html)
        # 통합 문서 화면: id 는 canvas-workspace-screen, 화면 슬롯은 data-screen-panel="documents".
        self.assertIn('data-screen-panel="documents"', html)
        self.assertIn('id="canvas-workspace-screen"', html)
        self.assertNotIn('id="documents-screen"', html)
        self.assertIn('id="settings-screen"', html)
        # v4 P2 (문서 통합): 검토·저장 레일은 캔버스 스테이지 옆 aside 로 인라인되며
        # 캔버스 인스펙터 클래스와 합쳐졌다(dm-canvas__inspector + dm-inspector).
        self.assertIn('className="dm-canvas__inspector dm-inspector"', html)
        self.assertIn("PDF 작업창 열기", html)
        self.assertIn('id="btn-save"', html)
        self.assertIn(">최종 저장<", html)
        self.assertNotIn("브라우저 미리보기용 임시 캔버스", html)
        self.assertNotIn('id="output-folder-summary"', html)
        self.assertNotIn('className="document-pill"', html)
        # v4 P2: work/stage 패널이 하나로 병합됐다. 실시간/작업 키워드 표면은 제거되어
        # 통합 문서 화면에도, 설정 화면에도 없어야 한다.
        settings_screen = html[html.index('id="settings-screen"') :]
        self.assertNotIn("실시간 키워드", html)
        self.assertNotIn("작업 키워드", settings_screen)
        self.assertIn("마스킹 카테고리", html)
        self.assertNotIn("검토 큐", html)
        self.assertNotIn("자동 탐지가 애매하게 본 후보", html)
        # v4.1: 안전 리포트가 내부 검증 장치로 내부화되며 검토·저장 레일에서 "안전
        # 리포트" 표면이 제거됐다. 레일의 현행 앵커(검토·저장 헤더)로 재조준한다.
        self.assertIn("검토·저장", html)
        self.assertNotIn("안전 리포트", html)
        self.assertNotIn('className="document-preview-workbench"', html)
        self.assertIn(".dm-header__home", shell)
        self.assertNotIn(".dm-rail ", shell)
        self.assertIn("#workspace-shell", shell)
        self.assertIn(".dm-inspector", canvas_css)
        self.assertIn("body.standalone-canvas-window .dm-canvas__body", canvas_css)

    def test_mobile_control_center_reaches_review_rail_without_a_dock(self):
        # v4 P3: 모바일 독 삭제. 좁은 폭에서는 상단 바가 화면 전환을 전담하고, 통합
        # 문서 화면은 스테이지+검토 레일을 세로로 스택해 독 없이 스크롤로 레일에
        # 접근한다. 독/구 documents-screen 잔재는 셸 CSS 에서 진짜 삭제되어야 한다.
        shell = style_css("shell.css")
        canvas = style_css("screen-canvas.css")

        self.assertNotIn(".mobile-action-dock", shell)
        self.assertNotIn("#documents-screen", shell)
        self.assertNotIn("[data-mobile-panel]", shell)
        self.assertIn("@media (max-width: 1023.98px)", shell)

        # 좁은 폭 캔버스 본문은 그리드 대신 블록 흐름으로 스테이지+레일을 세로 스택한다.
        self.assertIn("@media (max-width: 1023.98px)", canvas)
        canvas_mobile = canvas[canvas.index("@media (max-width: 1023.98px)") :]
        self.assertIn(".dm-canvas__body", canvas_mobile)
        self.assertIn("display: block;", canvas_mobile)
        self.assertIn(".dm-canvas__stage", canvas_mobile)
        # 레일 내부 스크롤을 풀어(본문이 스크롤 소유) 검토 레일 전체가 스택된다.
        self.assertIn("#canvas-workspace-screen .dm-inspector", canvas)

    def test_review_queue_workbench_is_removed_from_shell(self):
        html = frontend_markup()
        # v4 P2 (문서 통합): DocumentsWorkspace 삭제 → 검토 인스펙터가 통합 문서
        # 화면 CanvasWorkspace 안으로 인라인됐다.
        canvas_workspace = (REPO_ROOT / "src" / "components" / "CanvasWorkspace.tsx").read_text(encoding="utf-8")
        legacy_source = legacy_typescript_source()
        # v4 P2: 인스펙터 규칙은 screen-documents.css → screen-canvas.css 로 이관됨.
        css = style_css("screen-canvas.css")

        # Inspector collapse control replaces the removed review-queue screen.
        self.assertIn('className="dm-inspector__bar"', html)
        self.assertIn('id="btn-toggle-inspector"', html)
        self.assertIn("패널 접기", html)
        self.assertFalse((REPO_ROOT / "src" / "components" / "workspaces" / "ReviewQueueScreen.tsx").exists())
        self.assertNotIn('id="review-screen"', html)
        self.assertNotIn('data-screen-panel="review"', html)
        self.assertNotIn('data-screen-target="review"', html)
        self.assertNotIn('id="review-queue-table-body"', html)
        # 통합 문서 화면은 data-screen-panel="documents" 슬롯을 차지하고, 검토·저장
        # 레일(ReviewInspector 이식)을 인라인 aside 로 품는다.
        self.assertIn('data-screen-panel="documents"', canvas_workspace)
        self.assertIn('className="dm-canvas__inspector dm-inspector"', canvas_workspace)
        self.assertIn("function setInspectorCollapsed", legacy_source)
        self.assertIn("is-inspector-collapsed", legacy_source)
        self.assertIn("btnToggleInspector.addEventListener", legacy_source)
        # 통합 문서 화면(#canvas-workspace-screen)은 접이식 인스펙터와 자체 요약/저장
        # 표면을 가진 유계 그리드다. (구 doclist 는 문서 통합으로 삭제됨.)
        self.assertIn("#canvas-workspace-screen.dm-canvas {", css)
        self.assertIn("var(--dm-inspector-w)", css)
        self.assertIn("#canvas-workspace-screen .dm-inspector.is-collapsed", css)
        self.assertIn("#canvas-workspace-screen .dm-inspector {", css)
        self.assertIn(".dm-savesummary__grid", css)
        self.assertIn(".dm-detect__list", css)
        self.assertIn("@media (max-width:", css)

    def test_remaining_ux_css_prioritizes_workflow_steps_confirmation_and_narrow_stack(self):
        # v4 P2 (문서 통합): screen-documents.css 삭제. 살아남는 확인(save-gate)
        # 규칙은 #canvas-workspace-screen 스코프로 screen-canvas.css 에 이관됐다.
        docs = style_css("screen-canvas.css")
        settings = style_css("screen-settings.css")
        shell = style_css("shell.css")

        # v4 P2: 스텝퍼/빈 드롭존은 문서 통합에서 제거된 표면이라 이관되지 않았다.
        self.assertNotIn(".dm-stepper", docs)
        self.assertNotIn(".dm-dropzone", docs)
        # 저장 확인 게이트(save-gate readiness)는 통합 문서 화면에 그대로 살아 있다.
        self.assertIn(".dm-savegate__readiness", docs)
        self.assertIn('.dm-savegate__readiness[data-state="ready"]', docs)
        self.assertNotIn(".dm-output-preview", settings)
        self.assertIn(".dm-savewarn__location-note", docs)
        # v4 P3: 모바일 독 삭제(오버플로 메뉴 후신도 폐지). 좁은 폭에서 통합 문서
        # 화면은 인스펙터를 스테이지 아래로 세로 스택해 유계를 유지한다.
        self.assertNotIn(".mobile-action-dock", shell)
        docs_mobile = docs[docs.index("@media (max-width: 1023.98px)") :]
        self.assertIn(".dm-inspector", docs_mobile)

    def test_mobile_canvas_toolbar_and_panel_stack_without_overflow(self):
        css = style_css("screen-canvas.css")

        self.assertIn("@media (max-width: 1023.98px)", css)
        # v4 P3: 좁은 폭에서 캔버스 본문은 그리드 대신 블록 흐름으로 스테이지+인스펙터를
        # 세로 스택하고 본문이 스크롤을 소유한다(모바일 독 없이 검토 레일 접근).
        mobile = css[css.index("@media (max-width: 1023.98px)") :]
        self.assertIn(".dm-canvas__body", mobile)
        self.assertIn("display: block;", mobile)
        self.assertIn(".dm-canvas__stage", mobile)
        # 인스펙터 내부 스크롤은 풀리고(overflow: visible) 본문이 스크롤을 소유한다.
        self.assertIn("#canvas-workspace-screen .dm-inspector__scroll", css)
        self.assertIn("overflow: visible;", css)

    def test_preview_only_default_and_vertical_layout_are_bounded(self):
        html = frontend_markup()
        shell = style_css("shell.css")
        canvas_css = style_css("screen-canvas.css")

        canvas_screen = html[html.index('id="canvas-workspace-screen"') :]
        self.assertIn('id="masked-preview-panel"', canvas_screen)
        self.assertIn('id="original-compare-panel"', canvas_screen)
        # Original compare panel is hidden by default (preview-only default).
        self.assertIn('id="original-compare-panel" className="dm-canvas__viewer is-hidden"', canvas_screen)
        self.assertIn('id="toggle-original-compare"', canvas_screen)
        # The shell is a bounded 100dvh grid; the canvas body owns its scroll.
        self.assertIn("height: 100dvh;", shell)
        self.assertIn("overflow: hidden;", shell)
        self.assertIn("#workspace-shell", shell)
        self.assertIn("min-height: 0;", canvas_css)
        self.assertIn("overflow: auto;", canvas_css)

    def test_canvas_final_save_trust_summary_is_visible_and_bounded(self):
        html = frontend_markup()
        css = style_css("screen-canvas.css")

        canvas_screen = html[html.index('id="canvas-workspace-screen"') :]
        # v4 P2 (문서 통합): 캔버스 전용 요약 컨테이너(#canvas-final-save-summary)는
        # 검토·저장 레일의 저장 요약 카드(.dm-savesummary, aria-label="저장 요약")로
        # 대체됐다. 저장 게이트 텍스트 소스인 canvas-summary-* 프록시는 유지된다.
        self.assertIn('aria-label="저장 요약"', canvas_screen)
        self.assertIn('id="canvas-summary-mask-count"', canvas_screen)
        self.assertIn('id="canvas-summary-restore-count"', canvas_screen)
        self.assertIn('id="canvas-summary-keyword-count"', canvas_screen)
        self.assertIn('id="canvas-summary-output-state"', canvas_screen)
        self.assertIn("최종 저장 전 확인", canvas_screen)
        self.assertIn(".dm-canvas__summary", css)
        self.assertIn(".dm-canvas__props", css)
        self.assertIn(".canvas-editor-palette", css)

    def test_canvas_toolbar_separates_tool_selection_from_apply_action(self):
        html = frontend_markup()
        css = style_css("screen-canvas.css")

        canvas_screen = html[html.index('id="canvas-workspace-screen"') :]
        # v4 P2 (문서 통합): 통합 문서 화면의 상단 툴바 aria-label 은 "문서 도구".
        # 편집 도구 라벨은 "마스킹"/"복원" 세그먼트(도구 선택)로, 반영은 별도 액션.
        toolbar = canvas_screen[canvas_screen.index('aria-label="문서 도구"') : canvas_screen.index('id="canvas-tool-readiness"')]
        hidden_proxy = canvas_screen[canvas_screen.index('manual-command-proxy') :]
        self.assertIn('id="btn-canvas-tool-mask"', toolbar)
        self.assertIn(">마스킹<", toolbar)
        self.assertIn('id="btn-canvas-tool-restore"', toolbar)
        self.assertIn(">복원<", toolbar)
        self.assertIn('id="btn-canvas-apply"', toolbar)
        self.assertIn("수동 보정 반영", toolbar)
        self.assertNotIn('id="btn-canvas-mask"', toolbar)
        self.assertNotIn("수동 마스킹 시작", toolbar)
        self.assertNotIn('id="btn-canvas-restore"', toolbar)
        self.assertNotIn("수동 복원 시작", toolbar)
        self.assertNotIn('id="btn-canvas-mask"', hidden_proxy)
        self.assertNotIn('id="btn-canvas-restore"', hidden_proxy)
        # Apply is gated by a readiness banner, distinct from tool selection.
        self.assertIn(".dm-canvas__readiness", css)
        self.assertIn(".canvas-editor-palette", css)

    def test_final_save_readiness_is_visible_in_main_and_canvas_surfaces(self):
        html = frontend_markup()
        # v4 P2 (문서 통합): 저장 준비(save-gate) 규칙은 #canvas-workspace-screen
        # 스코프로 screen-canvas.css 에 이관됨.
        css = style_css("screen-canvas.css")

        # v4 P2: 문서 관제 보드 + 캔버스가 하나의 문서 화면으로 병합됐다. 저장 준비
        # 상태(#final-save-readiness)와 도구 게이트 사유(canvas-tool-readiness)가
        # 이 통합 화면 한 곳에서 함께 보인다.
        canvas_screen = html[html.index('id="canvas-workspace-screen"') :]
        self.assertIn('id="final-save-readiness"', canvas_screen)
        self.assertIn('aria-live="polite"', canvas_screen)
        self.assertIn("저장 위치와 파일명", canvas_screen)
        self.assertNotIn("출력 폴더", canvas_screen)
        self.assertIn('aria-describedby="canvas-tool-readiness"', canvas_screen)
        # 스텝퍼/진행률 바(workflow-progress-*)는 문서 통합에서 제거된 표면이다.
        self.assertNotIn('id="workflow-progress-percent"', canvas_screen)
        self.assertNotIn('id="workflow-progress-fill"', canvas_screen)
        self.assertIn(".dm-savegate__readiness", css)

    def test_standalone_canvas_keeps_tools_above_viewer_and_properties_aside(self):
        canvas_css = style_css("screen-canvas.css")
        shell = style_css("shell.css")

        # Standalone window: toolbar stays above the viewer (window grid rows),
        # the properties inspector stays aside, and the shell chrome is hidden.
        self.assertIn(".dm-canvas__window {", canvas_css)
        self.assertIn("grid-template-rows: auto 28px minmax(0, 1fr);", canvas_css)
        self.assertIn("body.standalone-canvas-window .dm-canvas__body {", canvas_css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) 300px;", canvas_css)
        self.assertIn("body.standalone-canvas-window .dm-header,", shell)
        self.assertIn("body.standalone-canvas-window .dm-statusbar {", shell)
        # v4: 레일이 폐지되어 standalone 숨김 목록에서도 사라졌다.
        self.assertNotIn(".dm-rail", shell)
        # v4 P3: 모바일 독 삭제 → standalone 숨김 목록에서도 사라졌다.
        self.assertNotIn(".mobile-action-dock", shell)

    def test_canvas_focus_mode_compacts_chrome_and_uses_right_properties_panel(self):
        html = frontend_markup()
        legacy_source = legacy_typescript_source()
        shell = style_css("shell.css")
        canvas_css = style_css("screen-canvas.css")

        # 화면 진입은 상단 문서 홈과 화면 내부 PDF 작업창 버튼이 담당한다.
        self.assertIn('data-screen-target="documents"', html)
        self.assertNotIn('data-screen-target="coordinate-template"', html)
        self.assertIn("PDF 작업창 열기", html)
        # Legacy still toggles a body class; the redesign renders only the
        # active screen, so the canvas is inherently a full-width focus surface.
        # v4 P2 (문서 통합): 캔버스가 곧 문서 화면이므로 별도 canvas-screen-active
        # 토글은 사라지고, standalone 작업창 진입만 body 클래스로 표시한다.
        self.assertIn('document.body.classList.toggle("standalone-canvas-window"', legacy_source)
        self.assertIn("#workspace-shell > [data-screen-panel]:not(.is-active) {", shell)
        self.assertIn("grid-template-columns: minmax(0, 1fr) var(--dm-inspector-w);", canvas_css)
        self.assertIn(".dm-canvas__toolbar {", canvas_css)
        self.assertIn(".dm-header__home {", shell)

    def test_top_bar_uses_document_home_and_explicit_settings_actions(self):
        # 좌표 템플릿 퇴역 후 상단 바는 문서 홈, 실행 명령, 설정 진입만 제공한다.
        html = frontend_markup()
        css = style_css("shell.css")

        # 사라진 레일 표면·id 는 마크업·CSS 에서 진짜 삭제되어야 한다 (숨김 잔존 금지).
        self.assertNotIn("rail-brand-card", html)
        self.assertNotIn("rail-home-button", html)
        self.assertNotIn("rail-ingest-panel", html)
        self.assertNotIn("rail-memory-panel", html)
        self.assertNotIn('id: "rail-documents"', html)
        self.assertNotIn('id: "rail-canvas"', html)
        self.assertNotIn('id: "rail-coordinate-template"', html)
        self.assertNotIn('id="rail-settings"', html)
        self.assertNotIn("새 문서 등록", html)
        self.assertNotIn(".dm-rail", css)

        self.assertIn('className="dm-header__home"', html)
        self.assertIn('data-screen-target="documents"', html)
        self.assertNotIn('data-screen-target="coordinate-template"', html)
        self.assertIn('className="dm-header__gear"', html)
        self.assertIn('data-screen-target="settings"', html)
        # 애매한 유니코드 글리프 네비 아이콘 금지.
        self.assertNotIn(">◇</span>", html)
        self.assertNotIn(">⌘</span>", html)
        self.assertNotIn(">↺</span>", html)
        self.assertNotIn(">⚙</span>", html)
        self.assertNotIn('className="rail-nav-icon"', html)
        self.assertIn(".dm-header__home", css)
        self.assertIn(".dm-header__gear", css)

    # v4 P4: 구 scripts/ui_risk_check.mjs 는 v3 구조(.app-shell/.app-rail/
    # .document-stage/.review-inspector/.work-log-panel)를 조회하는 죽은 스크립트라
    # 삭제됐다. 브라우저 레이아웃/오버플로·캔버스 편집 도구 무결성은 게이트
    # qa_redesign_smoke.mjs·qa_canvas_interactions.mjs 가 실제로 검증한다. 따라서
    # 이 스크립트를 텍스트로 잠그던 test_browser_ui_risk_check_remains_layout_only
    # 도 함께 제거했다(죽은 참조 0 원칙 — REDESIGN_V4_DARK §0).

    def test_final_status_and_safe_report_remain_primary_inspector_content(self):
        html = frontend_markup()

        final_state_index = html.index('id="final-state-card"')
        settings_index = html.index('id="settings-screen"')

        self.assertLess(final_state_index, settings_index)
        # v4 P2 (문서 통합): 최종 상태를 담은 검토·저장 레일이 통합 문서 화면의
        # 캔버스 인스펙터 aside 로 들어왔다. v4.1: 안전 리포트는 내부 검증 장치로
        # 내부화되어 인스펙터의 주 콘텐츠는 "검토 필요 항목" 탐지 카드가 됐다.
        self.assertIn('className="dm-canvas__inspector dm-inspector"', html)
        self.assertIn("검토 필요 항목", html)
        self.assertNotIn("안전 리포트", html)
        self.assertNotIn('id="review-queue"', html)
        # v4 P2 (문서 통합): 검토 인스펙터가 통합 문서 화면으로 들어오며 대기 문구가
        # #final-state-detail 로 통일됐다("열고" 표현).
        self.assertIn("문서를 열고 마스킹을 실행하세요.", html)

    def test_task15_css_consolidation_removes_refinement_files_and_imports(self):
        main = (REPO_ROOT / "src" / "main.tsx").read_text(encoding="utf-8")
        refinement_files = refinement_css_files()
        imported_refinements = [
            refinement_file
            for refinement_file in refinement_files
            if f'./{Path(refinement_file).name}' in main
        ]
        violations = []

        if refinement_files:
            violations.append(f"refinement CSS files still present: {', '.join(refinement_files)}")
        if imported_refinements:
            violations.append(f"main.tsx still imports refinement CSS: {', '.join(imported_refinements)}")

        self.assertEqual(
            [],
            violations,
            "Task 15 consolidation should leave src/styles.css as the single app stylesheet.\n"
            + "\n".join(violations),
        )

    def test_task15_deleted_ui_selectors_are_absent_from_css(self):
        css = all_style_css()
        deleted_ui_selectors = [
            ".history-list",
            ".history-status-pills",
            ".history-board",
            ".history-board-head",
            ".history-board-body",
            ".history-summary",
            ".stage-bottom-dock",
            ".safe-report-panel",
            ".final-save-checklist",
            "#settings-check-review-queue",
            "#dialog-restore-state",
        ]
        remaining_selectors = [selector for selector in deleted_ui_selectors if selector in css]

        self.assertEqual(
            [],
            remaining_selectors,
            "Task 15 CSS cleanup should not keep selectors for UI removed in Task 14.",
        )

    def test_task15_desktop_compact_layout_tokens_are_stable(self):
        variables = style_css("variables.css")
        shell = style_css("shell.css")
        # v4 P2 (문서 통합): 인스펙터 폭 토큰 사용처는 screen-documents.css →
        # screen-canvas.css 로 이관됨.
        documents = style_css("screen-canvas.css")
        # v4 셸은 레일 열을 없앤 단일 컬럼 그리드다. 레일 폭 토큰(--dm-rail-w)은
        # 제거되고 헤더/상태바/검토 패널 폭 토큰만 semantic --dm-* 로 남는다.
        variables_contract = [
            "--dm-header-h: 48px;",
            "--dm-inspector-w: 320px;",
            "--dm-statusbar-h: 28px;",
        ]
        missing = [snippet for snippet in variables_contract if snippet not in variables]
        self.assertEqual([], missing, f"missing layout tokens: {missing}")
        self.assertNotIn("--dm-rail-w", variables)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", shell)
        self.assertIn("var(--dm-inspector-w)", documents)


if __name__ == "__main__":
    unittest.main()
