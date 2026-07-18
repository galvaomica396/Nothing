import { DEFAULT_SETTINGS, loadSettings, saveSettings } from "../../settingsState";
import type { LegacyDomBindings } from "../domBindings";
import type { LegacyAppController } from "../legacyAppController";

export function wireSettingsEvents(bindings: LegacyDomBindings, controller: LegacyAppController): void {
  const {
    profileEl,
    regionScopeEl,
    settingsExportMaskedTextEl,
    settingsThemeInputs,
    settingsTabButtons,
    appScreenButtons,
    btnSettingsBack,
    btnMaskingSettingsBack,
    btnToggleInspector,
    documentsScreenEl,
    btnCloseFinalSaveDialog,
    btnDialogCancelSave,
    btnDialogSaveAll,
    finalSaveDialogEl,
    keywordDialogEl,
    newDocumentDialogEl,
    workspaceShellEl,
    btnSettingsSave,
    btnSettingsReset,
    btnSettingsClose,
    btnSettingsFooterClose,
    btnMaskingSettingsCancel,
    btnMaskingSettingsPreview,
    btnMaskingSettingsApply,
  } = bindings;
  const {
    applyProfileDefaults,
    updateRegionScopeControls,
    updateMaskedTextOptionControls,
    selectedTheme,
    applyTheme,
    activateSettingsTab,
    openSettingsScreen,
    rememberAuxReturnScreen,
    collectSettings,
    activateAppScreen,
    returnFromAuxScreen,
    setInspectorCollapsed,
    closeFinalSaveDialog,
    saveFinalOutput,
    closeKeywordDialog,
    resolveDiscardConfirmation,
    isAuxScreen,
    closeSettingsScreen,
    applySettings,
    renderFinalSaveConfirmation,
    renderCanvasFinalSaveSummary,
    setStatus,
  } = controller;

  profileEl.addEventListener("change", applyProfileDefaults);
  regionScopeEl.addEventListener("change", updateRegionScopeControls);
  settingsExportMaskedTextEl.addEventListener("change", updateMaskedTextOptionControls);
  for (const input of settingsThemeInputs) {
    input.addEventListener("change", () => {
      const theme = selectedTheme();
      applyTheme(theme);
      saveSettings({ ...loadSettings(), theme });
    });
  }
  for (const button of settingsTabButtons) {
    button.addEventListener("click", () => activateSettingsTab(button.dataset.settingsTab || "general"));
  }
  for (const button of appScreenButtons) {
    button.addEventListener("click", () => {
      const target = button.dataset.screenTarget || "documents";
      if (target === "settings") {
        openSettingsScreen();
        return;
      }
      // 마스킹 설정도 보조 화면 — 진입 전 1급 화면을 기억해 "← 돌아가기"로 복귀한다.
      if (target === "masking-settings") {
        rememberAuxReturnScreen();
        controller.settingsSnapshot = collectSettings();
      }
      activateAppScreen(target);
    });
  }
  btnSettingsBack.addEventListener("click", returnFromAuxScreen);
  btnMaskingSettingsBack.addEventListener("click", returnFromAuxScreen);
  btnToggleInspector.addEventListener("click", () => {
    setInspectorCollapsed(!documentsScreenEl.classList.contains("is-inspector-collapsed"));
  });
  btnCloseFinalSaveDialog.addEventListener("click", closeFinalSaveDialog);
  btnDialogCancelSave.addEventListener("click", closeFinalSaveDialog);
  // "그대로 저장": 경고를 확인한 사용자의 재량 저장. 확인 플래그를 넘겨 다이얼로그가
  // 다시 뜨지 않고 곧바로 저장이 진행되게 한다.
  btnDialogSaveAll.addEventListener("click", () => {
    closeFinalSaveDialog();
    void saveFinalOutput({ warningsConfirmed: true });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !finalSaveDialogEl.classList.contains("is-hidden")) {
      closeFinalSaveDialog();
      return;
    }
    if (event.key === "Escape" && !keywordDialogEl.classList.contains("is-hidden")) {
      closeKeywordDialog();
      return;
    }
    if (event.key === "Escape" && !newDocumentDialogEl.classList.contains("is-hidden")) {
      resolveDiscardConfirmation(false);
      return;
    }
    if (event.key === "Escape" && workspaceShellEl.dataset.activeScreen !== "documents") {
      // 보조 설정 화면은 진입 이전 작업 화면으로 복귀한다.
      if (isAuxScreen(workspaceShellEl.dataset.activeScreen ?? "")) {
        returnFromAuxScreen();
      } else {
        activateAppScreen("documents");
      }
    }
  });
  btnSettingsSave.addEventListener("click", () => {
    const saved = saveSettings(collectSettings());
    applySettings(saved);
    if (controller.settingsSnapshot) {
      controller.settingsSnapshot = null;
    }
    setStatus("설정 저장 완료");
  });
  btnSettingsReset.addEventListener("click", () => {
    applySettings(DEFAULT_SETTINGS);
    controller.settingsSnapshot = collectSettings();
    setStatus("설정 기본값 미리보기");
  });
  btnSettingsClose.addEventListener("click", closeSettingsScreen);
  btnSettingsFooterClose.addEventListener("click", closeSettingsScreen);
  btnMaskingSettingsCancel.addEventListener("click", () => {
    applySettings(controller.settingsSnapshot || loadSettings());
    controller.settingsSnapshot = null;
    setStatus("마스킹 설정 변경을 취소했습니다.");
  });
  btnMaskingSettingsPreview.addEventListener("click", () => {
    renderFinalSaveConfirmation();
    renderCanvasFinalSaveSummary();
    setStatus("현재 마스킹 설정으로 저장 조건을 미리 계산했습니다.");
  });
  btnMaskingSettingsApply.addEventListener("click", () => {
    const saved = saveSettings(collectSettings());
    applySettings(saved);
    controller.settingsSnapshot = null;
    setStatus("마스킹 설정 적용 완료");
  });
}
