import { Modal } from "./ui/Modal";
import { SegmentThumbnailStrip } from "./SegmentThumbnailStrip";
import { SymbolIcon } from "./ui/SymbolIcon";
import { classNames } from "./ui/classNames";
import { setActiveScreen, setInspectorCollapsed, useShellState } from "../state/shellStore";
import { appendKeywordValue, parseKeywordList } from "../features/keyword-dialog/keywordDialogController";
import {
  clearWorkspaceCanvasMount,
  registerWorkspaceCanvasMount,
  workspaceController,
} from "../features/canvas-workbench/workspaceRuntime";
import { useWorkspaceState } from "../state/workspaceStore";
import { beginSettingsDraft, useSettingsState } from "../state/settingsStore";
import { dashboardBlockedRestoreCount, dashboardMaskCounts, dashboardReviewSurfaceCounts } from "../dashboardSurfaceModels";
import type { DashboardReviewItem } from "../dashboardSurfaceModels";
import type { CanvasEditorTool } from "../features/manual-adjustment/manualAdjustmentController";
import type { ApplicationController } from "../app/applicationController";
import { useLayoutEffect, useRef } from "react";
import type { FocusEvent, KeyboardEvent, SyntheticEvent } from "react";

function closeDisclosure(details: HTMLDetailsElement, restoreFocus = false): void {
  details.open = false;
  if (restoreFocus) details.querySelector<HTMLElement>("summary")?.focus();
}

function handleDisclosureKeyDown(event: KeyboardEvent<HTMLDetailsElement>): void {
  const details = event.currentTarget;
  if (event.key === "Escape" && details.open) {
    event.preventDefault();
    event.stopPropagation();
    closeDisclosure(details, true);
    return;
  }
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const items = [...details.querySelectorAll<HTMLElement>('[role="menuitemradio"], [role="menuitemcheckbox"]')]
    .filter((item) => !item.matches(":disabled"));
  if (items.length === 0) return;
  event.preventDefault();
  details.open = true;
  const current = items.findIndex((item) => item === event.target || item.contains(event.target as Node));
  const next = event.key === "Home"
    ? 0
    : event.key === "End"
      ? items.length - 1
      : event.key === "ArrowUp"
        ? (current <= 0 ? items.length - 1 : current - 1)
        : (current + 1) % items.length;
  items[next]?.focus();
}

function handleToolSegmentKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button[data-canvas-tool]")]
    .filter((item) => !item.disabled);
  if (items.length === 0) return;
  event.preventDefault();
  const current = items.findIndex((item) => item === event.target || item.contains(event.target as Node));
  const next = event.key === "Home"
    ? 0
    : event.key === "End"
      ? items.length - 1
      : event.key === "ArrowLeft"
        ? (current <= 0 ? items.length - 1 : current - 1)
        : (current + 1) % items.length;
  items[next]?.focus();
}

function handleCompareTabKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
    .filter((tab) => !tab.disabled);
  if (tabs.length === 0) return;
  event.preventDefault();
  const current = tabs.findIndex((tab) => tab === event.target);
  const next = event.key === "Home"
    ? 0
    : event.key === "End"
      ? tabs.length - 1
      : event.key === "ArrowLeft"
        ? (current <= 0 ? tabs.length - 1 : current - 1)
        : (current + 1) % tabs.length;
  tabs[next]?.focus();
  tabs[next]?.click();
}

function handleDisclosureBlur(event: FocusEvent<HTMLDetailsElement>): void {
  const next = event.relatedTarget;
  if (next instanceof Node && event.currentTarget.contains(next)) return;
  closeDisclosure(event.currentTarget);
}

function syncAccordionExpanded(event: SyntheticEvent<HTMLDetailsElement>): void {
  const details = event.currentTarget;
  details.querySelector<HTMLElement>("summary")?.setAttribute("aria-expanded", String(details.open));
}

function focusableModalElements(element: HTMLElement): HTMLElement[] {
  return [...element.querySelectorAll<HTMLElement>(
    "button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])",
  )].filter((candidate) => !candidate.hasAttribute("hidden") && !candidate.closest("[hidden]"));
}

type ReviewAction = "mask" | "exclude" | "acknowledge" | "confirm_boundary" | "confirm_suggested_geometry" | "confirm_geometry" | "reanalyze";

function reviewActions(item: DashboardReviewItem): readonly { readonly action: ReviewAction; readonly label: string }[] {
  switch (item.kind) {
    case "name":
    case "institution": return [{ action: "mask", label: "마스킹 적용" }, { action: "exclude", label: "제외" }];
    case "acknowledge": return [{ action: "acknowledge", label: "확인" }];
    case "boundary": return [{ action: "confirm_boundary", label: "자동 경계 확인" }];
    case "region_geometry": return [
      { action: "confirm_suggested_geometry", label: "제안 영역 확정" },
      { action: "confirm_geometry", label: "영역 편집" },
    ];
    case "ocr": return [{ action: "reanalyze", label: "다시 분석" }];
  }
}

function navigateToScanManualPage(item: DashboardReviewItem): void {
  const controller = workspaceController();
  if (controller === null) return;
  controller.activateAppScreen("documents");
  controller.setCompareMode("pdf");
  controller.setCanvasMode(true);
  controller.setActiveCanvasTool("mask");
  void controller.goToReviewPage(item.pageStart);
}

function navigateToFirstMaskingLocation(page: number | null): void {
  if (page === null) return;
  void workspaceController()?.goToReviewPage(page);
}

function navigateToReviewLocation(item: DashboardReviewItem): void {
  void workspaceController()?.navigateToReviewLocation(item);
}

