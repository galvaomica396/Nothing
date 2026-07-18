import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
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
import type { LegacySessionState } from "../../legacy/startLegacyApp";
import { parseSafeReport } from "../../state/maskingSession";
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
  LegacySessionState,
  | "documentProvenance"
  | "outputDir"
  | "previewWorkDir"
  | "currentOrigPage"
  | "currentResultPage"
  | "boxes"
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
  readonly displayModeEl: HTMLSelectElement;
  readonly modeMask: HTMLInputElement;
  readonly modeRestore: HTMLInputElement;
  readonly invokeCommand: InvokeCommand;
  readonly hasTauriRuntime: () => boolean;
  readonly clampPage: (page: number, doc: PDFDocumentProxy | null) => number;
  readonly renderCompare: () => Promise<void>;
  readonly renderDocumentReviewSurfaces: () => void;
  readonly setCompareMode: (mode: "pdf" | "text") => void;
  readonly setTextCompareContents: (extractedText: string, maskedText: string) => void;
  readonly setBaseMaskingProgress: (progress: BaseMaskingProgress) => void;
  readonly renderFinalState: (report: SafeReport | null) => void;
  readonly updateOutputDirectoryState: () => void;
  readonly updateWorkflowReadiness: () => void;
  readonly updateCanvasControls: () => void;
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
  readonly loadCanvasWorkspacePdf: (targetPath: string, originalPath?: string) => Promise<void>;
  readonly loadResultPdf: (path: string, fallbackPath?: string, isCurrent?: () => boolean) => Promise<void>;
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
};

