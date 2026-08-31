import type { RenderTask } from "pdfjs-dist";
import type { ApplicationSessionState, DragRejection } from "../../app/compositionRoot";
import {
  isQaDriveCancellationError,
  qaDriveCancellationError,
  traceQaDriveStage,
  withQaDriveCancellation,
  QA_DRIVE_RENDER_CANCEL_TIMEOUT_MS,
} from "../../app/qaDriveProtocol.ts";
import type { AnalysisOccurrenceV1, AnalysisRegionV1, ManualActionV1 } from "../../state/maskingSession";
import { createPdfThumbnailRenderer } from "./pdfThumbnailRenderer.ts";
import type { PdfPageThumbnail } from "./pdfThumbnailRenderer.ts";

// Canvas render/interaction controller (docs/CODE_REVIEW_2026-07-04.md
// composition root 분리: canvas-workbench 렌더 모듈).
//
// Owns the canvas rendering, overlay drawing, drag-to-draw interaction, zoom,
// and page navigation that used to live inline in compositionRoot.ts. The pure
// box/zoom/fixture model helpers stay in src/canvasWorkbench.ts (public names
// preserved); this controller is the view/interaction layer that the composition root
// wires with injected DOM canvases, the shared mutable run state, and the
// orchestration callbacks (clampPage / updateMeta / getActiveCanvasTool).
//
// composition root destructures the exposed methods into same-named local consts,
// so every existing renderCompare()/redrawOverlay()/adjustZoom()/moveOrigPage()/
// moveResultPage() call site stays byte-for-byte unchanged.

export type CanvasRenderFailureCode = "canvas_render_failed";
export class CanvasRenderFailure extends Error {
  readonly code: CanvasRenderFailureCode = "canvas_render_failed";

  readonly surface: "original" | "result";
  readonly cause: unknown;

  constructor(surface: "original" | "result", cause: unknown) {
    super(`${surface}_canvas_render_failed`);
    this.name = "CanvasRenderFailure";
    this.surface = surface;
    this.cause = cause;
  }
}

type CanvasEditorTool = "select" | "mask" | "restore" | "pan" | "delete";

// The slice of the composition root shared `state` object this controller reads and
// mutates. The full closure state object satisfies this structurally.
export type CanvasRenderState = Pick<
  ApplicationSessionState,
  | "scale"
  | "boxes"
  | "geometryDraft"
  | "lastDragRejection"
  | "documentEditRevision"
  | "mode"
  | "currentOrigPage"
  | "currentResultPage"
  | "origDoc"
  | "resultDoc"
  | "selectedCanvasBoxIndex"
  | "lastPreviewDiagnostics"
  | "syncPages"
  | "activeRunKind"
  | "savingInFlight"
  | "maskingRunning"
>;

export type CanvasRenderDeps = {
  readonly state: CanvasRenderState;
  readonly origCanvas: HTMLCanvasElement;
  readonly resultCanvas: HTMLCanvasElement;
  readonly overlay: HTMLCanvasElement;
  readonly origWrap: HTMLDivElement;
  readonly resultWrap: HTMLDivElement;
  readonly pdfCompareView: HTMLDivElement;
  readonly origCtx: CanvasRenderingContext2D;
  readonly resultCtx: CanvasRenderingContext2D;
  readonly octx: CanvasRenderingContext2D;
  readonly clampPage: (page: number, doc: any | null) => number;
  readonly updateMeta: () => void;
  readonly getActiveCanvasTool: () => CanvasEditorTool;
  readonly setStatus: (message: string) => void;
  readonly getPublicDetectionOverlay: () => {
    readonly regions: readonly AnalysisRegionV1[];
    readonly occurrences: readonly AnalysisOccurrenceV1[];
    readonly manualActions: readonly ManualActionV1[];
  } | null;
  readonly publishPageThumbnails: (thumbnails: readonly PdfPageThumbnail[]) => void;
};

export type CanvasRenderController = {
  readonly renderCompare: (signal?: AbortSignal) => Promise<void>;
  readonly redrawOverlay: () => void;
  readonly adjustZoom: (deltaSteps: number) => Promise<void>;
  readonly moveOrigPage: (delta: number) => Promise<void>;
  readonly moveResultPage: (delta: number) => Promise<void>;
  readonly goToReviewPage: (pageIndex: number) => Promise<void>;
  readonly loadPageThumbnails: (pageIndexes: readonly number[]) => Promise<void>;
  readonly cancelActiveInteraction: () => void;
  readonly setFocusedDetectionOccurrence: (occurrenceId: string | null) => void;
};

type CanvasRenderSlot = {
  task: RenderTask | null;
  generation: number;
};

function isPdfRenderCancelled(error: unknown): boolean {
  return error instanceof Error && error.name === "RenderingCancelledException";
}

