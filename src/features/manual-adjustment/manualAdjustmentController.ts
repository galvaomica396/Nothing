import { canvasBoxActionState, canvasZoomActionState, createCanvasBoxRows, deleteCanvasBoxAtIndex } from "../../canvasWorkbench";
import { canvasFinalSaveSummary, canvasToolReadinessText } from "../../canvasToolUx";
import { adoptManualPreview, hasMaskedArtifact, resultSourcePath } from "../../state/documentProvenance";
import type { LegacySessionState } from "../../legacy/startLegacyApp";
import { parseSafeReport } from "../../state/maskingSession";
import type { SafeReport } from "../../state/maskingSession";
import { canvasEntryReadiness } from "../../workflowFlow";
import type { BoxMode } from "../document-session/documentSessionController";

export type CanvasEditorTool = "select" | "mask" | "restore" | "pan" | "delete";

export type BoxItem = {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  mode: BoxMode;
  tag?: string;
};

export type ApplyResult = {
  status?: string;
  output_file: string;
  mask_count: number;
  restore_count: number;
  applied_count?: number;
  excluded_count?: number;
  mask_boxes_applied?: number;
  unmask_boxes_applied?: number;
  skipped_boxes?: number;
  warnings?: string[];
  requires_revalidation?: boolean;
  display_mode?: string;
  revalidation_report?: string;
  revalidation_status?: string;
};

type InvokeCommand = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type ManualAdjustmentState = Pick<
  LegacySessionState,
  | "documentProvenance"
  | "outputDir"
  | "currentResultPage"
  | "resultDoc"
  | "scale"
  | "boxes"
  | "documentEditRevision"
  | "mode"
  | "selectedCanvasBoxIndex"
  | "canvasMode"
  | "maskingRunning"
  | "batchRunning"
  | "savingInFlight"
  | "extractedText"
  | "maskedText"
  | "baseExtractedText"
  | "baseMaskedText"
  | "preManualPreviewPdf"
  | "preManualExtractedText"
  | "preManualMaskedText"
  | "latestReportPath"
  | "latestReport"
  | "latestMaskedPath"
  | "latestMaskedTextPolicy"
  | "lastPreviewDiagnostics"
  | "restoreRevalidationFailed"
>;

export type ManualAdjustmentDeps = {
  readonly state: ManualAdjustmentState;
  readonly invokeCommand: InvokeCommand;
  readonly displayModeEl: HTMLSelectElement;
  readonly customKeywordsEl: HTMLTextAreaElement;
  readonly modeMask: HTMLInputElement;
  readonly modeRestore: HTMLInputElement;
  readonly workspaceShellEl: HTMLElement;
  readonly overlay: HTMLCanvasElement;
  readonly canvasEditorToolButtons: readonly HTMLButtonElement[];
  readonly canvasActiveToolLabelEl: HTMLElement;
  readonly canvasToolReadinessEl: HTMLDivElement;
  readonly canvasBoxListEl: HTMLDivElement;
  readonly canvasBoxPropertiesEl: HTMLDivElement;
  readonly canvasBoxPropertyPageEl: HTMLElement;
  readonly canvasBoxPropertyTypeEl: HTMLElement;
  readonly canvasBoxPropertyCoordinatesEl: HTMLElement;
  readonly canvasBoxPropertySizeEl: HTMLElement;
  readonly canvasSummaryMaskCountEl: HTMLElement;
  readonly canvasSummaryRestoreCountEl: HTMLElement;
  readonly canvasSummaryKeywordCountEl: HTMLElement;
  readonly canvasSummaryOutputStateEl: HTMLElement;
  readonly canvasModeStatusEl: HTMLDivElement;
  readonly btnCanvasZoomOut: HTMLButtonElement;
  readonly btnCanvasZoomIn: HTMLButtonElement;
  readonly btnCanvasUndo: HTMLButtonElement;
  readonly btnCanvasDeleteBox: HTMLButtonElement;
  readonly btnCanvasClear: HTMLButtonElement;
  readonly btnCanvasBoxDelete: HTMLButtonElement;
  readonly btnCanvasBoxConvertMask: HTMLButtonElement;
  readonly btnCanvasBoxConvertRestore: HTMLButtonElement;
  readonly btnMaskCanvas: HTMLButtonElement;
  readonly isStandaloneCanvasWindow: boolean;
  readonly isPdfInput: () => boolean;
  readonly currentFinalDocumentPath: () => string;
  readonly getActiveCanvasTool: () => CanvasEditorTool;
  readonly setActiveCanvasToolState: (tool: CanvasEditorTool) => void;
  readonly ensurePreviewWorkDir: () => Promise<string>;
  readonly loadResultPdf: (path: string, fallbackPath?: string, isCurrent?: () => boolean) => Promise<void>;
  readonly redrawOverlay: () => void;
  readonly updateMeta: () => void;
  readonly renderFinalState: (report: SafeReport | null) => void;
  readonly setTextCompareContents: (extractedText: string, maskedText: string) => void;
  readonly updateWorkflowReadiness: () => void;
  readonly updateStatusDetail: () => void;
  readonly setStatus: (message: string) => void;
};

