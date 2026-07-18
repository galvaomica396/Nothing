import { invoke } from "@tauri-apps/api/core";
import { openPath } from "@tauri-apps/plugin-opener";
import type { PDFDocumentProxy } from "pdfjs-dist";
import type { BatchItem } from "../batchQueue";
import { renderDashboardSurfaces } from "../dashboardSurfaces";
import { createAppSettingsController } from "../features/app-settings/appSettingsController";
import { createCanvasRenderController } from "../features/canvas-workbench/canvasRenderController";
import { createDocumentBatchController } from "../features/document-batch/batchQueueController";
import { createDocumentSessionController } from "../features/document-session/documentSessionController";
import type { BoxMode } from "../features/document-session/documentSessionController";
import { createFinalizationController } from "../features/finalization/finalizationController";
import { createKeywordDialogController } from "../features/keyword-dialog/keywordDialogController";
import { createManualAdjustmentController } from "../features/manual-adjustment/manualAdjustmentController";
import type { BoxItem, CanvasEditorTool } from "../features/manual-adjustment/manualAdjustmentController";
import { createMaskingRunController } from "../features/masking-run/maskingRunController";
import type { MaskingPipelineArgs } from "../features/masking-run/maskingRunController";
import { finalSaveWarningPresentation } from "../features/save-gate/saveGate";
import type { MaskingOptions, MaskingResult } from "../services/tauri/maskingContracts";
import { loadSettings, maskingOutputArtifacts, saveSettings } from "../settingsState";
import type { AppSettings, DeidentificationMode } from "../settingsState";
import { emptyDocumentProvenance, statusSourcePath } from "../state/documentProvenance";
import type { DocumentProvenance } from "../state/documentProvenance";
import type { BaseMaskingProgress, SafeReport } from "../state/maskingSession";
import { isWorkflowScreen, settingsScopeStatus } from "../workflowFlow";
import type { WorkflowScreen } from "../workflowFlow";
import type { LegacyDomBindings } from "./domBindings";
type CompareMode = "pdf" | "text";

export interface LegacySessionState {
  documentProvenance: DocumentProvenance;
  outputDir: string;
  previewWorkDir: string;
  currentOrigPage: number;
  currentResultPage: number;
  scale: number;
  boxes: BoxItem[];
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
  openOutputAfterSave: boolean;
}

