import { canvasBoxActionState, canvasZoomActionState, createCanvasBoxRows, deleteCanvasBoxAtIndex } from "../../canvasWorkbench";
import { canvasFinalSaveSummary, canvasToolReadinessText } from "../../canvasToolUx";
import { adoptManualPreview, hasMaskedArtifact, resultSourcePath } from "../../state/documentProvenance";
import type { ApplicationSessionState } from "../../app/compositionRoot";
import { canonicalMaskCounts, parseBoundSafeReport, parseLegacySafeReport } from "../../state/maskingSession";
import type { CanonicalMaskCounts, PdfPointsTopLeftRect, SafeReport } from "../../state/maskingSession";
import { canvasEntryReadiness } from "../../workflowFlow";
import { publishWorkspaceCanvasSummary } from "../../state/workspaceStore";
import { currentSettings } from "../../state/settingsStore";
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
  gestureTrusted?: boolean;
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
  ApplicationSessionState,
  | "documentProvenance"
  | "outputDir"
  | "currentResultPage"
  | "resultDoc"
  | "scale"
  | "boxes"
  | "geometryDraft"
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
  | "activeRunKind"
  | "publicRunIdentity"
  | "latestMaskedPath"
  | "latestMaskedTextPolicy"
  | "lastPreviewDiagnostics"
  | "restoreRevalidationFailed"
>;

