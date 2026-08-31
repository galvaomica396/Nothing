import { finalSaveGate, finalSaveWarnings, legalCompatibilityFinalSaveGate, presentMaskingFailure, publicFinalSaveWarnings } from "../save-gate/saveGate";
import type { MaskingFailureDiagnostic } from "../save-gate/saveGate";
import { finalizeMaskingRun, parseFinalizeMaskingRunResult, prepareFinalizeMaskingRun } from "../../services/tauri/maskingContracts";
import type { LegacyFinalizeResult } from "../../services/tauri/maskingContracts";
import type { FinalizeSaveConfirmation, RestoreAuthorizationSummary } from "../../services/tauri/maskingContracts";
import {
  adoptLoadVerifiedFinalContinuation,
  adoptUnavailableFinalContinuation,
  finalSaveSourcePath,
  resultSourcePath,
} from "../../state/documentProvenance";
import type { ApplicationSessionState } from "../../app/compositionRoot";
import {
  documentWorkflowReadiness,
  finalSaveConfirmationSummary,
  finalSaveDefaultFileName,
} from "../../workflowFlow";
import type { AppSettings, SavedSettings } from "../../settingsState";
import type { ApplyResult } from "../manual-adjustment/manualAdjustmentController";
import { boundSafeReportIdentity, canonicalMaskCounts, isBoundSafeReport, parseBoundSafeReport } from "../../state/maskingSession";
import type { BoundSafeReport, CanonicalMaskCounts } from "../../state/maskingSession";
import {
  publishWorkspaceFinalSaveDialog,
  publishWorkspaceFinalSaveSummary,
  setWorkspaceFinalSaveDialogVisible,
} from "../../state/workspaceStore";
import { currentSettings } from "../../state/settingsStore";

type InvokeCommand = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

type FinalPdfSaveTarget = {
  readonly outputPath: string;
  readonly saveToken: string;
};

export type FinalSaveStage = "chooser" | "prepare" | "trusted-finalize" | "file-stat" | "complete";
export type FinalSaveStepStatus = "not_run" | "ok" | "failed" | "cancelled";
export type FinalSaveStep = {
  readonly status: FinalSaveStepStatus;
  readonly code: string | null;
};
export type FinalSaveOutcome = {
  readonly status: "ok" | "cancelled" | "blocked" | "failed";
  readonly stage: FinalSaveStage;
  readonly errorCode: string | null;
  readonly errorField: string | null;
  readonly finalPath: string | null;
  readonly saveConfirmation: FinalizeSaveConfirmation | null;
  readonly manualMaskCount: number | null;
  readonly restoreCount: number | null;
  readonly effectiveMaskCount: number | null;
  readonly restoreAuthorization: RestoreAuthorizationSummary | null;
  readonly failureDiagnostics: readonly MaskingFailureDiagnostic[];
  readonly steps: {
    readonly chooser: FinalSaveStep;
    readonly prepare: FinalSaveStep;
    readonly trustedFinalize: FinalSaveStep;
    readonly fileStat: FinalSaveStep;
  };
};

function initialFinalSaveOutcome(): FinalSaveOutcome {
  const notRun: FinalSaveStep = { status: "not_run", code: null };
  return {
    status: "blocked",
    stage: "prepare",
    errorCode: null,
    errorField: null,
    finalPath: null,
    saveConfirmation: null,
    manualMaskCount: null,
    restoreCount: null,
    effectiveMaskCount: null,
    restoreAuthorization: null,
    failureDiagnostics: [],
    steps: {
      chooser: notRun,
      prepare: notRun,
      trustedFinalize: notRun,
      fileStat: notRun,
    },
  };
}

function updateFinalSaveStep(
  outcome: FinalSaveOutcome,
  step: keyof FinalSaveOutcome["steps"],
  status: FinalSaveStepStatus,
  code: string | null = null,
): FinalSaveOutcome {
  return {
    ...outcome,
    steps: {
      ...outcome.steps,
      [step]: { status, code },
    },
  };
}

function parentDirectory(path: string): string {
  const normalized = path.trim().replace(/[\\/]+$/, "");
  const separatorIndex = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  if (separatorIndex < 0) return "";
  if (separatorIndex === 0) return normalized.slice(0, 1);
  if (separatorIndex === 2 && /^[A-Za-z]:[\\/]/.test(normalized)) return normalized.slice(0, 3);
  return normalized.slice(0, separatorIndex);
}