// v4 P2 (REDESIGN_V4_DARK §1): 문서 관제 화면과 수동 보정(캔버스) 화면을 하나의
// "문서" 화면으로 통합했다. 이 화면이 data-screen-panel="documents"를 가져가고
// 상단 탭 "문서"가 이걸 가리킨다. 빈 상태(히어로) → PDF 열면 마스킹 미리보기가
// 곧 화면 → 마스킹 실행 후 우측 슬림 레일(검토/저장)이 의미를 갖는다.
export function CanvasWorkspace() {
  const shell = useShellState();
  const { activePanel, inspectorCollapsed, reviewQueueActivationTick } = shell;
  const workspace = useWorkspaceState();
  const reviewCounts = dashboardReviewSurfaceCounts(workspace.report);
  const maskCounts = dashboardMaskCounts(workspace.report);
  const blockedRestoreCount = dashboardBlockedRestoreCount(workspace.report);
  const primaryReviewItems = workspace.reviewState.status === "valid"
    ? workspace.reviewState.items.filter((item) => item.kind !== "region_geometry")
    : [];
  const geometryReviewItems = workspace.reviewState.status === "valid"
    ? workspace.reviewState.items.filter((item) => item.kind === "region_geometry")
    : [];
  const settings = useSettingsState();
  const keywordEntryRef = useRef<HTMLInputElement>(null);
  const keywordOpenButtonRef = useRef<HTMLButtonElement>(null);
  const wasKeywordDialogOpen = useRef(false);
  const finalSaveConfirmButtonRef = useRef<HTMLButtonElement>(null);
  const finalSaveButtonRef = useRef<HTMLButtonElement>(null);
  const wasFinalSaveDialogOpen = useRef(false);
  const finalizationSuccessActionRef = useRef<HTMLButtonElement>(null);
  const wasFinalizationSuccessDialogOpen = useRef(false);
  const origCanvasRef = useRef<HTMLCanvasElement>(null);
  const resultCanvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const origWrapRef = useRef<HTMLDivElement>(null);
  const resultWrapRef = useRef<HTMLDivElement>(null);
  const pdfCompareViewRef = useRef<HTMLDivElement>(null);
  const textCompareViewRef = useRef<HTMLDivElement>(null);
  const extractedTextViewRef = useRef<HTMLPreElement>(null);
  const maskedTextViewRef = useRef<HTMLPreElement>(null);
  const finalStateCardRef = useRef<HTMLDetailsElement>(null);

  useLayoutEffect(() => {
    if (reviewQueueActivationTick === 0) return;
    setInspectorCollapsed(false);
    const finalStateCard = finalStateCardRef.current;
    if (finalStateCard !== null) finalStateCard.open = true;
    const frame = window.requestAnimationFrame(() => {
      finalStateCardRef.current?.querySelector<HTMLElement>("summary")?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [reviewQueueActivationTick]);

  useLayoutEffect(() => {
    const origCanvas = origCanvasRef.current;
    const resultCanvas = resultCanvasRef.current;
    const overlay = overlayRef.current;
    const origWrap = origWrapRef.current;
    const resultWrap = resultWrapRef.current;
    const pdfCompareView = pdfCompareViewRef.current;
    const textCompareView = textCompareViewRef.current;
    const extractedTextView = extractedTextViewRef.current;
    const maskedTextView = maskedTextViewRef.current;
    if (!origCanvas || !resultCanvas || !overlay || !origWrap || !resultWrap || !pdfCompareView || !textCompareView || !extractedTextView || !maskedTextView) return;
    const mount = { origCanvas, resultCanvas, overlay, origWrap, resultWrap, pdfCompareView, textCompareView, extractedTextView, maskedTextView };
    registerWorkspaceCanvasMount(mount);
    return () => clearWorkspaceCanvasMount(mount);
  }, []);

  useLayoutEffect(() => () => {
    workspaceController()?.setFocusedDetectionForReview(null);
  }, [workspace.reviewState]);

  const isKeywordDialogOpen = shell.modalVisibility["keyword-dialog"];
  useLayoutEffect(() => {
    if (!isKeywordDialogOpen) return;
    wasKeywordDialogOpen.current = true;
    const toolbar = document.querySelector<HTMLElement>(".dm-canvas__toolbar");
    const previousInert = toolbar?.inert ?? false;
    if (toolbar) toolbar.inert = true;
    keywordEntryRef.current?.focus();
    return () => {
      if (toolbar) toolbar.inert = previousInert;
    };
  }, [isKeywordDialogOpen]);

  useLayoutEffect(() => {
    if (isKeywordDialogOpen || !wasKeywordDialogOpen.current) return;
    wasKeywordDialogOpen.current = false;
    keywordOpenButtonRef.current?.focus();
  }, [isKeywordDialogOpen]);

  const isFinalSaveDialogOpen = workspace.finalSaveDialog.visible;
  useLayoutEffect(() => {
    if (isFinalSaveDialogOpen) {
      wasFinalSaveDialogOpen.current = true;
      finalSaveConfirmButtonRef.current?.focus();
      return;
    }
    if (!wasFinalSaveDialogOpen.current) return;
    wasFinalSaveDialogOpen.current = false;
    finalSaveButtonRef.current?.focus();
  }, [isFinalSaveDialogOpen]);

  const isFinalizationSuccessDialogOpen = workspace.finalizationSuccessDialog.visible;
  useLayoutEffect(() => {
    if (isFinalizationSuccessDialogOpen) {
      wasFinalizationSuccessDialogOpen.current = true;
      finalizationSuccessActionRef.current?.focus();
      return;
    }
    if (!wasFinalizationSuccessDialogOpen.current) return;
    wasFinalizationSuccessDialogOpen.current = false;
    finalSaveButtonRef.current?.focus();
  }, [isFinalizationSuccessDialogOpen]);

  const resolveReview = (item: DashboardReviewItem, action: ReviewAction, button: HTMLButtonElement) => {
    void workspaceController()?.resolveReviewFromRail(item.reviewId, action, button);
  };
  const focusReviewDetection = (reviewId: string | null): void => {
    workspaceController()?.setFocusedDetectionForReview(reviewId);
  };
  const clearReviewDetectionOnBlur = (event: FocusEvent<HTMLDivElement>): void => {
    const next = event.relatedTarget;
    if (next instanceof Node && event.currentTarget.contains(next)) return;
    focusReviewDetection(null);
  };
  const withWorkspace = (action: (controller: ApplicationController) => void): void => {
    const controller = workspaceController();
    if (controller !== null) action(controller);
  };
  const setCanvasTool = (tool: CanvasEditorTool): void => withWorkspace((controller) => controller.setActiveCanvasTool(tool));
  const keywordList = parseKeywordList(settings.settings.customKeywords);
  const writeKeywords = (nextValue: string): void => withWorkspace((controller) => controller.writeKeywordList(parseKeywordList(nextValue)));
  const appendKeywordEntry = (): void => {
    const input = keywordEntryRef.current;
    if (input === null) return;
    const nextValue = appendKeywordValue(settings.settings.customKeywords, input.value);
    if (nextValue === settings.settings.customKeywords) return;
    writeKeywords(nextValue);
    input.value = "";
    input.focus();
  };
  const handleKeywordEntryKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    appendKeywordEntry();
  };
  const handleKeywordDialogKeyDown = (event: KeyboardEvent<HTMLElement>): void => {
    if (event.key === "Escape") {
      event.preventDefault();
      withWorkspace((controller) => controller.closeKeywordDialog());
      return;
    }
    if (event.key !== "Tab") return;
    const targets = focusableModalElements(event.currentTarget);
    if (targets.length === 0) return;
    const first = event.currentTarget.querySelector<HTMLElement>("[data-modal-autofocus='true']") ?? targets[0];
    const last = targets[targets.length - 1];
    const active = document.activeElement;
    if (!event.shiftKey && active === last) {
      event.preventDefault();
      first?.focus();
    } else if (event.shiftKey && active === first) {
      event.preventDefault();
      last?.focus();
    }
  };
  const handleFinalizationSuccessDialogKeyDown = (event: KeyboardEvent<HTMLElement>): void => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    withWorkspace((controller) => controller.closeFinalizationSuccess());
  };

  return (
    <section id="canvas-workspace-screen" className={classNames("dm-canvas", activePanel === "documents" && "is-active", inspectorCollapsed && "is-inspector-collapsed")} data-screen-panel="documents" data-owner="react" aria-label="문서 작업공간">
      <section id="mask-canvas-window" className="dm-canvas__window" aria-label="문서 마스킹 작업">

        {/* ── 상단 도구 바: 보정 도구 · 키워드 · 보기 · 반영/저장 ── */}
          <div className="dm-canvas__toolbar" aria-label="문서 도구">
            <div className="dm-canvas__tool-group dm-canvas__tool-group--edit" aria-label="보정 도구">
              <div
                id="canvas-tool-menu"
                className="dm-seg dm-canvas__palette dm-canvas__tool-segment canvas-editor-palette"
                role="toolbar"
                aria-label="보정 도구"
                aria-orientation="horizontal"
                onKeyDown={handleToolSegmentKeyDown}
              >
                <button id="btn-canvas-tool-mask" className="tool-button is-active" type="button" data-canvas-tool="mask" aria-label="마스킹" aria-pressed="true" title="가릴 영역을 드래그하면 검은 박스로 덮습니다." onClick={() => setCanvasTool("mask")}><SymbolIcon name="draw" /><span className="dm-visually-hidden">마스킹</span></button>
                <button id="btn-canvas-tool-restore" className="tool-button" type="button" data-canvas-tool="restore" aria-label="복원" aria-pressed="false" title="가려진 영역을 드래그하면 다시 보이게 되돌립니다." onClick={() => setCanvasTool("restore")}><SymbolIcon name="ink_eraser" /><span className="dm-visually-hidden">복원</span></button>
                <button id="btn-canvas-tool-select" className="tool-button" type="button" data-canvas-tool="select" aria-label="선택" aria-pressed="false" title="박스를 클릭해 유형을 바꾸거나 삭제합니다." onClick={() => setCanvasTool("select")}><SymbolIcon name="ads_click" /><span className="dm-visually-hidden">선택</span></button>
                <button id="btn-canvas-tool-delete" className="tool-button" type="button" data-canvas-tool="delete" aria-pressed="false" title="클릭한 박스를 바로 지웁니다." onClick={() => setCanvasTool("delete")}><SymbolIcon name="delete" /><span className="dm-visually-hidden">삭제</span></button>
                <button id="btn-canvas-tool-pan" className="tool-button" type="button" data-canvas-tool="pan" aria-label="이동" aria-pressed="false" title="드래그해서 문서 보기를 옮깁니다." onClick={() => setCanvasTool("pan")}><SymbolIcon name="open_with" /><span className="dm-visually-hidden">이동</span></button>
                <span id="canvas-active-tool-label" className="dm-canvas__tool-label" aria-live="polite">마스킹</span>
              </div>
          </div>

          <div className="dm-canvas__tool-group dm-canvas__tool-group--keyword" aria-label="키워드">
            <button id="btn-open-keyword-dialog" ref={keywordOpenButtonRef} className="dm-btn" type="button" onClick={() => withWorkspace((controller) => controller.openKeywordDialog())}><SymbolIcon name="find_replace" /><span>키워드 관리</span></button>
          </div>

          <div className="dm-canvas__tool-group dm-canvas__tool-group--view" aria-label="보기">
            <div className="dm-canvas__zoom">
              <button id="btn-canvas-zoom-out" className="dm-btn dm-btn--ghost dm-icon-btn" type="button" aria-label="축소" onClick={() => withWorkspace((controller) => void controller.adjustZoom(-1))}><SymbolIcon name="zoom_out" /></button>
              <span id="zoom-info" className="dm-canvas__zoom-value">120%</span>
              <button id="btn-canvas-zoom-in" className="dm-btn dm-btn--ghost dm-icon-btn" type="button" aria-label="확대" onClick={() => withWorkspace((controller) => void controller.adjustZoom(1))}><SymbolIcon name="zoom_in" /></button>
            </div>
            <details
              id="canvas-view-menu"
              className="dm-canvas__disclosure"
              onKeyDown={handleDisclosureKeyDown}
              onBlur={handleDisclosureBlur}
            >
              <summary id="canvas-view-menu-trigger" className="dm-btn dm-btn--ghost dm-canvas__disclosure-trigger" aria-haspopup="menu">
                <span>보기</span>
                <SymbolIcon name="arrow_drop_down" aria-hidden="true" />
              </summary>
              <div className="dm-canvas__disclosure-panel dm-canvas__view-menu" role="menu">
                <label className="dm-canvas__view-toggle"><input id="toggle-original-compare" type="checkbox" role="menuitemcheckbox" aria-checked="false" onChange={(event) => { event.currentTarget.setAttribute("aria-checked", String(event.currentTarget.checked)); withWorkspace((controller) => controller.updateOriginalCompareVisibility()); }} /><span>원문 대조</span></label>
              </div>
            </details>
          </div>


          {/* 배치(여러 PDF)는 보조 액션 — 접힘 패널로 수납. 선택·실행은 상단 바(헤더)와 공유. */}
          <details className="dm-canvas__batch dm-canvas__tool-group" aria-label="여러 문서 일괄 처리" hidden>
            <summary className="dm-canvas__batch-summary">
              <SymbolIcon name="library_add" />
              <span>여러 PDF</span>
              <span id="batch-summary" className="dm-badge dm-badge--muted">0개</span>
            </summary>
            <div className="dm-canvas__batch-panel">
              <div id="batch-queue" className="dm-canvas__queue">
                <div className="batch-empty">큐에 문서가 없습니다.</div>
              </div>
              <button id="btn-run-batch" className="dm-btn dm-btn--primary dm-canvas__batch-run" type="button" hidden>
                <SymbolIcon name="playlist_play" />
                <span>대기 N개 모두 마스킹</span>
              </button>
            </div>
          </details>

          {/* 작업 완결 순서: 박스 그리기 → 수동 보정 반영 → 최종 저장 */}
          <div className="dm-canvas__tool-group dm-canvas__tool-group--commit dm-canvas__commit" aria-label="반영·저장">
            <button id="btn-canvas-undo" className="dm-btn dm-btn--ghost dm-canvas__commit-step" type="button" onClick={() => withWorkspace((controller) => controller.undoLastCanvasBox())}><SymbolIcon name="sync" /><span>실행 취소</span></button>
            <button id="btn-canvas-clear" className="dm-btn dm-btn--ghost dm-canvas__commit-step" type="button" onClick={() => withWorkspace((controller) => void controller.clearDerivedArtifacts())}><SymbolIcon name="delete" /><span>전체 초기화</span></button>
            <button id="btn-canvas-apply" className="dm-btn dm-canvas__commit-step" type="button" aria-describedby="canvas-tool-readiness" onClick={() => withWorkspace((controller) => void controller.applyPendingManualBoxes("수동마스킹실행"))}><SymbolIcon name="check_circle" /><span>수동 보정 반영</span></button>
            <SymbolIcon name="chevron_right" className="dm-canvas__commit-arrow" aria-hidden="true" />
            <button id="btn-new-document" className="dm-btn dm-btn--primary dm-canvas__commit-step is-hidden" type="button" title="저장된 파일은 유지하고 빈 작업 화면으로 돌아갑니다."><SymbolIcon name="note_add" /><span>저장 완료 · 새 작업 시작</span></button>
          </div>
        </div>

        {/* ── 상태 스트립: 선택한 도구 한 줄 안내 + 게이트 사유 ── */}
        <div className="dm-canvas__statusbar">
          <p className="dm-canvas__guide" role="status" aria-live="polite">
            <span className="dm-canvas__guide-dot" aria-hidden="true"></span>
            <span data-guide-for="mask">드래그해 가릴 영역을 덮습니다.</span>
            <span data-guide-for="restore">드래그해 가린 영역을 되돌립니다.</span>
            <span data-guide-for="select">박스를 클릭해 유형 변경·삭제합니다.</span>
            <span data-guide-for="delete">클릭한 박스를 지웁니다.</span>
            <span data-guide-for="pan">드래그해 문서 보기를 옮깁니다.</span>
          </p>
          <p id="canvas-tool-readiness" className="dm-canvas__readiness" aria-live="polite">PDF를 불러오고 기본 마스킹 미리보기를 만든 뒤 편집할 수 있습니다.</p>
        </div>

        {/* ── 본문: 미리보기 스테이지 + 우측 검토/저장 레일 ── */}
        <div className="dm-canvas__body">
          <section className="dm-canvas__stage" aria-label="PDF 미리보기 스테이지">
            {/* 빈 상태 — 1-2-3 작업 흐름과 문서 열기 CTA. PDF가 렌더되면(.has-rendered-pdf) 숨겨진다. */}
            <div className="dm-canvas__hero" aria-label="시작 안내">
              <div className="dm-canvas__flow" aria-label="PDF 마스킹 작업 순서">
                <div className="dm-canvas__flow-step is-current">
                  <span className="dm-canvas__flow-number">1</span>
                  <strong>PDF 열기</strong>
                </div>
                <span className="dm-canvas__flow-line" aria-hidden="true"></span>
                <div className="dm-canvas__flow-step">
                  <span className="dm-canvas__flow-number">2</span>
                  <strong>자동 마스킹</strong>
                </div>
                <span className="dm-canvas__flow-line" aria-hidden="true"></span>
                <div className="dm-canvas__flow-step">
                  <span className="dm-canvas__flow-number">3</span>
                  <strong>수동 보정 및 저장</strong>
                </div>
              </div>
              <strong className="dm-canvas__hero-title">PDF를 불러와 마스킹을 시작하세요</strong>
              <span className="dm-canvas__hero-desc">공문 PDF를 열면 개인정보를 가린 미리보기가 만들어지고, 이 화면에서 직접 보정할 수 있습니다.</span>
              <div className="dm-canvas__hero-actions">
                <button id="btn-canvas-load-pdf" className="dm-btn dm-btn--primary dm-canvas__hero-cta" type="button" onClick={() => withWorkspace((controller) => void controller.pickCanvasPdf())}><SymbolIcon name="folder_open" /><span>PDF 열기</span></button>
                <label className="dm-canvas__selected-file">
                  <span>선택한 PDF</span>
                  <input id="input-path" className="dm-input" type="text" readOnly aria-label="선택한 PDF 경로" />
                </label>
              </div>
            </div>

            <div className="dm-canvas__compare">
              <div className="dm-canvas__compare-grid" id="pdf-compare-view" ref={pdfCompareViewRef} role="tabpanel" aria-labelledby="compare-mode-pdf">
                <div id="original-compare-panel" className="dm-canvas__viewer is-hidden">
                  <div className="dm-canvas__viewer-head">
                    <span className="dm-canvas__viewer-name"><span className="dm-dot dm-dot--orig" aria-hidden="true"></span>원문</span>
                    <span className="dm-canvas__viewer-meta" id="viewer-meta-orig">페이지 0/0</span>
                  </div>
                  <div className="dm-canvas__scroll">
                    <div id="canvas-wrap-orig" className="dm-canvas__wrap" ref={origWrapRef}>
                      <canvas id="pdf-canvas-orig" ref={origCanvasRef}></canvas>
                    </div>
                  </div>
                </div>
                <div id="masked-preview-panel" className="dm-canvas__viewer">
                  <div className="dm-canvas__viewer-head">
                    <span className="dm-canvas__viewer-name"><span className="dm-dot dm-dot--result" aria-hidden="true"></span>마스킹 미리보기</span>
                    <span className="dm-canvas__viewer-meta" id="viewer-meta-result">페이지 0/0</span>
                  </div>
                  <div className="dm-canvas__scroll">
                    <div id="canvas-wrap-result" className="dm-canvas__wrap" ref={resultWrapRef}>
                      <div className="dm-canvas__placeholder dm-empty-state" aria-label="PDF 미리보기 대기">
                        <SymbolIcon name="draw" />
                        <strong>PDF 미리보기 대기</strong>
                        <span>문서를 열고 기본 마스킹을 실행하면 이 영역에 실제 PDF 미리보기가 표시됩니다.</span>
                      </div>
                      <canvas id="pdf-canvas-result" ref={resultCanvasRef}></canvas>
                      <canvas id="overlay-canvas-result" ref={overlayRef}></canvas>
                    </div>
                  </div>
                </div>
              </div>

              <div className="dm-canvas__compare-grid dm-canvas__compare-grid--text" id="text-compare-view" ref={textCompareViewRef} role="tabpanel" aria-labelledby="compare-mode-text" hidden>
                <div className="dm-canvas__viewer">
                  <div className="dm-canvas__viewer-head"><span className="dm-canvas__viewer-name">추출 텍스트</span><span className="dm-canvas__viewer-meta">추출 결과</span></div>
                  <pre id="extracted-text-view" className="dm-canvas__text" ref={extractedTextViewRef}>마스킹 실행 후 추출 텍스트가 표시됩니다.</pre>
                </div>
                <div className="dm-canvas__viewer">
                  <div className="dm-canvas__viewer-head"><span className="dm-canvas__viewer-name">마스킹 텍스트</span><span className="dm-canvas__viewer-meta">마스킹 결과</span></div>
                  <pre id="masked-text-view" className="dm-canvas__text" ref={maskedTextViewRef}>마스킹 실행 후 마스킹 텍스트가 표시됩니다.</pre>
                </div>
              </div>
            </div>

            <SegmentThumbnailStrip />

            {workspace.firstMaskingPage !== null && (
              <div
                id="first-masking-location-cta"
                className="dm-canvas__first-masking-cta"
                data-target-page={workspace.firstMaskingPage + 1}
                role="status"
                aria-live="polite"
              >
                <span>현재 페이지를 유지했습니다. 첫 마스킹 위치를 확인할 수 있습니다.</span>
                <button
                  id="btn-go-first-masking-location"
                  className="dm-btn dm-btn--compact"
                  type="button"
                  onClick={() => navigateToFirstMaskingLocation(workspace.firstMaskingPage)}
                >
                  첫 마스킹 위치로 이동
                </button>
              </div>
            )}

            <div className="dm-canvas__viewbar" aria-label="문서 보기 도구">
              <div className="dm-canvas__pager">
                <button id="btn-prev-orig" className="dm-btn dm-btn--ghost dm-icon-btn" type="button" aria-label="이전 페이지"><SymbolIcon name="chevron_left" /></button>
                <span className="dm-canvas__pager-label">0 / 0</span>
                <button id="btn-next-orig" className="dm-btn dm-btn--ghost dm-icon-btn" type="button" aria-label="다음 페이지"><SymbolIcon name="chevron_right" /></button>
              </div>
              <div className="dm-seg dm-canvas__compare-tabs" role="tablist" aria-label="문서 보기" onKeyDown={handleCompareTabKeyDown}>
                <button id="compare-mode-pdf" className="is-active" type="button" role="tab" aria-controls="pdf-compare-view" aria-selected="true" tabIndex={0}>PDF 보기</button>
                <button id="compare-mode-text" type="button" role="tab" aria-controls="text-compare-view" aria-selected="false" tabIndex={-1}>텍스트 보기</button>
              </div>
              <label className="dm-canvas__sync"><input id="sync-pages" type="checkbox" defaultChecked /><span>페이지 동기</span></label>
            </div>
          </section>

          {/* ── 우측 슬림 레일: 서버가 확인한 경계, OCR, 영역 좌표, 이름, 기관,
               공통 전용 확인의 여섯 검토 종류를 안전한 사유·건수·페이지 정보로
               표시한다. 현재 서버 세션의 미해결 항목은 최종 저장을 차단한다. ── */}
          <aside className={classNames("dm-canvas__inspector", "dm-inspector", inspectorCollapsed && "is-collapsed")} id="side-panel" aria-label="검출 항목">
            <header className="dm-inspector__bar">
              <span className="dm-inspector__bar-title">검출 항목<span className="dm-visually-hidden">검토·저장</span></span>
              <span id="review-total-count" className="dm-inspector__total" aria-label="전체 검출 항목">{reviewCounts.total}건</span>
              <button
                id="btn-toggle-inspector"
                className="dm-btn dm-btn--ghost dm-inspector__collapse"
                type="button"
                aria-expanded={!inspectorCollapsed}
                aria-controls="side-panel"
                aria-label={inspectorCollapsed ? "검토 열기" : "패널 접기"}
                onClick={() => setInspectorCollapsed(!inspectorCollapsed)}
              >
                <SymbolIcon name="dock_to_right" />
                <span className="dm-visually-hidden">{inspectorCollapsed ? "검토 열기" : "패널 접기"}</span>
              </button>
            </header>

            <div className="dm-inspector__filters" aria-label="검출 항목 현황">
              <span className="is-active">전체 <b id="review-filter-all-count">{reviewCounts.total}</b></span>
              <span>미확인 <b id="review-filter-pending-count">{reviewCounts.pending}</b></span>
              <span>확인 <b id="review-filter-resolved-count">{reviewCounts.resolved}</b></span>
            </div>

            <div className="dm-inspector__scroll">
              <div id="inspector-empty-guide" className="dm-inspector__empty-guide">
                <SymbolIcon name="fact_check" />
                <strong>문서를 열면 검토 항목이 여기에 표시됩니다</strong>
                <span>마스킹 실행 후 필요한 내용만 단계별로 펼쳐 보세요.</span>
              </div>

              <details ref={finalStateCardRef} className="dm-inspector__card dm-inspector__accordion dm-detect" id="final-state-card" data-state="idle" open onToggle={syncAccordionExpanded}>
                <summary className="dm-inspector__accordion-summary" aria-expanded="true" aria-controls="inspector-review-content">
                  <span className="dm-section-label" id="obsidian-detection-heading">검출 항목 목록<span className="dm-visually-hidden">검토 필요 항목</span></span>
                  <SymbolIcon name="arrow_drop_down" aria-hidden="true" />
                </summary>
                <div id="inspector-review-content" className="dm-inspector__accordion-content">
                  <div id="review-summary-banner" className="dm-review-summary" role="status" aria-live="polite">
                    자동 {maskCounts.automaticMaskCount}건 · 수동 {maskCounts.manualMaskCount}건(저장 시 적용) · 검토 필요 {reviewCounts.pending}건
                  </div>
                  <p id="review-summary-explanation" className="dm-review-summary__explanation" hidden={workspace.reviewState.status !== "valid" || reviewCounts.autoMasked !== 0 || maskCounts.manualMaskCount !== 0 || maskCounts.manualRestoreCount !== 0}>
                    확실한 항목이 없어 자동으로 가리지 않았습니다. 아래 항목을 검토해 확정하세요.
                  </p>
                  <section id="obsidian-detection-list" className="dm-detect__list" aria-label="검출 항목 목록">
                    {workspace.reviewState.status === "valid" && primaryReviewItems.map((item) => (
                      <div className="dm-detect__item" data-review-id={item.reviewId} data-state={item.status} key={item.reviewId} onMouseEnter={() => focusReviewDetection(item.reviewId)} onMouseLeave={() => focusReviewDetection(null)} onFocus={() => focusReviewDetection(item.reviewId)} onBlur={clearReviewDetectionOnBlur}>
                        <i className={item.status === "resolved" ? "dot-primary" : "dot-warning"}></i>
                        <strong>{item.locationOrdinal !== null && <span className="dm-detect__ordinal" aria-label={`${item.locationOrdinal}번 위치`}>{item.locationOrdinal}</span>}{item.kindLabel}</strong>
                        <em>{item.pageLabel}쪽 · {item.status === "pending" ? "검토 대기" : "해결됨"}</em>
                        <span className="dm-detect__detail" id={`review-detail-${item.reviewId}`}>{item.detail}</span>
                        <button className="dm-btn dm-btn--compact dm-detect__action dm-detect__location-action" type="button" data-review-location={item.reviewId} aria-describedby={`review-detail-${item.reviewId}`} aria-label={`${item.locationOrdinal === null ? "" : `${item.locationOrdinal}번 `}검토 위치 보기`} onClick={() => navigateToReviewLocation(item)}>위치 보기</button>
                        {item.scannedGeometryUnavailable && <button className="dm-btn dm-btn--compact dm-detect__action dm-detect__scan-page-action" type="button" aria-describedby={`review-detail-${item.reviewId}`} aria-label={`스캔 ${item.pageLabel}쪽으로 이동해 수동 마스킹`} onClick={() => navigateToScanManualPage(item)}>스캔 페이지로 이동</button>}
                        {item.status === "pending" && item.kind === "region_geometry" && workspace.geometryDraftReviewId === item.reviewId && <p id={`review-guidance-${item.reviewId}`} className="dm-detect__geometry-guidance">제안 영역이 맞으면 바로 확정하거나, 표시된 영역을 모두 덮도록 드래그한 뒤 [영역 확정]을 누르세요.</p>}
                        {workspace.reviewFailureById.get(item.reviewId) && <span className="dm-detect__feedback" data-state="failure" role="alert">처리 실패 ({workspace.reviewFailureById.get(item.reviewId)})</span>}
                        {item.status === "pending" ? <div className="dm-detect__actions">{reviewActions(item).map(({ action, label }) => <button className={action === "mask" || action === "confirm_suggested_geometry" ? "dm-btn dm-btn--compact dm-btn--primary dm-detect__action dm-detect__action--primary" : "dm-btn dm-btn--compact dm-detect__action"} data-review-id={item.reviewId} data-review-action={action} aria-describedby={action === "confirm_geometry" && workspace.geometryDraftReviewId === item.reviewId ? `review-detail-${item.reviewId} review-guidance-${item.reviewId}` : `review-detail-${item.reviewId}`} key={action} onClick={(event) => resolveReview(item, action, event.currentTarget)} type="button">{action === "confirm_geometry" && workspace.geometryDraftReviewId === item.reviewId ? "영역 확정" : label}</button>)}</div> : <span className="dm-detect__feedback" data-state="resolved">이 항목을 반영했습니다</span>}
                      </div>
                    ))}
                    {workspace.reviewState.status !== "valid" && <div data-state="blocking"><i className="dot-danger"></i><strong>{workspace.reviewState.status === "invalid" ? "검토 정보 계약 오류" : "검토 정보 확인 필요"}</strong><em>저장 차단</em><span>마스킹 실행 후 서버 검토 정보를 확인합니다.</span></div>}
                  </section>
                  {geometryReviewItems.length > 0 && <details id="advanced-geometry-reviews" className="dm-card dm-inspector__accordion" aria-label="고급 결재란 영역 표시" onToggle={syncAccordionExpanded}>
                    <summary className="dm-inspector__accordion-summary" aria-expanded="false" aria-controls="advanced-geometry-reviews-content">
                      <span className="dm-card__title">고급: 결재란 영역 표시</span>
                      <SymbolIcon name="arrow_drop_down" aria-hidden="true" />
                    </summary>
                    <div id="advanced-geometry-reviews-content" className="dm-inspector__accordion-content">
                      <p className="dm-review-summary__explanation">저장 전에 자동확인하지 못한 영역의 위치만 표시합니다.</p>
                      {geometryReviewItems.map((item) => <button className="dm-btn dm-btn--compact dm-detect__action" data-review-id={item.reviewId} key={item.reviewId} onMouseEnter={() => focusReviewDetection(item.reviewId)} onMouseLeave={() => focusReviewDetection(null)} onFocus={() => focusReviewDetection(item.reviewId)} onBlur={() => focusReviewDetection(null)} onClick={() => navigateToScanManualPage(item)} type="button">{item.pageLabel}쪽 결재란 표시</button>)}
                    </div>
                  </details>}
                  <footer className="dm-detect__state">
                    <p id="review-progress-summary">{workspace.reviewState.status === "valid" ? `${reviewCounts.total}건 중 ${reviewCounts.resolved}건 확인 완료` : "0건 중 0건 확인 완료"}</p>
                    <strong id="final-state-title">대기 중</strong>
                    <b id="final-state-detail">문서를 열고 마스킹을 실행하세요.</b>
                  </footer>
                </div>
              </details>

              <details id="canvas-box-accordion" className="dm-card dm-canvas__panel dm-inspector__accordion" aria-label="현재 페이지 박스" onToggle={syncAccordionExpanded}>
                <summary className="dm-inspector__accordion-summary" aria-expanded="false" aria-controls="canvas-box-accordion-content">
                  <span className="dm-card__title">현재 페이지 박스</span>
                  <SymbolIcon name="arrow_drop_down" aria-hidden="true" />
                </summary>
                <div id="canvas-box-accordion-content" className="dm-inspector__accordion-content">
                  <div id="canvas-box-properties" className="dm-canvas__props is-empty">
                    <dl className="dm-canvas__prop-grid dm-visually-hidden">
                      <div><dt>페이지</dt><dd id="canvas-box-property-page">-</dd></div>
                      <div><dt>유형</dt><dd id="canvas-box-property-type">-</dd></div>
                      {/* 좌표·크기(px)는 사용자가 알 필요 없는 내부 수치 — DOM 유지, 화면에서 숨김 */}
                      <div><dt>좌표</dt><dd id="canvas-box-property-coordinates">-</dd></div>
                      <div><dt>크기</dt><dd id="canvas-box-property-size">-</dd></div>
                    </dl>
                    <div className="dm-canvas__prop-actions">
                      <button id="btn-canvas-box-convert-mask" className="dm-btn" type="button" onClick={() => withWorkspace((controller) => controller.convertCanvasSelectedBox("mask"))}>마스킹으로 전환</button>
                      <button id="btn-canvas-box-convert-restore" className="dm-btn" type="button" onClick={() => withWorkspace((controller) => controller.convertCanvasSelectedBox("restore"))}>복원으로 전환</button>
                      <button id="btn-canvas-box-delete" className="dm-btn dm-btn--danger" type="button" onClick={() => withWorkspace((controller) => controller.deleteSelectedCanvasBox())}>선택 삭제</button>
                    </div>
                  </div>
                  <div id="canvas-box-list" className="dm-canvas__box-list">
                    <div className="canvas-box-empty dm-empty-state">현재 페이지에 박스가 없습니다.</div>
                  </div>
                </div>
              </details>

              <details id="save-summary-accordion" className="dm-inspector__card dm-savesummary dm-inspector__accordion" aria-label="저장 요약" open onToggle={syncAccordionExpanded}>
                <summary className="dm-inspector__accordion-summary" aria-expanded="true" aria-controls="save-summary-accordion-content">
                  <span className="dm-section-label">저장 요약</span>
                  <SymbolIcon name="arrow_drop_down" aria-hidden="true" />
                </summary>
                <div id="save-summary-accordion-content" className="dm-savesummary__grid dm-inspector__accordion-content">
                  <div className="dm-kv"><span>유효 마스킹</span><strong id="review-summary-mask-count">{workspace.saveSummary.maskCount}</strong></div>
                  <div className="dm-kv"><span>자동 마스킹</span><strong id="review-summary-automatic-mask-count">{workspace.saveSummary.automaticMaskCount}</strong></div>
                  <div className="dm-kv"><span>수동 마스킹</span><strong id="review-summary-manual-mask-count">{workspace.saveSummary.manualMaskCount}</strong></div>
                  <div className="dm-kv"><span>수동 복원</span><strong id="review-summary-manual-restore-count">{workspace.saveSummary.manualRestoreCount}</strong></div>
                  <div className="dm-kv"><span>복원 현황</span><strong id="review-summary-restore-count">{workspace.saveSummary.restoreCount}</strong></div>
                  <div className="dm-kv"><span>유효 마스킹 수</span><strong id="review-summary-effective-mask-count">{workspace.saveSummary.effectiveMaskCount}</strong></div>
                  <div className="dm-kv"><span>키워드</span><strong id="review-summary-keyword-count">{workspace.saveSummary.keywordCount}</strong></div>
                  <div className="dm-kv"><span>결과 파일</span><strong id="review-summary-output-file">{workspace.saveSummary.outputFile}</strong></div>
                  <div className="dm-kv"><span>PDF 가림</span><strong id="review-summary-pdf-policy">{workspace.saveSummary.pdfPolicy}</strong></div>
                  <div className="dm-kv"><span>TXT 산출</span><strong id="review-summary-txt-policy">{workspace.saveSummary.txtPolicy}</strong></div>
                  <p id="manual-staging-status" className="dm-savesummary__staging-status" data-state={blockedRestoreCount > 0 ? "blocked" : "staged"} hidden={maskCounts.manualMaskCount === 0 && maskCounts.manualRestoreCount === 0}>
                    {blockedRestoreCount > 0
                      ? `수동 복원 ${blockedRestoreCount}건이 확정 마스크와 겹쳐 저장이 차단됩니다.`
                      : `수동 보정 ${maskCounts.manualMaskCount + maskCounts.manualRestoreCount}건이 저장 시 적용됩니다.`}
                  </p>
                </div>
              </details>

              {/* 저장 게이트 사유 한 줄 — 스크롤 영역 하단에 두어 고정 액션 푸터가
                  저장 버튼까지 잘리지 않도록 한다. */}
              <p id="final-save-readiness" className="dm-savegate__readiness" data-state="pending" aria-live="polite">
                현재 서버 검토 세션의 모든 항목을 해결한 뒤 최종 저장할 수 있습니다.
              </p>
            </div>

            <footer className="dm-inspector__actions">
              <button id="btn-save" ref={finalSaveButtonRef} className="dm-btn dm-btn--primary" type="button" aria-describedby="final-save-readiness" title="검토가 완료된 현재 마스킹 PDF를 파일로 저장합니다." onClick={() => withWorkspace((controller) => void controller.saveFinalOutput())}>
                <SymbolIcon name="save" />
                최종 저장
              </button>
            </footer>
          </aside>
          <button id="btn-open-canvas-properties-tab" className="dm-canvas__props-tab" type="button" onClick={() => withWorkspace((controller) => controller.setCanvasPropertiesCollapsed(false))}>검토 패널 열기</button>
        </div>


        <Modal
          id="keyword-dialog"
          titleId="keyword-dialog-title"
          title="키워드 마스킹"
          description="쉼표 또는 줄바꿈으로 구분해 가릴 키워드를 추가합니다. 입력한 내용은 결과 파일이나 로그에 남기지 않습니다."
          closeButtonId="btn-close-keyword-dialog"
          owner="react"
          onClose={() => withWorkspace((controller) => controller.closeKeywordDialog())}
          onKeyDown={handleKeywordDialogKeyDown}
          footer={(
            <>
              <button id="btn-keyword-dialog-cancel" className="dm-btn dm-btn--ghost" type="button" onClick={() => withWorkspace((controller) => controller.closeKeywordDialog())}>취소</button>
              <button id="btn-keyword-dialog-apply" className="dm-btn dm-btn--primary" type="button" onClick={() => withWorkspace((controller) => controller.applyKeywords())}>키워드 반영 후 다시 탐지</button>
            </>
          )}
        >
          <div className="dm-keyword-dialog">
            <section className="dm-keyword-dialog__section">
              <div className="dm-section-label">키워드 입력</div>
              <div className="dm-keyword-entry">
                <input
                  id="keyword-entry-input"
                  ref={keywordEntryRef}
                  className="dm-input"
                  type="text"
                  data-modal-autofocus="true"
                  placeholder="가릴 키워드를 입력하세요"
                  onKeyDown={handleKeywordEntryKeyDown}
                />
                <button className="dm-btn" type="button" onClick={appendKeywordEntry}>추가</button>
              </div>
              <div className="dm-field" hidden>
                <label htmlFor="custom-keywords">등록 원본</label>
                <textarea id="custom-keywords" className="dm-input dm-keyword-dialog__textarea" rows={2} placeholder="예: 이름 또는 기관명, 프로젝트 코드" value={settings.settings.customKeywords} onChange={(event) => writeKeywords(event.currentTarget.value)} />
              </div>
              <div className="dm-section-label">등록 키워드</div>
              <p id="keyword-dialog-count" className="dm-keyword-dialog__count">추가된 키워드 {keywordList.length}</p>
              <div id="keyword-dialog-chip-list" className="keyword-chip-preview dm-keyword-dialog__chips">
                {keywordList.length === 0 ? <span>등록된 키워드 없음</span> : keywordList.map((keyword) => <span className="dm-keyword-chip" key={keyword}>{keyword}<button type="button" className="dm-keyword-chip__remove" aria-label={`${keyword} 삭제`} onClick={() => withWorkspace((controller) => controller.writeKeywordList(keywordList.filter((value) => value !== keyword)))}>×</button></span>)}
              </div>
              <button id="btn-keyword-policy" className="dm-keyword-dialog__policy" type="button" data-screen-target="masking-settings" onClick={() => { withWorkspace((controller) => { controller.closeKeywordDialog(); controller.rememberAuxReturnScreen(); }); beginSettingsDraft(); setActiveScreen("masking-settings"); }}>탐지 기준과 키워드 정책 보기</button>
            </section>
          </div>
        </Modal>

        <Modal
          id="new-document-dialog"
          titleId="new-document-dialog-title"
          title="진행 중인 작업이 있습니다"
          description="저장하지 않고 새 작업을 시작하시겠습니까? 기존 작업 내역은 사라집니다."
          closeButtonId="btn-close-new-document-dialog"
          footer={(
            <>
              <button id="btn-cancel-new-document" className="dm-btn dm-btn--ghost" type="button">취소</button>
              <button id="btn-confirm-new-document" className="dm-btn dm-btn--danger" type="button">새 작업 시작</button>
            </>
          )}
        >
          <div className="dm-modal-message">저장할 내용이 있다면 취소한 뒤 최종 저장을 먼저 완료하세요.</div>
        </Modal>

        {/* 저장 종류가 확정되기 전에는 중립 문구를 보이고, finalizationController가
            실제 public/legal 프레젠테이션과 동일한 문구·동작으로 갱신한다. */}
        <Modal
          id="final-save-dialog"
          titleId="final-save-dialog-title"
          title={workspace.finalSaveDialog.title}
          description={workspace.finalSaveDialog.description}
          hidden={!workspace.finalSaveDialog.visible}
          closeButtonId="btn-close-final-save-dialog"
          owner="react"
          onClose={() => withWorkspace((controller) => controller.closeFinalSaveDialog())}
          footer={(
            <>
              <button id="btn-dialog-cancel-save" className="dm-btn dm-btn--ghost" type="button" onClick={() => withWorkspace((controller) => controller.closeFinalSaveDialog())}>{workspace.finalSaveDialog.cancelLabel}</button>
              <button id="btn-dialog-save-all" ref={finalSaveConfirmButtonRef} className="dm-btn dm-btn--primary" type="button" disabled={!workspace.finalSaveDialog.confirmEnabled} onClick={() => withWorkspace((controller) => { controller.closeFinalSaveDialog(); void controller.saveFinalOutput({ warningsConfirmed: true }); })}>{workspace.finalSaveDialog.confirmLabel}</button>
            </>
          )}
        >
          <span id="final-save-dialog-state" className={classNames("dm-badge", "status-chip", `status-chip-${workspace.finalSaveDialog.stateTone}`)}>{workspace.finalSaveDialog.stateLabel}</span>
          <div className={classNames("dm-savewarn__summary", !workspace.finalSaveDialog.showAdvisory && "is-hidden")} role="note" hidden={!workspace.finalSaveDialog.showAdvisory}>
            <SymbolIcon name="error" className="dm-savewarn__summary-icon" aria-hidden="true" />
            <div>
              <strong>{workspace.finalSaveDialog.advisoryTitle}</strong>
              <span data-role="final-save-advisory-copy">{workspace.finalSaveDialog.advisoryCopy}</span>
              <ul id="final-save-warning-list" className="dm-savewarn" aria-label="저장 전 검토 항목">
                {workspace.finalSaveDialog.warnings.length === 0
                  ? <li className="dm-savewarn__empty">{workspace.finalSaveDialog.emptyMessage}</li>
                  : workspace.finalSaveDialog.warnings.map((warning) => <li className="dm-savewarn__item" key={warning}>{warning}</li>)}
              </ul>
            </div>
          </div>
          <p className="dm-savewarn__location-note" hidden>저장 위치와 파일명은 다음 단계에서 선택합니다.</p>
        </Modal>
        <Modal
          id="masking-progress-dialog"
          titleId="masking-progress-dialog-title"
          title="개인정보 자동 탐지 중"
          description="문서를 분석해 개인정보를 찾고 있습니다. 창을 닫아도 작업은 백그라운드에서 계속됩니다."
          closeButtonId="btn-close-masking-progress-dialog"
          footer={<button id="btn-cancel-masking-progress" className="dm-btn dm-btn--ghost" type="button">취소</button>}
        >
          <div className="dm-progress-dialog">
            <div className="dm-progress-dialog__progress-meta"><span id="masking-progress-stage">문서 분석 준비 중</span><strong id="masking-progress-percent">0%</strong></div>
            <progress id="masking-progress-value" max={100} value={0} aria-label="자동 마스킹 진행률" />
            <div className="dm-progress-dialog__stats">
              <div><span>발견 항목</span><strong id="masking-progress-detected">0건</strong></div>
              <div><span>분석 페이지</span><strong id="masking-progress-pages">확인 중</strong></div>
            </div>
            <span id="masking-progress-elapsed" className="dm-visually-hidden">0초</span>
            <p className="dm-progress-dialog__note">취소 또는 Esc를 누르면 창만 닫고, 현재 탐지는 백그라운드에서 계속 진행합니다.</p>
          </div>
        </Modal>
        <Modal
          id="finalization-success-dialog"
          titleId="finalization-success-dialog-title"
          title={<><span className="dm-save-success__title-mark" aria-hidden="true"><SymbolIcon name="check_circle" /></span>{workspace.finalizationSuccessDialog.title}</>}
          ariaLabel={workspace.finalizationSuccessDialog.title}
          description={workspace.finalizationSuccessDialog.description}
          closeButtonId="btn-close-finalization-success-dialog"
          hidden={!workspace.finalizationSuccessDialog.visible}
          owner="react"
          onClose={() => withWorkspace((controller) => controller.closeFinalizationSuccess())}
          onKeyDown={handleFinalizationSuccessDialogKeyDown}
          footer={(
            <>
              <button id="btn-final-save-go-storage" ref={finalizationSuccessActionRef} className="dm-btn dm-btn--ghost" type="button" onClick={() => withWorkspace((controller) => controller.showCompletedFinalStorage())}>저장함 열기</button>
              <button id="btn-final-save-open-file" className="dm-btn dm-btn--primary" type="button" onClick={() => withWorkspace((controller) => void controller.openCompletedFinalFile())}>문서 내보내기</button>
            </>
          )}
        >
          <div className="dm-save-success__file" aria-label="최종 저장 결과">
            <SymbolIcon name="description" aria-hidden="true" />
            <dl>
              <div><dt>저장 상태</dt><dd id="final-save-result-status" data-state={workspace.finalizationSuccessDialog.statusTone}>{workspace.finalizationSuccessDialog.statusLabel}</dd></div>
              <div><dt>저장한 파일</dt><dd id="final-save-result-file">{workspace.finalizationSuccessDialog.fileName}</dd></div>
              <div><dt>저장 정보</dt><dd id="final-save-result-meta">{workspace.finalizationSuccessDialog.meta}</dd></div>
              <div className="dm-visually-hidden"><dd id="final-save-result-path">{workspace.finalizationSuccessDialog.path}</dd><dd id="final-save-result-mask-count">{workspace.finalizationSuccessDialog.maskCount}</dd><dd id="final-save-result-time">{workspace.finalizationSuccessDialog.savedAt}</dd></div>
            </dl>
          </div>
          {workspace.finalizationSuccessDialog.warnings.length > 0 && (
            <div className="dm-save-success__warnings" role="alert" aria-label="미가림 가능성이 있는 항목">
              <strong>전송 전 확인할 미가림 가능성</strong>
              <ul id="final-save-result-warnings">
                {workspace.finalizationSuccessDialog.warnings.map((warning) => <li key={warning}>{warning}</li>)}
              </ul>
            </div>
          )}
        </Modal>
      </section>
    </section>
  );
}
