import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { currentSettings } from "../../state/settingsStore";
import { documentKindForPath } from "../../batchQueue";
import { defaultOutputDirForSelection } from "../../services/tauri/defaultOutputDir";
import {
  adoptGeneratedPreview,
  canvasWindowTargetCandidates,
  canvasWindowTargetPath,
  emptyDocumentProvenance,
  resetDerivedProvenance,
  resultSourcePath,
  selectOriginalDocument,
} from "../../state/documentProvenance";
import type { ApplicationSessionState } from "../../app/compositionRoot";
import {
  measureQaDriveStage,
  qaDriveCancellationError,
  withQaDriveCancellation,
} from "../../app/qaDriveProtocol";

import { parseLegacySafeReport, parseSafeReport } from "../../state/maskingSession";
import type { BaseMaskingProgress, SafeReport } from "../../state/maskingSession";

export type BoxMode = "mask" | "restore";
export type ResetReason = "new-document" | "clear" | "canvas-hydrate" | "remask";

type InvokeCommand = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

type CanvasWindowLaunchPayload = {
  targetPath: string;
  originalPath: string;
  outputDir: string;
  reportPath: string;
  mode: BoxMode;
  savedAt: number;
};

export type DocumentSessionState = Pick<
  ApplicationSessionState,
  | "documentProvenance"
  | "outputDir"
  | "previewWorkDir"
  | "currentOrigPage"
  | "currentResultPage"
  | "boxes"
  | "geometryDraft"
  | "documentEditRevision"
  | "selectedCanvasBoxIndex"
  | "origDoc"
  | "resultDoc"
  | "extractedText"
  | "maskedText"
  | "baseExtractedText"
  | "baseMaskedText"
  | "initialMaskingPreviewPdf"
  | "initialExtractedText"
  | "initialMaskedText"
  | "preManualPreviewPdf"
  | "preManualExtractedText"
  | "preManualMaskedText"
  | "lastPreviewDiagnostics"
  | "latestExtractedPath"
  | "latestMaskedPath"
  | "latestMaskedTextPolicy"
  | "latestReportPath"
  | "latestReport"
  | "activeRunKind"
  | "publicRunIdentity"
  | "publicReportBindingReason"
  | "restoreRevalidationFailed"
  | "baseMaskingProgress"
  | "mode"
  | "batchItems"
  | "batchActiveIndex"
  | "batchRunning"
>;

export type DocumentSessionDeps = {
  readonly state: DocumentSessionState;
  readonly inputPathEl: HTMLInputElement;
  readonly modeMask: HTMLButtonElement;
  readonly modeRestore: HTMLButtonElement;
  readonly invokeCommand: InvokeCommand;
  readonly hasTauriRuntime: () => boolean;
  readonly clampPage: (page: number, doc: PDFDocumentProxy | null) => number;
  readonly renderCompare: (signal?: AbortSignal) => Promise<void>;
  readonly renderDocumentReviewSurfaces: () => void;
  readonly setCompareMode: (mode: "pdf" | "text") => void;
  readonly setTextCompareContents: (extractedText: string, maskedText: string) => void;
  readonly setBaseMaskingProgress: (progress: BaseMaskingProgress) => void;
  readonly renderFinalState: (report: SafeReport | null) => void;
  readonly updateOutputDirectoryState: () => void;
  readonly updateWorkflowReadiness: () => void;
  readonly updateCanvasControls: () => void;
  readonly cancelGeometryDraft?: () => boolean;
  readonly setCanvasMode: (active: boolean, options?: { allowEmptyCanvas?: boolean }) => void;
  readonly redrawOverlay: () => void;
  readonly updateMeta: () => void;
  readonly resetLastSavedAt: () => void;
  readonly isBusy: () => boolean;
  readonly confirmDiscardCurrentWork: () => Promise<boolean>;
  readonly resetCompareView: () => void;
  readonly renderBatchQueue: () => void;
  readonly closeTransientDialogs: () => void;
  readonly setStatus: (message: string) => void;
};

