import { invoke } from "@tauri-apps/api/core";
import { openPath } from "@tauri-apps/plugin-opener";
import type { PDFDocumentProxy } from "pdfjs-dist";
import type { BatchItem } from "../batchQueue";
import { reportSessionCounts } from "../dashboardSurfaces";
import { dashboardReviewState, geometryReviewCluster } from "../dashboardSurfaceModels";
import { createCanvasRenderController } from "../features/canvas-workbench/canvasRenderController";
import type { FocusedDetectionTarget } from "../features/canvas-workbench/canvasRenderController";
import { createDocumentBatchController } from "../features/document-batch/batchQueueController";
import { createDocumentSessionController } from "../features/document-session/documentSessionController";
import type { BoxMode } from "../features/document-session/documentSessionController";
import { createFinalizationController } from "../features/finalization/finalizationController";
import type { FinalSaveOutcome } from "../features/finalization/finalizationController";
import { createKeywordDialogController } from "../features/keyword-dialog/keywordDialogController";
import { createManualAdjustmentController } from "../features/manual-adjustment/manualAdjustmentController";
import type { BoxItem, CanvasEditorTool } from "../features/manual-adjustment/manualAdjustmentController";
import { createMaskingRunController } from "../features/masking-run/maskingRunController";
import type { MaskingPipelineArgs } from "../features/masking-run/maskingRunController";
import { finalSaveWarningPresentation, formatPublicFinalSaveWarning, publicFinalSavePresentation } from "../features/save-gate/saveGate";
import { analyzeMaskingRun, applyManualActionV1, isBoundarySegmentKind, issueRestoreCapability, resolveMaskingReview } from "../services/tauri/maskingContracts";
import type { BoundaryResolution, FinalizeSaveConfirmation, LegacyMaskingResult, MaskingOptions, ResolveMaskingReviewRequest } from "../services/tauri/maskingContracts";
import { saveSettings } from "../settingsState";
import type { AppSettings, DeidentificationMode, SettingsApplication } from "../settingsState";
import { emptyDocumentProvenance, statusSourcePath } from "../state/documentProvenance";
import type { DocumentProvenance } from "../state/documentProvenance";
import { parseBoundSafeReport } from "../state/maskingSession";
import type { AnalysisManifestV1, AnalysisRegionKind, BaseMaskingProgress, BoundSafeReport, MaskingSessionIdentity, PdfPointsTopLeftRect, ReviewItemV1, SafeReport } from "../state/maskingSession";
import { activateScreen, setInspectorCollapsed, setModalVisibility, shellStateSnapshot } from "../state/shellStore";
import { applySettings as applySettingsStore, currentMaskingOptions, currentSettings, loadSettingsIntoStore, updateSettings } from "../state/settingsStore";
import { deskProfileFromValue, deskProfileLabel, publishSessionDocuments } from "../state/sessionDocumentsStore";
import type { SessionDocumentSurfaceItem, SessionSaveSurfaceItem } from "../state/sessionDocumentsStore";
import { publishWorkspaceFinalizationSuccessDialog, publishWorkspacePageThumbnails, publishWorkspaceSurface, setWorkspaceCurrentCanvasPage, setWorkspaceFinalizationSuccessDialogVisible, setWorkspaceFinalSaveDialogVisible } from "../state/workspaceStore";
import { isWorkflowScreen } from "../workflowFlow";
import type { WorkflowScreen } from "../workflowFlow";
import type { ApplicationDomBindings } from "./domBindings";
export type PublicReportBindingReason = "absent" | "malformed" | "stale";
export type DragRejectionReason =
  | "resultDocChanged"
  | "pageChanged"
  | "scaleChanged"
  | "modeChanged"
  | "draftOwnerChanged"
  | "tooSmall";
export type DragRejection = {
  readonly reason: DragRejectionReason;
  readonly expected: Record<string, unknown>;
  readonly actual: Record<string, unknown>;
};
type CompareMode = "pdf" | "text";
type AppScreen = WorkflowScreen | "desk" | "storage";
type AppScreenIntent = AppScreen | "review-queue";

export type GeometryDraftOwner = {
  readonly owner: string;
  readonly reviewId: string;
  readonly reviewIds: readonly string[];
  readonly targetId: string;
  readonly targetIds: readonly string[];
  readonly regionKind: AnalysisRegionKind;
  readonly candidateRects: readonly PdfPointsTopLeftRect[];
  readonly page: number;
  readonly analysisRevision: number;
  readonly runId: string;
  readonly manifestHash: string;
};

export interface ApplicationSessionState {
  documentProvenance: DocumentProvenance;
  outputDir: string;
  previewWorkDir: string;
  currentOrigPage: number;
  currentResultPage: number;
  scale: number;
  boxes: BoxItem[];
  geometryDraft: GeometryDraftOwner | null;
  lastDragRejection: DragRejection | null;
  documentEditRevision: number;
  mode: BoxMode;
  origDoc: PDFDocumentProxy | null;
  resultDoc: PDFDocumentProxy | null;
  compareMode: CompareMode;
  extractedText: string;
  maskedText: string;
  baseExtractedText: string;
  baseMaskedText: string;
  initialMaskingPreviewPdf: string;
  initialExtractedText: string;
  initialMaskedText: string;
  preManualPreviewPdf: string;
  preManualExtractedText: string;
  preManualMaskedText: string;
  lastPreviewDiagnostics: string;
  latestExtractedPath: string;
  latestMaskedPath: string;
  latestMaskedTextPolicy: DeidentificationMode | "";
  latestReportPath: string;
  latestReport: SafeReport | null;
  activeRunKind: "none" | "legal" | "public";
  publicRunIdentity: MaskingSessionIdentity | null;
  publicReportBindingReason: PublicReportBindingReason | null;
  restoreRevalidationFailed: boolean;
  baseMaskingProgress: BaseMaskingProgress;
  syncPages: boolean;
  batchItems: BatchItem[];
  batchActiveIndex: number;
  batchRunning: boolean;
  maskingRunning: boolean;
  savingInFlight: boolean;
  selectedCanvasBoxIndex: number;
  canvasMode: boolean;
}

function rectContains(outer: PdfPointsTopLeftRect, inner: PdfPointsTopLeftRect): boolean {
  return Math.min(outer.x0, outer.x1) <= Math.min(inner.x0, inner.x1)
    && Math.min(outer.y0, outer.y1) <= Math.min(inner.y0, inner.y1)
    && Math.max(outer.x0, outer.x1) >= Math.max(inner.x0, inner.x1)
    && Math.max(outer.y0, outer.y1) >= Math.max(inner.y0, inner.y1);
}

export function suggestedRegionGeometryRects(
  manifest: AnalysisManifestV1,
  review: ReviewItemV1,
): readonly PdfPointsTopLeftRect[] | null {
  if (review.kind !== "region_geometry") return null;
  const regions = geometryReviewCluster(manifest, review)
    .map((item) => manifest.regions.find((region) => region.regionId === item.targetId))
    .filter((region): region is NonNullable<typeof region> => region !== undefined);
  const candidateRects = regions.flatMap((region) => region.rects);
  if (candidateRects.length === 0) return null;
  const targetIds = new Set(regions.map((region) => region.regionId));
  const detectedRects = manifest.occurrences
    .filter((occurrence) => occurrence.page === review.pageStart && occurrence.regionId !== null && targetIds.has(occurrence.regionId))
    .flatMap((occurrence) => occurrence.rects);
  return [
    ...candidateRects,
    ...detectedRects.filter((detectedRect) => !candidateRects.some((candidateRect) => rectContains(candidateRect, detectedRect))),
  ];
}