export type ManualAdjustmentController = {
  readonly applyPendingManualBoxes: (statusLabel: string) => Promise<ApplyResult | null>;
  readonly renderCanvasBoxList: () => void;
  readonly renderCanvasBoxProperties: () => void;
  readonly renderCanvasFinalSaveSummary: () => void;
  readonly updateCanvasControls: () => void;
  readonly setActiveCanvasTool: (tool: CanvasEditorTool) => void;
  readonly convertCanvasSelectedBox: (mode: BoxMode) => void;
  readonly deleteSelectedCanvasBox: () => void;
  readonly undoLastCanvasBox: () => void;
  readonly setCanvasMode: (active: boolean, options?: { allowEmptyCanvas?: boolean }) => void;
};

export function createManualAdjustmentController(deps: ManualAdjustmentDeps): ManualAdjustmentController {
  const { state } = deps;

  function currentCanvasPageIndex(): number {
    return Math.max(0, (state.currentResultPage || 1) - 1);
  }
  function editsLocked(): boolean {
    return state.maskingRunning || state.batchRunning || state.savingInFlight;
  }

  function recordDocumentEdit(): void {
    state.documentEditRevision = (state.documentEditRevision || 0) + 1;
  }

  function normalizeSelectedCanvasBox(): void {
    const selected = state.boxes[state.selectedCanvasBoxIndex];
    if (!selected || selected.page !== currentCanvasPageIndex()) state.selectedCanvasBoxIndex = -1;
  }

  function renderCanvasBoxList(): void {
    normalizeSelectedCanvasBox();
    const rows = createCanvasBoxRows(state.boxes, currentCanvasPageIndex(), state.selectedCanvasBoxIndex);
    deps.canvasBoxListEl.replaceChildren();
    if (rows.length === 0) {
      const empty = document.createElement("div");
      empty.className = "canvas-box-empty";
      empty.textContent = "현재 페이지에 박스가 없습니다.";
      deps.canvasBoxListEl.appendChild(empty);
      return;
    }
    for (const row of rows) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = row.label;
      button.classList.toggle("is-active", row.selected);
      button.setAttribute("aria-pressed", String(row.selected));
      button.addEventListener("click", () => {
        state.selectedCanvasBoxIndex = row.globalIndex;
        renderCanvasBoxList();
        updateCanvasControls();
      });
      deps.canvasBoxListEl.appendChild(button);
    }
  }

  function selectedCanvasBox(): BoxItem | null {
    normalizeSelectedCanvasBox();
    return state.boxes[state.selectedCanvasBoxIndex] || null;
  }

  function boxModeLabel(mode: BoxMode): string {
    return mode === "mask" ? "마스킹 박스" : "복원 박스";
  }

  function renderCanvasBoxProperties(): void {
    const box = selectedCanvasBox();
    const canvasBoxAccordionEl = deps.canvasBoxPropertiesEl.closest<HTMLDetailsElement>("#canvas-box-accordion");
    deps.canvasBoxPropertiesEl.classList.toggle("is-empty", !box);
    deps.btnCanvasBoxDelete.disabled = editsLocked() || !box;
    deps.btnCanvasBoxConvertMask.disabled = editsLocked() || !box || box.mode === "mask";
    deps.btnCanvasBoxConvertRestore.disabled = editsLocked() || !box || box.mode === "restore";
    if (!box) {
      deps.canvasBoxPropertyPageEl.textContent = "-";
      deps.canvasBoxPropertyTypeEl.textContent = "-";
      deps.canvasBoxPropertyCoordinatesEl.textContent = "-";
      deps.canvasBoxPropertySizeEl.textContent = "-";
      return;
    }
    if (canvasBoxAccordionEl) {
      canvasBoxAccordionEl.open = true;
      canvasBoxAccordionEl.querySelector<HTMLElement>("summary")?.setAttribute("aria-expanded", "true");
    }
    const width = Math.abs(box.x1 - box.x0);
    const height = Math.abs(box.y1 - box.y0);
    deps.canvasBoxPropertyPageEl.textContent = `${box.page + 1}페이지`;
    deps.canvasBoxPropertyTypeEl.textContent = boxModeLabel(box.mode);
    deps.canvasBoxPropertyCoordinatesEl.textContent = `${box.x0.toFixed(1)}, ${box.y0.toFixed(1)} → ${box.x1.toFixed(1)}, ${box.y1.toFixed(1)}`;
    deps.canvasBoxPropertySizeEl.textContent = `${width.toFixed(1)} × ${height.toFixed(1)}`;
  }

  function convertCanvasSelectedBox(mode: BoxMode): void {
    if (editsLocked()) {
      deps.setStatus("실행 중에는 보정 박스를 변경할 수 없습니다.");
      return;
    }
    const box = selectedCanvasBox();
    if (!box) return;
    box.mode = mode;
    recordDocumentEdit();
    deps.setActiveCanvasToolState(mode);
    state.mode = mode;
    renderCanvasBoxList();
    deps.redrawOverlay();
    updateCanvasControls();
    deps.setStatus(mode === "mask" ? "선택 박스를 마스킹으로 전환했습니다." : "선택 박스를 복원으로 전환했습니다.");
  }

  function renderCanvasFinalSaveSummary(): void {
    const summary = canvasFinalSaveSummary({
      maskBoxes: state.boxes.filter((box) => box.mode === "mask").length,
      restoreBoxes: state.boxes.filter((box) => box.mode === "restore").length,
      keywords: deps.customKeywordsEl.value,
      hasFinalDocument: hasMaskedArtifact(state.documentProvenance),
    });
    deps.canvasSummaryMaskCountEl.textContent = summary.maskLabel;
    deps.canvasSummaryRestoreCountEl.textContent = summary.restoreLabel;
    deps.canvasSummaryKeywordCountEl.textContent = summary.keywordLabel;
    deps.canvasSummaryOutputStateEl.textContent = summary.saveLabel;
  }

  function setActiveCanvasTool(tool: CanvasEditorTool): void {
    if (editsLocked()) {
      deps.setStatus("실행 중에는 캔버스 도구를 변경할 수 없습니다.");
      return;
    }
    deps.setActiveCanvasToolState(tool);
    if (tool === "mask") {
      deps.modeMask.checked = true;
      state.mode = "mask";
    }
    if (tool === "restore") {
      deps.modeRestore.checked = true;
      state.mode = "restore";
    }
    updateCanvasControls();
  }

  function syncCanvasToolPalette(canEdit: boolean, editReason: string): void {
    let activeTool = deps.getActiveCanvasTool();
    if ((activeTool === "mask" && state.mode !== "mask") || (activeTool === "restore" && state.mode !== "restore")) {
      activeTool = state.mode;
      deps.setActiveCanvasToolState(activeTool);
    }
    const activeLabel = activeTool === "select" ? "선택" : activeTool === "pan" ? "이동" : activeTool === "delete" ? "삭제" : activeTool === "mask" ? "마스킹" : "복원";
    deps.canvasActiveToolLabelEl.textContent = activeLabel;
    deps.overlay.dataset.tool = activeTool;
    for (const button of deps.canvasEditorToolButtons) {
      const tool = button.dataset.canvasTool as CanvasEditorTool | undefined;
      const isActive = tool === activeTool;
      const needsEditablePdf = tool !== "select" && tool !== "pan";
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-checked", String(isActive));
      button.setAttribute("aria-pressed", String(isActive));
      button.disabled = needsEditablePdf && (editsLocked() || (!canEdit && !deps.isStandaloneCanvasWindow));
      button.title = button.disabled ? editReason : activeLabel;
    }
  }

  function canvasStatusText(): string {
    const windowMode = deps.isStandaloneCanvasWindow ? "독립 창" : "기본 창";
    const tool = state.mode === "mask" ? "마스킹" : "복원";
    const target = state.documentProvenance.original.path ? "PDF 준비" : "PDF 불러오기 필요";
    if (!deps.isStandaloneCanvasWindow && !state.canvasMode) {
      return `작업창 대기: 대상 ${target} · 별도 창에서 마스킹/복원 박스를 그립니다.`;
    }
    const readiness = state.resultDoc ? "수정본에 박스를 그릴 수 있습니다." : "PDF를 불러오면 박스를 그릴 수 있습니다.";
    const phase = state.canvasMode ? "캔버스 작업 중" : "캔버스 대기";
    return `${phase}: 대상 ${target} · 현재 도구 ${tool} · 박스 ${state.boxes.length}개 · 창 모드 ${windowMode} · ${readiness}`;
  }

  function updateCanvasControls(): void {
    const pdfEditable = deps.isPdfInput() && Boolean(state.resultDoc);
    const busy = state.maskingRunning || state.batchRunning || state.savingInFlight;
    normalizeSelectedCanvasBox();
    const boxActions = canvasBoxActionState({
      boxes: state.boxes,
      currentPage: currentCanvasPageIndex(),
      selectedBoxIndex: state.selectedCanvasBoxIndex,
      hasResultDoc: Boolean(state.resultDoc),
    });
    const zoomActions = canvasZoomActionState(state.scale);
    const readiness = canvasToolReadinessText({
      hasPdf: deps.isPdfInput(),
      hasResultDoc: Boolean(state.resultDoc),
      hasFinalDocument: Boolean(deps.currentFinalDocumentPath()),
    });
    deps.btnCanvasZoomOut.disabled = !zoomActions.canZoomOut;
    deps.btnCanvasZoomIn.disabled = !zoomActions.canZoomIn;
    deps.btnCanvasUndo.disabled = busy || !pdfEditable || state.boxes.length === 0;
    deps.btnCanvasDeleteBox.disabled = busy || !boxActions.canDeleteSelected;
    deps.btnCanvasClear.disabled = !state.documentProvenance.original.path || busy;
    deps.btnMaskCanvas.disabled = busy;
    deps.btnCanvasBoxDelete.disabled = busy || !boxActions.canDeleteSelected;
    const editReason = busy ? "실행 중에는 보정 박스를 변경할 수 없습니다." : readiness.editReason;
    deps.canvasToolReadinessEl.textContent = readiness.canEdit && !busy ? readiness.saveReason : `${editReason} ${readiness.saveReason}`;
    syncCanvasToolPalette(readiness.canEdit && !busy, editReason);
    renderCanvasBoxProperties();
    renderCanvasFinalSaveSummary();
    deps.canvasModeStatusEl.textContent = canvasStatusText();
    renderCanvasBoxList();
    deps.updateWorkflowReadiness();
  }

  function setCanvasMode(active: boolean, options: { allowEmptyCanvas?: boolean } = {}): void {
    const readiness = canvasEntryReadiness({
      documentKind: state.documentProvenance.original.kind,
      standalone: deps.isStandaloneCanvasWindow || options.allowEmptyCanvas === true,
    });
    if (active && !readiness.canEnter) {
      deps.setStatus(readiness.reason);
      active = false;
    }
    state.canvasMode = active;
    deps.workspaceShellEl.classList.toggle("canvas-mode", active);
    updateCanvasControls();
    deps.updateStatusDetail();
  }

  function deleteSelectedCanvasBox(): void {
    if (editsLocked()) {
      deps.setStatus("실행 중에는 보정 박스를 삭제할 수 없습니다.");
      return;
    }
    normalizeSelectedCanvasBox();
    if (state.selectedCanvasBoxIndex < 0) {
      deps.setStatus("선택 삭제: 현재 페이지에서 삭제할 박스를 선택하세요.");
      return;
    }
    const result = deleteCanvasBoxAtIndex(state.boxes, state.selectedCanvasBoxIndex);
    state.boxes = result.boxes;
    recordDocumentEdit();
    state.selectedCanvasBoxIndex = result.selectedBoxIndex;
    deps.redrawOverlay();
    deps.updateMeta();
    deps.setStatus("선택한 캔버스 박스를 삭제했습니다.");
  }

  function undoLastCanvasBox(): void {
    if (editsLocked()) {
      deps.setStatus("실행 중에는 보정 박스를 되돌릴 수 없습니다.");
      return;
    }
    if (state.boxes.length === 0) return;
    state.boxes.pop();
    recordDocumentEdit();
    state.selectedCanvasBoxIndex = -1;
    deps.redrawOverlay();
    deps.updateMeta();
  }

  function manualWarningSummary(result: ApplyResult): string {
    const skipped = result.skipped_boxes || 0;
    const warningCount = result.warnings?.length || 0;
    if (!skipped && !warningCount) return "";
    return ` / 건너뜀 ${skipped}건${warningCount ? ` / 경고 ${warningCount}건` : ""}`;
  }

  async function applyPendingManualBoxes(statusLabel: string): Promise<ApplyResult | null> {
    if (!state.documentProvenance.original.path) {
      deps.setStatus("먼저 문서를 선택하세요.");
      return null;
    }
    if (!deps.isPdfInput()) {
      deps.setStatus("PDF 문서를 먼저 선택하세요.");
      return null;
    }
    if (state.documentProvenance.continuation?.state === "unavailable") {
      deps.setStatus("저장된 PDF를 작업공간에서 다시 열 수 없습니다. PDF를 다시 선택하거나 열어주세요.");
      return null;
    }
    if (state.boxes.length === 0) {
      deps.setStatus("수동마스킹실행: 반영할 박스가 없습니다. (드래그로 박스 추가 후 실행)");
      return null;
    }
    if (state.maskingRunning || state.batchRunning) {
      deps.setStatus("실행 중입니다. 완료 후 다시 시도하세요.");
      return null;
    }
    const previousProvenance = state.documentProvenance;
    const sessionIsCurrent = () => state.documentProvenance === previousProvenance;
    const previousPreview = resultSourcePath(previousProvenance) || previousProvenance.original.path;
    const previousReport = state.latestReport;
    const previousReportPath = state.latestReportPath;
    const baseExtracted = state.baseExtractedText || state.extractedText || "";
    const baseMasked = state.baseMaskedText || state.maskedText || "";
    state.preManualPreviewPdf = previousPreview;
    state.preManualExtractedText = baseExtracted;
    state.preManualMaskedText = baseMasked;
    const appliedBoxes = state.boxes.map((box) => ({ ...box, tag: box.tag || "MANUAL" }));
    state.maskingRunning = true;
    deps.btnCanvasClear.disabled = true;
    deps.updateWorkflowReadiness();
    deps.setStatus(`${statusLabel}: 미리보기 반영 중...`);
    try {
      const previewWorkDir = await deps.ensurePreviewWorkDir();
      if (!sessionIsCurrent()) return null;
      const result = await deps.invokeCommand<ApplyResult>("apply_manual_boxes", {
        inputPdf: previousPreview,
        originalPdf: previousProvenance.original.path,
        outputDir: previewWorkDir,
        displayMode: deps.displayModeEl.value,
        reportPath: previousReportPath,
        boxes: appliedBoxes,
      });
      if (!sessionIsCurrent()) return null;
      await deps.loadResultPdf(result.output_file, previousPreview, sessionIsCurrent);
      if (!sessionIsCurrent()) return null;
      let nextReport = previousReport;
      let nextReportPath = previousReportPath;
      let nextPreviewDiagnostics = "";
      let revalidationReportAdopted = false;
      if (result.revalidation_report) {
        try {
          const revalidationText = await deps.invokeCommand<string>("read_text_file", { path: result.revalidation_report });
          if (!sessionIsCurrent()) return null;
          const parsedRevalidation = parseSafeReport(JSON.parse(revalidationText));
          if (!parsedRevalidation.ok) throw new Error("재검증 데이터 형식이 올바르지 않습니다.");
          nextReport = parsedRevalidation.value;
          nextReportPath = result.revalidation_report;
          revalidationReportAdopted = true;
        } catch {
          if (!sessionIsCurrent()) return null;
          nextPreviewDiagnostics = "수동 재검증 데이터를 읽지 못했습니다.";
        }
      }
      if (!sessionIsCurrent()) return null;
      const revalidationFailed = result.requires_revalidation === true && (!revalidationReportAdopted || result.revalidation_status !== "passed");
      state.documentProvenance = adoptManualPreview(previousProvenance, result.output_file);
      // The optional TXT represents the automatic run, not the manually edited
      // PDF. Any mask or restore changes that provenance, so finalization must
      // omit the stale TXT until masking is run again with the selected policy.
      state.latestMaskedPath = "";
      state.latestMaskedTextPolicy = "";
      state.boxes = [];
      recordDocumentEdit();
      state.selectedCanvasBoxIndex = -1;
      state.latestReport = nextReport;
      state.latestReportPath = nextReportPath;
      state.lastPreviewDiagnostics = nextPreviewDiagnostics;
      deps.updateMeta();
      deps.renderFinalState(nextReport);
      state.restoreRevalidationFailed = revalidationFailed;
      const maskApplied = result.mask_boxes_applied ?? result.applied_count ?? 0;
      const restoreApplied = result.unmask_boxes_applied ?? 0;
      const manualMaskCount = appliedBoxes.filter((box) => box.mode === "mask").length;
      const manualRestoreCount = appliedBoxes.filter((box) => box.mode === "restore").length;
      const manualSection = [
        "",
        "[수동보정영역]",
        manualMaskCount > 0 ? `- 수동마스킹 ${manualMaskCount}건` : "",
        manualRestoreCount > 0 ? `- 수동복원 ${manualRestoreCount}건` : "",
      ].filter(Boolean).join("\n");
      deps.setTextCompareContents(baseExtracted, baseMasked ? `${baseMasked}\n${manualSection}` : manualSection.trim());
      const applyStateLabel = result.status === "no_effect" ? "변경 없음" : "완료";
      const revalidationLabel = result.requires_revalidation
        ? result.revalidation_status === "passed" ? " / 재검증 완료" : " / 재검증 확인 권장"
        : "";
      deps.setStatus(`${statusLabel} ${applyStateLabel}(미리보기): 마스킹입력 ${result.mask_count} / 복원입력 ${result.restore_count} / 마스킹적용 ${maskApplied} / 복원적용 ${restoreApplied}${manualWarningSummary(result)}${revalidationLabel}`);
      deps.updateWorkflowReadiness();
      if (revalidationFailed) deps.setStatus("복원 반영됨 — 저장 시 복원 영역 재노출 여부를 확인하는 것을 권장합니다.");
      return result;
    } catch {
      if (sessionIsCurrent()) {
        state.documentProvenance = previousProvenance;
        deps.setStatus(`${statusLabel} 실패: 이전 미리보기를 유지합니다.`);
      }
      return null;
    } finally {
      state.maskingRunning = false;
      deps.btnCanvasClear.disabled = !state.documentProvenance.original.path || state.batchRunning || state.savingInFlight;
      deps.updateWorkflowReadiness();
    }
  }

  return {
    applyPendingManualBoxes,
    renderCanvasBoxList,
    renderCanvasBoxProperties,
    renderCanvasFinalSaveSummary,
    updateCanvasControls,
    setActiveCanvasTool,
    convertCanvasSelectedBox,
    deleteSelectedCanvasBox,
    undoLastCanvasBox,
    setCanvasMode,
  };
}