export type ManualAdjustmentDeps = {
  readonly state: ManualAdjustmentState;
  readonly invokeCommand: InvokeCommand;
  readonly modeMask: HTMLButtonElement;
  readonly modeRestore: HTMLButtonElement;
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
  readonly btnCanvasZoomOut: HTMLButtonElement;
  readonly btnCanvasZoomIn: HTMLButtonElement;
  readonly btnCanvasUndo: HTMLButtonElement;
  readonly btnCanvasClear: HTMLButtonElement;
  readonly btnCanvasBoxDelete: HTMLButtonElement;
  readonly btnCanvasBoxConvertMask: HTMLButtonElement;
  readonly btnCanvasBoxConvertRestore: HTMLButtonElement;
  readonly isStandaloneCanvasWindow: boolean;
  readonly isPdfInput: () => boolean;
  readonly currentFinalDocumentPath: () => string;
  readonly getActiveCanvasTool: () => CanvasEditorTool;
  readonly setActiveCanvasToolState: (tool: CanvasEditorTool) => void;
  readonly ensurePreviewWorkDir: () => Promise<string>;
  readonly loadResultPdf: (path: string) => Promise<boolean>;
  readonly redrawOverlay: () => void;
  readonly updateMeta: () => void;
  readonly renderFinalState: (report: SafeReport | null) => void;
  readonly renderCompare: () => Promise<void>;
  readonly setTextCompareContents: (extractedText: string, maskedText: string) => void;
  readonly updateWorkflowReadiness: () => void;
  readonly updateStatusDetail: () => void;
  readonly setStatus: (message: string) => void;
  readonly applyPublicManualMaskActions?: (actions: readonly { readonly page: number; readonly rects: readonly PdfPointsTopLeftRect[]; readonly mode: BoxMode; readonly gestureTrusted?: boolean }[]) => Promise<boolean>;
  readonly renderDocumentReviewSurfaces?: () => void;
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
  let activeApplyToken: symbol | null = null;

  function currentCanvasPageIndex(): number {
    return Math.max(0, (state.currentResultPage || 1) - 1);
  }
  function editsLocked(): boolean {
    return state.maskingRunning || state.batchRunning || state.savingInFlight;
  }

  function isObservableBox(_box: BoxItem): boolean {
    return true;
  }

  function recordDocumentEdit(): void {
    state.documentEditRevision = (state.documentEditRevision || 0) + 1;
  }

  function normalizeSelectedCanvasBox(): void {
    const selected = state.boxes[state.selectedCanvasBoxIndex];
    if (!selected || selected.page !== currentCanvasPageIndex() || !isObservableBox(selected)) state.selectedCanvasBoxIndex = -1;
  }

  function renderCanvasBoxList(): void {
    normalizeSelectedCanvasBox();
    const rows = createCanvasBoxRows(state.boxes, currentCanvasPageIndex(), state.selectedCanvasBoxIndex)
      .filter((row) => isObservableBox(state.boxes[row.globalIndex]));
    if (typeof deps.canvasBoxListEl.replaceChildren === "function") {
      deps.canvasBoxListEl.replaceChildren();
    } else if ("textContent" in deps.canvasBoxListEl) {
      deps.canvasBoxListEl.textContent = "";
    }
    const appendChild = deps.canvasBoxListEl.appendChild;
    if (typeof appendChild !== "function") return;
    if (rows.length === 0) {
      const empty = document.createElement("div");
      empty.className = "canvas-box-empty";
      empty.textContent = "현재 페이지에 박스가 없습니다.";
      appendChild.call(deps.canvasBoxListEl, empty);
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
      appendChild.call(deps.canvasBoxListEl, button);
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

  function committedPublicMaskCounts(): CanonicalMaskCounts {
    if (state.activeRunKind !== "public" || !state.latestReport || !state.publicRunIdentity) {
      return {
        automaticMaskCount: 0,
        manualMaskCount: 0,
        manualRestoreCount: 0,
        effectiveMaskCount: 0,
      };
    }
    const report = parseBoundSafeReport(state.latestReport, state.publicRunIdentity);
    if (!report.ok) {
      return {
        automaticMaskCount: 0,
        manualMaskCount: 0,
        manualRestoreCount: 0,
        effectiveMaskCount: 0,
      };
    }
    const counts = canonicalMaskCounts(report.value);
    return counts.ok ? counts.value : {
      automaticMaskCount: 0,
      manualMaskCount: 0,
      manualRestoreCount: 0,
      effectiveMaskCount: 0,
    };
  }

  function renderCanvasFinalSaveSummary(): void {
    const observableBoxes = state.boxes.filter(isObservableBox);
    const committed = committedPublicMaskCounts();
    const draftMaskCount = observableBoxes.filter((box) => box.mode === "mask").length;
    const draftRestoreCount = observableBoxes.filter((box) => box.mode === "restore").length;
    const automaticMaskCount = committed.automaticMaskCount;
    const manualMaskCount = committed.manualMaskCount + draftMaskCount;
    const manualRestoreCount = committed.manualRestoreCount + draftRestoreCount;
    const effectiveMaskCount = committed.effectiveMaskCount + draftMaskCount;
    const summary = canvasFinalSaveSummary({
      maskBoxes: effectiveMaskCount,
      restoreBoxes: manualRestoreCount,
      keywords: currentSettings().customKeywords,
      hasFinalDocument: hasMaskedArtifact(state.documentProvenance),
    });
    publishWorkspaceCanvasSummary({
      maskCount: `${effectiveMaskCount}개`,
      restoreCount: `${manualRestoreCount}개`,
      automaticMaskCount: `${automaticMaskCount}건`,
      manualMaskCount: `${manualMaskCount}건(저장 시 적용)`,
      manualRestoreCount: `${manualRestoreCount}건`,
      effectiveMaskCount: `${effectiveMaskCount}건`,
      keywordCount: summary.keywordLabel.replace("키워드 ", ""),
      outputFile: summary.saveLabel,
    });
  }

  function setActiveCanvasTool(tool: CanvasEditorTool): void {
    if (editsLocked()) {
      deps.setStatus("실행 중에는 캔버스 도구를 변경할 수 없습니다.");
      return;
    }
    deps.setActiveCanvasToolState(tool);
    if (tool === "mask") {
      deps.modeMask.setAttribute("aria-pressed", "true");
      deps.modeRestore.setAttribute("aria-pressed", "false");
      state.mode = "mask";
    }
    if (tool === "restore") {
      deps.modeMask.setAttribute("aria-pressed", "false");
      deps.modeRestore.setAttribute("aria-pressed", "true");
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
    deps.btnCanvasUndo.disabled = busy || !pdfEditable || !state.boxes.some(isObservableBox);
    deps.btnCanvasClear.disabled = !state.documentProvenance.original.path || busy;
    deps.btnCanvasBoxDelete.disabled = busy || !boxActions.canDeleteSelected;
    const editReason = busy ? "실행 중에는 보정 박스를 변경할 수 없습니다." : readiness.editReason;
    deps.canvasToolReadinessEl.textContent = readiness.canEdit && !busy ? readiness.saveReason : `${editReason} ${readiness.saveReason}`;
    syncCanvasToolPalette(readiness.canEdit && !busy, editReason);
    renderCanvasBoxProperties();
    renderCanvasFinalSaveSummary();
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
    if (state.activeRunKind === "public") {
      const observableBoxes = state.boxes.filter(isObservableBox);
      if (observableBoxes.length !== state.boxes.length || !deps.applyPublicManualMaskActions) {
        deps.setStatus("공공 세션에서 수동 박스를 반영할 수 없습니다.");
        return null;
      }
      const applied = await deps.applyPublicManualMaskActions(observableBoxes.map((box) => ({
        page: box.page,
        rects: [{ x0: box.x0, y0: box.y0, x1: box.x1, y1: box.y1 }],
        mode: box.mode,
        gestureTrusted: box.gestureTrusted,
      })));
      if (!applied) return null;
      state.boxes = [];
      state.selectedCanvasBoxIndex = -1;
      recordDocumentEdit();
      deps.redrawOverlay();
      deps.updateMeta();
      deps.renderFinalState(state.latestReport);
      deps.renderDocumentReviewSurfaces?.();
      deps.updateWorkflowReadiness();
      deps.setStatus(`${statusLabel}: 공공 수동 보정 ${observableBoxes.length}건을 검토 세션에 반영했습니다. 저장 시 적용됩니다. 페이지 확인 후 저장하세요.`);
      return null;
    }
    const transactionSnapshot = { ...state };
    const restoreTransaction = (): void => {
      const mutableState = state as Record<string, unknown>;
      for (const key of Object.keys(mutableState)) {
        if (!(key in transactionSnapshot)) delete mutableState[key];
      }
      Object.assign(state, transactionSnapshot);
    };
    const previousProvenance = state.documentProvenance;
    const originalDocumentPath = previousProvenance.original.path;
    const applyToken = Symbol("manual-apply");
    activeApplyToken = applyToken;
    const sessionIsCurrent = () => activeApplyToken === applyToken
      && state.documentProvenance.original.path === originalDocumentPath;
    const previousPreview = resultSourcePath(previousProvenance) || originalDocumentPath;
    const previousReport = state.latestReport;
    const hadPublicAuthority = previousReport?.analysisManifest !== undefined;
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
        displayMode: currentSettings().displayMode,
        reportPath: previousReportPath,
        boxes: appliedBoxes,
      });
      if (!sessionIsCurrent()) return null;
      const validCount = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
      const allowedResultFields = ["status", "output_file", "mask_count", "restore_count", "applied_count", "excluded_count", "mask_boxes_applied", "unmask_boxes_applied", "skipped_boxes", "warnings", "requires_revalidation", "display_mode", "revalidation_report", "revalidation_status"];
      if (!result || Object.keys(result).some((key) => !allowedResultFields.includes(key))
        || typeof result.output_file !== "string" || !result.output_file
        || !validCount(result.mask_count) || !validCount(result.restore_count)
        || (result.applied_count !== undefined && !validCount(result.applied_count))
        || (result.excluded_count !== undefined && !validCount(result.excluded_count))
        || (result.mask_boxes_applied !== undefined && !validCount(result.mask_boxes_applied))
        || (result.unmask_boxes_applied !== undefined && !validCount(result.unmask_boxes_applied))
        || (result.skipped_boxes !== undefined && !validCount(result.skipped_boxes))
        || (result.warnings !== undefined && (!Array.isArray(result.warnings) || !result.warnings.every((warning) => typeof warning === "string")))
        || (result.requires_revalidation !== undefined && typeof result.requires_revalidation !== "boolean")
        || (result.status !== undefined && typeof result.status !== "string")
        || (result.revalidation_report !== undefined && typeof result.revalidation_report !== "string")
        || (result.revalidation_status !== undefined && typeof result.revalidation_status !== "string")) {
        throw new Error("invalid_manual_apply_result");
      }
      const manualMaskCount = appliedBoxes.filter((box) => box.mode === "mask").length;
      const manualRestoreCount = appliedBoxes.filter((box) => box.mode === "restore").length;
      if (result.mask_count !== manualMaskCount || result.restore_count !== manualRestoreCount) {
        throw new Error("invalid_manual_apply_counts");
      }
      if (!(await deps.loadResultPdf(result.output_file))) return null;
      if (!sessionIsCurrent()) return null;
      let nextReport = previousReport;
      let nextReportPath = previousReportPath;
      let nextPreviewDiagnostics = "";
      let revalidationReportAdopted = false;
      if (result.revalidation_report) {
        try {
          const revalidationText = await deps.invokeCommand<string>("read_text_file", { path: result.revalidation_report });
          if (!sessionIsCurrent()) return null;
          const revalidationValue: unknown = JSON.parse(revalidationText);
          const publicIdentity = state.publicRunIdentity;
          const hasPublicSchema = previousReport?.analysisManifest !== undefined;
          const parsedRevalidation = hasPublicSchema && publicIdentity
            ? parseBoundSafeReport(revalidationValue, publicIdentity)
            : parseLegacySafeReport(revalidationValue);
          if (!parsedRevalidation.ok) throw new Error("재검증 데이터 형식이 올바르지 않습니다.");
          nextReport = parsedRevalidation.value;
          nextReportPath = result.revalidation_report;
          revalidationReportAdopted = true;
        } catch (error) {
          if (!sessionIsCurrent()) return null;
          nextPreviewDiagnostics = `수동 재검증 데이터를 읽지 못했습니다 (${safeFailureCode(error)}).`;
        }
      }
      if (!sessionIsCurrent()) return null;
      const revalidationRequired = result.requires_revalidation === true || manualRestoreCount > 0;
      const revalidationFailed = hadPublicAuthority
        || (revalidationRequired && (!revalidationReportAdopted || result.revalidation_status !== "passed"));
      if (revalidationFailed) {
        nextPreviewDiagnostics = nextPreviewDiagnostics || (hadPublicAuthority
          ? "수동 미리보기는 서버 분석 권위가 아니므로 이전 검토 증거를 무효화했습니다."
          : "수동 재검증이 통과하지 않아 이전 검토 증거를 무효화했습니다.");
      }
      state.documentProvenance = adoptManualPreview(previousProvenance, result.output_file);
      // The optional TXT represents the automatic run, not the manually edited
      // PDF. Any mask or restore changes that provenance, so finalization must
      // omit the stale TXT until masking is run again with the selected policy.
      state.latestMaskedPath = "";
      state.latestMaskedTextPolicy = "";
      state.boxes = [];
      recordDocumentEdit();
      state.selectedCanvasBoxIndex = -1;
      state.latestReport = hadPublicAuthority ? null : nextReport;
      state.latestReportPath = hadPublicAuthority ? "" : nextReportPath;
      if (hadPublicAuthority) state.publicRunIdentity = null;
      state.lastPreviewDiagnostics = nextPreviewDiagnostics;
      deps.updateMeta();
      state.restoreRevalidationFailed = revalidationFailed;
      deps.renderFinalState(state.latestReport);
      const maskApplied = result.mask_boxes_applied ?? result.applied_count ?? 0;
      const restoreApplied = result.unmask_boxes_applied ?? 0;
      const manualSection = [
        "",
        "[수동보정영역]",
        manualMaskCount > 0 ? `- 수동마스킹 ${manualMaskCount}건` : "",
        manualRestoreCount > 0 ? `- 수동복원 ${manualRestoreCount}건` : "",
      ].filter(Boolean).join("\n");
      deps.setTextCompareContents(baseExtracted, baseMasked ? `${baseMasked}\n${manualSection}` : manualSection.trim());
      const applyStateLabel = result.status === "no_effect" ? "변경 없음" : "완료";
      const revalidationLabel = revalidationRequired
        ? result.revalidation_status === "passed" && revalidationReportAdopted ? " / 재검증 완료" : " / 재검증 실패"
        : "";
      deps.setStatus(`${statusLabel} ${applyStateLabel}(미리보기): 마스킹입력 ${result.mask_count} / 복원입력 ${result.restore_count} / 마스킹적용 ${maskApplied} / 복원적용 ${restoreApplied}${manualWarningSummary(result)}${revalidationLabel}`);
      deps.updateWorkflowReadiness();
      if (revalidationFailed) deps.setStatus(hadPublicAuthority
        ? "수동 미리보기는 아직 서버가 승인한 마스킹이 아닙니다. 마스킹을 다시 실행해 검토 증거를 갱신하세요."
        : "수동 재검증 필요: 통과하지 않아 이전 검토 증거를 유지하지 못했습니다. 마스킹을 다시 실행하세요.");
      return result;
    } catch (error) {
      if (sessionIsCurrent()) {
        const failureCode = safeFailureCode(error);
        restoreTransaction();
        let rollbackFailure = "";
        try {
          await deps.renderCompare();
        } catch (rollbackError) {
          rollbackFailure = ` / 이전 화면 복구 실패 (${safeFailureCode(rollbackError)})`;
        }
        deps.setStatus(`${statusLabel} 실패 (${failureCode})${rollbackFailure}: 이전 미리보기를 유지합니다.`);
      }
      return null;
    } finally {
      if (activeApplyToken === applyToken) {
        activeApplyToken = null;
        state.maskingRunning = false;
        deps.btnCanvasClear.disabled = !state.documentProvenance.original.path || state.batchRunning || state.savingInFlight;
        deps.updateWorkflowReadiness();
      }
    }
  }

  function safeFailureCode(error: unknown): "ipc" | "pdf" | "invalid" | "unknown" {
    if (error instanceof SyntaxError) return "invalid";
    const message = error instanceof Error ? error.message.toLowerCase() : "";
    if (message.includes("ipc") || message.includes("invoke") || message.includes("permission")) return "ipc";
    if (message.includes("pdf") || message.includes("document")) return "pdf";
    return "unknown";
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