export type DocumentSessionController = {
  readonly loadPdfDoc: (path: string) => Promise<PDFDocumentProxy>;
  readonly loadOriginalDocument: (path: string) => Promise<void>;
  readonly loadOriginalPdf: (path: string) => Promise<void>;
  readonly loadCanvasWorkspacePdf: (targetPath: string, originalPath?: string, signal?: AbortSignal) => Promise<boolean>;
  readonly loadResultPdf: (path: string) => Promise<boolean>;
  readonly pickInputDocument: () => Promise<void>;
  readonly pickCanvasPdf: () => Promise<void>;
  readonly ensurePreviewWorkDir: () => Promise<string>;
  readonly maybeApplyDefaultOutputDir: (paths: readonly string[]) => Promise<void>;
  readonly hydrateStandaloneCanvasWindow: () => Promise<void>;
  readonly openCanvasDesktopWindow: () => Promise<void>;
  readonly clearDerivedArtifacts: () => Promise<void>;
  readonly getResultSourcePath: () => string;
  readonly getCanvasWindowTargetPath: () => string;
  readonly getCanvasWindowTargetPathCandidates: () => string[];
  readonly resetDerivedArtifacts: (reason: ResetReason, targetPath?: string) => void;
  readonly resetDocumentSession: () => Promise<boolean>;
  readonly prepareForDocumentReplacement: () => Promise<boolean>;
  readonly invalidateLifecycle: () => void;
};

function hasPublicReportSchema(value: unknown): boolean {
  return typeof value === "object"
    && value !== null
    && ("analysisManifest" in value || "reviewQueue" in value);
}

