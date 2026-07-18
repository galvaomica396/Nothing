// App settings controller (docs/CODE_REVIEW_2026-07-04.md
// "startLegacyApp 분리": app-settings 모듈).
//
// Owns theme application plus the settings save/restore logic (collect the live
// control values into an AppSettings snapshot, and apply a persisted snapshot
// back onto the controls + shared run state). The persisted-shape helpers stay
// in src/settingsState.ts (loadSettings/saveSettings/themeAttribute, public
// names preserved); this controller is the DOM-binding layer that startLegacyApp
// wires with injected controls and the re-render callbacks.
//
// startLegacyApp destructures the exposed methods into same-named local consts,
// so every existing selectedTheme()/applyTheme()/collectSettings()/
// applySettings() call site stays unchanged. Screen navigation (activateAppScreen
// / openSettingsScreen / closeSettingsScreen) stays in startLegacyApp.

import { resolveTheme, themeAttribute } from "../../settingsState";
import type { LegacySessionState } from "../../legacy/startLegacyApp";
import type { AppSettings, DeidentificationMode, SettingsTheme } from "../../settingsState";

export type AppSettingsState = Pick<LegacySessionState, "outputDir" | "openOutputAfterSave">;

export type AppSettingsDeps = {
  readonly state: AppSettingsState;
  readonly settingsThemeInputs: HTMLInputElement[];
  readonly profileEl: HTMLSelectElement;
  readonly engineEl: HTMLSelectElement;
  readonly displayModeEl: HTMLSelectElement;
  readonly deidentificationPolicyEl: HTMLSelectElement;
  readonly regionScopeEl: HTMLSelectElement;
  readonly customRegionsEl: HTMLInputElement;
  readonly customKeywordsEl: HTMLTextAreaElement;
  readonly optPdfRedactionEl: HTMLInputElement;
  readonly settingsExportMaskedTextEl: HTMLInputElement;
  readonly settingsOpenOutputAfterSaveEl: HTMLInputElement;
  readonly updateOutputDirectoryState: () => void;
  readonly applyProfileDefaults: () => void;
  readonly updateRegionScopeControls: () => void;
  readonly updateMaskedTextOptionControls: () => void;
  readonly syncKeywordDialogChips: () => void;
};

export type AppSettingsController = {
  readonly selectedTheme: () => SettingsTheme;
  readonly applyTheme: (theme: SettingsTheme) => void;
  readonly collectSettings: () => AppSettings;
  readonly applySettings: (settings: AppSettings) => void;
};

export function createAppSettingsController(deps: AppSettingsDeps): AppSettingsController {
  const { state } = deps;
  const colorSchemeQuery = window.matchMedia("(prefers-color-scheme: dark)");
  let appliedTheme: SettingsTheme = "dark";

  function renderTheme() {
    document.documentElement.setAttribute("data-theme", resolveTheme(appliedTheme, colorSchemeQuery.matches));
    document.documentElement.setAttribute("data-theme-preference", appliedTheme);
  }

  colorSchemeQuery.addEventListener("change", () => {
    if (appliedTheme === "system") renderTheme();
  });

  function selectedTheme(): SettingsTheme {
    return themeAttribute(deps.settingsThemeInputs.find((input) => input.checked)?.value);
  }

  function applyTheme(theme: SettingsTheme) {
    const nextTheme = themeAttribute(theme);
    appliedTheme = nextTheme;
    renderTheme();
    for (const input of deps.settingsThemeInputs) {
      input.checked = input.value === nextTheme;
    }
  }

  function collectSettings(): AppSettings {
    return {
      theme: selectedTheme(),
      outputDir: state.outputDir,
      profile: deps.profileEl.value,
      engine: deps.engineEl.value,
      displayMode: deps.displayModeEl.value as AppSettings["displayMode"],
      deidentificationMode: deps.deidentificationPolicyEl.value as DeidentificationMode,
      regionScope: deps.regionScopeEl.value,
      customRegions: deps.customRegionsEl.value.trim(),
      customKeywords: deps.customKeywordsEl.value.trim(),
      pdfRedaction: deps.optPdfRedactionEl.checked,
      exportMaskedText: deps.settingsExportMaskedTextEl.checked,
      openOutputAfterSave: deps.settingsOpenOutputAfterSaveEl.checked,
    };
  }

  function applySettings(settings: AppSettings) {
    applyTheme(settings.theme);
    state.outputDir = settings.outputDir;
    state.openOutputAfterSave = settings.openOutputAfterSave;
    deps.profileEl.value = settings.profile;
    deps.engineEl.value = settings.engine;
    deps.displayModeEl.value = settings.displayMode;
    deps.deidentificationPolicyEl.value = settings.deidentificationMode;
    deps.regionScopeEl.value = settings.regionScope;
    deps.customRegionsEl.value = settings.customRegions;
    deps.customKeywordsEl.value = settings.customKeywords;
    deps.optPdfRedactionEl.checked = settings.pdfRedaction;
    deps.settingsExportMaskedTextEl.checked = settings.exportMaskedText;
    deps.settingsOpenOutputAfterSaveEl.checked = settings.openOutputAfterSave;
    deps.displayModeEl.dispatchEvent(new Event("change", { bubbles: true }));
    deps.deidentificationPolicyEl.dispatchEvent(new Event("change", { bubbles: true }));
    deps.updateOutputDirectoryState();
    deps.applyProfileDefaults();
    deps.updateRegionScopeControls();
    deps.updateMaskedTextOptionControls();
    deps.syncKeywordDialogChips();
  }

  return { selectedTheme, applyTheme, collectSettings, applySettings };
}