export function createApplicationController(bindings: ApplicationDomBindings) {

const state: ApplicationSessionState = {
  documentProvenance: emptyDocumentProvenance(),
  outputDir: "",
  previewWorkDir: "",
  currentOrigPage: 1,
  currentResultPage: 1,
  scale: window.innerWidth <= 420 ? 0.72 : window.innerWidth <= 900 ? 0.88 : 1,
  boxes: [] as BoxItem[],
  geometryDraft: null,
  lastDragRejection: null,
  documentEditRevision: 0,
  mode: "mask" as BoxMode,
  origDoc: null as PDFDocumentProxy | null,
  resultDoc: null as PDFDocumentProxy | null,
  compareMode: "pdf" as CompareMode,
  extractedText: "",
  maskedText: "",
  baseExtractedText: "",
  baseMaskedText: "",
  initialMaskingPreviewPdf: "",
  initialExtractedText: "",
  initialMaskedText: "",
  preManualPreviewPdf: "",
  preManualExtractedText: "",
  preManualMaskedText: "",
  lastPreviewDiagnostics: "",
  latestExtractedPath: "",
  latestMaskedPath: "",
  latestMaskedTextPolicy: "" as DeidentificationMode | "",
  latestReportPath: "",
  latestReport: null as SafeReport | null,
  activeRunKind: "none",
  publicRunIdentity: null,
  publicReportBindingReason: null,
  // 복원 재검증 실패는 법률 문서에서는 저장 전 권고이고, 공공문서 서버 세션에서는
  // 최종 저장을 차단한다. 새 리포트가 채택되면 이전 실패 상태는 무효다.
  restoreRevalidationFailed: false,
  baseMaskingProgress: { status: "idle", percent: 0, displayMode: "black" } as BaseMaskingProgress,
  syncPages: true,
  batchItems: [] as BatchItem[],
  batchActiveIndex: -1,
  batchRunning: false,
  maskingRunning: false,
  savingInFlight: false,
  selectedCanvasBoxIndex: -1,
  canvasMode: false,
};

const {
  statusEl,
  statusDetailEl,
  inputPathEl,
  pageInfoOrigEl,
  pageInfoResultEl,
  pagerLabelEl,
  zoomInfoEl,
  origCanvas,
  resultCanvas,
  overlay,
  origWrap,
  resultWrap,
  pdfCompareView,
  textCompareView,
  extractedTextView,
  maskedTextView,
  origCtx,
  resultCtx,
  octx,
  btnPickPdf,
  btnRunMasking,
  btnSave,
  finalSaveReadinessEl,
  newDocumentDialogEl,
  btnConfirmNewDocument,
  compareModePdf,
  compareModeText,
  toggleOriginalCompareEl,
  originalComparePanelEl,
  finalStateCardEl,
  finalStateTitleEl,
  finalStateDetailEl,
  workspaceShellEl,
  documentsScreenEl,
  btnPickBatch,
  btnRunBatch,
  batchSummaryEl,
  batchQueueEl,
  btnCanvasToolMask,
  btnCanvasToolRestore,
  btnCanvasZoomOut,
  btnCanvasZoomIn,
  btnCanvasUndo,
  btnCanvasClear,
  btnCanvasApply,
  btnNewDocument,
  btnCanvasBoxDelete,
  canvasActiveToolLabelEl,
  canvasToolReadinessEl,
  canvasBoxListEl,
  canvasBoxPropertiesEl,
  canvasBoxPropertyPageEl,
  canvasBoxPropertyTypeEl,
  canvasBoxPropertyCoordinatesEl,
  canvasBoxPropertySizeEl,
  btnCanvasBoxConvertMask,
  btnCanvasBoxConvertRestore,
  finalSaveDialogEl,
  maskingProgressModalEl,
  maskingProgressValueEl,
  maskingProgressPercentEl,
  maskingProgressStageEl,
  btnCloseMaskingProgress,
  btnCancelMaskingProgress,
  canvasEditorToolButtons,
} = bindings;

let lastSavedAt = "-";
let lastFinalSaveOutcome: FinalSaveOutcome | null = null;
let completedFinalPath = "";
const sessionDocuments: SessionDocumentSurfaceItem[] = [];
const sessionSaves: SessionSaveSurfaceItem[] = [];
let sessionSaveSequence = 0;
let activeCanvasTool: CanvasEditorTool = "mask";
let maskingProgressStartedAt = 0;
let maskingProgressTimer: number | null = null;
let maskingProgressDismissed = false;
const isStandaloneCanvasWindow = new URLSearchParams(window.location.search).get("mode") === "canvas";
document.body.classList.toggle("standalone-canvas-window", isStandaloneCanvasWindow);

const maskingProgressPagesEl = document.querySelector<HTMLElement>("#masking-progress-pages");
const maskingProgressDetectedEl = document.querySelector<HTMLElement>("#masking-progress-detected");
const maskingProgressElapsedEl = document.querySelector<HTMLElement>("#masking-progress-elapsed");
const maskingProgressDescriptionEl = maskingProgressModalEl.querySelector<HTMLElement>(".ux-modal-head p");
const maskingProgressStats = { current: 0, total: 0, detected: 0 };
const modalInertStates = new Map<HTMLElement, boolean>();

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}초`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder > 0 ? `${minutes}분 ${remainder}초` : `${minutes}분`;
}

function stopMaskingProgressTimer(): void {
  if (maskingProgressTimer !== null) {
    window.clearInterval(maskingProgressTimer);
    maskingProgressTimer = null;
  }
}

function isVisibleModal(element: HTMLElement): boolean {
  return !element.classList.contains("is-hidden") && element.getAttribute("aria-hidden") !== "true";
}

function currentModalLayer(): HTMLElement | null {
  for (const element of [finalSaveDialogEl, newDocumentDialogEl, maskingProgressModalEl]) {
    if (isVisibleModal(element)) return element;
  }
  return null;
}

function focusableModalElements(element: HTMLElement): HTMLElement[] {
  return [...element.querySelectorAll<HTMLElement>(
    "button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])",
  )].filter((candidate) => !candidate.hasAttribute("hidden") && !candidate.closest("[hidden]"));
}

function applyModalInertTree(activeModal: HTMLElement | null): void {
  for (const [element, inert] of modalInertStates) {
    element.inert = inert;
  }
  modalInertStates.clear();
  if (!activeModal) return;
  let branch: HTMLElement | null = activeModal;
  while (branch && branch !== document.body) {
    const parent: HTMLElement | null = branch.parentElement;
    if (!parent) break;
    for (const sibling of [...parent.children]) {
      if (!(sibling instanceof HTMLElement) || sibling === branch || sibling.contains(activeModal)) continue;
      if (!modalInertStates.has(sibling)) modalInertStates.set(sibling, sibling.inert);
      sibling.inert = true;
    }
    branch = parent;
  }
}

function ensureModalFocus(activeModal: HTMLElement, reverse = false): void {
  const autofocus = activeModal.querySelector<HTMLElement>("[data-modal-autofocus='true']");
  const targets = focusableModalElements(activeModal);
  const fallback = activeModal.querySelector<HTMLElement>(".ux-modal");
  (reverse ? targets[targets.length - 1] : autofocus ?? targets[0] ?? fallback)?.focus();
}

function updateMaskingProgressMeta(): void {
  if (maskingProgressPagesEl) {
    maskingProgressPagesEl.textContent = maskingProgressStats.total > 0
      ? `${maskingProgressStats.current} / ${maskingProgressStats.total}페이지`
      : "확인 중";
  }
  if (maskingProgressDetectedEl) {
    maskingProgressDetectedEl.textContent = `${maskingProgressStats.detected}건`;
  }
  if (maskingProgressElapsedEl) {
    const elapsedSeconds = maskingProgressStartedAt > 0
      ? Math.max(0, Math.floor((Date.now() - maskingProgressStartedAt) / 1000))
      : 0;
    maskingProgressElapsedEl.textContent = formatElapsed(elapsedSeconds);
  }
}

function closeMaskingProgressDialog(): void {
  if (state.baseMaskingProgress.status === "running") {
    maskingProgressDismissed = true;
    setStatus("자동 탐지를 백그라운드에서 계속 진행합니다.");
  }
  setModalVisibility("masking-progress-dialog", false);
  window.requestAnimationFrame(() => {
    btnRunMasking.focus();
  });
}

function compactPath(path: string) {
  if (!path) return "-";
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/g, "");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length === 0) return path;
  if (/^[A-Za-z]:$/.test(parts[0] ?? "")) parts.shift();
  const homeScoped = parts[0] === "Users" || parts[0] === "home";
  const tail = homeScoped ? parts.slice(Math.max(2, parts.length - 2)) : parts.slice(-2);
  const prefix = homeScoped ? "개인 폴더" : parts.length > tail.length ? "..." : "";
  return prefix ? `${prefix}/${tail.join("/")}` : tail.join("/");
}

function updateOutputDirectoryState() {
  updateSettings({ outputDir: state.outputDir });
  updateWorkflowReadiness();
}


function setBaseMaskingProgress(progress: BaseMaskingProgress) {
  const previousStatus = state.baseMaskingProgress.status;
  state.baseMaskingProgress = {
    status: progress.status,
    percent: progress.percent,
    displayMode: progress.displayMode,
  };
  const percent = Math.max(0, Math.min(100, Math.round(progress.percent)));
  const running = progress.status === "running";
  if (running && previousStatus !== "running") {
    maskingProgressStartedAt = Date.now();
    maskingProgressDismissed = false;
    maskingProgressStats.current = 0;
    maskingProgressStats.total = 0;
    maskingProgressStats.detected = 0;
    stopMaskingProgressTimer();
    maskingProgressTimer = window.setInterval(updateMaskingProgressMeta, 1000);
  }
  if (!running) {
    stopMaskingProgressTimer();
    maskingProgressDismissed = false;
  }
  maskingProgressStats.total = Number.isSafeInteger(progress.totalPages) && (progress.totalPages ?? 0) > 0
    ? progress.totalPages ?? 0
    : 0;
  maskingProgressStats.current = maskingProgressStats.total > 0
    ? Math.min(maskingProgressStats.total, Math.max(0, progress.currentPage ?? 0))
    : 0;
  maskingProgressStats.detected = Math.max(0, progress.detectedItems ?? 0);
  maskingProgressValueEl.value = percent;
  maskingProgressPercentEl.textContent = `${percent}%`;
  maskingProgressStageEl.textContent = percent < 45
    ? "문서 구조를 확인하고 있습니다"
    : percent < 80
      ? "개인정보 후보를 찾고 있습니다"
      : "검토 항목을 정리하고 있습니다";
  btnCloseMaskingProgress.disabled = false;
  btnCloseMaskingProgress.textContent = running ? "백그라운드로 닫기" : "닫기";
  if (maskingProgressDescriptionEl) {
    maskingProgressDescriptionEl.textContent = running
      ? "문서를 분석해 개인정보를 찾고 있습니다. 창을 닫아도 작업은 백그라운드에서 계속됩니다."
      : "문서 분석이 끝나면 검토 패널에서 결과를 확인할 수 있습니다.";
  }
  updateMaskingProgressMeta();
  setModalVisibility("masking-progress-dialog", running && !maskingProgressDismissed);
  if (running && !maskingProgressDismissed) {
    window.requestAnimationFrame(() => {
      btnCloseMaskingProgress.focus();
    });
  }
}

type AppScreen = WorkflowScreen | "desk" | "storage";

function isAppScreen(screenName: string): screenName is AppScreen {
  return screenName === "desk" || screenName === "storage" || isWorkflowScreen(screenName);
}

function screenPanelForIntent(screenName: AppScreenIntent): AppScreen {
  return screenName === "review-queue" ? "documents" : screenName;
}

function activateAppScreen(screenName: AppScreenIntent) {
  const panelName = screenPanelForIntent(screenName);
  if (!isAppScreen(panelName)) {
    setStatus("화면 이동 정보를 확인할 수 없습니다. 화면을 다시 열어주세요.");
    return;
  }
  activateScreen(screenName);
  updateStatusDetail();
}

// 보조 화면에서 돌아갈 단일 문서 화면을 기억한다.
const AUX_SCREENS: readonly WorkflowScreen[] = ["settings", "masking-settings"];
let auxReturnScreen: AppScreen = "desk";

function isAuxScreen(screen: string): boolean {
  return AUX_SCREENS.includes(screen as WorkflowScreen);
}

function rememberAuxReturnScreen() {
  const current = screenPanelForIntent(shellStateSnapshot().activeScreen);
  if (!isAuxScreen(current) && isAppScreen(current)) {
    auxReturnScreen = current;
  }
}

// 보조 화면에서 이전 1급 화면으로 복귀한다(닫기=문서 홈과 달리 진입 지점으로 되돌림).
function returnFromAuxScreen() {
  activateAppScreen(auxReturnScreen);
  document.querySelector<HTMLElement>(`[data-screen-target="${auxReturnScreen}"]`)?.focus();
}

function showFinalizationSuccess(finalPath: string, saveConfirmation: FinalizeSaveConfirmation | null = null): void {
  if (!finalPath) return;
  completedFinalPath = finalPath;
  const currentDocument = sessionDocuments.find((item) => item.path === state.documentProvenance.original.path);
  sessionSaves.unshift({
    id: `session-save-${sessionSaveSequence += 1}`,
    path: finalPath,
    maskCount: currentDocument?.maskCount ?? null,
    savedAt: lastSavedAt,
  });
  publishSessionDocuments({ documents: sessionDocuments, saves: sessionSaves, profile: deskProfileFromValue(currentSettings().profile) });
  const count = currentDocument?.maskCount === null || currentDocument?.maskCount === undefined ? "확인 불가" : `${currentDocument.maskCount}건`;
  const warnings = saveConfirmation?.unresolvedReviews.map((warning) => formatPublicFinalSaveWarning(warning)) ?? [];
  const partial = saveConfirmation?.status === "user_confirmed" && warnings.length > 0;
  const parts = finalPath.split(/[\\/]/).filter(Boolean);
  publishWorkspaceFinalizationSuccessDialog({
    title: partial ? "부분 마스킹본이 저장되었습니다" : "안전 문서로 저장되었습니다",
    description: partial
      ? "확인 저장을 완료했지만 아래 항목은 가려지지 않았을 수 있습니다. 전송 전에 직접 확인하세요."
      : "개인정보가 마스킹된 안전 문서를 지정한 위치에 저장했습니다.",
    statusLabel: partial ? `미해결 항목 ${warnings.length}건 · 확인 저장` : "완전 마스킹본",
    statusTone: partial ? "warn" : "ok",
    warnings,
    fileName: parts[parts.length - 1] ?? finalPath,
    meta: `${count} · ${partial ? "부분 마스킹 · 확인 저장" : "현재 세션 저장"}`,
    path: compactPath(finalPath),
    maskCount: count,
    savedAt: lastSavedAt,
  });
  setWorkspaceFinalizationSuccessDialogVisible(true);
}

function closeFinalizationSuccess(): void {
  setWorkspaceFinalizationSuccessDialogVisible(false);
  completedFinalPath = "";
}

async function openCompletedFinalStorage(): Promise<void> {
  if (!completedFinalPath) return;
  const normalized = completedFinalPath.trim().replace(/[\\/]+$/, "");
  const separatorIndex = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  const parent = separatorIndex > 0 ? normalized.slice(0, separatorIndex) : normalized;
  try {
    await openPath(parent);
  } catch {
    setStatus("저장된 파일 위치를 열지 못했습니다.");
  }
}

async function openCompletedFinalFile(): Promise<void> {
  if (!completedFinalPath) return;
  try {
    await openPath(completedFinalPath);
  } catch {
    setStatus("저장된 문서를 열지 못했습니다.");
  }
}

function showCompletedFinalStorage(): void {
  closeFinalizationSuccess();
  activateAppScreen("storage");
}

let discardConfirmationResolve: ((confirmed: boolean) => void) | null = null;

function confirmDiscardCurrentWork(): Promise<boolean> {
  if (discardConfirmationResolve) return Promise.resolve(false);
  setModalVisibility("new-document-dialog", true);
  btnConfirmNewDocument.focus();
  return new Promise((resolve) => {
    discardConfirmationResolve = resolve;
  });
}

function resolveDiscardConfirmation(confirmed: boolean) {
  setModalVisibility("new-document-dialog", false);
  const resolve = discardConfirmationResolve;
  discardConfirmationResolve = null;
  resolve?.(confirmed);
}

// Canvas rendering/interaction and single-document masking-run are owned by
// feature controllers; the composition root binds the DOM elements and orchestration
// callbacks and destructures the controller methods so the existing wiring call
// sites stay unchanged. The run_masking_pipeline IPC stays anchored here as a
// thin wrapper injected into the masking-run controller.
async function runMaskingPipeline(args: MaskingPipelineArgs): Promise<LegacyMaskingResult> {
  return invoke<LegacyMaskingResult>("run_masking_pipeline", {
    inputFile: args.inputFile,
    originalFile: args.originalFile,
    outputDir: args.outputDir,
    opts: args.opts,
  });
}

const canvasRenderController = createCanvasRenderController({
  state,
  origCanvas,
  resultCanvas,
  overlay,
  origWrap,
  resultWrap,
  pdfCompareView,
  origCtx,
  resultCtx,
  octx,
  clampPage,
  updateMeta,
  getActiveCanvasTool: () => activeCanvasTool,
  setStatus,
  getPublicDetectionOverlay: () => {
    const manifest = boundPublicReport(state.latestReport)?.analysisManifest;
    return manifest
      ? { regions: manifest.regions, occurrences: manifest.occurrences, manualActions: manifest.manualActions }
      : null;
  },
  publishPageThumbnails: publishWorkspacePageThumbnails,
});
const {
  renderCompare,
  redrawOverlay,
  adjustZoom,
  moveOrigPage,
  moveResultPage,
  goToReviewPage,
  goToReviewLocation: goToReviewCanvasLocation,
  loadPageThumbnails,
  cancelActiveInteraction,
  setFocusedDetectionTarget,
} = canvasRenderController;

const reviewFailureById = new Map<string, string>();

function renderDocumentReviewSurfaces() {
  const report = boundPublicReport(state.latestReport);
  const currentPath = state.documentProvenance.original.path;
  if (currentPath) {
    const counts = reportSessionCounts(report);
    const current = sessionDocuments.findIndex((item) => item.path === currentPath);
    const item: SessionDocumentSurfaceItem = {
      path: currentPath,
      status: state.latestReportPath ? (counts.pendingCount ? "검토 대기" : "분석 완료") : "선택됨",
      detectedCount: counts.detectedCount,
      maskCount: counts.maskCount,
      pendingCount: counts.pendingCount,
      profileLabel: deskProfileLabel(currentSettings().profile),
    };
    if (current >= 0) sessionDocuments[current] = item;
    else sessionDocuments.unshift(item);
  }
  publishSessionDocuments({ documents: sessionDocuments, saves: sessionSaves, profile: deskProfileFromValue(currentSettings().profile) });
  publishWorkspaceSurface({
    selectedPath: state.documentProvenance.original.path,
    documentKind: state.documentProvenance.original.kind,
    batchItems: state.batchItems,
    latestDocumentPath: statusSourcePath(state.documentProvenance),
    latestReportPath: state.latestReportPath,
    report,
    geometryDraftReviewId: state.geometryDraft?.reviewId ?? "",
    reviewFailureById,
  });
}

function setCanvasWideMode() {
  const canvasScreen = documentsScreenEl;
  const wide = canvasScreen.classList.contains("tools-collapsed") || canvasScreen.classList.contains("properties-collapsed");
  canvasScreen.classList.toggle("canvas-wide-mode", wide);
}

function setCanvasToolsCollapsed(collapsed: boolean) {
  const canvasScreen = documentsScreenEl;
  canvasScreen.classList.toggle("tools-collapsed", collapsed);
  setCanvasWideMode();
}

function setCanvasPropertiesCollapsed(collapsed: boolean) {
  const canvasScreen = documentsScreenEl;
  canvasScreen.classList.toggle("properties-collapsed", collapsed);
  setCanvasWideMode();
}

function expandCanvasPanels() {
  setCanvasToolsCollapsed(false);
  setCanvasPropertiesCollapsed(false);
}

function setStatus(msg: string) {
  const repeated = statusEl.textContent === msg;
  statusEl.textContent = msg;
  if (repeated) {
    const nextState = statusEl.dataset.state === "repeat-a" ? "repeat-b" : "repeat-a";
    statusEl.dataset.state = nextState;
  } else {
    delete statusEl.dataset.state;
  }
  statusEl.removeAttribute("title");
  updateStatusDetail();
}

function isCustomRegionScope(): boolean {
  return currentSettings().regionScope === "custom";
}

function collectMaskingOptions(): MaskingOptions {
  return currentMaskingOptions();
}

function isPdfInput(): boolean {
  return state.documentProvenance.original.kind === "pdf";
}

function clampPage(page: number, doc: PDFDocumentProxy | null) {
  if (!doc) return 1;
  return Math.max(1, Math.min(page, doc.numPages));
}

function updateMeta() {
  const origCount = state.origDoc?.numPages || 0;
  const resultCount = state.resultDoc?.numPages || 0;
  const origPage = clampPage(state.currentOrigPage, state.origDoc);
  const resultPage = clampPage(state.currentResultPage, state.resultDoc);
  pageInfoOrigEl.textContent = origCount > 0 ? `${origPage}/${origCount}` : "0/0";
  pageInfoResultEl.textContent = resultCount > 0 ? `${resultPage}/${resultCount}` : "0/0";
  pagerLabelEl.textContent = origCount > 0 ? `${origPage} / ${origCount}` : "0 / 0";
  setWorkspaceCurrentCanvasPage(Math.max(origPage, resultPage) - 1);
  updateCanvasControls();
  updateStatusDetail();
}

function updateStatusDetail() {
  const totalPages = Math.max(state.origDoc?.numPages || 0, state.resultDoc?.numPages || 0);
  const current = totalPages > 0 ? Math.max(state.currentOrigPage, state.currentResultPage) : 0;
  const sourcePath = statusSourcePath(state.documentProvenance);
  zoomInfoEl.textContent = `${Math.round(state.scale * 100)}%`;
  statusDetailEl.textContent = `문서 ${sourcePath ? "준비" : "없음"}  ·  페이지 ${current}/${totalPages}  ·  저장 ${lastSavedAt}`;
}

function updateDocumentControls() {
  const pdfActive = isPdfInput();
  const hasInput = state.documentProvenance.original.kind !== "";
  btnCanvasApply.disabled = hasInput && !pdfActive;
  btnCanvasUndo.disabled = hasInput && !pdfActive;
  btnCanvasToolMask.disabled = hasInput && !pdfActive;
  btnCanvasToolRestore.disabled = hasInput && !pdfActive;
  compareModePdf.disabled = hasInput && !pdfActive;
  updateCanvasControls();
}

function hasTauriRuntime() {
  return Boolean((window as any).__TAURI_INTERNALS__ || (window as any).__TAURI__);
}



function boundPublicReport(report: SafeReport | null): BoundSafeReport | null {
  if (state.activeRunKind !== "public" || !report) {
    state.publicReportBindingReason = "absent";
    return null;
  }
  if (!state.publicRunIdentity) {
    state.publicReportBindingReason = "stale";
    return null;
  }
  const parsed = parseBoundSafeReport(report, state.publicRunIdentity);
  if (parsed.ok) {
    state.publicReportBindingReason = null;
    return parsed.value;
  }
  state.publicReportBindingReason = parsed.errors.some((issue) => issue.field === "safeReport.identity")
    ? "stale"
    : "malformed";
  return null;
}

function renderFinalState(report: SafeReport | null) {
  const presentation = state.activeRunKind === "public"
    ? publicFinalSavePresentation({ report: boundPublicReport(report), restoreRevalidationFailed: state.restoreRevalidationFailed })
    : finalSaveWarningPresentation({ hasReportPath: Boolean(state.latestReportPath), report, restoreRevalidationFailed: state.restoreRevalidationFailed });
  finalStateCardEl.dataset.state = presentation.stateName;
  finalStateTitleEl.textContent = presentation.title;
  finalStateDetailEl.textContent = presentation.detail;
  updateWorkflowReadiness();
}

function setTextCompareContents(extractedText: string, maskedText: string) {
  state.extractedText = extractedText;
  state.maskedText = maskedText;
  extractedTextView.textContent = extractedText || "표시할 원문 텍스트가 없습니다.";
  maskedTextView.textContent = maskedText || "표시할 마스킹 텍스트가 없습니다.";
}

function setCompareMode(mode: CompareMode) {
  state.compareMode = mode;
  const isPdf = mode === "pdf";
  pdfCompareView.classList.toggle("is-hidden", !isPdf);
  textCompareView.classList.toggle("is-hidden", isPdf);
  compareModePdf.classList.toggle("is-active", isPdf);
  compareModeText.classList.toggle("is-active", !isPdf);
  compareModePdf.setAttribute("aria-selected", String(isPdf));
  compareModeText.setAttribute("aria-selected", String(!isPdf));
  updateDocumentControls();
  updateStatusDetail();
}

function updateOriginalCompareVisibility() {
  toggleOriginalCompareEl.setAttribute("aria-checked", String(toggleOriginalCompareEl.checked));
  originalComparePanelEl.classList.toggle("is-hidden", !toggleOriginalCompareEl.checked);
}

const invokeCommand = <T>(command: string, args?: Record<string, unknown>): Promise<T> => invoke<T>(command, args);

const documentSessionController = createDocumentSessionController({
  state,
  inputPathEl,
  modeMask: btnCanvasToolMask,
  modeRestore: btnCanvasToolRestore,
  invokeCommand,
  hasTauriRuntime,
  clampPage,
  renderCompare,
  renderDocumentReviewSurfaces,
  setCompareMode,
  setTextCompareContents,
  setBaseMaskingProgress,
  renderFinalState,
  updateOutputDirectoryState,
  updateWorkflowReadiness: () => updateWorkflowReadiness(),
  updateCanvasControls: () => updateCanvasControls(),
  cancelGeometryDraft: () => cancelGeometryDraft(),
  setCanvasMode: (active, options) => setCanvasMode(active, options),
  redrawOverlay,
  updateMeta,
  resetLastSavedAt: () => {
    lastSavedAt = "-";
    lastFinalSaveOutcome = null;
  },
  isBusy: () => state.maskingRunning || state.batchRunning || state.savingInFlight,
  confirmDiscardCurrentWork,
  resetCompareView: () => {
    toggleOriginalCompareEl.checked = false;
    updateOriginalCompareVisibility();
    setCompareMode("pdf");
  },
  renderBatchQueue: () => renderBatchQueue(),
  closeTransientDialogs: () => {
    setModalVisibility("keyword-dialog", false);
    setWorkspaceFinalSaveDialogVisible(false);
    setWorkspaceFinalizationSuccessDialogVisible(false);
    resolveDiscardConfirmation(false);
  },
  setStatus,
});
const {
  loadPdfDoc,
  loadOriginalDocument,
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
  resetDerivedArtifacts,
  resetDocumentSession,
  prepareForDocumentReplacement,
  invalidateLifecycle,
} = documentSessionController;

async function pickSessionDocument(): Promise<boolean> {
  const previousProvenance = state.documentProvenance;
  await pickInputDocument();
  const selected = state.documentProvenance.original.path;
  const succeeded = Boolean(selected) && state.documentProvenance !== previousProvenance;
  if (succeeded) renderDocumentReviewSurfaces();
  return succeeded;
}

async function pickDeskDocument(): Promise<void> {
  if (await pickSessionDocument()) activateAppScreen("documents");
}

async function openStoredDocument(path: string): Promise<void> {
  try {
    await openPath(path);
  } catch {
    setStatus("저장된 문서를 열지 못했습니다.");
  }
}

async function openQaDocument(path: string): Promise<void> {
  if (!(await prepareForDocumentReplacement())) throw new Error("QA_DRIVE_DOCUMENT_REPLACEMENT_BLOCKED");
  await loadOriginalDocument(path);
  if (state.documentProvenance.original.path !== path) throw new Error("QA_DRIVE_DOCUMENT_LOAD_FAILED");
  await maybeApplyDefaultOutputDir([path]);
  if (state.documentProvenance.original.path !== path) throw new Error("QA_DRIVE_DOCUMENT_STALE");
  activateAppScreen("documents");
  renderDocumentReviewSurfaces();
  setStatus("원문 PDF 로드 완료");
}

let applyPublicManualMaskActions: (actions: readonly { readonly page: number; readonly rects: readonly PdfPointsTopLeftRect[]; readonly mode: "mask" | "restore"; readonly gestureTrusted?: boolean }[]) => Promise<boolean> = async () => false;

const manualAdjustmentController = createManualAdjustmentController({
  state,
  invokeCommand,
  modeMask: btnCanvasToolMask,
  modeRestore: btnCanvasToolRestore,
  workspaceShellEl,
  overlay,
  canvasEditorToolButtons,
  canvasActiveToolLabelEl,
  canvasToolReadinessEl,
  canvasBoxListEl,
  canvasBoxPropertiesEl,
  canvasBoxPropertyPageEl,
  canvasBoxPropertyTypeEl,
  canvasBoxPropertyCoordinatesEl,
  canvasBoxPropertySizeEl,
  btnCanvasZoomOut,
  btnCanvasZoomIn,
  btnCanvasUndo,
  btnCanvasClear,
  btnCanvasBoxDelete,
  btnCanvasBoxConvertMask,
  btnCanvasBoxConvertRestore,
  isStandaloneCanvasWindow,
  isPdfInput,
  currentFinalDocumentPath: () => currentFinalDocumentPath(),
  getActiveCanvasTool: () => activeCanvasTool,
  setActiveCanvasToolState: (tool) => {
    activeCanvasTool = tool;
  },
  ensurePreviewWorkDir,
  loadResultPdf,
  redrawOverlay,
  updateMeta,
  renderFinalState,
  renderCompare,
  setTextCompareContents,
  updateWorkflowReadiness: () => updateWorkflowReadiness(),
  updateStatusDetail,
  setStatus,
  applyPublicManualMaskActions: (actions) => applyPublicManualMaskActions(actions),
  renderDocumentReviewSurfaces,
});
const {
  applyPendingManualBoxes,
  renderCanvasFinalSaveSummary,
  updateCanvasControls,
  setActiveCanvasTool,
  convertCanvasSelectedBox,
  deleteSelectedCanvasBox,
  undoLastCanvasBox,
  setCanvasMode,
} = manualAdjustmentController;

const finalizationController = createFinalizationController({
  state,
  invokeCommand,
  openPath,
  btnSave,
  btnCanvasFinalSave: btnSave,
  btnNewDocument,
  btnRunMasking,
  btnManualApply: btnCanvasApply,
  btnCanvasApply,
  btnPickPdf,
  btnPickBatch,
  btnRunBatch,
  btnClear: btnCanvasClear,
  finalSaveReadinessEl,
  isPdfInput,
  applyPendingManualBoxes,
  collectSettings: () => collectSettings(),
  saveSettings: (settings) => {
    const saved = saveSettings(settings);
    applySettingsStore(saved);
    return saved;
  },
  loadResultPdf,
  updateCanvasControls,
  cancelGeometryDraft: () => cancelGeometryDraft(),
  cancelCanvasInteraction: cancelActiveInteraction,
  renderDocumentReviewSurfaces,
  renderCanvasFinalSaveSummary,
  recordSavedAt: (value) => {
    lastSavedAt = value;
  },
  showFinalizationSuccess,
  setStatus,
});
const {
  saveFinalOutput: saveFinalOutputImpl,
  openFinalSaveDialog: openFinalSaveDialogImpl,
  closeFinalSaveDialog,
  renderFinalSaveConfirmation,
  currentFinalDocumentPath,
  updateWorkflowReadiness,
} = finalizationController;
const saveFinalOutput = async (options?: { warningsConfirmed?: boolean }): Promise<FinalSaveOutcome> => {
  lastFinalSaveOutcome = null;
  const outcome = await saveFinalOutputImpl(options);
  lastFinalSaveOutcome = outcome;
  return outcome;
};
const openFinalSaveDialog = (): void => {
  lastFinalSaveOutcome = null;
  openFinalSaveDialogImpl();
};

const maskingRunController = createMaskingRunController({
  state,
  inputPathEl,
  isPdfInput,
  isCustomRegionScope,
  getResultSourcePath,
  analyzeMaskingRun: ({ inputFile, profile, options, reanalysis }) => {
    if (profile === "internal_review") {
      return analyzeMaskingRun(invoke, {
        inputFile,
        profile,
        options: { ...options, profile: "internal_review" },
        ...(reanalysis ? { reanalysis } : {}),
      });
    }
    if (profile === "official_dispatch") {
      return analyzeMaskingRun(invoke, {
        inputFile,
        profile,
        options: { ...options, profile: "official_dispatch" },
        ...(reanalysis ? { reanalysis } : {}),
      });
    }
    return analyzeMaskingRun(invoke, {
      inputFile,
      profile: "mixed",
      options: { ...options, profile: "mixed" },
      ...(reanalysis ? { reanalysis } : {}),
    });
  },
  resolveMaskingReview: (request, report) => resolveMaskingReview(invoke, request, report),
  applyManualActionV1: (request, report) => applyManualActionV1(invoke, request, report),
  issueRestoreCapability: (request, report) => issueRestoreCapability(invoke, request, report),
  readTextFile: (path) => invoke<string>("read_text_file", { path }),
  ensurePreviewWorkDir,
  collectMaskingOptions,
  clampPage,
  loadPdfDoc,
  loadResultPdf,
  renderCompare,
  redrawOverlay,
  setCompareMode,
  setStatus,
  setBaseMaskingProgress,
  setTextCompareContents,
  renderFinalState,
  renderDocumentReviewSurfaces,
  resetDerivedArtifacts: () => resetDerivedArtifacts("remask"),
  updateWorkflowReadiness,
  updateCanvasControls,
  cancelGeometryDraft: () => cancelGeometryDraft(),
  onReviewResolutionFailure: (request, error) => reportReviewResolutionFailure(request.reviewId, error),
  runMaskingPipeline,
});
applyPublicManualMaskActions = (actions) => maskingRunController.applyPublicManualMaskActions(actions);
const { runMaskingForSelectedDocument } = maskingRunController;
const disposers = new Set<() => void>();
function registerDisposer(cleanup: () => void): () => void {
  let registered = true;
  disposers.add(cleanup);
  return () => {
    if (!registered) return;
    registered = false;
    disposers.delete(cleanup);
  };
}
function dispose() {
  applyModalInertTree(null);
  const cleanups = [...disposers];
  disposers.clear();
  for (const cleanup of cleanups) cleanup();
}

type ReviewRailAction = "mask" | "exclude" | "acknowledge" | "confirm_boundary" | "confirm_suggested_geometry" | "confirm_geometry" | "reanalyze";

const REVIEW_RESOLUTION_FAILURE_MESSAGES: Readonly<Record<string, string>> = {
  INVALID_REVIEW_GEOMETRY: "그린 영역이 표시된 검출 위치를 모두 덮지 않았습니다. 강조 표시를 포함하도록 다시 그려주세요.",
  REVIEW_RESOLUTION_REJECTED: "검토 해결 요청이 거부되었거나 최신 검토 세션이 아닙니다. 최신 상태를 확인한 뒤 다시 시도하세요.",
  REVIEW_RESOLUTION_FAILED: "검토 해결을 서버에 반영하지 못했습니다. 최신 상태를 확인한 뒤 다시 시도하세요.",
};

function reviewResolutionFailureCode(error: unknown): string {
  if (error && typeof error === "object" && "code" in error && typeof error.code === "string") return error.code;
  if (error instanceof Error && /^[A-Z][A-Z0-9_]+$/.test(error.message)) return error.message;
  return "REVIEW_RESOLUTION_FAILED";
}

function reviewResolutionFailureMessage(code: string): string {
  return REVIEW_RESOLUTION_FAILURE_MESSAGES[code] ?? `검토 해결에 실패했습니다. (${code}) 최신 상태를 확인하세요.`;
}

function reportReviewResolutionFailure(reviewId: string, error: unknown): void {
  console.error("Masking review action failed.", error);
  const code = reviewResolutionFailureCode(error);
  const message = reviewResolutionFailureMessage(code);
  reviewFailureById.set(reviewId, message);
  setStatus(message);
  renderDocumentReviewSurfaces();
}

function rectsIntersect(left: PdfPointsTopLeftRect, right: PdfPointsTopLeftRect): boolean {
  return Math.max(Math.min(left.x0, left.x1), Math.min(right.x0, right.x1))
    < Math.min(Math.max(left.x0, left.x1), Math.max(right.x0, right.x1))
    && Math.max(Math.min(left.y0, left.y1), Math.min(right.y0, right.y1))
      < Math.min(Math.max(left.y0, left.y1), Math.max(right.y0, right.y1));
}
function cancelGeometryDraft(): boolean {
  const draft = state.geometryDraft;
  if (!draft) return false;
  state.boxes = state.boxes.filter((box) => box.tag !== draft.owner);
  state.geometryDraft = null;
  state.documentEditRevision = (state.documentEditRevision || 0) + 1;
  state.selectedCanvasBoxIndex = -1;
  redrawOverlay();
  updateCanvasControls();
  return true;
}

function setFocusedDetectionForReview(reviewId: string | null): void {
  const report = boundPublicReport(state.latestReport);
  const manifest = report?.analysisManifest;
  const review = reviewId === null ? null : report?.reviewQueue?.find((item) => item.reviewId === reviewId);
  if (!review || !manifest) {
    setFocusedDetectionTarget(null);
    return;
  }
  const reviewSurface = dashboardReviewState(report);
  const surfaceItem = reviewSurface.status === "valid"
    ? reviewSurface.items.find((item) => item.reviewId === review.reviewId)
    : undefined;
  const occurrence = review.kind === "name" || review.kind === "institution"
    ? manifest.occurrences.find((item) => item.occurrenceId === review.targetId)
    : null;
  const region = review.kind === "region_geometry"
    ? manifest.regions.find((item) => item.regionId === review.targetId)
    : null;
  const segmentOccurrences = review.kind !== "name" && review.kind !== "institution" && review.kind !== "region_geometry"
    ? manifest.occurrences.filter((item) => item.segmentId === review.targetId && item.page === review.pageStart)
    : [];
  const target: FocusedDetectionTarget = {
    page: occurrence?.page ?? region?.page ?? review.pageStart,
    rects: surfaceItem?.targetRects ?? occurrence?.rects ?? region?.rects ?? segmentOccurrences.flatMap((item) => item.rects),
    occurrenceIds: occurrence ? [occurrence.occurrenceId]
      : region ? manifest.occurrences.filter((item) => item.regionId === region.regionId && item.page === region.page).map((item) => item.occurrenceId)
        : segmentOccurrences.map((item) => item.occurrenceId),
    regionId: region?.regionId ?? null,
    ordinal: surfaceItem?.locationOrdinal ?? null,
  };
  setFocusedDetectionTarget(target);
}

function navigateToReviewLocation(item: {
  readonly reviewId: string;
  readonly pageStart: number;
  readonly targetRects: readonly PdfPointsTopLeftRect[];
}): Promise<void> {
  setFocusedDetectionForReview(item.reviewId);
  return goToReviewCanvasLocation(item.pageStart, item.targetRects);
}


async function resolveReviewFromRail(
  reviewId: string,
  action: ReviewRailAction,
  button: HTMLButtonElement | null,
  boundaryResolution?: BoundaryResolution,
): Promise<void> {
  setFocusedDetectionForReview(null);
  const manifest = state.latestReport?.analysisManifest;
  const review = state.latestReport?.reviewQueue?.find((item) => item.reviewId === reviewId);
  if (!manifest || !review || review.status !== "pending") {
    cancelGeometryDraft();
    setStatus("검토 항목이 오래되었거나 사용할 수 없습니다. 최신 상태를 확인하세요.");
    renderDocumentReviewSurfaces();
    return;
  }

  if (state.geometryDraft && state.geometryDraft.reviewId !== reviewId) cancelGeometryDraft();
  reviewFailureById.delete(reviewId);
  if (button !== null) button.disabled = true;
  try {
    let resolution: ResolveMaskingReviewRequest["resolution"];
    let geometryDraft: GeometryDraftOwner | null = null;
    if (review.kind === "ocr" && action === "reanalyze") {
      cancelGeometryDraft();
      resolution = { kind: "ocr", accepted: true };
    } else if ((review.kind === "name" || review.kind === "institution") && (action === "mask" || action === "exclude")) {
      resolution = { kind: review.kind, action };
    } else if (review.kind === "acknowledge" && action === "acknowledge") {
      resolution = { kind: "acknowledge", acknowledged: true };
    } else if (review.kind === "boundary" && action === "confirm_boundary") {
      const segment = manifest.segments.find((item) => item.segmentId === review.targetId);
      if (!segment) {
        setStatus("자동 경계의 문서 유형을 확정할 수 없습니다. 문서 유형을 선택한 뒤 다시 분석하세요.");
        return;
      }
      if (boundaryResolution !== undefined) {
        resolution = boundaryResolution;
      } else {
        const { kind: segmentKind } = segment;
        if (!isBoundarySegmentKind(segmentKind)) {
          setStatus("자동 경계의 문서 유형을 확정할 수 없습니다. 문서 유형을 선택한 뒤 다시 분석하세요.");
          return;
        }
        resolution = {
          kind: "boundary",
          pageStart: segment.pageStart,
          pageEnd: segment.pageEnd,
          segmentKind,
        };
      }
    } else if (review.kind === "region_geometry" && action === "confirm_suggested_geometry") {
      const rects = suggestedRegionGeometryRects(manifest, review);
      if (!rects) {
        setStatus("제안 영역을 찾지 못했습니다. 최신 상태를 확인한 뒤 다시 시도하세요.");
        return;
      }
      const clusteredReviews = geometryReviewCluster(manifest, review);
      const clusteredRegions = clusteredReviews
        .map((item) => manifest.regions.find((region) => region.regionId === item.targetId))
        .filter((region): region is NonNullable<typeof region> => region !== undefined);
      const regionKind = clusteredRegions[0]?.kind;
      if (!regionKind) {
        setStatus("영역 검토 대상을 찾지 못했습니다. 최신 상태를 확인한 뒤 다시 시도하세요.");
        return;
      }
      geometryDraft = {
        owner: `suggested:${review.reviewId}`,
        reviewId: review.reviewId,
        reviewIds: clusteredReviews.map((item) => item.reviewId),
        targetId: review.targetId,
        targetIds: clusteredReviews.map((item) => item.targetId),
        regionKind,
        candidateRects: clusteredRegions.flatMap((region) => region.rects),
        page: review.pageStart,
        analysisRevision: manifest.analysisRevision,
        runId: manifest.runId,
        manifestHash: manifest.manifestHash,
      };
      resolution = { kind: "region_geometry", rects };
    } else if (review.kind === "region_geometry" && action === "confirm_geometry") {
      if (review.pageStart !== review.pageEnd) {
        setStatus("여러 페이지에 걸친 영역은 페이지별로 확인해야 합니다.");
        return;
      }
      const draftOwner = `review:${review.reviewId}:${review.targetId}`;
      const priorDraft = state.geometryDraft;
      if (!priorDraft || priorDraft.reviewId !== review.reviewId || priorDraft.targetId !== review.targetId) {
        if (priorDraft) cancelGeometryDraft();
        const clusteredReviews = geometryReviewCluster(manifest, review);
        const clusteredRegions = clusteredReviews
          .map((item) => manifest.regions.find((region) => region.regionId === item.targetId))
          .filter((region): region is NonNullable<typeof region> => region !== undefined);
        const regionKind = clusteredRegions[0]?.kind;
        if (!regionKind) {
          setStatus("영역 검토 대상을 찾지 못했습니다. 최신 상태를 확인한 뒤 다시 시도하세요.");
          return;
        }
        state.geometryDraft = {
          owner: draftOwner,
          reviewId: review.reviewId,
          reviewIds: clusteredReviews.map((item) => item.reviewId),
          targetId: review.targetId,
          targetIds: clusteredReviews.map((item) => item.targetId),
          regionKind,
          candidateRects: clusteredRegions.flatMap((region) => region.rects),
          page: review.pageStart,
          analysisRevision: manifest.analysisRevision,
          runId: manifest.runId,
          manifestHash: manifest.manifestHash,
        };
        activateAppScreen("documents");
        setInspectorCollapsed(false);
        setCompareMode("pdf");
        setCanvasMode(true);
        setActiveCanvasTool("mask");
        await goToReviewPage(review.pageStart);
        redrawOverlay();
        updateCanvasControls();
        setStatus(`${review.pageStart + 1}쪽의 표시된 영역과 강조된 검출 위치를 모두 덮도록 드래그한 뒤 영역 확정을 누르세요.`);
        renderDocumentReviewSurfaces();
        return;
      }
      if (priorDraft.analysisRevision !== manifest.analysisRevision || priorDraft.runId !== manifest.runId || priorDraft.manifestHash !== manifest.manifestHash) {
        cancelGeometryDraft();
        setStatus("검토 세션이 변경되어 그린 영역을 취소했습니다. 최신 항목을 다시 선택하세요.");
        return;
      }
      const rects = state.boxes
        .filter((box) => box.mode === "mask" && box.page === review.pageStart && box.tag === draftOwner)
        .map(({ x0, y0, x1, y1 }) => ({ x0, y0, x1, y1 }));
      if (rects.length === 0) {
        renderDocumentReviewSurfaces();
        setStatus(`${review.pageStart + 1}쪽 보정 화면에 마스킹 영역을 그린 뒤 다시 확인하세요.`);
        return;
      }
      const linkedOccurrenceRects = manifest.occurrences
        .filter((occurrence) => occurrence.page === review.pageStart && occurrence.regionId !== null && priorDraft.targetIds.includes(occurrence.regionId))
        .flatMap((occurrence) => occurrence.rects);
      if (linkedOccurrenceRects.some((occurrenceRect) => !rects.some((draftRect) => rectContains(draftRect, occurrenceRect)))) {
        const code = "INVALID_REVIEW_GEOMETRY";
        const message = reviewResolutionFailureMessage(code);
        reviewFailureById.set(reviewId, message);
        setStatus(message);
        renderDocumentReviewSurfaces();
        return;
      }
      geometryDraft = priorDraft;
      resolution = { kind: "region_geometry", rects };
    } else {
      setStatus("선택한 해결 방법이 이 검토 항목과 맞지 않습니다.");
      return;
    }

    const resolutionCount = resolution.kind === "region_geometry" ? geometryDraft?.reviewIds.length ?? 1 : 1;
    for (let resolutionIndex = 0; resolutionIndex < resolutionCount; resolutionIndex += 1) {
      const currentManifest = state.latestReport?.analysisManifest;
      const currentReview = resolution.kind === "region_geometry" && geometryDraft
        ? currentManifest?.reviewItems.find((candidate) => {
          if (candidate.kind !== "region_geometry" || candidate.status !== "pending") return false;
          const region = currentManifest.regions.find((item) => item.regionId === candidate.targetId);
          return region?.page === geometryDraft.page && region.kind === geometryDraft.regionKind
            && region.rects.some((regionRect) => geometryDraft.candidateRects.some((candidateRect) => rectsIntersect(regionRect, candidateRect)));
        })
        : currentManifest?.reviewItems.find((candidate) => candidate.kind === review.kind && candidate.status === "pending" && candidate.targetId === review.targetId);
      if (!currentManifest || !currentReview) {
        cancelGeometryDraft();
        const code = "REVIEW_RESOLUTION_REJECTED";
        reviewFailureById.set(reviewId, reviewResolutionFailureMessage(code));
        setStatus(reviewResolutionFailureMessage(code));
        renderDocumentReviewSurfaces();
        return;
      }
      const resolved = await maskingRunController.resolveReview({
        runId: currentManifest.runId,
        analysisRevision: currentManifest.analysisRevision,
        manifestHash: currentManifest.manifestHash,
        reviewId: currentReview.reviewId,
        resolution,
      });
      if (!resolved) {
        cancelGeometryDraft();
        const code = "REVIEW_RESOLUTION_REJECTED";
        const message = reviewFailureById.get(reviewId) ?? reviewResolutionFailureMessage(code);
        reviewFailureById.set(reviewId, message);
        setStatus(message);
        renderDocumentReviewSurfaces();
        return;
      }
    }
    if (resolution.kind === "region_geometry") cancelGeometryDraft();
    reviewFailureById.delete(reviewId);
    setStatus("검토 항목을 반영했습니다.");
    renderDocumentReviewSurfaces();
  } catch (error) {
    cancelGeometryDraft();
    reportReviewResolutionFailure(reviewId, error);
  } finally {
    if (button !== null) button.disabled = false;
  }
}

async function resolveBoundaryReviewFromStrip(reviewId: string, resolution: BoundaryResolution): Promise<void> {
  await resolveReviewFromRail(reviewId, "confirm_boundary", null, resolution);
}

const keywordController = createKeywordDialogController({
  setStatus,
  renderCanvasFinalSaveSummary,
  renderFinalSaveConfirmation,
  updateWorkflowReadiness,
  hasSelectedDocument: () => Boolean(state.documentProvenance.original.path),
  rerunMasking: () => btnRunMasking.click(),
});
const { keywordList, writeKeywordList, openKeywordDialog, closeKeywordDialog, applyKeywords } = keywordController;

const batchController = createDocumentBatchController({
  state,
  batchSummaryEl,
  batchQueueEl,
  btnRunBatch,
  compactPath,
  openPath,
  setStatus,
  renderDocumentReviewSurfaces,
  loadOriginalDocument,
  runMaskingForSelectedDocument,
});
const { renderBatchQueue, addBatchDocuments, processBatchItem } = batchController;

function applySettings(application: AppSettings | SettingsApplication): void {
  const settings = applySettingsStore(application);
  state.outputDir = settings.outputDir;
  updateOutputDirectoryState();
  publishSessionDocuments({ documents: sessionDocuments, saves: sessionSaves, profile: deskProfileFromValue(settings.profile) });
}

function collectSettings(): AppSettings {
  return { ...currentSettings(), outputDir: state.outputDir };
}

function initialize() {
  const loadedSettings = loadSettingsIntoStore();
  applySettings(loadedSettings);
  renderFinalState(null);
  renderBatchQueue();
  renderCompare();
  updateMeta();
  setTextCompareContents("", "");
  renderDocumentReviewSurfaces();
  const listen = (target: EventTarget, type: string, listener: EventListener, capture = false) => {
    target.addEventListener(type, listener, capture);
    registerDisposer(() => target.removeEventListener(type, listener, capture));
  };
  listen(btnCloseMaskingProgress, "click", closeMaskingProgressDialog);
  listen(btnCancelMaskingProgress, "click", closeMaskingProgressDialog);
  listen(document, "keydown", (event) => {
    if (!(event instanceof KeyboardEvent)) return;
    const activeModal = currentModalLayer();
    if (!activeModal || event.key !== "Tab") return;
    const targets = focusableModalElements(activeModal);
    if (targets.length === 0) {
      event.preventDefault();
      ensureModalFocus(activeModal, event.shiftKey);
      return;
    }
    const first = activeModal.querySelector<HTMLElement>("[data-modal-autofocus='true']") ?? targets[0];
    const last = targets[targets.length - 1];
    const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!event.shiftKey && active === last) {
      event.preventDefault();
      first?.focus();
    } else if (event.shiftKey && active === first) {
      event.preventDefault();
      last?.focus();
    }
  }, true);
  listen(document, "focusin", (event) => {
    const activeModal = currentModalLayer();
    const target = event.target;
    if (!activeModal || !(target instanceof HTMLElement) || activeModal.contains(target)) return;
    ensureModalFocus(activeModal, false);
  });
  const dismissByBackdrop = (modal: HTMLElement, close: () => void) => {
    listen(modal, "mousedown", (event) => {
      if (event.target === modal && modal.dataset.modalDismissible === "true") close();
    });
  };
  dismissByBackdrop(maskingProgressModalEl, closeMaskingProgressDialog);
  listen(document, "keydown", (event) => {
    if (!(event instanceof KeyboardEvent)) return;
    if (event.key !== "Escape") return;
    if (!maskingProgressModalEl.classList.contains("is-hidden")) {
      event.preventDefault();
      closeMaskingProgressDialog();
    }
  });
  setCompareMode("pdf");
  if (isStandaloneCanvasWindow) {
    // v4 P2: 독립 작업창(?mode=canvas)도 통합 "문서" 화면을 띄운다(구 canvas 패널 폐지).
    activateAppScreen("documents");
    void hydrateStandaloneCanvasWindow();
  } else {
    activateAppScreen("desk");
    setCanvasMode(true, { allowEmptyCanvas: true });
    setStatus(loadedSettings.diagnostic.status === "defaulted"
      ? "설정 복구 실패: 기본값을 적용했습니다. 설정을 검토한 뒤 저장하세요."
      : "대기 중: PDF 열기 → 마스킹 실행 → 검토 → 최종 저장");
  }
}

return {
  state,
  initialize,
  dispose,
  registerDisposer,
  pickInputDocument: pickSessionDocument,
  pickDeskDocument,
  openStoredDocument,
  openQaDocument,
  loadCanvasWorkspacePdf,
  addBatchDocuments,
  maybeApplyDefaultOutputDir,
  setStatus,
  runMaskingForSelectedDocument,
  isCustomRegionScope,
  renderBatchQueue,
  updateWorkflowReadiness,
  processBatchItem,
  clampPage,
  renderCompare,
  goToReviewPage,
  moveOrigPage,
  moveResultPage,
  saveFinalOutput,
  lastFinalSaveOutcome: () => lastFinalSaveOutcome,
  openFinalSaveDialog,
  setCompareMode,
  rememberAuxReturnScreen,
  collectSettings,
  activateAppScreen,
  returnFromAuxScreen,
  closeFinalSaveDialog,
  closeFinalizationSuccess,
  openCompletedFinalStorage,
  openCompletedFinalFile,
  showCompletedFinalStorage,
  closeKeywordDialog,
  isAuxScreen,
  applySettings,
  renderFinalSaveConfirmation,
  renderCanvasFinalSaveSummary,
  refreshFinalSaveSummary: () => {
    renderCanvasFinalSaveSummary();
    renderFinalSaveConfirmation();
  },
  keywordList,
  writeKeywordList,
  openKeywordDialog,
  applyKeywords,
  openCanvasDesktopWindow,
  setCanvasMode,
  setCanvasToolsCollapsed,
  setCanvasPropertiesCollapsed,
  expandCanvasPanels,
  pickCanvasPdf,
  resetDocumentSession,
  prepareForDocumentReplacement,
  invalidateLifecycle,
  resolveDiscardConfirmation,
  resolveReviewFromRail,
  navigateToReviewLocation,
  resolveBoundaryReviewFromStrip,
  loadPageThumbnails,
  setFocusedDetectionForReview,
  setActiveCanvasTool,
  adjustZoom,
  deleteSelectedCanvasBox,
  convertCanvasSelectedBox,
  applyPendingManualBoxes,
  updateOriginalCompareVisibility,
  undoLastCanvasBox,
  clearDerivedArtifacts,
  get publicReportBindingReason() {
    return state.publicReportBindingReason;
  },
};

}
export type ApplicationController = ReturnType<typeof createApplicationController>;