async function sha256PdfBytes(invokeCommand: InvokeCommand, path: string): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error("original identity hash unavailable");
  const bytes = await invokeCommand<number[]>("read_pdf_bytes", { path });
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new Uint8Array(bytes));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function createDocumentSessionController(deps: DocumentSessionDeps): DocumentSessionController {
  const { state } = deps;
  let lifecycleEpoch = 0;
  let pickerInFlight = false;

  const beginLifecycle = (): number => ++lifecycleEpoch;
  const ownsLifecycle = (epoch: number): boolean => epoch === lifecycleEpoch;
  const invalidateLifecycle = (): void => {
    lifecycleEpoch += 1;
  };
  function getResultSourcePath(): string {
    return resultSourcePath(state.documentProvenance);
  }

  function getCanvasWindowTargetPath(): string {
    return canvasWindowTargetPath(state.documentProvenance);
  }

  function getCanvasWindowTargetPathCandidates(): string[] {
    return canvasWindowTargetCandidates(state.documentProvenance);
  }

  function resetDerivedArtifacts(reason: ResetReason, targetPath = ""): void {
    invalidateLifecycle();
    const geometryDraftCancelled = deps.cancelGeometryDraft?.() ?? false;
    const originalPath = state.documentProvenance.original.path;
    const hydratedPreview = reason === "canvas-hydrate" && targetPath !== originalPath ? targetPath : "";
    state.documentProvenance = resetDerivedProvenance(state.documentProvenance);
    if (hydratedPreview) {
      state.documentProvenance = adoptGeneratedPreview(state.documentProvenance, hydratedPreview, hydratedPreview);
    }
    state.resultDoc = null;
    state.currentResultPage = 1;
    state.boxes = [];
    state.geometryDraft = null;
    if (!geometryDraftCancelled) state.documentEditRevision = (state.documentEditRevision || 0) + 1;
    state.selectedCanvasBoxIndex = -1;
    state.extractedText = "";
    state.maskedText = "";
    state.baseExtractedText = "";
    state.baseMaskedText = "";
    state.initialMaskingPreviewPdf = hydratedPreview;
    state.initialExtractedText = "";
    state.initialMaskedText = "";
    state.preManualPreviewPdf = hydratedPreview;
    state.preManualExtractedText = "";
    state.preManualMaskedText = "";
    state.lastPreviewDiagnostics = "";
    state.latestExtractedPath = "";
    state.latestMaskedPath = "";
    state.latestMaskedTextPolicy = "";
    state.latestReportPath = "";
    state.latestReport = null;
    state.activeRunKind = "none";
    state.publicRunIdentity = null;
    state.publicReportBindingReason = null;
    state.restoreRevalidationFailed = false;
    deps.setBaseMaskingProgress({
      status: hydratedPreview ? "complete" : "idle",
      percent: hydratedPreview ? 100 : 0,
      displayMode: currentSettings().displayMode,
    });
    deps.setTextCompareContents("", "");
    deps.renderFinalState(null);
  }

  function hasUnsavedDocumentWork(): boolean {
    if (!state.documentProvenance.original.path) return false;
    return state.documentProvenance.continuation?.state !== "ready" || state.boxes.length > 0;
  }

  async function resetDocumentSession(): Promise<boolean> {
    if (deps.isBusy()) {
      deps.setStatus("실행 중입니다. 완료 후 새 작업을 시작하세요.");
      return false;
    }
    invalidateLifecycle();
    const geometryDraftCancelled = deps.cancelGeometryDraft?.() ?? false;
    state.documentProvenance = emptyDocumentProvenance();
    state.outputDir = "";
    state.previewWorkDir = "";
    state.currentOrigPage = 1;
    state.currentResultPage = 1;
    state.boxes = [];
    state.geometryDraft = null;
    if (!geometryDraftCancelled) state.documentEditRevision = (state.documentEditRevision || 0) + 1;
    state.selectedCanvasBoxIndex = -1;
    state.origDoc = null;
    state.resultDoc = null;
    state.extractedText = "";
    state.maskedText = "";
    state.baseExtractedText = "";
    state.baseMaskedText = "";
    state.initialMaskingPreviewPdf = "";
    state.initialExtractedText = "";
    state.initialMaskedText = "";
    state.preManualPreviewPdf = "";
    state.preManualExtractedText = "";
    state.preManualMaskedText = "";
    state.lastPreviewDiagnostics = "";
    state.latestExtractedPath = "";
    state.latestMaskedPath = "";
    state.latestMaskedTextPolicy = "";
    state.latestReportPath = "";
    state.latestReport = null;
    state.activeRunKind = "none";
    state.publicRunIdentity = null;
    state.publicReportBindingReason = null;
    state.restoreRevalidationFailed = false;
    state.mode = "mask";
    state.batchItems = [];
    state.batchActiveIndex = -1;
    state.batchRunning = false;
    deps.modeMask.setAttribute("aria-pressed", "true");
    deps.modeRestore.setAttribute("aria-pressed", "false");
    deps.inputPathEl.value = "";
    deps.resetLastSavedAt();
    deps.closeTransientDialogs();
    deps.resetCompareView();
    deps.setBaseMaskingProgress({ status: "idle", percent: 0, displayMode: currentSettings().displayMode });
    deps.setTextCompareContents("", "");
    deps.renderFinalState(null);
    await deps.renderCompare();
    deps.redrawOverlay();
    deps.renderBatchQueue();
    deps.renderDocumentReviewSurfaces();
    deps.updateOutputDirectoryState();
    deps.updateCanvasControls();
    deps.updateWorkflowReadiness();
    deps.setStatus("대기 중: PDF 열기 → 마스킹 실행 → 검토 → 최종 저장");
    return true;
  }

  async function prepareForDocumentReplacement(): Promise<boolean> {
    if (deps.isBusy()) {
      deps.setStatus("실행 중에는 새 문서를 열 수 없습니다.");
      return false;
    }
    if (hasUnsavedDocumentWork() && !(await deps.confirmDiscardCurrentWork())) return false;
    if (state.documentProvenance.original.path || state.batchItems.length > 0) {
      return resetDocumentSession();
    }
    return true;
  }

  async function loadPdfDoc(path: string, signal?: AbortSignal): Promise<PDFDocumentProxy> {
    const bytes = signal
      ? await measureQaDriveStage(
        "read_pdf_bytes",
        () => withQaDriveCancellation(
          () => deps.invokeCommand<number[]>("read_pdf_bytes", { path }),
          signal,
          "read_pdf_bytes",
        ),
      )
      : await deps.invokeCommand<number[]>("read_pdf_bytes", { path });
    if (signal?.aborted) throw qaDriveCancellationError("read_pdf_bytes");
    const loadingTask = pdfjsLib.getDocument({ data: new Uint8Array(bytes) });
    const document = signal
      ? await measureQaDriveStage(
        "pdfjs_get_document",
        () => withQaDriveCancellation(
          () => loadingTask.promise,
          signal,
          "pdfjs_get_document",
          () => loadingTask.destroy(),
        ),
      )
      : await loadingTask.promise;
    if (signal?.aborted) {
      void document.destroy();
      throw qaDriveCancellationError("pdfjs_get_document");
    }
    return document;
  }

  async function loadOriginalPdf(path: string): Promise<void> {
    let epoch = beginLifecycle();
    const originalDoc = await loadPdfDoc(path);
    if (!ownsLifecycle(epoch)) return;
    state.documentProvenance = selectOriginalDocument(state.documentProvenance, path, "pdf");
    resetDerivedArtifacts("new-document");
    epoch = beginLifecycle();
    state.origDoc = originalDoc;
    state.resultDoc = originalDoc;
    state.currentOrigPage = 1;
    state.currentResultPage = 1;
    deps.renderDocumentReviewSurfaces();
    deps.setCompareMode("pdf");
    deps.inputPathEl.value = path;
    await deps.renderCompare();
    if (!ownsLifecycle(epoch)) return;
    deps.updateWorkflowReadiness();
  }

  async function loadCanvasWorkspacePdf(
    targetPath: string,
    originalPath = "",
    signal?: AbortSignal,
  ): Promise<boolean> {
    let epoch = beginLifecycle();
    const sourcePath = originalPath || targetPath;
    const originalDoc = await loadPdfDoc(sourcePath, signal);
    if (signal?.aborted) {
      void originalDoc.destroy();
      throw qaDriveCancellationError("loadCanvasWorkspacePdf");
    }
    if (!ownsLifecycle(epoch)) {
      void originalDoc.destroy();
      return false;
    }
    const resultDoc = targetPath === sourcePath ? originalDoc : await loadPdfDoc(targetPath, signal);
    if (signal?.aborted) {
      if (resultDoc !== originalDoc) void resultDoc.destroy();
      void originalDoc.destroy();
      throw qaDriveCancellationError("loadCanvasWorkspacePdf");
    }
    if (!ownsLifecycle(epoch)) {
      if (resultDoc !== originalDoc) void resultDoc.destroy();
      void originalDoc.destroy();
      return false;
    }

    const previousState = { ...state };
    const previousInputPath = deps.inputPathEl.value;
    state.documentProvenance = selectOriginalDocument(state.documentProvenance, sourcePath, "pdf");
    resetDerivedArtifacts("canvas-hydrate", targetPath);
    epoch = beginLifecycle();
    state.origDoc = originalDoc;
    state.resultDoc = resultDoc;
    state.currentOrigPage = 1;
    state.currentResultPage = 1;
    deps.setCompareMode("pdf");
    deps.inputPathEl.value = sourcePath;
    try {
      await deps.renderCompare(signal);
      if (signal?.aborted) throw qaDriveCancellationError("loadCanvasWorkspacePdf");
      if (!ownsLifecycle(epoch)) return false;
      return true;
    } catch (error) {
      if (ownsLifecycle(epoch)) {
        Object.assign(state, previousState);
        deps.inputPathEl.value = previousInputPath;
        try {
          await deps.renderCompare();
        } catch {
          // Keep the prior committed state even when restoring its canvas fails.
        }
      }
      throw error;
    }
  }

  async function readCanvasWindowLaunchState(): Promise<CanvasWindowLaunchPayload | null> {
    if (!deps.hasTauriRuntime()) return null;
    const token = new URLSearchParams(window.location.search).get("token") || "";
    if (!token) return null;
    let parsed: Partial<CanvasWindowLaunchPayload> | null;
    try {
      parsed = await deps.invokeCommand<Partial<CanvasWindowLaunchPayload> | null>("take_canvas_launch_payload", { token });
    } catch (error) {
      throw new Error(`canvas launch payload unavailable (${safeFailureCode(error)})`);
    }
    if (
      !parsed
      || typeof parsed.targetPath !== "string"
      || (parsed.originalPath !== undefined && typeof parsed.originalPath !== "string")
      || (parsed.outputDir !== undefined && typeof parsed.outputDir !== "string")
      || (parsed.reportPath !== undefined && typeof parsed.reportPath !== "string")
      || (parsed.mode !== undefined && parsed.mode !== "mask" && parsed.mode !== "restore")
      || (parsed.savedAt !== undefined && typeof parsed.savedAt !== "number")
    ) {
      throw new Error("invalid canvas launch payload");
    }
    return {
      targetPath: parsed.targetPath,
      originalPath: typeof parsed.originalPath === "string" ? parsed.originalPath : "",
      outputDir: typeof parsed.outputDir === "string" ? parsed.outputDir : "",
      reportPath: typeof parsed.reportPath === "string" ? parsed.reportPath : "",
      mode: parsed.mode === "restore" ? "restore" : "mask",
      savedAt: typeof parsed.savedAt === "number" ? parsed.savedAt : 0,
    };
  }

  async function hydrateStandaloneCanvasWindow(): Promise<void> {
    let payload: CanvasWindowLaunchPayload | null;
    try {
      payload = await readCanvasWindowLaunchState();
    } catch (error) {
      deps.setCanvasMode(true, { allowEmptyCanvas: true });
      deps.setStatus(`독립 작업창 실행 정보를 불러오지 못했습니다 (${safeFailureCode(error)}). 창을 닫고 문서에서 다시 여세요.`);
      deps.updateCanvasControls();
      return;
    }
    deps.setCanvasMode(true, { allowEmptyCanvas: true });
    if (payload?.outputDir) {
      state.outputDir = payload.outputDir;
      deps.updateOutputDirectoryState();
    }
    if (payload?.mode === "restore") {
      deps.modeMask.setAttribute("aria-pressed", "false");
      deps.modeRestore.setAttribute("aria-pressed", "true");
      state.mode = "restore";
    } else {
      deps.modeMask.setAttribute("aria-pressed", "true");
      deps.modeRestore.setAttribute("aria-pressed", "false");
      state.mode = "mask";
    }
    if (!payload?.targetPath) {
      deps.setStatus("독립 작업창 대기 중: PDF 불러오기 → 박스 그리기 → 미리보기 반영");
      deps.updateCanvasControls();
      return;
    }
    try {
      let reportValidationFailed = false;
      const targetAdopted = await loadCanvasWorkspacePdf(payload.targetPath, payload.originalPath || payload.targetPath);
      if (!targetAdopted) return;
      const reportEpoch = lifecycleEpoch;
      const reportProvenance = state.documentProvenance;
      const reportEditRevision = state.documentEditRevision;
      const reportIsCurrent = () => ownsLifecycle(reportEpoch)
        && state.documentProvenance === reportProvenance
        && state.documentEditRevision === reportEditRevision;
      if (payload.reportPath) {
        try {
          const reportText = await deps.invokeCommand<string>("read_text_file", { path: payload.reportPath });
          if (!reportIsCurrent()) return;
          const reportValue: unknown = JSON.parse(reportText);
          const parsedReport = hasPublicReportSchema(reportValue)
            ? parseSafeReport(reportValue)
            : parseLegacySafeReport(reportValue);
          if (!parsedReport.ok) throw new Error("invalid report");
          if (parsedReport.value.analysisManifest) {
            const originalHash = await sha256PdfBytes(deps.invokeCommand, reportProvenance.original.path);
            if (!reportIsCurrent() || originalHash !== parsedReport.value.analysisManifest.originalDocumentHash) {
              throw new Error("report original identity mismatch");
            }
          }
          if (!reportIsCurrent()) return;
          state.latestReport = parsedReport.value;
          state.latestReportPath = payload.reportPath;
          state.activeRunKind = parsedReport.value.analysisManifest ? "public" : "legal";
          deps.renderFinalState(state.latestReport);
        } catch (error) {
          if (!reportIsCurrent()) return;
          reportValidationFailed = true;
          state.latestReport = null;
          state.latestReportPath = "";
          state.activeRunKind = "none";
          deps.renderFinalState(null);
          deps.setStatus(`독립 작업창 자동 검증 정보를 검증하지 못했습니다 (${safeFailureCode(error)}). 마스킹을 다시 실행하세요.`);
        }
      } else {
        if (!reportIsCurrent()) return;
        deps.renderFinalState(null);
      }
      if (!reportIsCurrent()) return;
      deps.setStatus(reportValidationFailed ? "독립 작업창 로드 완료 (자동 검증 정보 없음)" : "독립 작업창 로드 완료");
    } catch (error) {
      deps.setStatus(`독립 작업창 자동 로드 실패 (${safeFailureCode(error)}): PDF 불러오기로 다시 선택하세요.`);
    }
    deps.updateCanvasControls();
  }

  async function loadOriginalDocument(path: string): Promise<void> {
    deps.resetLastSavedAt();
    if (documentKindForPath(path) === "pdf") {
      await loadOriginalPdf(path);
      return;
    }
    throw new Error("PDF 파일만 선택할 수 있습니다.");
  }

  async function maybeApplyDefaultOutputDir(selectedDocumentPaths: readonly string[]): Promise<void> {
    const resolved = await defaultOutputDirForSelection(deps.invokeCommand, {
      currentOutputDir: state.outputDir,
      selectedDocumentPaths,
    });
    if (!resolved || resolved === state.outputDir) return;
    state.outputDir = resolved;
    deps.updateOutputDirectoryState();
  }

  async function loadResultPdf(path: string): Promise<boolean> {
    const epoch = beginLifecycle();
    const expectedProvenance = state.documentProvenance;
    const expectedEditRevision = state.documentEditRevision;
    const expectedReport = state.latestReport;
    const expectedReportPath = state.latestReportPath;
    const previousResultDoc = state.resultDoc;
    const previousResultPage = state.currentResultPage;
    const isCurrent = () => ownsLifecycle(epoch)
      && state.documentProvenance === expectedProvenance
      && state.documentEditRevision === expectedEditRevision
      && state.latestReport === expectedReport
      && state.latestReportPath === expectedReportPath;
    try {
      const resultDoc = await loadPdfDoc(path);
      if (!isCurrent()) return false;
      state.resultDoc = resultDoc;
      state.currentResultPage = deps.clampPage(previousResultPage, resultDoc);
      await deps.renderCompare();
      if (!isCurrent()) {
        if (ownsLifecycle(epoch) && state.resultDoc === resultDoc) {
          state.resultDoc = previousResultDoc;
          state.currentResultPage = previousResultPage;
          try {
            await deps.renderCompare();
          } catch (rollbackError) {
            state.lastPreviewDiagnostics = `수정본 PDF 세션 변경 / 이전 화면 복구 실패 (${safeFailureCode(rollbackError)})`;
          }
        }
        return false;
      }
      state.lastPreviewDiagnostics = "";
      return true;
    } catch (error) {
      if (!ownsLifecycle(epoch)) return false;
      state.resultDoc = previousResultDoc;
      state.currentResultPage = previousResultPage;
      let rollbackFailure = "";
      try {
        await deps.renderCompare();
      } catch (rollbackError) {
        rollbackFailure = ` / 이전 화면 복구 실패 (${safeFailureCode(rollbackError)})`;
      }
      state.lastPreviewDiagnostics = `수정본 PDF 로드 실패 (${safeFailureCode(error)})${rollbackFailure}`;
      const previewFailure = new Error(`수정본 PDF를 불러오지 못했습니다 (${safeFailureCode(error)}).${rollbackFailure}`);
      Object.defineProperty(previewFailure, "cause", { value: error });
      throw previewFailure;
    }
  }

  function safeFailureCode(error: unknown): "ipc" | "pdf" | "invalid" | "unknown" {
    if (error instanceof SyntaxError) return "invalid";
    const message = error instanceof Error ? error.message.toLowerCase() : "";
    if (message.includes("ipc") || message.includes("invoke") || message.includes("permission")) return "ipc";
    if (message.includes("pdf") || message.includes("document")) return "pdf";
    return "unknown";
  }

  async function ensurePreviewWorkDir(): Promise<string> {
    if (state.previewWorkDir) return state.previewWorkDir;
    const epoch = lifecycleEpoch;
    const previewWorkDir = await deps.invokeCommand<string>("get_preview_workdir");
    if (!ownsLifecycle(epoch)) throw new Error("stale document session");
    state.previewWorkDir = previewWorkDir;
    return previewWorkDir;
  }

  async function pickInputDocument(): Promise<void> {
    if (pickerInFlight) return;
    pickerInFlight = true;
    try {
      const selected = await deps.invokeCommand<string | null>("pick_input_document");
      if (!selected || !(await prepareForDocumentReplacement())) return;
      await loadOriginalDocument(selected);
      if (state.documentProvenance.original.path !== selected) return;
      await maybeApplyDefaultOutputDir([selected]);
      if (state.documentProvenance.original.path !== selected) return;
      deps.setStatus("원문 PDF 로드 완료");
    } catch (error) {
      deps.setStatus(`문서 로드 실패 (${safeFailureCode(error)})`);
    } finally {
      pickerInFlight = false;
    }
  }

  async function pickCanvasPdf(): Promise<void> {
    if (pickerInFlight) return;
    pickerInFlight = true;
    try {
      const selected = await deps.invokeCommand<string | null>("pick_input_pdf");
      if (!selected || !(await prepareForDocumentReplacement())) return;
      await loadOriginalPdf(selected);
      if (state.documentProvenance.original.path !== selected) return;
      await maybeApplyDefaultOutputDir([selected]);
      if (state.documentProvenance.original.path !== selected) return;
      deps.setCanvasMode(true, { allowEmptyCanvas: true });
      deps.setStatus("캔버스 PDF 로드 완료");
    } catch (error) {
      deps.setStatus(`캔버스 PDF 로드 실패 (${safeFailureCode(error)})`);
    } finally {
      pickerInFlight = false;
    }
  }

  async function createCanvasWindowLaunchToken(targetPath: string): Promise<string> {
    const payload: CanvasWindowLaunchPayload = {
      targetPath,
      originalPath: state.documentProvenance.original.kind === "pdf" ? state.documentProvenance.original.path : "",
      outputDir: state.outputDir,
      reportPath: state.latestReportPath,
      mode: state.mode,
      savedAt: Date.now(),
    };
    return deps.invokeCommand<string>("create_canvas_launch_token", { payload });
  }

  async function openCanvasDesktopWindow(): Promise<void> {
    let targetPath = getCanvasWindowTargetPath();
    if (!deps.hasTauriRuntime()) {
      deps.setStatus("브라우저 미리보기에서는 별도 작업창을 띄울 수 없습니다. 캔버스 화면에서 직접 보정하세요.");
      return;
    }
    try {
      let launchToken = "";
      let candidateFailure = "";
      const candidates = getCanvasWindowTargetPathCandidates();
      if (candidates.length === 0) {
        launchToken = await createCanvasWindowLaunchToken("");
        targetPath = "";
      } else {
        for (const candidate of candidates) {
          try {
            launchToken = await createCanvasWindowLaunchToken(candidate);
            targetPath = candidate;
            break;
          } catch (error) {
            candidateFailure = safeFailureCode(error);
          }
        }
      }
      if (!launchToken) throw new Error(candidateFailure ? `candidate-${candidateFailure}` : "missing candidate");
      await deps.invokeCommand("open_mask_canvas_window", { targetPath: launchToken });
      deps.setStatus(targetPath ? "마스킹 작업창을 열었습니다." : "마스킹 작업창을 열었습니다. 작업창에서 PDF를 불러오세요.");
    } catch (error) {
      deps.setStatus(`마스킹 작업창 열기 실패 (${safeFailureCode(error)})`);
    }
  }

  async function clearDerivedArtifacts(): Promise<void> {
    if (deps.isBusy()) {
      deps.setStatus("실행 중입니다. 완료 후 초기화하세요.");
      return;
    }
    invalidateLifecycle();
    const hadBoxes = state.boxes.length > 0;
    const originalPath = state.documentProvenance.original.path;
    resetDerivedArtifacts("clear");
    if (!originalPath) {
      deps.redrawOverlay();
      deps.updateMeta();
      deps.setStatus(hadBoxes ? "전체초기화 완료" : "초기화할 항목이 없습니다.");
      deps.updateWorkflowReadiness();
      return;
    }
    try {
      if (!(await loadResultPdf(originalPath))) return;
      deps.setCompareMode("pdf");
      deps.setStatus("전체초기화 완료 (마스킹 실행 이전 원본 상태로 복원)");
    } catch {
      deps.setStatus("전체초기화 실패: 원본 미리보기를 복원하지 못했습니다.");
    }
    deps.updateWorkflowReadiness();
  }

  return {
    loadPdfDoc,
    loadOriginalDocument,
    loadOriginalPdf,
    loadCanvasWorkspacePdf,
    loadResultPdf,
    pickInputDocument,
    pickCanvasPdf,
    ensurePreviewWorkDir,
    maybeApplyDefaultOutputDir,
    hydrateStandaloneCanvasWindow,
    openCanvasDesktopWindow,
    clearDerivedArtifacts,
    getResultSourcePath,
    getCanvasWindowTargetPath,
    getCanvasWindowTargetPathCandidates,
    resetDerivedArtifacts,
    resetDocumentSession,
    prepareForDocumentReplacement,
    invalidateLifecycle,
  };
}