export type FinalizationState = Pick<
  ApplicationSessionState,
  | "documentProvenance"
  | "outputDir"
  | "resultDoc"
  | "latestExtractedPath"
  | "latestMaskedPath"
  | "latestMaskedTextPolicy"
  | "latestReportPath"
  | "latestReport"
  | "activeRunKind"
  | "publicRunIdentity"
  | "restoreRevalidationFailed"
  | "baseMaskingProgress"
  | "boxes"
  | "geometryDraft"
  | "documentEditRevision"
  | "maskingRunning"
  | "batchRunning"
  | "savingInFlight"
  | "batchItems"
>;

export type FinalizationDeps = {
  readonly state: FinalizationState;
  readonly invokeCommand: InvokeCommand;
  readonly openPath: (path: string) => Promise<void>;
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
  readonly isPdfInput: () => boolean;
  readonly applyPendingManualBoxes: (statusLabel: string) => Promise<ApplyResult | null>;
  readonly collectSettings: () => AppSettings;
  readonly saveSettings: (settings: AppSettings) => SavedSettings;
  readonly loadResultPdf: (path: string) => Promise<boolean>;
  readonly updateCanvasControls: () => void;
  readonly cancelGeometryDraft?: () => boolean;
  readonly cancelCanvasInteraction: () => void;
  readonly renderDocumentReviewSurfaces: () => void;
  readonly renderCanvasFinalSaveSummary: () => void;
  readonly recordSavedAt: (value: string) => void;
  readonly showFinalizationSuccess: (finalPath: string, saveConfirmation?: FinalizeSaveConfirmation | null) => void;
  readonly setStatus: (message: string) => void;
};

export type FinalizationController = {
  readonly saveFinalOutput: (options?: { warningsConfirmed?: boolean }) => Promise<FinalSaveOutcome>;
  readonly openFinalSaveDialog: () => void;
  readonly closeFinalSaveDialog: () => void;
  readonly renderFinalSaveConfirmation: () => void;
  readonly currentFinalSaveWarnings: () => readonly string[];
  readonly currentFinalDocumentPath: () => string;
  readonly updateWorkflowReadiness: () => void;
};