function waitForRenderTaskSettlement(task: RenderTask): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (callback: () => void): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      callback();
    };
    const timer = setTimeout(() => {
      finish(() => reject(new Error("QA_DRIVE_RENDER_CANCEL_TIMEOUT")));
    }, QA_DRIVE_RENDER_CANCEL_TIMEOUT_MS);
    Promise.resolve(task.promise).then(
      () => finish(resolve),
      (error) => {
        if (isPdfRenderCancelled(error)) {
          finish(resolve);
          return;
        }
        finish(() => reject(error));
      },
    );
  });
}

async function cancelRenderTask(
  slot: CanvasRenderSlot,
  waitForCompletion = true,
): Promise<void> {
  const task = slot.task;
  if (!task) return;
    task.cancel();
  if (!waitForCompletion) {
    if (slot.task === task) slot.task = null;
          return;
  }
  try {
    await waitForRenderTaskSettlement(task);
  } finally {
    if (slot.task === task) slot.task = null;
  }
}
export function createCanvasRenderController(deps: CanvasRenderDeps): CanvasRenderController {
  const { state, overlay, resultCanvas, octx } = deps;

  let dragStart: { x: number; y: number } | null = null;
  let dragCurrent: { x: number; y: number } | null = null;
  let dragOwner: {
    readonly resultDoc: CanvasRenderState["resultDoc"];
    readonly page: number;
    readonly scale: number;
    readonly mode: "mask" | "restore";
    readonly draftOwner: string | null;
    readonly gestureTrusted: boolean;
  } | null = null;
  const resultDocIds = new WeakMap<object, string>();
  let nextResultDocId = 1;
  const origRenderSlot: CanvasRenderSlot = { task: null, generation: 0 };
  const resultRenderSlot: CanvasRenderSlot = { task: null, generation: 0 };
  let compareGeneration = 0;
  let resultOverlayEnabled = false;
  let focusedDetectionOccurrenceId: string | null = null;
  const thumbnailRenderer = createPdfThumbnailRenderer({
    getDocument: () => state.resultDoc ?? state.origDoc,
    publish: deps.publishPageThumbnails,
  });
  // Active pan gesture (tool === "pan"): remembers where the drag began and the
  // scroll offset at that moment so mousemove can translate the scroll container.
  let panGesture: { startX: number; startY: number; scrollLeft: number; scrollTop: number } | null = null;
  function cancelActiveInteraction(): void {
    const hadInteraction = Boolean(dragStart || dragCurrent || panGesture);
    dragStart = null;
    dragCurrent = null;
    dragOwner = null;
    panGesture = null;
    overlay.classList.remove("is-panning");
    if (hadInteraction) redrawOverlay();
  }

  function recordDocumentEdit(): void {
    state.documentEditRevision = (state.documentEditRevision || 0) + 1;
  }

  function resultDocId(doc: CanvasRenderState["resultDoc"]): string | null {
    if (doc === null) return null;
    const existing = resultDocIds.get(doc);
    if (existing) return existing;
    const created = `resultDoc#${nextResultDocId}`;
    nextResultDocId += 1;
    resultDocIds.set(doc, created);
    return created;
  }

  function dragContext(owner: NonNullable<typeof dragOwner>): Record<string, unknown> {
    return {
      resultDoc: resultDocId(owner.resultDoc),
      page: owner.page,
      scale: owner.scale,
      mode: owner.mode,
      draftOwner: owner.draftOwner,
    };
  }

  function currentDragContext(): Record<string, unknown> {
    return {
      resultDoc: resultDocId(state.resultDoc),
      page: deps.clampPage(state.currentResultPage, state.resultDoc),
      scale: state.scale,
      mode: state.mode,
      draftOwner: state.geometryDraft?.owner ?? null,
    };
  }

  function dragGuardRejection(owner: NonNullable<typeof dragOwner>): DragRejection | null {
    const expected = dragContext(owner);
    const actual = currentDragContext();
    if (owner.resultDoc !== state.resultDoc) return { reason: "resultDocChanged", expected, actual };
    if (owner.page !== deps.clampPage(state.currentResultPage, state.resultDoc)) return { reason: "pageChanged", expected, actual };
    if (owner.scale !== state.scale) return { reason: "scaleChanged", expected, actual };
    if (owner.mode !== state.mode) return { reason: "modeChanged", expected, actual };
    if (owner.draftOwner !== (state.geometryDraft?.owner ?? null)) return { reason: "draftOwnerChanged", expected, actual };
    return null;
  }

  function recordDragRejection(rejection: DragRejection): void {
    state.lastDragRejection = rejection;
  }

  function cssVar(name: string) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function isObservableBox(_box: CanvasRenderState["boxes"][number]): boolean {
    return true;
  }

  function manualRectOverlapsConfirmedMask(
    action: ManualActionV1,
    rect: ManualActionV1["rects"][number],
    occurrences: readonly AnalysisOccurrenceV1[],
  ): boolean {
    if (action.mode !== "restore"
      || (action.linkedOccurrenceId !== null && action.restoreAuthorizationHash !== null)) return false;
    return rectOverlapsConfirmedMask(rect, action.page, occurrences);
  }

  function manualActionOverlapsConfirmedMask(
    action: ManualActionV1,
    occurrences: readonly AnalysisOccurrenceV1[],
  ): boolean {
    return action.rects.some((rect) => manualRectOverlapsConfirmedMask(action, rect, occurrences));
  }

  function rectOverlapsConfirmedMask(
    rect: ManualActionV1["rects"][number],
    page: number,
    occurrences: readonly AnalysisOccurrenceV1[],
  ): boolean {
    return occurrences.some((occurrence) =>
      occurrence.proposedAction === "mask"
        && (occurrence.state === "confirmed" || occurrence.state === "user_confirmed")
        && occurrence.page === page
        && occurrence.rects.some((mask) =>
          Math.min(rect.x0, rect.x1) < Math.max(mask.x0, mask.x1)
            && Math.max(rect.x0, rect.x1) > Math.min(mask.x0, mask.x1)
            && Math.min(rect.y0, rect.y1) < Math.max(mask.y0, mask.y1)
            && Math.max(rect.y0, rect.y1) > Math.min(mask.y0, mask.y1),
        ),
    );
  }


  function getEmptyCanvasWidth() {
    return Math.min(700, Math.max(320, Math.floor(window.innerWidth - 18)));
  }

  function drawEmpty(canvasEl: HTMLCanvasElement, ctx: CanvasRenderingContext2D, text: string) {
    canvasEl.width = getEmptyCanvasWidth();
    canvasEl.height = 900;
    ctx.fillStyle = cssVar("--surface-canvas") || "#fff";
    ctx.fillRect(0, 0, canvasEl.width, canvasEl.height);
    ctx.strokeStyle = cssVar("--border-default") || "#d0d4da";
    ctx.strokeRect(1, 1, canvasEl.width - 2, canvasEl.height - 2);
    ctx.fillStyle = cssVar("--text-tertiary") || "#5b626d";
    ctx.font = `600 16px ${cssVar("--font-sans") || "sans-serif"}`;
    ctx.fillText(text, 24, 40);
  }

  function drawManualActionLabel(
    x: number,
    y: number,
    text: string,
    color: string,
  ): void {
    const labelHeight = 17;
    const labelWidth = Math.max(72, text.length * 11 + 12);
    const labelX = Math.max(0, Math.min(x, overlay.width - labelWidth));
    const labelY = Math.max(0, y - labelHeight - 3);
    octx.save();
    octx.globalAlpha = 0.94;
    octx.fillStyle = color;
    octx.fillRect(labelX, labelY, labelWidth, labelHeight);
    octx.globalAlpha = 1;
    octx.fillStyle = "#ffffff";
    octx.font = `700 10px ${cssVar("--font-sans") || "sans-serif"}`;
    octx.fillText(text, labelX + 6, labelY + 12);
    octx.restore();
  }

  function drawManualAction(
    action: ManualActionV1,
    rect: ManualActionV1["rects"][number],
    blocked: boolean,
    confirmedMaskOverlap: boolean,
  ): void {
    const x = Math.min(rect.x0, rect.x1) * state.scale;
    const y = Math.min(rect.y0, rect.y1) * state.scale;
    const w = Math.abs(rect.x1 - rect.x0) * state.scale;
    const h = Math.abs(rect.y1 - rect.y0) * state.scale;
    const color = blocked
      ? cssVar("--dm-danger") || "#be3127"
      : action.mode === "mask"
        ? cssVar("--dm-staged") || "#0f7b6c"
        : cssVar("--dm-warning") || "#8b5600";
    octx.save();
    if (!confirmedMaskOverlap) {
      octx.globalAlpha = blocked ? 0.24 : 0.2;
      octx.fillStyle = color;
      octx.fillRect(x, y, w, h);
    }
    octx.globalAlpha = 1;
    octx.strokeStyle = color;
    octx.lineWidth = blocked ? 3 : 2;
    octx.setLineDash(blocked ? [4, 3] : [8, 4]);
    octx.strokeRect(x, y, w, h);
    if (blocked) {
      octx.setLineDash([]);
      octx.lineWidth = 2;
      octx.beginPath();
      octx.moveTo(x, y);
      octx.lineTo(x + w, y + h);
      octx.moveTo(x + w, y);
      octx.lineTo(x, y + h);
      octx.stroke();
    }
    octx.restore();
    drawManualActionLabel(
      x,
      y,
      blocked ? "저장 차단 · 복원" : action.mode === "mask" ? "저장 시 적용" : "저장 시 적용 · 복원",
      color,
    );
  }

  function redrawOverlay() {
    overlay.width = resultCanvas.width;
    overlay.height = resultCanvas.height;
    octx.clearRect(0, 0, overlay.width, overlay.height);
    overlay.dataset.stagedMaskCount = "0";
    overlay.dataset.stagedRestoreCount = "0";
    overlay.dataset.blockedRestoreCount = "0";
    overlay.dataset.stagedOverlayStyle = "none";
    overlay.dataset.stagedRestoreState = "none";

    if (!state.resultDoc) return;
    if (!resultOverlayEnabled) return;
    const page = deps.clampPage(state.currentResultPage, state.resultDoc);
    const publicDetectionOverlay = deps.getPublicDetectionOverlay();
    if (publicDetectionOverlay) {
      for (const occurrence of publicDetectionOverlay.occurrences) {
        if (occurrence.page !== page - 1 || occurrence.proposedAction === "exclude") continue;
        const isAppliedMask = occurrence.proposedAction === "mask"
          && (occurrence.state === "confirmed" || occurrence.state === "user_confirmed");
        const isPendingReview = occurrence.proposedAction === "review" && occurrence.state === "review_required";
        if (!isAppliedMask && !isPendingReview) continue;
        for (const rect of occurrence.rects) {
          const x = Math.min(rect.x0, rect.x1) * state.scale;
          const y = Math.min(rect.y0, rect.y1) * state.scale;
          const w = Math.abs(rect.x1 - rect.x0) * state.scale;
          const h = Math.abs(rect.y1 - rect.y0) * state.scale;
          if (isAppliedMask) {
            octx.fillStyle = cssVar("--dm-mask") || "#000";
            octx.fillRect(x, y, w, h);
          } else {
            const pendingColor = cssVar("--dm-warning") || "#8b5600";
            octx.save();
            octx.globalAlpha = 0.18;
            octx.fillStyle = pendingColor;
            octx.fillRect(x, y, w, h);
            octx.globalAlpha = 1;
            octx.strokeStyle = pendingColor;
            octx.lineWidth = 2;
            octx.setLineDash([6, 3]);
            octx.strokeRect(x, y, w, h);
            octx.restore();
          }
          if (occurrence.occurrenceId === focusedDetectionOccurrenceId) {
            octx.strokeStyle = cssVar("--dm-accent") || "#256ef4";
            octx.lineWidth = 3;
            octx.setLineDash([]);
            octx.strokeRect(x, y, w, h);
          }
        }
      }
      const draft = state.geometryDraft;
      if (draft) {
        const draftTargetIds = new Set(draft.targetIds);
        const candidateColor = "#007c6a";
        const occurrenceColor = "#b45309";
        for (const region of publicDetectionOverlay.regions) {
          if (region.page !== page - 1 || !draftTargetIds.has(region.regionId)) continue;
          for (const rect of region.rects) {
            const x = Math.min(rect.x0, rect.x1) * state.scale;
            const y = Math.min(rect.y0, rect.y1) * state.scale;
            const w = Math.abs(rect.x1 - rect.x0) * state.scale;
            const h = Math.abs(rect.y1 - rect.y0) * state.scale;
            octx.save();
            octx.globalAlpha = 0.12;
            octx.fillStyle = candidateColor;
            octx.fillRect(x, y, w, h);
            octx.globalAlpha = 1;
            octx.strokeStyle = candidateColor;
            octx.lineWidth = 2;
            octx.setLineDash([6, 4]);
            octx.strokeRect(x, y, w, h);
            octx.restore();
          }
        }
        for (const occurrence of publicDetectionOverlay.occurrences) {
          if (occurrence.page !== page - 1 || occurrence.regionId === null || !draftTargetIds.has(occurrence.regionId)) continue;
          for (const rect of occurrence.rects) {
            const x = Math.min(rect.x0, rect.x1) * state.scale;
            const y = Math.min(rect.y0, rect.y1) * state.scale;
            const w = Math.abs(rect.x1 - rect.x0) * state.scale;
            const h = Math.abs(rect.y1 - rect.y0) * state.scale;
            octx.save();
            octx.globalAlpha = 0.22;
            octx.fillStyle = occurrenceColor;
            octx.fillRect(x, y, w, h);
            octx.globalAlpha = 1;
            octx.strokeStyle = occurrenceColor;
            octx.lineWidth = 3;
            octx.setLineDash([]);
            octx.strokeRect(x, y, w, h);
            octx.restore();
          }
        }
      }
      const manualActions = publicDetectionOverlay.manualActions ?? [];
      const currentPageManualActions = manualActions.filter((action) => action.page === page - 1);
      const stagedMaskCount = currentPageManualActions.filter((action) => action.mode === "mask").length;
      const stagedRestoreCount = currentPageManualActions.filter((action) => action.mode === "restore").length;
      const blockedRestoreCount = currentPageManualActions.filter((action) =>
        manualActionOverlapsConfirmedMask(action, publicDetectionOverlay.occurrences),
      ).length;
      overlay.dataset.stagedMaskCount = String(stagedMaskCount);
      overlay.dataset.stagedRestoreCount = String(stagedRestoreCount);
      overlay.dataset.blockedRestoreCount = String(blockedRestoreCount);
      overlay.dataset.stagedOverlayStyle = currentPageManualActions.length > 0
        ? "translucent-dashed-labeled"
        : "none";
      overlay.dataset.stagedRestoreState = blockedRestoreCount > 0
        ? "blocked"
        : stagedRestoreCount > 0
          ? "staged"
          : "none";
      for (const action of currentPageManualActions) {
        const blocked = manualActionOverlapsConfirmedMask(action, publicDetectionOverlay.occurrences);
        for (const rect of action.rects) {
          const confirmedMaskOverlap = rectOverlapsConfirmedMask(rect, action.page, publicDetectionOverlay.occurrences);
          drawManualAction(action, rect, blocked, confirmedMaskOverlap);
        }
      }
      // Staged actions are deliberately painted after the detection layer so
      // their status remains visible. Reapply the focused detection edge last
      // so hover/focus feedback cannot be mistaken for the staged style.
      const focusedOccurrence = publicDetectionOverlay.occurrences.find((occurrence) =>
        occurrence.occurrenceId === focusedDetectionOccurrenceId
          && occurrence.page === page - 1
          && occurrence.proposedAction !== "exclude",
      );
      if (focusedOccurrence) {
        octx.strokeStyle = cssVar("--dm-accent") || "#256ef4";
        octx.lineWidth = 3;
        octx.setLineDash([]);
        for (const rect of focusedOccurrence.rects) {
          const x = Math.min(rect.x0, rect.x1) * state.scale;
          const y = Math.min(rect.y0, rect.y1) * state.scale;
          const w = Math.abs(rect.x1 - rect.x0) * state.scale;
          const h = Math.abs(rect.y1 - rect.y0) * state.scale;
          octx.strokeRect(x, y, w, h);
        }
      }
    }
    const pageBoxes = state.boxes
      .map((box, globalIndex) => ({ box, globalIndex }))
      .filter(({ box }) => box.page === page - 1 && isObservableBox(box));
    for (const { box: b, globalIndex } of pageBoxes) {
      const x = b.x0 * state.scale;
      const y = b.y0 * state.scale;
      const w = (b.x1 - b.x0) * state.scale;
      const h = (b.y1 - b.y0) * state.scale;
      const isSelected = globalIndex === state.selectedCanvasBoxIndex;
      const baseColor = b.mode === "mask" ? cssVar("--mask-edge") || "#0b66f0" : cssVar("--warning") || "#8a4b00";
      if (isSelected) {
        // Clearly legible selection: tinted fill + a heavier highlight-colored
        // edge with corner handles, so a click-selected box is unmistakable.
        const highlight = b.mode === "mask"
          ? cssVar("--mask-edge-sel") || "#073e95"
          : cssVar("--warning") || "#8a4b00";
        octx.fillStyle = b.mode === "mask" ? "rgba(11, 102, 240, 0.16)" : "rgba(138, 75, 0, 0.16)";
        octx.fillRect(x, y, w, h);
        octx.strokeStyle = highlight;
        octx.lineWidth = 3;
        octx.strokeRect(x, y, w, h);
        octx.fillStyle = highlight;
        const handle = 6;
        for (const [hx, hy] of [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]) {
          octx.fillRect(hx - handle / 2, hy - handle / 2, handle, handle);
        }
      } else {
        octx.strokeStyle = baseColor;
        octx.lineWidth = 1.5;
        octx.strokeRect(x, y, w, h);
      }
    }
    if (dragStart && dragCurrent) {
      const x = Math.min(dragStart.x, dragCurrent.x);
      const y = Math.min(dragStart.y, dragCurrent.y);
      const w = Math.abs(dragStart.x - dragCurrent.x);
      const h = Math.abs(dragStart.y - dragCurrent.y);
      octx.setLineDash([4, 3]);
      octx.strokeStyle = state.mode === "mask" ? cssVar("--mask-edge-sel") || "#073e95" : cssVar("--warning") || "#8a4b00";
      octx.lineWidth = 2;
      octx.strokeRect(x, y, w, h);
      octx.setLineDash([]);
    }
  }

  async function renderDocToCanvas(
    doc: any | null,
    pageNum: number,
    scale: number,
    canvasEl: HTMLCanvasElement,
    ctx: CanvasRenderingContext2D,
    wrapEl: HTMLDivElement,
    emptyText: string,
    slot: CanvasRenderSlot,
    signal?: AbortSignal,
  ): Promise<boolean> {
    const generation = ++slot.generation;
    await cancelRenderTask(slot, doc !== null);
    if (signal?.aborted) throw qaDriveCancellationError(canvasEl === resultCanvas ? "result-canvas" : "original-canvas");
    if (generation !== slot.generation) return false;

    const scrollContainer = wrapEl.parentElement;
    const previousScrollLeft = scrollContainer?.scrollLeft ?? 0;
    const previousScrollTop = scrollContainer?.scrollTop ?? 0;
    const restoreScrollPosition = (): void => {
      if (!scrollContainer) return;
      scrollContainer.scrollLeft = Math.min(
        previousScrollLeft,
        Math.max(0, scrollContainer.scrollWidth - scrollContainer.clientWidth),
      );
      scrollContainer.scrollTop = Math.min(
        previousScrollTop,
        Math.max(0, scrollContainer.scrollHeight - scrollContainer.clientHeight),
      );
    };
    wrapEl.classList.toggle("has-rendered-pdf", doc !== null);
    if (canvasEl === resultCanvas) resultOverlayEnabled = false;
    if (!doc) {
      drawEmpty(canvasEl, ctx, emptyText);
      wrapEl.style.width = `${canvasEl.width}px`;
      wrapEl.style.height = `${canvasEl.height}px`;
      restoreScrollPosition();
      return true;
    }

    const safePage = deps.clampPage(pageNum, doc);
    const page = signal
      ? await withQaDriveCancellation(
        () => doc.getPage(safePage),
        signal,
        "pdf_get_page",
      )
      : await doc.getPage(safePage);
    if (signal?.aborted) throw qaDriveCancellationError("pdf_get_page");
    if (generation !== slot.generation) return false;
    const viewport = page.getViewport({ scale });
    canvasEl.width = Math.ceil(viewport.width);
    canvasEl.height = Math.ceil(viewport.height);
    wrapEl.style.width = `${canvasEl.width}px`;
    wrapEl.style.height = `${canvasEl.height}px`;
    const renderTask: RenderTask = page.render({ canvasContext: ctx, viewport });
    slot.task = renderTask;
    const renderStartedAt = Date.now();
    const renderDetail = canvasEl === resultCanvas ? "result" : "original";
    try {
      traceQaDriveStage("pdf_render", "start", { detail: renderDetail });
      if (signal) {
        await withQaDriveCancellation(
          () => renderTask.promise,
          signal,
          "pdf_render",
          () => {
            renderTask.cancel();
            return waitForRenderTaskSettlement(renderTask);
          },
        );
      } else {
        await renderTask.promise;
      }
      traceQaDriveStage("pdf_render", "complete", {
        elapsedMs: Math.max(0, Date.now() - renderStartedAt),
        detail: renderDetail,
      });
    } catch (error) {
      traceQaDriveStage("pdf_render", "failed", {
        elapsedMs: Math.max(0, Date.now() - renderStartedAt),
        errorCode: error instanceof Error ? error.message.match(/[A-Z][A-Z0-9_]{2,}/)?.[0] : undefined,
        detail: renderDetail,
      });
      if (isQaDriveCancellationError(error)) throw error;
      if (!isPdfRenderCancelled(error)) throw error;
      return false;
    } finally {
      if (slot.task === renderTask) slot.task = null;
      restoreScrollPosition();
    }
    if (signal?.aborted) throw qaDriveCancellationError("pdf_render");
    const rendered = generation === slot.generation;
    if (canvasEl === resultCanvas && rendered) {
      resultOverlayEnabled = true;
      redrawOverlay();
    }
    return rendered;
  }

  function safeRenderFailureCode(error: unknown): "pdf" | "permission" | "invalid" | "unknown" {
    if (error instanceof SyntaxError) return "invalid";
    const message = error instanceof Error ? error.message.toLowerCase() : "";
    if (message.includes("permission") || message.includes("denied")) return "permission";
    if (message.includes("pdf") || message.includes("document") || message.includes("render")) return "pdf";
    return "unknown";
  }

  async function renderCompare(signal?: AbortSignal) {
    const generation = ++compareGeneration;
    const scale = state.scale;
    try {
      const rendered = await renderDocToCanvas(
        state.origDoc,
        state.currentOrigPage,
        scale,
        deps.origCanvas,
        deps.origCtx,
        deps.origWrap,
        "PDF 문서를 선택하세요.",
        origRenderSlot,
        signal,
      );
      if (signal?.aborted) throw qaDriveCancellationError("original-canvas");
      if (!rendered || generation !== compareGeneration) return;
    } catch (error) {
      if (generation !== compareGeneration) return;
      if (signal?.aborted || isQaDriveCancellationError(error)) throw error;
      drawEmpty(deps.origCanvas, deps.origCtx, "원문 렌더 실패");
      resultOverlayEnabled = false;
      redrawOverlay();
      state.lastPreviewDiagnostics = `원문 렌더 실패 (${safeRenderFailureCode(error)})`;
      throw new CanvasRenderFailure("original", error);
    }

    try {
      const rendered = await renderDocToCanvas(
        state.resultDoc,
        state.currentResultPage,
        scale,
        resultCanvas,
        deps.resultCtx,
        deps.resultWrap,
        "마스킹 실행 후 수정본이 표시됩니다.",
        resultRenderSlot,
        signal,
      );
      if (signal?.aborted) throw qaDriveCancellationError("result-canvas");
      if (!rendered || generation !== compareGeneration) return;
    } catch (error) {
      if (generation !== compareGeneration) return;
      if (signal?.aborted || isQaDriveCancellationError(error)) throw error;
      drawEmpty(resultCanvas, deps.resultCtx, "수정본 렌더 실패");
      state.lastPreviewDiagnostics = [state.lastPreviewDiagnostics, `수정본 렌더 실패 (${safeRenderFailureCode(error)})`].filter(Boolean).join(" | ");
      redrawOverlay();
      throw new CanvasRenderFailure("result", error);
    }

    if (generation !== compareGeneration) return;
    redrawOverlay();
    deps.updateMeta();
    state.lastPreviewDiagnostics = "";
  }

  function getCanvasPos(ev: MouseEvent) {
    const rect = overlay.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(ev.clientX - rect.left, overlay.width)),
      y: Math.max(0, Math.min(ev.clientY - rect.top, overlay.height)),
    };
  }

  // Hit-test: return the global index of the topmost (last-drawn) box on the
  // current result page whose rect contains the given canvas-pixel point, or -1.
  // Coordinates are stored in PDF points, so scale up to canvas pixels to match
  // what redrawOverlay() paints.
  function boxIndexAtCanvasPoint(x: number, y: number): number {
    if (!state.resultDoc) return -1;
    const page = deps.clampPage(state.currentResultPage, state.resultDoc) - 1;
    for (let index = state.boxes.length - 1; index >= 0; index -= 1) {
      const b = state.boxes[index];
      if (b.page !== page || !isObservableBox(b)) continue;
      const left = Math.min(b.x0, b.x1) * state.scale;
      const right = Math.max(b.x0, b.x1) * state.scale;
      const top = Math.min(b.y0, b.y1) * state.scale;
      const bottom = Math.max(b.y0, b.y1) * state.scale;
      if (x >= left && x <= right && y >= top && y <= bottom) return index;
    }
    return -1;
  }

  // The scrollable ancestor that the pan tool nudges. The overlay lives inside
  // .dm-canvas__scroll (overflow:auto); fall back to the wrap's parent so the
  // controller does not hard-depend on that class name.
  function getScrollContainer(): HTMLElement | null {
    return (overlay.closest(".dm-canvas__scroll") as HTMLElement | null) ?? deps.resultWrap.parentElement;
  }

  function selectBoxAtPoint(x: number, y: number) {
    const hit = boxIndexAtCanvasPoint(x, y);
    if (hit === state.selectedCanvasBoxIndex) return;
    state.selectedCanvasBoxIndex = hit;
    redrawOverlay();
    deps.updateMeta();
  }

  function deleteBoxAtPoint(x: number, y: number) {
    const hit = boxIndexAtCanvasPoint(x, y);
    if (hit < 0) return;
    state.boxes.splice(hit, 1);
    recordDocumentEdit();
    if (state.selectedCanvasBoxIndex === hit) {
      state.selectedCanvasBoxIndex = -1;
    } else if (state.selectedCanvasBoxIndex > hit) {
      state.selectedCanvasBoxIndex -= 1;
    }
    redrawOverlay();
    deps.updateMeta();
  }

  async function adjustZoom(deltaSteps: number) {
    const minScale = 0.5;
    const maxScale = 2.5;
    const next = Math.max(minScale, Math.min(maxScale, Number((state.scale + deltaSteps * 0.1).toFixed(2))));
    if (next === state.scale) return;
    cancelActiveInteraction();
    state.scale = next;
    await renderCompare();
  }

  async function moveOrigPage(delta: number) {
    const maxPages = state.origDoc?.numPages || 0;
    if (maxPages === 0) return;
    cancelActiveInteraction();
    state.currentOrigPage = Math.max(1, Math.min(state.currentOrigPage + delta, maxPages));
    if (state.syncPages && state.resultDoc) {
      state.currentResultPage = deps.clampPage(state.currentOrigPage, state.resultDoc);
    }
    await renderCompare();
  }

  async function moveResultPage(delta: number) {
    const maxPages = state.resultDoc?.numPages || 0;
    if (maxPages === 0) return;
    cancelActiveInteraction();
    state.currentResultPage = Math.max(1, Math.min(state.currentResultPage + delta, maxPages));
    if (state.syncPages) {
      state.currentOrigPage = deps.clampPage(state.currentResultPage, state.origDoc);
    }
    await renderCompare();
  }

  async function goToReviewPage(pageIndex: number) {
    const targetPage = pageIndex + 1;
    if (state.resultDoc) {
      await moveResultPage(targetPage - state.currentResultPage);
      return;
    }
    await moveOrigPage(targetPage - state.currentOrigPage);
  }

  function setFocusedDetectionOccurrence(occurrenceId: string | null): void {
    if (focusedDetectionOccurrenceId === occurrenceId) return;
    focusedDetectionOccurrenceId = occurrenceId;
    redrawOverlay();
  }

  overlay.addEventListener("mousedown", (ev) => {
    if (state.maskingRunning) {
      deps.setStatus("마스킹 실행 중에는 박스를 그릴 수 없습니다. 완료 후 그려 주세요.");
      cancelActiveInteraction();
      return;
    }
    if (state.savingInFlight) {
      cancelActiveInteraction();
      return;
    }
    if (!state.resultDoc) return;
    const activeCanvasTool = deps.getActiveCanvasTool();
    const pos = getCanvasPos(ev);

    if (activeCanvasTool === "mask" || activeCanvasTool === "restore") {
      const page = deps.clampPage(state.currentResultPage, state.resultDoc);
      dragStart = pos;
      dragCurrent = pos;
      dragOwner = {
        resultDoc: state.resultDoc,
        page,
        scale: state.scale,
        mode: activeCanvasTool,
        draftOwner: state.geometryDraft?.owner ?? null,
        gestureTrusted: ev.isTrusted,
      };
      redrawOverlay();
      return;
    }

    if (activeCanvasTool === "select") {
      selectBoxAtPoint(pos.x, pos.y);
      return;
    }

    if (activeCanvasTool === "delete") {
      deleteBoxAtPoint(pos.x, pos.y);
      return;
    }

    if (activeCanvasTool === "pan") {
      const container = getScrollContainer();
      if (!container) return;
      ev.preventDefault();
      panGesture = {
        startX: ev.clientX,
        startY: ev.clientY,
        scrollLeft: container.scrollLeft,
        scrollTop: container.scrollTop,
      };
      overlay.classList.add("is-panning");
    }
  });

  overlay.addEventListener("mousemove", (ev) => {
    if (state.maskingRunning) {
      deps.setStatus("마스킹 실행 중에는 박스를 그릴 수 없습니다. 완료 후 그려 주세요.");
      cancelActiveInteraction();
      return;
    }
    if (state.savingInFlight) {
      cancelActiveInteraction();
      return;
    }
    if (panGesture) {
      const container = getScrollContainer();
      if (container) {
        container.scrollLeft = panGesture.scrollLeft - (ev.clientX - panGesture.startX);
        container.scrollTop = panGesture.scrollTop - (ev.clientY - panGesture.startY);
      }
      return;
    }
    if (!dragStart || !dragOwner) return;
    const rejection = dragGuardRejection(dragOwner);
    if (rejection) {
      recordDragRejection(rejection);
      cancelActiveInteraction();
      return;
    }
    dragCurrent = getCanvasPos(ev);
    redrawOverlay();
  });

  window.addEventListener("mouseup", () => {
    if (state.maskingRunning) {
      deps.setStatus("마스킹 실행 중에는 박스를 그릴 수 없습니다. 완료 후 그려 주세요.");
      cancelActiveInteraction();
      return;
    }
    if (state.savingInFlight) {
      cancelActiveInteraction();
      return;
    }
    if (panGesture) {
      panGesture = null;
      overlay.classList.remove("is-panning");
      return;
    }
    if (!dragStart || !dragCurrent || !dragOwner || !state.resultDoc) return;
    const rejection = dragGuardRejection(dragOwner);
    if (rejection) {
      recordDragRejection(rejection);
      cancelActiveInteraction();
      return;
    }
    const x = Math.min(dragStart.x, dragCurrent.x);
    const y = Math.min(dragStart.y, dragCurrent.y);
    const w = Math.abs(dragStart.x - dragCurrent.x);
    const h = Math.abs(dragStart.y - dragCurrent.y);
    const owner = dragOwner;

    dragStart = null;
    dragCurrent = null;
    dragOwner = null;

    if (w < 4 || h < 4) {
      recordDragRejection({
        reason: "tooSmall",
        expected: { minWidth: 4, minHeight: 4 },
        actual: { width: w, height: h },
      });
      redrawOverlay();
      return;
    }

    const p0 = { x: x / owner.scale, y: y / owner.scale };
    const p1 = { x: (x + w) / owner.scale, y: (y + h) / owner.scale };
    const pageIndex = owner.page - 1;
    const tag = owner.draftOwner && owner.mode === "mask" ? owner.draftOwner : "MANUAL";
    state.boxes.push({
      page: pageIndex,
      x0: p0.x,
      y0: p0.y,
      x1: p1.x,
      y1: p1.y,
      mode: owner.mode,
      tag,
      gestureTrusted: owner.gestureTrusted,
    });
    recordDocumentEdit();
    state.selectedCanvasBoxIndex = state.boxes.length - 1;
    redrawOverlay();
    deps.updateMeta();
  });

  deps.pdfCompareView.addEventListener(
    "wheel",
    (ev) => {
      if (!ev.ctrlKey) return;
      ev.preventDefault();
      void adjustZoom(ev.deltaY < 0 ? 1 : -1);
    },
    { passive: false },
  );

  return {
    renderCompare,
    redrawOverlay,
    adjustZoom,
    moveOrigPage,
    moveResultPage,
    goToReviewPage,
    loadPageThumbnails: thumbnailRenderer.load,
    cancelActiveInteraction,
    setFocusedDetectionOccurrence,
  };
}
