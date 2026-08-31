import { workspaceCanvasMount } from "../features/canvas-workbench/workspaceRuntime";

export function bindApplicationDom() {
  const $ = <T extends Element>(sel: string) => document.querySelector<T>(sel)!;
  const canvasMount = workspaceCanvasMount();

  const statusEl = $("#status") as HTMLDivElement;
  const statusDetailEl = $("#status-detail") as HTMLDivElement;
  const inputPathEl = $("#input-path") as HTMLInputElement;
  const pageInfoOrigEl = $("#viewer-meta-orig") as HTMLSpanElement;
  const pageInfoResultEl = $("#viewer-meta-result") as HTMLSpanElement;
  const pagerLabelEl = $(".dm-canvas__pager-label") as HTMLSpanElement;
  const zoomInfoEl = $("#zoom-info") as HTMLSpanElement;

  const {
    origCanvas,
    resultCanvas,
    overlay,
    origWrap,
    resultWrap,
    pdfCompareView,
    textCompareView,
    extractedTextView,
    maskedTextView,
  } = canvasMount;

  const origCtx = origCanvas.getContext("2d")!;
  const resultCtx = resultCanvas.getContext("2d")!;
  const octx = overlay.getContext("2d")!;

  const btnPickPdf = $("#btn-pick-pdf") as HTMLButtonElement;
  const btnRunMasking = $("#btn-run-masking") as HTMLButtonElement;
  const btnPrevOrig = $("#btn-prev-orig") as HTMLButtonElement;
  const btnNextOrig = $("#btn-next-orig") as HTMLButtonElement;
  const btnSave = $("#btn-save") as HTMLButtonElement;
  const finalSaveReadinessEl = $("#final-save-readiness") as HTMLDivElement;
  const newDocumentDialogEl = $("#new-document-dialog") as HTMLElement;
  const btnCloseNewDocumentDialog = $("#btn-close-new-document-dialog") as HTMLButtonElement;
  const btnCancelNewDocument = $("#btn-cancel-new-document") as HTMLButtonElement;
  const btnConfirmNewDocument = $("#btn-confirm-new-document") as HTMLButtonElement;
  const compareModePdf = $("#compare-mode-pdf") as HTMLButtonElement;
  const compareModeText = $("#compare-mode-text") as HTMLButtonElement;
  const toggleOriginalCompareEl = $("#toggle-original-compare") as HTMLInputElement;
  const originalComparePanelEl = $("#original-compare-panel") as HTMLDivElement;


  const syncPagesEl = $("#sync-pages") as HTMLInputElement;
  const finalStateCardEl = $("#final-state-card") as HTMLDivElement;
  const finalStateTitleEl = $("#final-state-title") as HTMLElement;
  const finalStateDetailEl = $("#final-state-detail") as HTMLParagraphElement;
  const workspaceShellEl = $("#workspace-shell") as HTMLElement;
  const documentsScreenEl = document.querySelector<HTMLElement>('[data-screen-panel="documents"]')!;
  const btnPickBatch = $("#btn-pick-batch") as HTMLButtonElement;
  const btnRunBatch = $("#btn-run-batch") as HTMLButtonElement;
  const batchSummaryEl = $("#batch-summary") as HTMLDivElement;
  const batchQueueEl = $("#batch-queue") as HTMLDivElement;
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
  const btnNewDocument = $("#btn-new-document") as HTMLButtonElement;
  const btnCanvasBoxDelete = $("#btn-canvas-box-delete") as HTMLButtonElement;
  const canvasActiveToolLabelEl = $("#canvas-active-tool-label") as HTMLElement;
  const canvasToolReadinessEl = $("#canvas-tool-readiness") as HTMLDivElement;
  const canvasBoxListEl = $("#canvas-box-list") as HTMLDivElement;
  const canvasBoxPropertiesEl = $("#canvas-box-properties") as HTMLDivElement;
  const canvasBoxPropertyPageEl = $("#canvas-box-property-page") as HTMLElement;
  const canvasBoxPropertyTypeEl = $("#canvas-box-property-type") as HTMLElement;
  const canvasBoxPropertyCoordinatesEl = $("#canvas-box-property-coordinates") as HTMLElement;
  const canvasBoxPropertySizeEl = $("#canvas-box-property-size") as HTMLElement;
  const btnCanvasBoxConvertMask = $("#btn-canvas-box-convert-mask") as HTMLButtonElement;
  const btnCanvasBoxConvertRestore = $("#btn-canvas-box-convert-restore") as HTMLButtonElement;
  const finalSaveDialogEl = $("#final-save-dialog") as HTMLElement;
  const maskingProgressModalEl = $("#masking-progress-dialog") as HTMLElement;
  const maskingProgressValueEl = $("#masking-progress-value") as HTMLProgressElement;
  const maskingProgressPercentEl = $("#masking-progress-percent") as HTMLElement;
  const maskingProgressStageEl = $("#masking-progress-stage") as HTMLElement;
  const btnCloseMaskingProgress = $("#btn-close-masking-progress-dialog") as HTMLButtonElement;
  const btnCancelMaskingProgress = $("#btn-cancel-masking-progress") as HTMLButtonElement;
  const canvasEditorToolButtons = [btnCanvasToolSelect, btnCanvasToolMask, btnCanvasToolRestore, btnCanvasToolPan, btnCanvasToolDelete];

  return {
    $,
    statusEl, statusDetailEl, inputPathEl, pageInfoOrigEl, pageInfoResultEl, pagerLabelEl,
    zoomInfoEl,
    origCanvas, resultCanvas, overlay, origWrap, resultWrap, pdfCompareView, textCompareView, extractedTextView, maskedTextView,
    origCtx, resultCtx, octx,
    btnPickPdf, btnRunMasking, btnPrevOrig, btnNextOrig,
    btnSave, finalSaveReadinessEl,
    newDocumentDialogEl, btnCloseNewDocumentDialog,
    btnCancelNewDocument, btnConfirmNewDocument,
    compareModePdf, compareModeText, toggleOriginalCompareEl, originalComparePanelEl,
    syncPagesEl, finalStateCardEl, finalStateTitleEl, finalStateDetailEl, workspaceShellEl, documentsScreenEl,
    btnPickBatch, btnRunBatch, batchSummaryEl, batchQueueEl,
    btnCanvasToolSelect, btnCanvasToolMask,
    btnCanvasToolRestore, btnCanvasToolPan, btnCanvasToolDelete, btnCanvasZoomOut, btnCanvasZoomIn, btnCanvasUndo,
    btnCanvasClear, btnCanvasApply, btnNewDocument, btnCanvasBoxDelete,
    canvasActiveToolLabelEl, canvasToolReadinessEl, canvasBoxListEl, canvasBoxPropertiesEl, canvasBoxPropertyPageEl,
    canvasBoxPropertyTypeEl, canvasBoxPropertyCoordinatesEl, canvasBoxPropertySizeEl, btnCanvasBoxConvertMask,
    btnCanvasBoxConvertRestore, finalSaveDialogEl, maskingProgressModalEl, maskingProgressValueEl, maskingProgressPercentEl,
    maskingProgressStageEl, btnCloseMaskingProgress, btnCancelMaskingProgress,
    canvasEditorToolButtons,
  };
}

export type ApplicationDomBindings = ReturnType<typeof bindApplicationDom>;