export function createFinalizationController(deps: FinalizationDeps): FinalizationController {
  const { state } = deps;
  const cancelGeometryDraft = (): boolean => !state.geometryDraft || deps.cancelGeometryDraft?.() === true;


  function currentFinalDocumentPath(): string {
    return finalSaveSourcePath(state.documentProvenance);
  }

  function currentWorkflowReadiness() {
    const editablePdfSourcePath = deps.isPdfInput() && state.resultDoc
      ? resultSourcePath(state.documentProvenance)
      : "";
    const publicManualMaskEligible = currentRunKind() === "public"
      && state.boxes.length > 0
      && state.boxes.every((box) => box.mode === "mask" || box.mode === "restore");
    return documentWorkflowReadiness({
      documentKind: state.documentProvenance.original.kind,
      basePreviewPath: editablePdfSourcePath,
      manualPreviewPath: state.documentProvenance.manual.path,
      safeReportPath: state.latestReportPath,
      boxCount: state.boxes.length,
      publicManualMaskEligible,
      latestDocumentPath: editablePdfSourcePath,
      continuationUnavailable: state.documentProvenance.continuation?.state === "unavailable",
    });
  }

  function currentBoundPublicReport(): BoundSafeReport | null {
    const report = state.latestReport;
    if (!report?.analysisManifest || !state.publicRunIdentity || !isBoundSafeReport(report)) return null;
    const parsed = parseBoundSafeReport(report, state.publicRunIdentity);
    return parsed.ok ? parsed.value : null;
  }

  function currentPublicSaveDecision() {
    return finalSaveGate({
      report: currentBoundPublicReport(),
      restoreRevalidationFailed: state.restoreRevalidationFailed,
    });
  }

  function currentRunKind(): "none" | "legal" | "public" {
    return state.latestReport?.analysisManifest ? "public" : state.activeRunKind === "legal" ? "legal" : "none";
  }

  function currentLegalSaveDecision() {
    const decision = legalCompatibilityFinalSaveGate({
      hasReportPath: Boolean(state.latestReportPath),
      report: state.latestReport,
      restoreRevalidationFailed: state.restoreRevalidationFailed,
    });
    return state.restoreRevalidationFailed
      ? { eligible: false, state: "blocked" as const, reasonCodes: ["legal_restore_revalidation_failed"] }
      : decision;
  }

  function currentFinalSaveWarnings(): readonly string[] {
    const warnings = currentRunKind() === "public"
      ? [...publicFinalSaveWarnings({
          report: currentBoundPublicReport(),
          restoreRevalidationFailed: state.restoreRevalidationFailed,
        })]
      : currentRunKind() === "legal"
        ? [...finalSaveWarnings({
            hasReportPath: Boolean(state.latestReportPath),
            report: state.latestReport,
            restoreRevalidationFailed: state.restoreRevalidationFailed,
          })]
        : [];
    const settings = currentSettings();
    const selectedPolicy = settings.deidentificationMode;
    if (
      currentRunKind() === "legal"
      && settings.exportMaskedText
      && (!state.latestMaskedPath || state.latestMaskedTextPolicy !== selectedPolicy)
    ) {
      warnings.push("선택한 방식의 비식별 TXT가 아직 없습니다. TXT가 필요하면 이 설정으로 마스킹을 다시 실행하세요.");
    }
    return warnings;
  }

  function currentFinalMaskCounts(): CanonicalMaskCounts {
    const publicReport = currentBoundPublicReport();
    const committed = publicReport
      ? canonicalMaskCounts(publicReport)
      : null;
    const base: CanonicalMaskCounts = committed?.ok
      ? committed.value
      : {
          automaticMaskCount: 0,
          manualMaskCount: 0,
          manualRestoreCount: 0,
          effectiveMaskCount: 0,
        };
    const draftMaskCount = state.boxes.filter((box) => box.mode === "mask").length;
    const draftRestoreCount = state.boxes.filter((box) => box.mode === "restore").length;
    return {
      automaticMaskCount: base.automaticMaskCount,
      manualMaskCount: base.manualMaskCount + draftMaskCount,
      manualRestoreCount: base.manualRestoreCount + draftRestoreCount,
      effectiveMaskCount: base.effectiveMaskCount + draftMaskCount,
    };
  }

  function currentFinalSaveConfirmationSummary() {
    const settings = currentSettings();
    const selectedPolicy = settings.deidentificationMode;
    const maskedTxtExport = settings.exportMaskedText
      && Boolean(state.latestMaskedPath)
      && state.latestMaskedTextPolicy === selectedPolicy;
    const publicDecision = currentRunKind() === "public" ? currentPublicSaveDecision() : null;
    const defaultFileName = finalSaveDefaultFileName(
      state.documentProvenance.original.path,
      publicDecision?.state === "advisory",
    );
    const maskCounts = currentFinalMaskCounts();
    return finalSaveConfirmationSummary({
      maskBoxes: maskCounts.effectiveMaskCount,
      restoreBoxes: maskCounts.manualRestoreCount,
      keywords: settings.customKeywords,
      outputFileName: `${defaultFileName}.pdf`,
      pdfRedaction: settings.pdfRedaction,
      displayMode: state.baseMaskingProgress.displayMode,
      maskedTxtExport,
      maskedTxtRequested: settings.exportMaskedText,
      deidentificationMode: selectedPolicy,
      safeReportPath: state.latestReportPath,
    });
  }

  function publishFinalSaveDialog(): void {
    const warnings = currentFinalSaveWarnings();
    const isPublicSession = state.latestReport?.analysisManifest !== undefined;
    const publicDecision = isPublicSession ? currentPublicSaveDecision() : null;
    publishWorkspaceFinalSaveDialog({
      title: isPublicSession ? "저장 전 검토" : "저장 전 확인",
      description: isPublicSession
        ? "미해결 검토 항목을 확인한 뒤 저장할 수 있습니다. 무결성 오류는 저장할 수 없습니다."
        : "검토 결과와 권고 항목을 확인한 뒤 저장할 수 있습니다.",
      advisoryTitle: isPublicSession
        ? publicDecision?.eligible ? "검토 세션 해결 완료" : publicDecision?.state === "blocked" ? "저장할 수 없는 무결성 오류가 있습니다" : "사용자 확인이 필요한 검토 항목이 있습니다"
        : "추가 확인이 필요한 항목이 있습니다",
      advisoryCopy: isPublicSession
        ? publicDecision?.state === "blocked"
          ? "현재 서버 검토 세션을 다시 분석해 무결성 오류를 해결하세요."
          : "경계, OCR, 영역 좌표, 이름, 기관, 공통 전용 확인 항목을 확인하고 저장할 수 있습니다."
        : "우측 패널에서 권고 항목을 확인하거나 바로 저장할 수 있습니다.",
      showAdvisory: warnings.length > 0,
      cancelLabel: isPublicSession ? "검토로 돌아가기" : "취소하고 검토하기",
      confirmLabel: isPublicSession
        ? publicDecision?.eligible ? "검토 완료 상태로 저장" : "경고 확인 후 부분 마스킹본 저장"
        : "무시하고 그대로 저장",
      confirmEnabled: publicDecision === null || publicDecision.state !== "blocked",
      stateLabel: isPublicSession
        ? publicDecision?.eligible ? "검토 세션 해결 완료" : publicDecision?.state === "blocked" ? "저장 차단 · 무결성 오류" : `확인 후 저장 가능 ${warnings.length}건`
        : warnings.length > 0
          ? `확인 권장 ${warnings.length}건`
          : "저장 준비 완료",
      stateTone: publicDecision?.state === "blocked" ? "pending" : warnings.length === 0 ? "ok" : "warn",
      warnings,
      emptyMessage: isPublicSession
        ? "모든 서버 검토 항목이 해결되었습니다. 최종 저장할 수 있습니다."
        : "권고할 사항이 없습니다. 그대로 저장할 수 있습니다.",
    });
  }

  function renderFinalSaveConfirmation(): void {
    const summary = currentFinalSaveConfirmationSummary();
    const maskCounts = currentFinalMaskCounts();
    publishWorkspaceFinalSaveSummary({
      maskCount: `${maskCounts.effectiveMaskCount}개`,
      restoreCount: `${maskCounts.manualRestoreCount}개`,
      automaticMaskCount: `${maskCounts.automaticMaskCount}건`,
      manualMaskCount: `${maskCounts.manualMaskCount}건(저장 시 적용)`,
      manualRestoreCount: `${maskCounts.manualRestoreCount}건`,
      effectiveMaskCount: `${maskCounts.effectiveMaskCount}건`,
      keywordCount: summary.keywordCountLabel.replace("키워드 수 ", ""),
      outputFile: summary.outputFileLabel,
      pdfPolicy: summary.pdfPolicyLabel.replace("PDF: ", ""),
      txtPolicy: summary.txtPolicyLabel.replace("TXT: ", ""),
    });
    publishFinalSaveDialog();
  }

  function openFinalSaveDialog(): void {
    renderFinalSaveConfirmation();
    setWorkspaceFinalSaveDialogVisible(true);
  }

  function closeFinalSaveDialog(): void {
    setWorkspaceFinalSaveDialogVisible(false);
  }

  function updateWorkflowReadiness(): void {
    const readiness = currentWorkflowReadiness();
    const warnings = currentFinalSaveWarnings();
    const publicGate = currentRunKind() === "public" ? currentPublicSaveDecision() : null;
    const legalGate = currentRunKind() === "legal" ? currentLegalSaveDecision() : null;
    const canFinalSave = publicGate
      ? publicGate.state !== "blocked" && state.boxes.length === 0
      : legalGate
        ? legalGate.state !== "blocked" && readiness.canFinalSave
        : false;
    const busy = state.maskingRunning || state.batchRunning || state.savingInFlight;
    const savedSessionReady = (state.documentProvenance.continuation?.state === "ready"
      || state.documentProvenance.continuation?.state === "unavailable")
      && !state.documentProvenance.manual.path
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
    const saveLabel = currentRunKind() === "public"
      ? canFinalSave
        ? warnings.length > 0 ? `확인 후 저장 가능 ${warnings.length}건` : "서버 검토 완료"
        : warnings[0] ?? "서버 검토 세션을 다시 확인하세요."
      : currentRunKind() === "legal"
        ? canFinalSave ? advisory : readiness.finalSaveReason
        : "먼저 문서를 마스킹하세요.";
    deps.btnSave.title = saveLabel;
    deps.btnCanvasFinalSave.title = saveLabel;
    deps.finalSaveReadinessEl.dataset.state = !canFinalSave ? "blocked" : warnings.length > 0 ? "review" : "ready";
    deps.finalSaveReadinessEl.textContent = `최종 저장 · ${readiness.phaseLabel}: ${saveLabel}`;
    renderFinalSaveConfirmation();
    deps.renderDocumentReviewSurfaces();
  }

  async function saveFinalOutput({ warningsConfirmed = false }: { warningsConfirmed?: boolean } = {}): Promise<FinalSaveOutcome> {
    if (state.savingInFlight) {
      return {
        ...initialFinalSaveOutcome(),
        status: "blocked",
        stage: "prepare",
        errorCode: "SAVE_ALREADY_IN_FLIGHT",
      };
    }
    let outcome = initialFinalSaveOutcome();
    let currentStage: FinalSaveStage = "prepare";
    const markStep = (
      step: keyof FinalSaveOutcome["steps"],
      status: FinalSaveStepStatus,
      code: string | null = null,
    ): void => {
      outcome = updateFinalSaveStep(outcome, step, status, code);
    };
    const finish = (
      status: FinalSaveOutcome["status"],
      stage: FinalSaveStage,
      errorCode: string | null = null,
      finalPath?: string | null,
      errorField?: string | null,
    ): FinalSaveOutcome => {
      outcome = {
        ...outcome,
        status,
        stage,
        errorCode: errorCode === null ? null : errorCode,
        errorField: errorField === undefined ? outcome.errorField : errorField,
        finalPath: finalPath === undefined ? outcome.finalPath : finalPath,
      };
      return outcome;
    };
    const blocked = (stage: FinalSaveStage, code: string, field: string | null = null): FinalSaveOutcome => {
      markStep(stage === "chooser" ? "chooser" : stage === "trusted-finalize" ? "trustedFinalize" : "prepare", "failed", code);
      return finish("blocked", stage, code, undefined, field);
    };
    state.savingInFlight = true;
    deps.cancelCanvasInteraction();
    deps.updateCanvasControls();
    try {
      const publicSession = state.latestReport?.analysisManifest !== undefined;
      if (publicSession && state.boxes.length > 0) {
        deps.setStatus("최종 저장이 차단되었습니다. 수동 보정 박스를 반영하고 다시 분석하세요.");
        return blocked("prepare", "MANUAL_ACTIONS_PENDING");
      }
      if (!cancelGeometryDraft()) {
        deps.setStatus("진행 중인 영역 보정을 취소하지 못했습니다. 저장을 중단했습니다.");
        return blocked("prepare", "GEOMETRY_DRAFT_CANCEL_FAILED");
      }
      const authoritativeReport = currentBoundPublicReport();
      const authoritativeManifest = authoritativeReport?.analysisManifest;
      if (publicSession && !authoritativeManifest) {
        deps.setStatus("현재 서버 검토 세션이 손상되었습니다. 문서를 다시 분석하세요.");
        return blocked("prepare", "MASKING_SESSION_REPORT_MISSING");
      }
      const publicSaveDecision = publicSession ? currentPublicSaveDecision() : null;
      if (publicSaveDecision?.state === "blocked") {
        deps.setStatus("최종 저장이 차단되었습니다. 서버 검토 세션을 다시 분석하세요.");
        return blocked("prepare", publicSaveDecision.reasonCodes[0] ?? "MASKING_SESSION_SAVE_BLOCKED");
      }
      const legalSaveDecision = currentRunKind() === "legal" ? currentLegalSaveDecision() : null;
      if (legalSaveDecision?.state === "blocked") {
        deps.setStatus("최종 저장이 차단되었습니다. 법률 보고서를 다시 확인하세요.");
        return blocked("prepare", legalSaveDecision.reasonCodes[0] ?? "LEGAL_SAVE_BLOCKED");
      }
      if (currentRunKind() === "legal" && deps.isPdfInput() && state.boxes.length > 0) {
        const applied = await deps.applyPendingManualBoxes("최종 저장 전 수동 보정 자동 반영");
        if (!applied) return blocked("prepare", "MANUAL_APPLY_FAILED");
        if (currentLegalSaveDecision().state === "blocked") {
          deps.setStatus("최종 저장이 차단되었습니다. 법률 보고서를 다시 확인하세요.");
          return blocked("prepare", "LEGAL_SAVE_BLOCKED");
        }
        updateWorkflowReadiness();
      }
      if (currentRunKind() === "none") {
        deps.setStatus("저장할 마스킹본이 없습니다. 먼저 기본 마스킹을 실행하세요.");
        return blocked("prepare", "MASKING_OUTPUT_MISSING");
      }
      const previewPdf = finalSaveSourcePath(state.documentProvenance);
      if (currentRunKind() === "legal" && !previewPdf) {
        deps.setStatus("저장할 마스킹본이 없습니다. 먼저 기본 마스킹을 실행하세요.");
        return blocked("prepare", "MASKING_OUTPUT_MISSING");
      }
      if (!warningsConfirmed) {
        openFinalSaveDialog();
        return finish("cancelled", "prepare", "SAVE_WARNINGS_CONFIRMATION_REQUIRED");
      }
      if (publicSession && authoritativeManifest && authoritativeReport) {
        const finalizationProvenance = state.documentProvenance;
        const finalizationEditRevision = state.documentEditRevision;
        const finalizationSessionIdentity = state.publicRunIdentity;
        const finalizationIdentity = boundSafeReportIdentity(authoritativeReport);
        const manifestIsCurrent = () => {
          const current = currentBoundPublicReport();
          const currentManifest = current?.analysisManifest;
          if (!currentManifest) return false;
          const currentIdentity = boundSafeReportIdentity(current);
          return state.documentProvenance === finalizationProvenance
            && state.documentEditRevision === finalizationEditRevision
            && state.publicRunIdentity === finalizationSessionIdentity
            && currentIdentity.runId === finalizationIdentity.runId
            && currentIdentity.originalDocumentHash === finalizationIdentity.originalDocumentHash
            && currentIdentity.analysisRevision === finalizationIdentity.analysisRevision
            && currentIdentity.manifestHash === finalizationIdentity.manifestHash
            && currentIdentity.profile === finalizationIdentity.profile
            && currentManifest.runId === finalizationIdentity.runId
            && currentManifest.originalDocumentHash === finalizationIdentity.originalDocumentHash
            && currentManifest.analysisRevision === finalizationIdentity.analysisRevision
            && currentManifest.manifestHash === finalizationIdentity.manifestHash
            && currentManifest.profile === finalizationIdentity.profile;
        };
        currentStage = "chooser";
        const saveTarget = await deps.invokeCommand<FinalPdfSaveTarget | null>("choose_final_pdf_path", {
          defaultFileName: finalSaveDefaultFileName(
            state.documentProvenance.original.path,
            publicSaveDecision?.state === "advisory",
          ),
          runId: finalizationIdentity.runId,
          analysisRevision: finalizationIdentity.analysisRevision,
          manifestHash: finalizationIdentity.manifestHash,
        });
        if (!saveTarget) {
          markStep("chooser", "cancelled", "SAVE_CHOOSER_CANCELLED");
          return finish("cancelled", "chooser", "SAVE_CHOOSER_CANCELLED");
        }
        markStep("chooser", "ok");
        outcome = { ...outcome, finalPath: saveTarget.outputPath };
        if (!manifestIsCurrent()) {
          deps.setStatus("저장 중 검토 세션이 변경되었습니다. 현재 검토 항목을 다시 확인하세요.");
          return blocked("prepare", "MASKING_SESSION_CHANGED_DURING_SAVE");
        }
        const saveDecisionAfterChooser = currentPublicSaveDecision();
        if (saveDecisionAfterChooser.state === "blocked") {
          deps.setStatus("최종 저장이 차단되었습니다. 서버 검토 세션을 다시 분석하세요.");
          return blocked("prepare", saveDecisionAfterChooser.reasonCodes[0] ?? "MASKING_SESSION_SAVE_BLOCKED");
        }
        currentStage = "prepare";
        const prepared = prepareFinalizeMaskingRun({
          runId: finalizationIdentity.runId,
          analysisRevision: finalizationIdentity.analysisRevision,
          manifestHash: finalizationIdentity.manifestHash,
          destination: saveTarget.outputPath,
          saveToken: saveTarget.saveToken,
          warningsConfirmed,
        }, authoritativeReport, state.restoreRevalidationFailed);
        if (!prepared.ok) {
          const issue = prepared.errors[0];
          const code = issue?.code ?? "MASKING_SESSION_PREPARE_REJECTED";
          markStep("prepare", "failed", code);
          deps.setStatus("최종 저장 실패: 현재 서버 검토 세션 또는 저장 경로를 검증하지 못했습니다.");
          return finish("blocked", "prepare", code, undefined, issue?.field ?? "finalize_request");
        }
        markStep("prepare", "ok");
        currentStage = "trusted-finalize";
        const finalized = parseFinalizeMaskingRunResult(
          await finalizeMaskingRun(deps.invokeCommand, prepared.value),
          prepared.value,
        );
        if (!finalized.ok) {
          const issue = finalized.errors[0];
          const code = issue?.code ?? "MASKING_SESSION_FINALIZE_RESULT_INVALID";
          markStep("trustedFinalize", "failed", code);
          deps.setStatus("파일은 저장되었으나 서버 응답을 검증하지 못했습니다. 저장된 PDF를 다시 열어주세요.");
          return finish("failed", "trusted-finalize", code, saveTarget.outputPath, issue?.field ?? "finalize_result");
        }
        markStep("trustedFinalize", "ok");
        outcome = {
          ...outcome,
          saveConfirmation: finalized.value.saveConfirmation,
          manualMaskCount: finalized.value.manualMaskCount ?? null,
          restoreCount: finalized.value.restoreCount ?? null,
          effectiveMaskCount: finalized.value.effectiveMaskCount ?? null,
          restoreAuthorization: finalized.value.restoreAuthorization ?? null,
        };
        if (!manifestIsCurrent() || currentPublicSaveDecision().state === "blocked") {
          deps.setStatus("파일은 저장되었으나 저장 후 검토 세션 또는 편집 내용이 변경되어 검증하지 못했습니다. 저장된 PDF를 다시 열어주세요.");
          return finish("failed", "trusted-finalize", "MASKING_SESSION_CHANGED_AFTER_SAVE", finalized.value.finalPath);
        }
        const finalizationWasCurrent = manifestIsCurrent();
        state.outputDir = parentDirectory(saveTarget.outputPath);
        const postCommitIssues: string[] = [];
        if (finalizationWasCurrent) {
          const hadBoxes = state.boxes.length > 0;
          state.latestReport = null;
          state.latestReportPath = "";
          state.activeRunKind = "none";
          state.boxes = [];
          state.geometryDraft = null;
          if (hadBoxes) state.documentEditRevision = (state.documentEditRevision || 0) + 1;
        } else {
          postCommitIssues.push("저장 후 변경된 검토 세션 또는 편집 내용은 유지되었습니다");
        }
        try {
          if (!(await deps.loadResultPdf(finalized.value.finalPath))) throw new Error("FINAL_RESULT_LOAD_FAILED");
          state.documentProvenance = adoptLoadVerifiedFinalContinuation(
            state.documentProvenance,
            finalized.value.finalPath,
          );
        } catch {
          state.documentProvenance = adoptUnavailableFinalContinuation(
            state.documentProvenance,
            finalized.value.finalPath,
          );
          state.resultDoc = null;
          postCommitIssues.push("저장된 PDF를 작업공간에서 다시 열지 못했습니다");
        }
        try {
          if (deps.saveSettings(deps.collectSettings()).diagnostic.status === "failed") {
            postCommitIssues.push("설정을 저장하지 못했습니다");
          }
        } catch {
          postCommitIssues.push("설정을 저장하지 못했습니다");
        }
        try {
          deps.recordSavedAt(new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false }));
        } catch {
          postCommitIssues.push("저장 시각을 기록하지 못했습니다");
        }
        const savedStatus = postCommitIssues.length > 0
          ? `최종 저장 완료, ${postCommitIssues.join("; ")}.`
          : "최종 저장 완료";
        deps.setStatus(savedStatus);
        deps.showFinalizationSuccess(finalized.value.finalPath, finalized.value.saveConfirmation);
        if (currentSettings().openOutputAfterSave) {
          void deps.openPath(finalized.value.finalPath).catch(() => {
            deps.setStatus(`${savedStatus}, 파일을 열지 못했습니다.`);
          });
        }
        currentStage = "file-stat";
        return finish("ok", "file-stat", null, finalized.value.finalPath);
      }

      const legalGate = currentLegalSaveDecision();
      if (legalGate.state === "blocked") {
        deps.setStatus("최종 저장이 차단되었습니다. 법률 보고서를 다시 확인하세요.");
        return blocked("prepare", legalGate.reasonCodes[0] ?? "LEGAL_SAVE_BLOCKED");
      }
      const finalizationSnapshot = {
        provenance: state.documentProvenance,
        editRevision: state.documentEditRevision,
        previewPdf,
        originalPdf: state.documentProvenance.original.path,
        reportPath: state.latestReportPath,
        maskedPath: currentSettings().exportMaskedText
          && state.latestMaskedTextPolicy === currentSettings().deidentificationMode
          ? state.latestMaskedPath
          : "",
      };
      const finalizationIsCurrent = () => state.documentProvenance === finalizationSnapshot.provenance
        && state.documentEditRevision === finalizationSnapshot.editRevision
        && finalSaveSourcePath(state.documentProvenance) === finalizationSnapshot.previewPdf
        && state.documentProvenance.original.path === finalizationSnapshot.originalPdf
        && state.latestReportPath === finalizationSnapshot.reportPath
        && (currentLegalSaveDecision().state !== "blocked");

      currentStage = "chooser";
      const saveTarget = await deps.invokeCommand<FinalPdfSaveTarget | null>("choose_final_pdf_path", {
        defaultFileName: finalSaveDefaultFileName(finalizationSnapshot.originalPdf),
        mode: "legacy_direct",
      });
      if (!saveTarget) {
        markStep("chooser", "cancelled", "SAVE_CHOOSER_CANCELLED");
        return finish("cancelled", "chooser", "SAVE_CHOOSER_CANCELLED");
      }
      markStep("chooser", "ok");
      outcome = { ...outcome, finalPath: saveTarget.outputPath };
      if (!finalizationIsCurrent()) {
        deps.setStatus("저장 중 문서 또는 검토 상태가 변경되었습니다. 다시 저장하세요.");
        return blocked("prepare", "MASKING_SESSION_CHANGED_DURING_SAVE");
      }

      deps.setStatus("최종 저장 중...");
      currentStage = "trusted-finalize";
      const result = await deps.invokeCommand<LegacyFinalizeResult>("finalize_manual_output_to_selected_path", {
        previewPdf: finalizationSnapshot.previewPdf,
        originalPdf: finalizationSnapshot.originalPdf,
        outputPath: saveTarget.outputPath,
        saveToken: saveTarget.saveToken,
        extractedPath: "",
        maskedPath: finalizationSnapshot.maskedPath,
        reportPath: finalizationSnapshot.reportPath,
        copyReport: false,
      });
      if (!result || typeof result.final_output_file !== "string" || result.final_output_file !== saveTarget.outputPath) {
        markStep("trustedFinalize", "failed", "LEGACY_FINALIZE_RESULT_INVALID");
        deps.setStatus("최종 저장 실패: 서버 응답의 저장 경로를 검증하지 못했습니다.");
        return finish("failed", "trusted-finalize", "LEGACY_FINALIZE_RESULT_INVALID", saveTarget.outputPath);
      }
      markStep("trustedFinalize", "ok");
      if (!finalizationIsCurrent()) {
        deps.setStatus("파일은 저장되었으나 저장 중 문서 변경으로 무결성 확인을 완료하지 못했습니다. 저장된 PDF를 다시 열어주세요.");
        return finish("failed", "trusted-finalize", "MASKING_SESSION_CHANGED_AFTER_SAVE", saveTarget.outputPath);
      }
      const selectedOutdir = parentDirectory(saveTarget.outputPath);
      state.outputDir = selectedOutdir;
      const postCommitIssues: string[] = [];
      try {
        if (deps.saveSettings(deps.collectSettings()).diagnostic.status === "failed") {
          postCommitIssues.push("설정을 저장하지 못했습니다");
        }
      } catch {
        postCommitIssues.push("설정을 저장하지 못했습니다");
      }
      const savedMessage = (message: string) => postCommitIssues.length > 0
        ? `${message}, ${postCommitIssues.join("; ")}.`
        : message;
      state.latestMaskedPath = "";
      state.latestMaskedTextPolicy = "";
      const copied = result.copied_files && result.copied_files.length > 0 ? ` / 부가산출물 ${result.copied_files.length}건` : "";
      const markContinuationUnavailable = (message: string) => {
        state.documentProvenance = adoptUnavailableFinalContinuation(
          state.documentProvenance,
          saveTarget.outputPath,
        );
        state.resultDoc = null;
        deps.setStatus(savedMessage(message));
      };
      try {
        if (!(await deps.loadResultPdf(saveTarget.outputPath))) throw new Error("FINAL_RESULT_LOAD_FAILED");
        if (!finalizationIsCurrent()) {
          return finish("failed", "trusted-finalize", "MASKING_SESSION_CHANGED_AFTER_SAVE", saveTarget.outputPath);
        }
        state.documentProvenance = adoptLoadVerifiedFinalContinuation(
          state.documentProvenance,
          saveTarget.outputPath,
        );
        try {
          deps.recordSavedAt(new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false }));
        } catch {
          postCommitIssues.push("저장 시각을 기록하지 못했습니다");
        }
        deps.setStatus(savedMessage(`최종 저장 완료${copied}`));
        deps.showFinalizationSuccess(saveTarget.outputPath);
      } catch {
        if (!finalizationIsCurrent()) {
          return finish("failed", "trusted-finalize", "MASKING_SESSION_CHANGED_AFTER_SAVE", saveTarget.outputPath);
        }
        markContinuationUnavailable("파일은 저장되었으나 무결성 확인에 실패했습니다. 저장된 PDF를 다시 열어주세요.");
      }
      deps.updateCanvasControls();
      updateWorkflowReadiness();
      if (currentSettings().openOutputAfterSave) {
        deps.openPath(selectedOutdir).catch(() => {
          if (state.documentProvenance.continuation?.state === "ready") {
            deps.setStatus(`${savedMessage(`최종 저장 완료${copied}`)}, 파일 위치를 열지 못했습니다.`);
          }
        });
      }
      currentStage = "file-stat";
      const legacyFinalStatus = state.documentProvenance.continuation?.state === "ready" ? "ok" : "failed";
      return finish(
        legacyFinalStatus,
        "file-stat",
        legacyFinalStatus === "ok" ? null : "FINAL_RESULT_LOAD_FAILED",
        saveTarget.outputPath,
      );
    } catch (error) {
      const failure = presentMaskingFailure(error);
      outcome = { ...outcome, failureDiagnostics: failure.diagnostics };
      const failureStep: keyof FinalSaveOutcome["steps"] = currentStage === "chooser"
        ? "chooser"
        : currentStage === "trusted-finalize"
          ? "trustedFinalize"
          : "prepare";
      markStep(failureStep, "failed", failure.code);
      const prefix = `최종 저장 실패 (${failure.code} · ${failure.hint})`;
      deps.setStatus(failure.code.includes("SAVE_OVERWRITE_RECONFIRM_REQUIRED")
        ? `${prefix}: PDF 확장자를 적용한 경로에 기존 파일이 있습니다. 저장 다이얼로그에서 .pdf 파일명을 직접 입력해 덮어쓰기를 다시 확인해 주세요.`
        : `${prefix}: 저장 작업을 완료하지 못했습니다.`);
      return finish("failed", currentStage, failure.code, outcome.finalPath);
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
