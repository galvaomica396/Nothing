export function bindLegacyDom() {
  const $ = <T extends Element>(sel: string) => document.querySelector<T>(sel)!;

  const statusEl = $("#status") as HTMLDivElement;
  const statusDetailEl = $("#status-detail") as HTMLDivElement;
  const inputPathEl = $("#input-path") as HTMLInputElement;
  const pageInfoOrigEl = $("#page-info-orig") as HTMLSpanElement;
  const pageInfoResultEl = $("#page-info-result") as HTMLSpanElement;
  const boxInfoEl = $("#box-info") as HTMLSpanElement;
  const viewerMetaOrigEl = $("#viewer-meta-orig") as HTMLSpanElement;
  const viewerMetaResultEl = $("#viewer-meta-result") as HTMLSpanElement;
  const zoomInfoEl = $("#zoom-info") as HTMLSpanElement;

  const origCanvas = $("#pdf-canvas-orig") as HTMLCanvasElement;
  const resultCanvas = $("#pdf-canvas-result") as HTMLCanvasElement;
  const overlay = $("#overlay-canvas-result") as HTMLCanvasElement;
  const origWrap = $("#canvas-wrap-orig") as HTMLDivElement;
  const resultWrap = $("#canvas-wrap-result") as HTMLDivElement;
  const pdfCompareView = $("#pdf-compare-view") as HTMLDivElement;
  const textCompareView = $("#text-compare-view") as HTMLDivElement;
  const extractedTextView = $("#extracted-text-view") as HTMLPreElement;
  const maskedTextView = $("#masked-text-view") as HTMLPreElement;

  const origCtx = origCanvas.getContext("2d")!;
  const resultCtx = resultCanvas.getContext("2d")!;
  const octx = overlay.getContext("2d")!;

  const btnPickPdf = $("#btn-pick-pdf") as HTMLButtonElement;
  const btnCanvasHeroBatch = document.querySelector<HTMLButtonElement>("#btn-canvas-hero-batch");
  const btnRunMasking = $("#btn-run-masking") as HTMLButtonElement;
  const btnPrevOrig = $("#btn-prev-orig") as HTMLButtonElement;
  const btnNextOrig = $("#btn-next-orig") as HTMLButtonElement;
  const btnPrevResult = $("#btn-prev-result") as HTMLButtonElement;
  const btnNextResult = $("#btn-next-result") as HTMLButtonElement;
  const btnUndo = $("#btn-undo") as HTMLButtonElement;
  const btnClear = $("#btn-clear") as HTMLButtonElement;
  const btnManualApply = $("#btn-manual-apply") as HTMLButtonElement;
  const btnSave = $("#btn-save") as HTMLButtonElement;
  const finalSaveReadinessEl = $("#final-save-readiness") as HTMLDivElement;
  const btnOpenKeywordDialog = $("#btn-open-keyword-dialog") as HTMLButtonElement;
  const keywordDialogEl = $("#keyword-dialog") as HTMLElement;
  const keywordDialogChipListEl = $("#keyword-dialog-chip-list") as HTMLElement;
  const btnCloseKeywordDialog = $("#btn-close-keyword-dialog") as HTMLButtonElement;
  const btnKeywordPolicy = $("#btn-keyword-policy") as HTMLButtonElement;
  const btnKeywordDialogApply = $("#btn-keyword-dialog-apply") as HTMLButtonElement;
  const newDocumentDialogEl = $("#new-document-dialog") as HTMLElement;
  const btnCloseNewDocumentDialog = $("#btn-close-new-document-dialog") as HTMLButtonElement;
  const btnCancelNewDocument = $("#btn-cancel-new-document") as HTMLButtonElement;
  const btnConfirmNewDocument = $("#btn-confirm-new-document") as HTMLButtonElement;
  const compareModePdf = $("#compare-mode-pdf") as HTMLButtonElement;
  const compareModeText = $("#compare-mode-text") as HTMLButtonElement;
  const toggleOriginalCompareEl = $("#toggle-original-compare") as HTMLInputElement;
  const originalComparePanelEl = $("#original-compare-panel") as HTMLDivElement;

  const modeMask = $("#mode-mask") as HTMLInputElement;
  const modeRestore = $("#mode-restore") as HTMLInputElement;

  const profileEl = $("#profile") as HTMLSelectElement;
  const engineEl = $("#engine") as HTMLSelectElement;
  const displayModeEl = $("#display-mode") as HTMLSelectElement;
  const deidentificationPolicyEl = $("#deidentification-policy") as HTMLSelectElement;
  const regionScopeEl = $("#region-scope") as HTMLSelectElement;
  const customRegionsEl = $("#custom-regions") as HTMLInputElement;
  const customKeywordsEl = $("#custom-keywords") as HTMLTextAreaElement;
  const syncPagesEl = $("#sync-pages") as HTMLInputElement;
  const finalStateCardEl = $("#final-state-card") as HTMLDivElement;
  const finalStateTitleEl = $("#final-state-title") as HTMLElement;
  const finalStateDetailEl = $("#final-state-detail") as HTMLParagraphElement;
  const workspaceShellEl = $("#workspace-shell") as HTMLElement;
  // v4 P2: 문서/캔버스 통합 후 통합 화면 루트는 #canvas-workspace-screen 이다
  // (구 #documents-screen 은 삭제됨). 검토 레일 접기 등 화면 루트 토글에 사용한다.
  const documentsScreenEl = $("#canvas-workspace-screen") as HTMLElement;
  const reviewInspectorEl = $("#side-panel") as HTMLElement;
  const btnToggleInspector = $("#btn-toggle-inspector") as HTMLButtonElement;
  const btnPickBatch = $("#btn-pick-batch") as HTMLButtonElement;
  const btnRunBatch = $("#btn-run-batch") as HTMLButtonElement;
  const batchSummaryEl = $("#batch-summary") as HTMLDivElement;
  const batchQueueEl = $("#batch-queue") as HTMLDivElement;
  const btnMaskCanvas = $("#btn-mask-canvas") as HTMLButtonElement;
  const btnOpenCanvasWindow = $("#btn-open-canvas-window") as HTMLButtonElement;
  const btnCloseCanvas = $("#btn-close-canvas") as HTMLButtonElement;
  const btnCollapseCanvasTools = $("#btn-collapse-canvas-tools") as HTMLButtonElement;
  const btnCollapseCanvasProperties = $("#btn-collapse-canvas-properties") as HTMLButtonElement;
  const btnExpandCanvasPanels = $("#btn-expand-canvas-panels") as HTMLButtonElement;
  const btnCanvasLoadPdf = $("#btn-canvas-load-pdf") as HTMLButtonElement;
  const btnCanvasToolSelect = $("#btn-canvas-tool-select") as HTMLButtonElement;
  const btnCanvasToolMask = $("#btn-canvas-tool-mask") as HTMLButtonElement;
  const btnCanvasToolRestore = $("#btn-canvas-tool-restore") as HTMLButtonElement;
  const btnCanvasToolPan = $("#btn-canvas-tool-pan") as HTMLButtonElement;
  const btnCanvasToolDelete = $("#btn-canvas-tool-delete") as HTMLButtonElement;
  const btnCanvasZoomOut = $("#btn-canvas-zoom-out") as HTMLButtonElement;
  const btnCanvasZoomIn = $("#btn-canvas-zoom-in") as HTMLButtonElement;
  const btnCanvasUndo = $("#btn-canvas-undo") as HTMLButtonElement;
  const btnCanvasClear = $("#btn-canvas-clear") as HTMLButtonElement;
  const btnCanvasApply = $("#btn-canvas-apply") as HTMLButtonElement;
  const btnCanvasFinalSave = $("#btn-canvas-final-save") as HTMLButtonElement;
  const btnNewDocument = $("#btn-new-document") as HTMLButtonElement;
  const btnCanvasDeleteBox = $("#btn-canvas-delete-box") as HTMLButtonElement;
  const btnCanvasBoxDelete = $("#btn-canvas-box-delete") as HTMLButtonElement;
  const canvasModeStatusEl = $("#canvas-mode-status") as HTMLDivElement;
  const canvasActiveToolLabelEl = $("#canvas-active-tool-label") as HTMLElement;
  const canvasToolReadinessEl = $("#canvas-tool-readiness") as HTMLDivElement;
  const canvasBoxListEl = $("#canvas-box-list") as HTMLDivElement;
  const canvasBoxPropertiesEl = $("#canvas-box-properties") as HTMLDivElement;
  const canvasBoxPropertyPageEl = $("#canvas-box-property-page") as HTMLElement;
  const canvasBoxPropertyTypeEl = $("#canvas-box-property-type") as HTMLElement;
  const canvasBoxPropertyCoordinatesEl = $("#canvas-box-property-coordinates") as HTMLElement;
  const canvasBoxPropertySizeEl = $("#canvas-box-property-size") as HTMLElement;
  const canvasSummaryMaskCountEl = $("#canvas-summary-mask-count") as HTMLElement;
  const canvasSummaryRestoreCountEl = $("#canvas-summary-restore-count") as HTMLElement;
  const canvasSummaryKeywordCountEl = $("#canvas-summary-keyword-count") as HTMLElement;
  const canvasSummaryOutputStateEl = $("#canvas-summary-output-state") as HTMLElement;
  const btnCanvasBoxConvertMask = $("#btn-canvas-box-convert-mask") as HTMLButtonElement;
  const btnCanvasBoxConvertRestore = $("#btn-canvas-box-convert-restore") as HTMLButtonElement;
  const btnOpenCanvasPropertiesTab = $("#btn-open-canvas-properties-tab") as HTMLButtonElement;
  const finalSaveDialogEl = $("#final-save-dialog") as HTMLElement;
  const finalSaveDialogStateEl = $("#final-save-dialog-state") as HTMLElement;
  const finalSaveWarningListEl = $("#final-save-warning-list") as HTMLElement;
  const btnCloseFinalSaveDialog = $("#btn-close-final-save-dialog") as HTMLButtonElement;
  const btnDialogCancelSave = $("#btn-dialog-cancel-save") as HTMLButtonElement;
  const btnDialogSaveAll = $("#btn-dialog-save-all") as HTMLButtonElement;
  const reviewSummaryMaskCountEl = $("#review-summary-mask-count") as HTMLElement;
  const reviewSummaryRestoreCountEl = $("#review-summary-restore-count") as HTMLElement;
  const reviewSummaryKeywordCountEl = $("#review-summary-keyword-count") as HTMLElement;
  const reviewSummaryOutputFileEl = $("#review-summary-output-file") as HTMLElement;
  const reviewSummaryPdfPolicyEl = $("#review-summary-pdf-policy") as HTMLElement;
  const reviewSummaryTxtPolicyEl = $("#review-summary-txt-policy") as HTMLElement;
  const settingsApplyScopeStatusEl = $("#settings-apply-scope-status") as HTMLElement;
  const btnSettingsSave = $("#btn-app-settings-save") as HTMLButtonElement;
  const btnSettingsReset = $("#btn-app-settings-reset") as HTMLButtonElement;
  const btnSettingsClose = $("#btn-settings-close") as HTMLButtonElement;
  const btnSettingsBack = $("#btn-settings-back") as HTMLButtonElement;
  const btnMaskingSettingsBack = $("#btn-masking-settings-back") as HTMLButtonElement;
  const btnSettingsFooterClose = $("#btn-app-settings-close") as HTMLButtonElement;
  const btnMaskingSettingsCancel = $("#btn-masking-settings-cancel") as HTMLButtonElement;
  const btnMaskingSettingsPreview = $("#btn-masking-settings-preview") as HTMLButtonElement;
  const btnMaskingSettingsApply = $("#btn-masking-settings-apply") as HTMLButtonElement;
  const settingsTabButtons = Array.from(document.querySelectorAll<HTMLButtonElement>("[data-settings-tab]"));
  const settingsPanels = Array.from(document.querySelectorAll<HTMLElement>("[data-settings-panel]"));
  const settingsThemeInputs = Array.from(document.querySelectorAll<HTMLInputElement>('input[name="settings-theme"]'));
  const settingsExportMaskedTextEl = $("#settings-export-masked-text") as HTMLInputElement;
  const optPdfRedactionEl = $("#opt-pdf-redaction") as HTMLInputElement;
  const settingsOpenOutputAfterSaveEl = $("#settings-open-output-after-save") as HTMLInputElement;
  const appScreenButtons = Array.from(document.querySelectorAll<HTMLButtonElement>("[data-screen-target]"));
  const appScreens = Array.from(document.querySelectorAll<HTMLElement>("[data-screen-panel]"));
  const canvasEditorToolButtons = [btnCanvasToolSelect, btnCanvasToolMask, btnCanvasToolRestore, btnCanvasToolPan, btnCanvasToolDelete];

  return {
    $,
    statusEl, statusDetailEl, inputPathEl, pageInfoOrigEl, pageInfoResultEl, boxInfoEl,
    viewerMetaOrigEl, viewerMetaResultEl, zoomInfoEl,
    origCanvas, resultCanvas, overlay, origWrap, resultWrap, pdfCompareView, textCompareView, extractedTextView, maskedTextView,
    origCtx, resultCtx, octx,
    btnPickPdf, btnCanvasHeroBatch, btnRunMasking, btnPrevOrig, btnNextOrig,
    btnPrevResult, btnNextResult, btnUndo, btnClear, btnManualApply, btnSave, finalSaveReadinessEl,
    btnOpenKeywordDialog, keywordDialogEl, keywordDialogChipListEl, btnCloseKeywordDialog,
    btnKeywordPolicy, btnKeywordDialogApply, newDocumentDialogEl, btnCloseNewDocumentDialog,
    btnCancelNewDocument, btnConfirmNewDocument,
    compareModePdf, compareModeText, toggleOriginalCompareEl, originalComparePanelEl,
    modeMask, modeRestore,
    profileEl, engineEl, displayModeEl, deidentificationPolicyEl, regionScopeEl, customRegionsEl, customKeywordsEl,
    syncPagesEl, finalStateCardEl, finalStateTitleEl, finalStateDetailEl, workspaceShellEl, documentsScreenEl,
    reviewInspectorEl, btnToggleInspector, btnPickBatch, btnRunBatch, batchSummaryEl, batchQueueEl, btnMaskCanvas,
    btnOpenCanvasWindow, btnCloseCanvas, btnCollapseCanvasTools, btnCollapseCanvasProperties, btnExpandCanvasPanels,
    btnCanvasLoadPdf, btnCanvasToolSelect, btnCanvasToolMask,
    btnCanvasToolRestore, btnCanvasToolPan, btnCanvasToolDelete, btnCanvasZoomOut, btnCanvasZoomIn, btnCanvasUndo,
    btnCanvasClear, btnCanvasApply, btnCanvasFinalSave, btnNewDocument, btnCanvasDeleteBox, btnCanvasBoxDelete, canvasModeStatusEl,
    canvasActiveToolLabelEl, canvasToolReadinessEl, canvasBoxListEl, canvasBoxPropertiesEl, canvasBoxPropertyPageEl,
    canvasBoxPropertyTypeEl, canvasBoxPropertyCoordinatesEl, canvasBoxPropertySizeEl, canvasSummaryMaskCountEl,
    canvasSummaryRestoreCountEl, canvasSummaryKeywordCountEl, canvasSummaryOutputStateEl, btnCanvasBoxConvertMask,
    btnCanvasBoxConvertRestore, btnOpenCanvasPropertiesTab, finalSaveDialogEl, finalSaveDialogStateEl,
    finalSaveWarningListEl, btnCloseFinalSaveDialog, btnDialogCancelSave,
    btnDialogSaveAll, reviewSummaryMaskCountEl, reviewSummaryRestoreCountEl, reviewSummaryKeywordCountEl,
    reviewSummaryOutputFileEl, reviewSummaryPdfPolicyEl, reviewSummaryTxtPolicyEl,
    settingsApplyScopeStatusEl, btnSettingsSave, btnSettingsReset,
    btnSettingsClose, btnSettingsBack, btnMaskingSettingsBack, btnSettingsFooterClose, btnMaskingSettingsCancel,
    btnMaskingSettingsPreview, btnMaskingSettingsApply, settingsTabButtons, settingsPanels, settingsThemeInputs,
    settingsExportMaskedTextEl, optPdfRedactionEl, settingsOpenOutputAfterSaveEl, appScreenButtons, appScreens,
    canvasEditorToolButtons,
  };
}

export type LegacyDomBindings = ReturnType<typeof bindLegacyDom>;