export function createLegacyAppController(bindings: LegacyDomBindings) {

const state: LegacySessionState = {
  documentProvenance: emptyDocumentProvenance(),
  outputDir: "",
  previewWorkDir: "",
  currentOrigPage: 1,
  currentResultPage: 1,
  scale: 1.2,
  boxes: [] as BoxItem[],
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
  // 복원(un-mask) 반영 후 재검증이 통과하지 못한 상태. 저장을 막지 않고 저장 직전
  // 경고로만 노출한다. 리포트가 새로 채택되면(renderFinalState) false로 초기화한다.
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
  openOutputAfterSave: false,
};

const {
  $,
  statusEl,
  statusDetailEl,
  inputPathEl,
  pageInfoOrigEl,
  pageInfoResultEl,
  boxInfoEl,
  viewerMetaOrigEl,
  viewerMetaResultEl,
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
  btnUndo,
  btnClear,
  btnManualApply,
  btnSave,
  finalSaveReadinessEl,
  btnOpenKeywordDialog,
  keywordDialogEl,
  keywordDialogChipListEl,
  newDocumentDialogEl,
  btnConfirmNewDocument,
  compareModePdf,
  compareModeText,
  toggleOriginalCompareEl,
  originalComparePanelEl,
  modeMask,
  modeRestore,
  profileEl,
  engineEl,
  displayModeEl,
  deidentificationPolicyEl,
  regionScopeEl,
  customRegionsEl,
  customKeywordsEl,
  finalStateCardEl,
  finalStateTitleEl,
  finalStateDetailEl,
  workspaceShellEl,
  documentsScreenEl,
  reviewInspectorEl,
  btnToggleInspector,
  btnPickBatch,
  btnRunBatch,
  batchSummaryEl,
  batchQueueEl,
  btnMaskCanvas,
  btnCollapseCanvasTools,
  btnCollapseCanvasProperties,
  btnCanvasZoomOut,
  btnCanvasZoomIn,
  btnCanvasUndo,
  btnCanvasClear,
  btnCanvasApply,
  btnCanvasFinalSave,
  btnNewDocument,
  btnCanvasDeleteBox,
  btnCanvasBoxDelete,
  canvasModeStatusEl,
  canvasActiveToolLabelEl,
  canvasToolReadinessEl,
  canvasBoxListEl,
  canvasBoxPropertiesEl,
  canvasBoxPropertyPageEl,
  canvasBoxPropertyTypeEl,
  canvasBoxPropertyCoordinatesEl,
  canvasBoxPropertySizeEl,
  canvasSummaryMaskCountEl,
  canvasSummaryRestoreCountEl,
  canvasSummaryKeywordCountEl,
  canvasSummaryOutputStateEl,
  btnCanvasBoxConvertMask,
  btnCanvasBoxConvertRestore,
  finalSaveDialogEl,
  finalSaveDialogStateEl,
  finalSaveWarningListEl,
  btnDialogSaveAll,
  reviewSummaryMaskCountEl,
  reviewSummaryRestoreCountEl,
  reviewSummaryKeywordCountEl,
  reviewSummaryOutputFileEl,
  reviewSummaryPdfPolicyEl,
  reviewSummaryTxtPolicyEl,
  settingsApplyScopeStatusEl,
  settingsTabButtons,
  settingsPanels,
  settingsThemeInputs,
  settingsExportMaskedTextEl,
  optPdfRedactionEl,
  settingsOpenOutputAfterSaveEl,
  appScreenButtons,
  appScreens,
  canvasEditorToolButtons,
} = bindings;

let lastSavedAt = "-";
let activeCanvasTool: CanvasEditorTool = "mask";
let settingsSnapshot: AppSettings | null = null;
const isStandaloneCanvasWindow = new URLSearchParams(window.location.search).get("mode") === "canvas";
document.body.classList.toggle("standalone-canvas-window", isStandaloneCanvasWindow);

function compactPath(path: string) {
  if (!path) return "-";
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length > 3 ? `.../${parts.slice(-3).join("/")}` : path;
}

function updateOutputDirectoryState() {
  renderSettingsScopeStatus();
  updateWorkflowReadiness();
}

function renderSettingsScopeStatus() {
  const originalPath = state.documentProvenance.original.path;
  const scope = settingsScopeStatus({
    selectedDocumentPath: originalPath,
    currentDocumentName: originalPath ? compactPath(originalPath) : "",
  });
  settingsApplyScopeStatusEl.textContent = scope.applyLabel;
  settingsApplyScopeStatusEl.title = scope.scopeLabel;
}


function setBaseMaskingProgress(progress: BaseMaskingProgress) {
  // v4 P2: 진행률 스텝바(workflow-progress-*)는 문서 관제와 함께 제거됐다.
  // 진행 상태는 상태 리본/버튼 활성으로 드러난다. 내부 상태만 갱신한다.
  state.baseMaskingProgress = {
    status: progress.status,
    percent: progress.percent,
    displayMode: progress.displayMode,
  };
}

function activateSettingsTab(tabName: string) {
  for (const button of settingsTabButtons) {
    const active = button.dataset.settingsTab === tabName;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  }
  for (const panel of settingsPanels) {
    panel.classList.toggle("is-hidden", panel.dataset.settingsPanel !== tabName);
  }
}

function activateAppScreen(screenName: string) {
  const nextScreen: WorkflowScreen = isWorkflowScreen(screenName) ? screenName : "documents";
  workspaceShellEl.dataset.activeScreen = nextScreen;
  for (const button of appScreenButtons) {
    const active = button.dataset.screenTarget === screenName || button.dataset.screenTarget === nextScreen;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  for (const panel of appScreens) {
    panel.classList.toggle("is-active", panel.dataset.screenPanel === nextScreen);
  }
  updateStatusDetail();
}

function setInspectorCollapsed(collapsed: boolean) {
  documentsScreenEl.classList.toggle("is-inspector-collapsed", collapsed);
  reviewInspectorEl.classList.toggle("is-collapsed", collapsed);
  btnToggleInspector.setAttribute("aria-expanded", String(!collapsed));
  btnToggleInspector.textContent = collapsed ? "검토 열기" : "패널 접기";
}

// 보조 화면에서 돌아갈 단일 문서 화면을 기억한다.
const AUX_SCREENS: readonly WorkflowScreen[] = ["settings", "masking-settings"];
let auxReturnScreen: WorkflowScreen = "documents";

function isAuxScreen(screen: string): boolean {
  return AUX_SCREENS.includes(screen as WorkflowScreen);
}

function rememberAuxReturnScreen() {
  const current = workspaceShellEl.dataset.activeScreen ?? "documents";
  if (!isAuxScreen(current) && isWorkflowScreen(current)) {
    auxReturnScreen = current;
  }
}

// 보조 화면에서 이전 1급 화면으로 복귀한다(닫기=문서 홈과 달리 진입 지점으로 되돌림).
function returnFromAuxScreen() {
  activateAppScreen(auxReturnScreen);
  document.querySelector<HTMLElement>(`[data-screen-target="${auxReturnScreen}"]`)?.focus();
}

function openSettingsScreen(tabName = "general") {
  rememberAuxReturnScreen();
  settingsSnapshot = collectSettings();
  activateSettingsTab(tabName);
  activateAppScreen("settings");
  document.querySelector<HTMLElement>("[data-settings-panel]:not(.is-hidden) input, [data-settings-panel]:not(.is-hidden) select, [data-settings-panel]:not(.is-hidden) button")?.focus();
}

function closeSettingsScreen() {
  activateAppScreen("documents");
  document.querySelector<HTMLElement>('[data-screen-target="documents"]')?.focus();
}

function setModalVisible(element: HTMLElement, visible: boolean) {
  element.classList.toggle("is-hidden", !visible);
  element.setAttribute("aria-hidden", String(!visible));
}

let discardConfirmationResolve: ((confirmed: boolean) => void) | null = null;

function confirmDiscardCurrentWork(): Promise<boolean> {
  if (discardConfirmationResolve) return Promise.resolve(false);
  setModalVisible(newDocumentDialogEl, true);
  btnConfirmNewDocument.focus();
  return new Promise((resolve) => {
    discardConfirmationResolve = resolve;
  });
}

function resolveDiscardConfirmation(confirmed: boolean) {
  setModalVisible(newDocumentDialogEl, false);
  const resolve = discardConfirmationResolve;
  discardConfirmationResolve = null;
  resolve?.(confirmed);
}

// Canvas rendering/interaction and single-document masking-run are owned by
// feature controllers; startLegacyApp binds the DOM elements and orchestration
// callbacks and destructures the controller methods so the existing wiring call
// sites stay unchanged. The run_masking_pipeline IPC stays anchored here as a
// thin wrapper injected into the masking-run controller.
async function runMaskingPipeline(args: MaskingPipelineArgs): Promise<MaskingResult> {
  return invoke<MaskingResult>("run_masking_pipeline", {
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
});
const {
  renderCompare,
  redrawOverlay,
  adjustZoom,
  moveOrigPage,
  moveResultPage,
  cancelActiveInteraction,
} = canvasRenderController;

function renderDocumentReviewSurfaces() {
  renderDashboardSurfaces(document, {
    selectedPath: state.documentProvenance.original.path,
    documentKind: state.documentProvenance.original.kind,
    batchItems: state.batchItems,
    latestDocumentPath: statusSourcePath(state.documentProvenance),
    latestReportPath: state.latestReportPath,
    report: state.latestReport,
    keywordCount: keywordList().length,
    maskBoxCount: state.boxes.filter((box) => box.mode === "mask").length,
    restoreBoxCount: state.boxes.filter((box) => box.mode === "restore").length,
  });
}

function setCanvasWideMode() {
  const canvasScreen = $("#canvas-workspace-screen") as HTMLElement;
  const wide = canvasScreen.classList.contains("tools-collapsed") || canvasScreen.classList.contains("properties-collapsed");
  canvasScreen.classList.toggle("canvas-wide-mode", wide);
}

function setCanvasToolsCollapsed(collapsed: boolean) {
  const canvasScreen = $("#canvas-workspace-screen") as HTMLElement;
  canvasScreen.classList.toggle("tools-collapsed", collapsed);
  btnCollapseCanvasTools.setAttribute("aria-pressed", String(collapsed));
  btnCollapseCanvasTools.textContent = collapsed ? "도구 패널 열기" : "도구 패널 접기";
  setCanvasWideMode();
}

function setCanvasPropertiesCollapsed(collapsed: boolean) {
  const canvasScreen = $("#canvas-workspace-screen") as HTMLElement;
  canvasScreen.classList.toggle("properties-collapsed", collapsed);
  btnCollapseCanvasProperties.setAttribute("aria-pressed", String(collapsed));
  btnCollapseCanvasProperties.textContent = collapsed ? "속성 패널 열기" : "속성 패널 접기";
  setCanvasWideMode();
}

function expandCanvasPanels() {
  setCanvasToolsCollapsed(false);
  setCanvasPropertiesCollapsed(false);
}

function setStatus(msg: string) {
  statusEl.textContent = msg;
  statusEl.removeAttribute("title");
  updateStatusDetail();
}

function getRule(id: string): boolean {
  return ($(`#rule-${id}`) as HTMLInputElement).checked;
}

function isCustomRegionScope(): boolean {
  return regionScopeEl.value === "custom";
}

function collectMaskingOptions(): MaskingOptions {
  // 안전 리포트는 계속 내부에 생성한다. 사용자가 명시적으로 선택한 경우에만
  // 원문이 아닌 비식별 TXT를 추가한다.
  return {
    rrn: getRule("rrn"),
    phone: getRule("phone"),
    business_reg: getRule("business_reg"),
    name: getRule("name"),
    address: getRule("address"),
    place: getRule("place"),
    legal_party: getRule("legal_party"),
    company: getRule("company"),
    court: getRule("court"),
    case_title: getRule("case_title"),
    case_number: getRule("case_number"),
    law_firm: getRule("law_firm"),
    attorney: getRule("attorney"),
    approval_line: getRule("approval_line"),
    region_context: getRule("region_context"),
    doc_meta: getRule("doc_meta"),
    pdf_redaction: ($("#opt-pdf-redaction") as HTMLInputElement).checked,
    custom_keywords: customKeywordsEl.value.trim(),
    extract_engine: engineEl.value,
    profile: profileEl.value,
    output_artifacts: maskingOutputArtifacts(settingsExportMaskedTextEl.checked),
    display_mode: displayModeEl.value as MaskingOptions["display_mode"],
    deidentification_policy: deidentificationPolicyEl.value as DeidentificationMode,
    region_scope: regionScopeEl.value,
    custom_regions: isCustomRegionScope() ? customRegionsEl.value.trim() : "",
    return_text_preview: false,
  };
}

function setRuleState(id: string, checked: boolean, disabled: boolean) {
  const el = $(`#rule-${id}`) as HTMLInputElement;
  el.checked = checked;
  el.disabled = disabled;
  el.closest("label")?.classList.toggle("is-disabled", disabled);
}

function applyProfileDefaults() {
  const profile = profileEl.value;
  if (profile === "legal") {
    setRuleState("approval_line", false, true);
    setRuleState("region_context", false, true);
    setRuleState("doc_meta", false, true);
  } else {
    setRuleState("approval_line", true, false);
    setRuleState("region_context", true, false);
    setRuleState("doc_meta", true, false);
  }
}

function updateRegionScopeControls() {
  const customEnabled = isCustomRegionScope();
  customRegionsEl.disabled = !customEnabled;
  customRegionsEl.closest(".config-cell")?.classList.toggle("is-disabled", !customEnabled);
  if (!customEnabled) {
    customRegionsEl.value = "";
  }
}

function updateMaskedTextOptionControls() {
  const enabled = settingsExportMaskedTextEl.checked;
  deidentificationPolicyEl.disabled = !enabled;
  deidentificationPolicyEl.closest(".dm-field")?.classList.toggle("is-disabled", !enabled);
  deidentificationPolicyEl.dispatchEvent(new Event("change", { bubbles: true }));
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
  viewerMetaOrigEl.textContent = `페이지 ${pageInfoOrigEl.textContent}`;
  viewerMetaResultEl.textContent = `페이지 ${pageInfoResultEl.textContent}`;
  const pageBoxes = state.boxes.filter((b) => b.page === resultPage - 1);
  boxInfoEl.textContent = `현재 ${pageBoxes.length}개 / 전체 ${state.boxes.length}개`;
  updateCanvasControls();
  updateStatusDetail();
}

function updateStatusDetail() {
  const totalPages = Math.max(state.origDoc?.numPages || 0, state.resultDoc?.numPages || 0);
  const current = totalPages > 0 ? Math.max(state.currentOrigPage, state.currentResultPage) : 0;
  const sourcePath = statusSourcePath(state.documentProvenance);
  zoomInfoEl.textContent = `${Math.round(state.scale * 100)}%`;
  const compareLabel = state.compareMode === "pdf" ? "PDF 대조" : "텍스트 대조";
  const workMode = state.canvasMode ? "캔버스" : "일반";
  // 상태바는 핵심 3개만 노출한다 (경로·페이지·저장). 배율은 캔버스 zoom-info,
  // 보기/작업/박스는 화면 자체에서 드러나므로 상태바 나열에서 제거.
  void compareLabel;
  void workMode;
  statusDetailEl.textContent = `문서 ${sourcePath ? "준비" : "없음"}  ·  페이지 ${current}/${totalPages}  ·  저장 ${lastSavedAt}`;
}

function updateDocumentControls() {
  const pdfActive = isPdfInput();
  const hasInput = state.documentProvenance.original.kind !== "";
  btnManualApply.disabled = hasInput && !pdfActive;
  btnUndo.disabled = hasInput && !pdfActive;
  modeMask.disabled = hasInput && !pdfActive;
  modeRestore.disabled = hasInput && !pdfActive;
  compareModePdf.disabled = hasInput && !pdfActive;
  updateCanvasControls();
}

function hasTauriRuntime() {
  return Boolean((window as any).__TAURI_INTERNALS__ || (window as any).__TAURI__);
}

function renderFinalState(report: SafeReport | null) {
  // 리포트가 교체/무효화되는 유일한 지점. 새 리포트가 채택되면 이전 복원 재검증
  // 실패 플래그는 무효이므로 초기화한다. 실패 시에는 호출부(applyPendingManualBoxes)가
  // 이 함수 이후에 플래그를 다시 세운다.
  state.restoreRevalidationFailed = false;
  const presentation = finalSaveWarningPresentation({ hasReportPath: Boolean(report), report });
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
  originalComparePanelEl.classList.toggle("is-hidden", !toggleOriginalCompareEl.checked);
}

const invokeCommand = <T>(command: string, args?: Record<string, unknown>): Promise<T> => invoke<T>(command, args);

const documentSessionController = createDocumentSessionController({
  state,
  inputPathEl,
  displayModeEl,
  modeMask,
  modeRestore,
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
  setCanvasMode: (active, options) => setCanvasMode(active, options),
  redrawOverlay,
  updateMeta,
  resetLastSavedAt: () => {
    lastSavedAt = "-";
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
    setModalVisible(keywordDialogEl, false);
    setModalVisible(finalSaveDialogEl, false);
    resolveDiscardConfirmation(false);
  },
  setStatus,
});
const {
  loadPdfDoc,
  loadOriginalDocument,
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
} = documentSessionController;

const manualAdjustmentController = createManualAdjustmentController({
  state,
  invokeCommand,
  displayModeEl,
  customKeywordsEl,
  modeMask,
  modeRestore,
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
  canvasSummaryMaskCountEl,
  canvasSummaryRestoreCountEl,
  canvasSummaryKeywordCountEl,
  canvasSummaryOutputStateEl,
  canvasModeStatusEl,
  btnCanvasZoomOut,
  btnCanvasZoomIn,
  btnCanvasUndo,
  btnCanvasDeleteBox,
  btnCanvasClear,
  btnCanvasBoxDelete,
  btnCanvasBoxConvertMask,
  btnCanvasBoxConvertRestore,
  btnMaskCanvas,
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
  setTextCompareContents,
  updateWorkflowReadiness: () => updateWorkflowReadiness(),
  updateStatusDetail,
  setStatus,
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
  customKeywordsEl,
  deidentificationPolicyEl,
  exportMaskedTextEl: settingsExportMaskedTextEl,
  optPdfRedactionEl,
  finalSaveDialogEl,
  finalSaveDialogStateEl,
  finalSaveWarningListEl,
  btnDialogSaveAll,
  btnSave,
  btnCanvasFinalSave,
  btnNewDocument,
  btnRunMasking,
  btnManualApply,
  btnCanvasApply,
  btnPickPdf,
  btnPickBatch,
  btnRunBatch,
  btnClear,
  finalSaveReadinessEl,
  reviewSummaryMaskCountEl,
  reviewSummaryRestoreCountEl,
  reviewSummaryKeywordCountEl,
  reviewSummaryOutputFileEl,
  reviewSummaryPdfPolicyEl,
  reviewSummaryTxtPolicyEl,
  isPdfInput,
  applyPendingManualBoxes,
  setModalVisible,
  collectSettings: () => collectSettings(),
  saveSettings,
  loadResultPdf,
  updateCanvasControls,
  cancelCanvasInteraction: cancelActiveInteraction,
  renderDocumentReviewSurfaces,
  renderCanvasFinalSaveSummary,
  recordSavedAt: (value) => {
    lastSavedAt = value;
  },
  setStatus,
});
const {
  saveFinalOutput,
  closeFinalSaveDialog,
  renderFinalSaveConfirmation,
  currentFinalDocumentPath,
  updateWorkflowReadiness,
} = finalizationController;

const maskingRunController = createMaskingRunController({
  state,
  customRegionsEl,
  displayModeEl,
  inputPathEl,
  isPdfInput,
  isCustomRegionScope,
  getResultSourcePath,
  ensurePreviewWorkDir,
  collectMaskingOptions,
  clampPage,
  loadPdfDoc,
  loadResultPdf,
  renderCompare,
  setCompareMode,
  setStatus,
  setBaseMaskingProgress,
  setTextCompareContents,
  renderFinalState,
  renderDocumentReviewSurfaces,
  resetDerivedArtifacts: () => resetDerivedArtifacts("remask"),
  updateWorkflowReadiness,
  updateCanvasControls,
  runMaskingPipeline,
});
const { runMaskingForSelectedDocument } = maskingRunController;

const keywordController = createKeywordDialogController({
  customKeywordsEl,
  keywordDialogChipListEl,
  keywordDialogEl,
  btnOpenKeywordDialog,
  setModalVisible,
  setStatus,
  renderCanvasFinalSaveSummary,
  renderFinalSaveConfirmation,
  updateWorkflowReadiness,
});
const { keywordList, writeKeywordList, syncKeywordDialogChips, openKeywordDialog, closeKeywordDialog } = keywordController;

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

const appSettingsController = createAppSettingsController({
  state,
  settingsThemeInputs,
  profileEl,
  engineEl,
  displayModeEl,
  deidentificationPolicyEl,
  regionScopeEl,
  customRegionsEl,
  customKeywordsEl,
  optPdfRedactionEl,
  settingsExportMaskedTextEl,
  settingsOpenOutputAfterSaveEl,
  updateOutputDirectoryState,
  applyProfileDefaults,
  updateRegionScopeControls,
  updateMaskedTextOptionControls,
  syncKeywordDialogChips,
});
const { selectedTheme, applyTheme, collectSettings, applySettings } = appSettingsController;

function initialize() {
  applySettings(loadSettings());
  renderFinalState(null);
  renderBatchQueue();
  renderCompare();
  updateMeta();
  setTextCompareContents("", "");
  renderDocumentReviewSurfaces();
  setCompareMode("pdf");
  if (isStandaloneCanvasWindow) {
    // v4 P2: 독립 작업창(?mode=canvas)도 통합 "문서" 화면을 띄운다(구 canvas 패널 폐지).
    activateAppScreen("documents");
    void hydrateStandaloneCanvasWindow();
  } else {
    activateAppScreen("documents");
    setCanvasMode(true, { allowEmptyCanvas: true });
    setStatus("대기 중: PDF 열기 → 마스킹 실행 → 검토 → 최종 저장");
  }
}

return {
  state,
  get settingsSnapshot() {
    return settingsSnapshot;
  },
  set settingsSnapshot(snapshot: AppSettings | null) {
    settingsSnapshot = snapshot;
  },
  initialize,
  pickInputDocument,
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
  moveOrigPage,
  moveResultPage,
  saveFinalOutput,
  setCompareMode,
  applyProfileDefaults,
  updateRegionScopeControls,
  updateMaskedTextOptionControls,
  selectedTheme,
  applyTheme,
  activateSettingsTab,
  openSettingsScreen,
  rememberAuxReturnScreen,
  collectSettings,
  activateAppScreen,
  returnFromAuxScreen,
  setInspectorCollapsed,
  closeFinalSaveDialog,
  closeKeywordDialog,
  isAuxScreen,
  closeSettingsScreen,
  applySettings,
  renderFinalSaveConfirmation,
  renderCanvasFinalSaveSummary,
  keywordList,
  writeKeywordList,
  openKeywordDialog,
  syncKeywordDialogChips,
  openCanvasDesktopWindow,
  setCanvasMode,
  setCanvasToolsCollapsed,
  setCanvasPropertiesCollapsed,
  expandCanvasPanels,
  pickCanvasPdf,
  resetDocumentSession,
  prepareForDocumentReplacement,
  resolveDiscardConfirmation,
  setActiveCanvasTool,
  adjustZoom,
  deleteSelectedCanvasBox,
  convertCanvasSelectedBox,
  applyPendingManualBoxes,
  updateOriginalCompareVisibility,
  undoLastCanvasBox,
  clearDerivedArtifacts,
};

}
export type LegacyAppController = ReturnType<typeof createLegacyAppController>;
