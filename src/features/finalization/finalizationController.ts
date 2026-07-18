import { finalSaveWarnings } from "../save-gate/saveGate";
import type { FinalizeResult } from "../../services/tauri/maskingContracts";
import {
  adoptLoadVerifiedFinalContinuation,
  adoptUnavailableFinalContinuation,
  finalSaveSourcePath,
  resultSourcePath,
} from "../../state/documentProvenance";
import type { LegacySessionState } from "../../legacy/startLegacyApp";
import {
  documentWorkflowReadiness,
  finalSaveConfirmationSummary,
  finalSaveDefaultFileName,
} from "../../workflowFlow";
import type { AppSettings, DeidentificationMode } from "../../settingsState";
import type { ApplyResult } from "../manual-adjustment/manualAdjustmentController";

type InvokeCommand = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

type FinalPdfSaveTarget = {
  readonly outputPath: string;
  readonly saveToken: string;
};

function parentDirectory(path: string): string {
  const normalized = path.trim().replace(/[\\/]+$/, "");
  const separatorIndex = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  if (separatorIndex < 0) return "";
  if (separatorIndex === 0) return normalized.slice(0, 1);
  if (separatorIndex === 2 && /^[A-Za-z]:[\\/]/.test(normalized)) return normalized.slice(0, 3);
  return normalized.slice(0, separatorIndex);
}

export type FinalizationState = Pick<
  LegacySessionState,
  | "documentProvenance"
  | "outputDir"
  | "resultDoc"
  | "latestExtractedPath"
  | "latestMaskedPath"
  | "latestMaskedTextPolicy"
  | "latestReportPath"
  | "latestReport"
  | "restoreRevalidationFailed"
  | "baseMaskingProgress"
  | "boxes"
  | "documentEditRevision"
  | "maskingRunning"
  | "batchRunning"
  | "savingInFlight"
  | "batchItems"
  | "openOutputAfterSave"
>;

export type FinalizationDeps = {
  readonly state: FinalizationState;
  readonly invokeCommand: InvokeCommand;
  readonly openPath: (path: string) => Promise<void>;
  readonly customKeywordsEl: HTMLTextAreaElement;
  readonly deidentificationPolicyEl: HTMLSelectElement;
  readonly exportMaskedTextEl: HTMLInputElement;
  readonly optPdfRedactionEl: HTMLInputElement;
  readonly finalSaveDialogEl: HTMLElement;
  readonly finalSaveDialogStateEl: HTMLElement;
  readonly finalSaveWarningListEl: HTMLElement;
  readonly btnDialogSaveAll: HTMLButtonElement;
  readonly btnSave: HTMLButtonElement;
  readonly btnCanvasFinalSave: HTMLButtonElement;
  readonly btnRunMasking: HTMLButtonElement;
  readonly btnManualApply: HTMLButtonElement;
  readonly btnCanvasApply: HTMLButtonElement;
  readonly btnNewDocument: HTMLButtonElement;
  readonly btnPickPdf: HTMLButtonElement;
  readonly btnPickBatch: HTMLButtonElement;
  readonly btnRunBatch: HTMLButtonElement;
  readonly btnClear: HTMLButtonElement;
  readonly finalSaveReadinessEl: HTMLDivElement;
  readonly reviewSummaryMaskCountEl: HTMLElement;
  readonly reviewSummaryRestoreCountEl: HTMLElement;
  readonly reviewSummaryKeywordCountEl: HTMLElement;
  readonly reviewSummaryOutputFileEl: HTMLElement;
  readonly reviewSummaryPdfPolicyEl: HTMLElement;
  readonly reviewSummaryTxtPolicyEl: HTMLElement;
  readonly isPdfInput: () => boolean;
  readonly applyPendingManualBoxes: (statusLabel: string) => Promise<ApplyResult | null>;
  readonly setModalVisible: (element: HTMLElement, visible: boolean) => void;
  readonly collectSettings: () => AppSettings;
  readonly saveSettings: (settings: AppSettings) => void;
  readonly loadResultPdf: (path: string, fallbackPath?: string, isCurrent?: () => boolean) => Promise<void>;
  readonly updateCanvasControls: () => void;
  readonly cancelCanvasInteraction: () => void;
  readonly renderDocumentReviewSurfaces: () => void;
  readonly renderCanvasFinalSaveSummary: () => void;
  readonly recordSavedAt: (value: string) => void;
  readonly setStatus: (message: string) => void;
};

