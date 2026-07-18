import type { LegacyDomBindings } from "../domBindings";
import type { LegacyAppController } from "../legacyAppController";

export function wireCanvasEvents(bindings: LegacyDomBindings, controller: LegacyAppController): void {
  const {
    btnMaskCanvas,
    btnOpenCanvasWindow,
    btnCloseCanvas,
    btnCollapseCanvasTools,
    btnCollapseCanvasProperties,
    btnExpandCanvasPanels,
    btnCanvasLoadPdf,
    btnCanvasToolSelect,
    btnCanvasToolMask,
    btnCanvasToolRestore,
    btnCanvasToolPan,
    btnCanvasToolDelete,
    btnCanvasZoomOut,
    btnCanvasZoomIn,
    btnCanvasUndo,
    btnUndo,
    btnCanvasDeleteBox,
    btnCanvasBoxDelete,
    btnCanvasBoxConvertMask,
    btnCanvasBoxConvertRestore,
    btnOpenCanvasPropertiesTab,
    btnCanvasClear,
    btnClear,
    btnCanvasApply,
    btnCanvasFinalSave,
    customKeywordsEl,
    toggleOriginalCompareEl,
    btnManualApply,
    modeMask,
    modeRestore,
    documentsScreenEl,
  } = bindings;
  const {
    openCanvasDesktopWindow,
    setCanvasMode,
    setCanvasToolsCollapsed,
    setCanvasPropertiesCollapsed,
    expandCanvasPanels,
    pickCanvasPdf,
    setActiveCanvasTool,
    adjustZoom,
    deleteSelectedCanvasBox,
    convertCanvasSelectedBox,
    applyPendingManualBoxes,
    saveFinalOutput,
    renderCanvasFinalSaveSummary,
    renderFinalSaveConfirmation,
    updateOriginalCompareVisibility,
    undoLastCanvasBox,
    clearDerivedArtifacts,
  } = controller;

  btnMaskCanvas.addEventListener("click", () => {
    void openCanvasDesktopWindow();
  });
  btnOpenCanvasWindow.addEventListener("click", () => {
    setCanvasMode(true, { allowEmptyCanvas: true });
  });
  btnCloseCanvas.addEventListener("click", () => setCanvasMode(false));
  btnCollapseCanvasTools.addEventListener("click", () => {
    const canvasScreen = documentsScreenEl;
    setCanvasToolsCollapsed(!canvasScreen.classList.contains("tools-collapsed"));
  });
  btnCollapseCanvasProperties.addEventListener("click", () => {
    const canvasScreen = documentsScreenEl;
    setCanvasPropertiesCollapsed(!canvasScreen.classList.contains("properties-collapsed"));
  });
  btnExpandCanvasPanels.addEventListener("click", expandCanvasPanels);
  btnCanvasLoadPdf.addEventListener("click", () => {
    void pickCanvasPdf();
  });
  btnCanvasToolSelect.addEventListener("click", () => setActiveCanvasTool("select"));
  btnCanvasToolMask.addEventListener("click", () => setActiveCanvasTool("mask"));
  btnCanvasToolRestore.addEventListener("click", () => setActiveCanvasTool("restore"));
  btnCanvasToolPan.addEventListener("click", () => setActiveCanvasTool("pan"));
  btnCanvasToolDelete.addEventListener("click", () => setActiveCanvasTool("delete"));
  btnCanvasZoomOut.addEventListener("click", () => {
    void adjustZoom(-1);
  });
  btnCanvasZoomIn.addEventListener("click", () => {
    void adjustZoom(1);
  });
  btnCanvasUndo.addEventListener("click", () => btnUndo.click());
  btnCanvasDeleteBox.addEventListener("click", deleteSelectedCanvasBox);
  btnCanvasBoxDelete.addEventListener("click", deleteSelectedCanvasBox);
  btnCanvasBoxConvertMask.addEventListener("click", () => convertCanvasSelectedBox("mask"));
  btnCanvasBoxConvertRestore.addEventListener("click", () => convertCanvasSelectedBox("restore"));
  btnOpenCanvasPropertiesTab.addEventListener("click", () => setCanvasPropertiesCollapsed(false));
  btnCanvasClear.addEventListener("click", () => btnClear.click());
  btnCanvasApply.addEventListener("click", async () => {
    await applyPendingManualBoxes("수동마스킹실행");
  });
  btnCanvasFinalSave.addEventListener("click", async () => {
    await saveFinalOutput();
  });
  customKeywordsEl.addEventListener("input", renderCanvasFinalSaveSummary);
  customKeywordsEl.addEventListener("input", renderFinalSaveConfirmation);
  toggleOriginalCompareEl.addEventListener("change", updateOriginalCompareVisibility);

  btnUndo.addEventListener("click", () => {
    undoLastCanvasBox();
  });

  btnClear.addEventListener("click", () => {
    void clearDerivedArtifacts();
  });

  btnManualApply.addEventListener("click", async () => {
    await applyPendingManualBoxes("수동마스킹실행");
  });

  modeMask.addEventListener("change", () => {
    if (modeMask.checked) setActiveCanvasTool("mask");
  });
  modeRestore.addEventListener("change", () => {
    if (modeRestore.checked) setActiveCanvasTool("restore");
  });
}