export function createDocumentSessionController(deps: DocumentSessionDeps): DocumentSessionController {
  const { state } = deps;

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
    const originalPath = state.documentProvenance.original.path;
    const hydratedPreview = reason === "canvas-hydrate" && targetPath !== originalPath ? targetPath : "";
    state.documentProvenance = resetDerivedProvenance(state.documentProvenance);
    if (hydratedPreview) {
      state.documentProvenance = adoptGeneratedPreview(state.documentProvenance, hydratedPreview, hydratedPreview);
    }
    state.resultDoc = null;
    state.currentResultPage = 1;
    state.boxes = [];
    state.documentEditRevision = (state.documentEditRevision || 0) + 1;
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
    state.restoreRevalidationFailed = false;
    deps.setBaseMaskingProgress({
      status: hydratedPreview ? "complete" : "idle",
      percent: hydratedPreview ? 100 : 0,
      displayMode: deps.displayModeEl.value as BaseMaskingProgress["displayMode"],
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
    state.documentProvenance = emptyDocumentProvenance();
    state.outputDir = "";
    state.previewWorkDir = "";
    state.currentOrigPage = 1;
    state.currentResultPage = 1;
    state.boxes = [];
    state.documentEditRevision = (state.documentEditRevision || 0) + 1;
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
    state.restoreRevalidationFailed = false;
    state.mode = "mask";
    state.batchItems = [];
    state.batchActiveIndex = -1;
    state.batchRunning = false;
    deps.modeMask.checked = true;
    deps.modeRestore.checked = false;
    deps.inputPathEl.value = "";
    deps.resetLastSavedAt();
    deps.closeTransientDialogs();
    deps.resetCompareView();
    deps.setBaseMaskingProgress({ status: "idle", percent: 0, displayMode: deps.displayModeEl.value as BaseMaskingProgress["displayMode"] });
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

  async function loadPdfDoc(path: string): Promise<PDFDocumentProxy> {
    const bytes = await deps.invokeCommand<number[]>("read_pdf_bytes", { path });
    return pdfjsLib.getDocument({ data: new Uint8Array(bytes) }).promise;
  }

  async function loadOriginalPdf(path: string): Promise<void> {
    const originalDoc = await loadPdfDoc(path);
    state.documentProvenance = selectOriginalDocument(state.documentProvenance, path, "pdf");
    resetDerivedArtifacts("new-document");
    state.origDoc = originalDoc;
    state.resultDoc = originalDoc;
    state.currentOrigPage = 1;
    state.currentResultPage = 1;
    deps.renderDocumentReviewSurfaces();
    deps.setCompareMode("pdf");
    deps.inputPathEl.value = path;
    await deps.renderCompare();
    deps.updateWorkflowReadiness();
  }

  async function loadCanvasWorkspacePdf(targetPath: string, originalPath = ""): Promise<void> {
    const sourcePath = originalPath || targetPath;
    const originalDoc = await loadPdfDoc(sourcePath);
    const resultDoc = targetPath === sourcePath ? originalDoc : await loadPdfDoc(targetPath);
    state.documentProvenance = selectOriginalDocument(state.documentProvenance, sourcePath, "pdf");
    resetDerivedArtifacts("canvas-hydrate", targetPath);
    state.origDoc = originalDoc;
    state.resultDoc = resultDoc;
    state.currentOrigPage = 1;
    state.currentResultPage = 1;
    deps.setCompareMode("pdf");
    deps.inputPathEl.value = sourcePath;
    await deps.renderCompare();
  }

  async function readCanvasWindowLaunchState(): Promise<CanvasWindowLaunchPayload | null> {
    if (!deps.hasTauriRuntime()) return null;
    const token = new URLSearchParams(window.location.search).get("token") || "";
    if (!token) return null;
    try {
      const parsed = await deps.invokeCommand<Partial<CanvasWindowLaunchPayload> | null>("take_canvas_launch_payload", { token });
      if (!parsed || typeof parsed.targetPath !== "string") return null;
      return {
        targetPath: parsed.targetPath,
        originalPath: typeof parsed.originalPath === "string" ? parsed.originalPath : "",
        outputDir: typeof parsed.outputDir === "string" ? parsed.outputDir : "",
        reportPath: typeof parsed.reportPath === "string" ? parsed.reportPath : "",
        mode: parsed.mode === "restore" ? "restore" : "mask",
        savedAt: typeof parsed.savedAt === "number" ? parsed.savedAt : 0,
      };
    } catch {
      return null;
    }
  }

  async function hydrateStandaloneCanvasWindow(): Promise<void> {
    const payload = await readCanvasWindowLaunchState();
    deps.setCanvasMode(true, { allowEmptyCanvas: true });
    if (payload?.outputDir) {
      state.outputDir = payload.outputDir;
      deps.updateOutputDirectoryState();
    }
    if (payload?.mode === "restore") {
      deps.modeRestore.checked = true;
      state.mode = "restore";
    } else {
      deps.modeMask.checked = true;
      state.mode = "mask";
    }
    if (!payload?.targetPath) {
      deps.setStatus("독립 작업창 대기 중: PDF 불러오기 → 박스 그리기 → 미리보기 반영");
      deps.updateCanvasControls();
      return;
    }
    try {
      await loadCanvasWorkspacePdf(payload.targetPath, payload.originalPath || payload.targetPath);
      if (payload.reportPath) {
        try {
          const reportText = await deps.invokeCommand<string>("read_text_file", { path: payload.reportPath });
          const parsedReport = parseSafeReport(JSON.parse(reportText));
          if (!parsedReport.ok) throw new Error("invalid report");
          state.latestReport = parsedReport.value;
          state.latestReportPath = payload.reportPath;
          deps.renderFinalState(state.latestReport);
        } catch {
          state.latestReport = null;
          state.latestReportPath = "";
          deps.renderFinalState(null);
          deps.setStatus("독립 작업창 자동 검증 정보를 불러오지 못했습니다.");
        }
      } else {
        deps.renderFinalState(null);
      }
      deps.setStatus("독립 작업창 로드 완료");
    } catch {
      deps.setStatus("독립 작업창 자동 로드 실패: PDF 불러오기로 다시 선택하세요.");
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

  async function loadResultPdf(path: string, fallbackPath = "", isCurrent: () => boolean = () => true): Promise<void> {
    try {
      const resultDoc = await loadPdfDoc(path);
      if (!isCurrent()) throw new Error("stale document session");
      state.resultDoc = resultDoc;
      state.currentResultPage = deps.clampPage(state.currentResultPage, state.resultDoc);
      await deps.renderCompare();
      if (!isCurrent()) throw new Error("stale document session");
      state.lastPreviewDiagnostics = "";
    } catch (error) {
      if (!isCurrent()) throw error;
      state.resultDoc = null;
      let fallbackLoaded = false;
      if (fallbackPath) {
        try {
          const fallbackDoc = await loadPdfDoc(fallbackPath);
          if (!isCurrent()) throw new Error("stale document session");
          state.resultDoc = fallbackDoc;
          state.currentResultPage = deps.clampPage(state.currentResultPage, state.resultDoc);
          fallbackLoaded = true;
        } catch (fallbackError) {
          if (!isCurrent()) throw fallbackError;
          state.resultDoc = null;
        }
      }
      state.lastPreviewDiagnostics = fallbackLoaded
        ? "수정본 PDF 로드 실패 1건, 이전 미리보기 복원 완료"
        : "수정본 PDF 로드 실패 1건, 이전 미리보기 복원 실패 1건";
      await deps.renderCompare();
      throw new Error("수정본 PDF를 불러오지 못했습니다.");
    }
  }

  async function ensurePreviewWorkDir(): Promise<string> {
    if (state.previewWorkDir) return state.previewWorkDir;
    state.previewWorkDir = await deps.invokeCommand<string>("get_preview_workdir");
    return state.previewWorkDir;
  }

  async function pickInputDocument(): Promise<void> {
    const selected = await deps.invokeCommand<string | null>("pick_input_document");
    if (!selected) return;
    if (!(await prepareForDocumentReplacement())) return;
    try {
      await loadOriginalDocument(selected);
      await maybeApplyDefaultOutputDir([selected]);
      deps.setStatus("원문 PDF 로드 완료");
    } catch {
      deps.setStatus("문서 로드 실패");
    }
  }

  async function pickCanvasPdf(): Promise<void> {
    const selected = await deps.invokeCommand<string | null>("pick_input_pdf");
    if (!selected) return;
    if (!(await prepareForDocumentReplacement())) return;
    try {
      await loadOriginalPdf(selected);
      await maybeApplyDefaultOutputDir([selected]);
      deps.setCanvasMode(true, { allowEmptyCanvas: true });
      deps.setStatus("캔버스 PDF 로드 완료");
    } catch {
      deps.setStatus("캔버스 PDF 로드 실패");
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
      let candidateRejected = false;
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
          } catch {
            candidateRejected = true;
          }
        }
      }
      if (!launchToken) throw new Error(candidateRejected ? "candidate rejected" : "missing candidate");
      await deps.invokeCommand("open_mask_canvas_window", { targetPath: launchToken });
      deps.setStatus(targetPath ? "마스킹 작업창을 열었습니다." : "마스킹 작업창을 열었습니다. 작업창에서 PDF를 불러오세요.");
    } catch {
      deps.setStatus("마스킹 작업창 열기 실패");
    }
  }

  async function clearDerivedArtifacts(): Promise<void> {
    if (deps.isBusy()) {
      deps.setStatus("실행 중입니다. 완료 후 초기화하세요.");
      return;
    }
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
      await loadResultPdf(originalPath);
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
  };
}