export type FinalizationController = {
  readonly saveFinalOutput: (options?: { warningsConfirmed?: boolean }) => Promise<void>;
  readonly openFinalSaveDialog: () => void;
  readonly closeFinalSaveDialog: () => void;
  readonly renderFinalSaveConfirmation: () => void;
  readonly currentFinalSaveWarnings: () => readonly string[];
  readonly currentFinalDocumentPath: () => string;
  readonly updateWorkflowReadiness: () => void;
};

export function createFinalizationController(deps: FinalizationDeps): FinalizationController {
  const { state } = deps;

  function currentFinalDocumentPath(): string {
    return finalSaveSourcePath(state.documentProvenance);
  }

  function currentWorkflowReadiness() {
    const editablePdfSourcePath = deps.isPdfInput() && state.resultDoc
      ? resultSourcePath(state.documentProvenance)
      : "";
    return documentWorkflowReadiness({
      documentKind: state.documentProvenance.original.kind,
      basePreviewPath: editablePdfSourcePath,
      manualPreviewPath: state.documentProvenance.manual.path,
      safeReportPath: state.latestReportPath,
      boxCount: state.boxes.length,
      latestDocumentPath: editablePdfSourcePath,
      continuationUnavailable: state.documentProvenance.continuation?.state === "unavailable",
    });
  }

  function currentFinalSaveWarnings(): readonly string[] {
    const warnings = [...finalSaveWarnings({
      hasReportPath: Boolean(state.latestReportPath),
      report: state.latestReport,
      restoreRevalidationFailed: state.restoreRevalidationFailed,
    })];
    const selectedPolicy = deps.deidentificationPolicyEl.value as DeidentificationMode;
    if (
      deps.exportMaskedTextEl.checked
      && (!state.latestMaskedPath || state.latestMaskedTextPolicy !== selectedPolicy)
    ) {
      warnings.push("선택한 방식의 비식별 TXT가 아직 없습니다. TXT가 필요하면 이 설정으로 마스킹을 다시 실행하세요.");
    }
    return warnings;
  }

  function currentFinalSaveConfirmationSummary() {
    const selectedPolicy = deps.deidentificationPolicyEl.value as DeidentificationMode;
    const maskedTxtExport = deps.exportMaskedTextEl.checked
      && Boolean(state.latestMaskedPath)
      && state.latestMaskedTextPolicy === selectedPolicy;
    const defaultFileName = finalSaveDefaultFileName(state.documentProvenance.original.path);
    return finalSaveConfirmationSummary({
      maskBoxes: state.boxes.filter((box) => box.mode === "mask").length,
      restoreBoxes: state.boxes.filter((box) => box.mode === "restore").length,
      keywords: deps.customKeywordsEl.value,
      outputFileName: `${defaultFileName}.pdf`,
      pdfRedaction: deps.optPdfRedactionEl.checked,
      displayMode: state.baseMaskingProgress.displayMode,
      maskedTxtExport,
      maskedTxtRequested: deps.exportMaskedTextEl.checked,
      deidentificationMode: selectedPolicy,
      safeReportPath: state.latestReportPath,
    });
  }

  function renderFinalSaveDialogSummary(): void {
    const warnings = currentFinalSaveWarnings();
    deps.finalSaveDialogStateEl.textContent = warnings.length > 0 ? `확인 권장 ${warnings.length}건` : "저장 준비 완료";
    deps.finalSaveDialogStateEl.classList.toggle("status-chip-ok", warnings.length === 0);
    deps.finalSaveDialogStateEl.classList.toggle("status-chip-warn", warnings.length > 0);
    deps.finalSaveWarningListEl.replaceChildren();
    if (warnings.length === 0) {
      const item = document.createElement("li");
      item.className = "dm-savewarn__empty";
      item.textContent = "권고할 사항이 없습니다. 그대로 저장할 수 있습니다.";
      deps.finalSaveWarningListEl.append(item);
    } else {
      for (const warning of warnings) {
        const item = document.createElement("li");
        item.className = "dm-savewarn__item";
        item.textContent = warning;
        deps.finalSaveWarningListEl.append(item);
      }
    }
    deps.btnDialogSaveAll.disabled = false;
  }

  function renderFinalSaveConfirmation(): void {
    const summary = currentFinalSaveConfirmationSummary();
    deps.reviewSummaryMaskCountEl.textContent = summary.maskCountLabel.replace("마스킹 박스 수 ", "");
    deps.reviewSummaryRestoreCountEl.textContent = summary.restoreCountLabel.replace("복원 박스 수 ", "");
    deps.reviewSummaryKeywordCountEl.textContent = summary.keywordCountLabel.replace("키워드 수 ", "");
    deps.reviewSummaryOutputFileEl.textContent = summary.outputFileLabel;
    deps.reviewSummaryPdfPolicyEl.textContent = summary.pdfPolicyLabel.replace("PDF: ", "");
    deps.reviewSummaryTxtPolicyEl.textContent = summary.txtPolicyLabel.replace("TXT: ", "");
    renderFinalSaveDialogSummary();
  }

  function openFinalSaveDialog(): void {
    renderFinalSaveConfirmation();
    deps.setModalVisible(deps.finalSaveDialogEl, true);
    deps.btnDialogSaveAll.focus();
  }

  function closeFinalSaveDialog(): void {
    deps.setModalVisible(deps.finalSaveDialogEl, false);
    deps.btnSave.focus();
  }

  function updateWorkflowReadiness(): void {
    const readiness = currentWorkflowReadiness();
    const warnings = currentFinalSaveWarnings();
    const canFinalSave = readiness.canFinalSave;
    const busy = state.maskingRunning || state.batchRunning || state.savingInFlight;
    const savedSessionReady = state.documentProvenance.continuation?.state === "ready"
      && state.boxes.length === 0;
    const hasPendingManualEdits = state.boxes.length > 0 && !savedSessionReady;
    const runLabel = deps.btnRunMasking.querySelector<HTMLElement>('[data-role="run-label"]');
    deps.btnRunMasking.disabled = !readiness.canRunBaseMasking || busy;
    deps.btnRunMasking.dataset.running = String(state.maskingRunning);
    if (runLabel) runLabel.textContent = state.maskingRunning ? "마스킹 중..." : "현재 PDF 마스킹";
    deps.btnManualApply.disabled = !readiness.canApplyManualPreview || busy;
    deps.btnCanvasApply.disabled = !readiness.canApplyManualPreview || busy;
    deps.btnSave.disabled = !canFinalSave || busy;
    deps.btnCanvasFinalSave.disabled = !canFinalSave || busy;
    deps.btnCanvasApply.classList.toggle("is-disclosed", hasPendingManualEdits);
    deps.btnCanvasApply.classList.toggle("is-hidden", savedSessionReady);
    deps.btnCanvasApply.setAttribute("aria-hidden", String(!hasPendingManualEdits));
    deps.btnCanvasApply.tabIndex = hasPendingManualEdits ? 0 : -1;
    deps.btnCanvasApply.nextElementSibling?.classList.toggle("is-disclosed", hasPendingManualEdits);
    deps.btnCanvasFinalSave.classList.toggle("is-hidden", savedSessionReady);
    deps.btnNewDocument.classList.toggle("is-hidden", !savedSessionReady);
    deps.btnNewDocument.disabled = busy;
    deps.btnPickPdf.disabled = busy;
    deps.btnPickBatch.disabled = busy;
    deps.btnRunBatch.disabled = !state.batchItems.some((item) => item.status === "대기") || busy;
    deps.btnClear.disabled = !state.documentProvenance.original.path || busy;
    deps.btnRunMasking.title = readiness.baseMaskingReason;
    deps.btnNewDocument.title = busy ? "실행 또는 저장이 끝난 뒤 새 작업을 시작할 수 있습니다." : "빈 작업 화면으로 돌아갑니다.";
    deps.btnManualApply.title = readiness.manualApplyReason;
    deps.btnCanvasApply.title = readiness.manualApplyReason;
    const advisory = warnings.length > 0 ? `저장 가능 · 확인 권장 ${warnings.length}건` : readiness.finalSaveReason;
    const saveLabel = canFinalSave ? advisory : readiness.finalSaveReason;
    deps.btnSave.title = saveLabel;
    deps.btnCanvasFinalSave.title = saveLabel;
    deps.finalSaveReadinessEl.dataset.state = !canFinalSave ? "blocked" : warnings.length > 0 ? "review" : "ready";
    deps.finalSaveReadinessEl.textContent = `최종 저장 · ${readiness.phaseLabel}: ${saveLabel}`;
    renderFinalSaveConfirmation();
    deps.renderDocumentReviewSurfaces();
  }

  async function saveFinalOutput({ warningsConfirmed = false }: { warningsConfirmed?: boolean } = {}): Promise<void> {
    if (state.savingInFlight) return;
    state.savingInFlight = true;
    deps.cancelCanvasInteraction();
    deps.updateCanvasControls();
    try {
      if (deps.isPdfInput() && state.boxes.length > 0) {
        const applied = await deps.applyPendingManualBoxes("최종 저장 전 수동 보정 자동 반영");
        if (!applied) return;
        updateWorkflowReadiness();
      }
      const previewPdf = finalSaveSourcePath(state.documentProvenance);
      if (!previewPdf) {
        deps.setStatus("저장할 마스킹본이 없습니다. 먼저 기본 마스킹을 실행하세요.");
        return;
      }
      if (!warningsConfirmed) {
        openFinalSaveDialog();
        return;
      }

      const saveTarget = await deps.invokeCommand<FinalPdfSaveTarget | null>("choose_final_pdf_path", {
        defaultFileName: finalSaveDefaultFileName(state.documentProvenance.original.path),
      });
      if (!saveTarget) return;

      deps.setStatus("최종 저장 중...");
      const finalizationProvenance = state.documentProvenance;
      const finalizationEditRevision = state.documentEditRevision;
      const result = await deps.invokeCommand<FinalizeResult>("finalize_manual_output_to_selected_path", {
        previewPdf,
        originalPdf: state.documentProvenance.original.path,
        outputPath: saveTarget.outputPath,
        saveToken: saveTarget.saveToken,
        extractedPath: "",
        maskedPath: deps.exportMaskedTextEl.checked
          && state.latestMaskedTextPolicy === deps.deidentificationPolicyEl.value
          ? state.latestMaskedPath
          : "",
        reportPath: state.latestReportPath,
        copyReport: false,
      });
      const sessionIsCurrent = () => state.documentProvenance === finalizationProvenance;
      const finalizationIsCurrent = () => sessionIsCurrent()
        && state.documentEditRevision === finalizationEditRevision;
      if (!sessionIsCurrent()) return;
      const selectedOutdir = parentDirectory(result.final_output_file);
      state.outputDir = selectedOutdir;
      deps.saveSettings(deps.collectSettings());
      state.latestMaskedPath = "";
      state.latestMaskedTextPolicy = "";
      const copied = result.copied_files && result.copied_files.length > 0 ? ` / 부가산출물 ${result.copied_files.length}건` : "";
      const markContinuationUnavailable = (message: string) => {
        state.documentProvenance = adoptUnavailableFinalContinuation(
          state.documentProvenance,
          result.final_output_file,
        );
        state.resultDoc = null;
        deps.setStatus(message);
      };
      if (!finalizationIsCurrent()) {
        markContinuationUnavailable("파일은 저장되었으나 저장 중 문서 변경으로 무결성 확인을 완료하지 못했습니다. 저장된 PDF를 다시 열어주세요.");
        return;
      }
      try {
        await deps.loadResultPdf(result.final_output_file, "", finalizationIsCurrent);
        if (!sessionIsCurrent()) return;
        if (!finalizationIsCurrent()) {
          markContinuationUnavailable("파일은 저장되었으나 저장 중 문서 변경으로 무결성 확인을 완료하지 못했습니다. 저장된 PDF를 다시 열어주세요.");
          return;
        }
        state.documentProvenance = adoptLoadVerifiedFinalContinuation(
          state.documentProvenance,
          result.final_output_file,
        );
        deps.recordSavedAt(new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false }));
        deps.setStatus(`최종 저장 완료${copied}`);
      } catch {
        if (!sessionIsCurrent()) return;
        if (!finalizationIsCurrent()) {
          markContinuationUnavailable("파일은 저장되었으나 저장 중 문서 변경으로 무결성 확인을 완료하지 못했습니다. 저장된 PDF를 다시 열어주세요.");
        } else {
          markContinuationUnavailable("파일은 저장되었으나 무결성 확인에 실패했습니다. 저장된 PDF를 다시 열어주세요.");
        }
      }
      deps.updateCanvasControls();
      updateWorkflowReadiness();
      if (state.openOutputAfterSave) {
        deps.openPath(selectedOutdir).catch(() => {
          if (state.documentProvenance.continuation?.state === "ready") {
            deps.setStatus("최종 저장 완료, 파일 위치를 열지 못했습니다.");
          }
        });
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      deps.setStatus(message.includes("SAVE_OVERWRITE_RECONFIRM_REQUIRED")
        ? "PDF 확장자를 적용한 경로에 기존 파일이 있습니다. 저장 다이얼로그에서 .pdf 파일명을 직접 입력해 덮어쓰기를 다시 확인해 주세요."
        : "최종 저장 실패: 저장 작업을 완료하지 못했습니다.");
    } finally {
      state.savingInFlight = false;
      deps.updateCanvasControls();
      updateWorkflowReadiness();
    }
  }

  return {
    saveFinalOutput,
    openFinalSaveDialog,
    closeFinalSaveDialog,
    renderFinalSaveConfirmation,
    currentFinalSaveWarnings,
    currentFinalDocumentPath,
    updateWorkflowReadiness,
  };
}
