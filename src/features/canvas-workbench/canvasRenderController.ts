import type { RenderTask } from "pdfjs-dist";
import type { LegacySessionState } from "../../legacy/startLegacyApp";

// Canvas render/interaction controller (docs/CODE_REVIEW_2026-07-04.md
// "startLegacyApp 분리": canvas-workbench 렌더 모듈).
//
// Owns the canvas rendering, overlay drawing, drag-to-draw interaction, zoom,
// and page navigation that used to live inline in startLegacyApp.ts. The pure
// box/zoom/fixture model helpers stay in src/canvasWorkbench.ts (public names
// preserved); this controller is the view/interaction layer that startLegacyApp
// wires with injected DOM canvases, the shared mutable run state, and the
// orchestration callbacks (clampPage / updateMeta / getActiveCanvasTool).
//
// startLegacyApp destructures the exposed methods into same-named local consts,
// so every existing renderCompare()/redrawOverlay()/adjustZoom()/moveOrigPage()/
// moveResultPage() call site stays byte-for-byte unchanged.

type CanvasEditorTool = "select" | "mask" | "restore" | "pan" | "delete";

// The slice of startLegacyApp's shared `state` object this controller reads and
// mutates. The full closure state object satisfies this structurally.
export type CanvasRenderState = Pick<
  LegacySessionState,
  | "scale"
  | "boxes"
  | "documentEditRevision"
  | "mode"
  | "currentOrigPage"
  | "currentResultPage"
  | "origDoc"
  | "resultDoc"
  | "selectedCanvasBoxIndex"
  | "lastPreviewDiagnostics"
  | "syncPages"
  | "savingInFlight"
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
};

export type CanvasRenderController = {
  readonly renderCompare: () => Promise<void>;
  readonly redrawOverlay: () => void;
  readonly adjustZoom: (deltaSteps: number) => Promise<void>;
  readonly moveOrigPage: (delta: number) => Promise<void>;
  readonly moveResultPage: (delta: number) => Promise<void>;
  readonly cancelActiveInteraction: () => void;
};

type CanvasRenderSlot = {
  task: RenderTask | null;
  generation: number;
};

function isPdfRenderCancelled(error: unknown): boolean {
  return error instanceof Error && error.name === "RenderingCancelledException";
}

async function cancelRenderTask(slot: CanvasRenderSlot): Promise<void> {
  const task = slot.task;
  if (!task) return;
  task.cancel();
  try {
    await task.promise;
  } catch (error) {
    if (!isPdfRenderCancelled(error)) throw error;
  } finally {
    if (slot.task === task) slot.task = null;
  }
}

export function createCanvasRenderController(deps: CanvasRenderDeps): CanvasRenderController {
  const { state, overlay, resultCanvas, octx } = deps;

  let dragStart: { x: number; y: number } | null = null;
  let dragCurrent: { x: number; y: number } | null = null;
  const origRenderSlot: CanvasRenderSlot = { task: null, generation: 0 };
  const resultRenderSlot: CanvasRenderSlot = { task: null, generation: 0 };
  let compareGeneration = 0;
  // Active pan gesture (tool === "pan"): remembers where the drag began and the
  // scroll offset at that moment so mousemove can translate the scroll container.
  let panGesture: { startX: number; startY: number; scrollLeft: number; scrollTop: number } | null = null;
  function cancelActiveInteraction(): void {
    const hadInteraction = Boolean(dragStart || dragCurrent || panGesture);
    dragStart = null;
    dragCurrent = null;
    panGesture = null;
    overlay.classList.remove("is-panning");
    if (hadInteraction) redrawOverlay();
  }

  function recordDocumentEdit(): void {
    state.documentEditRevision = (state.documentEditRevision || 0) + 1;
  }

  function cssVar(name: string) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function toPdfPoint(x: number, y: number) {
    return { x: x / state.scale, y: y / state.scale };
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

  function redrawOverlay() {
    overlay.width = resultCanvas.width;
    overlay.height = resultCanvas.height;
    octx.clearRect(0, 0, overlay.width, overlay.height);

    if (!state.resultDoc) return;
    const page = deps.clampPage(state.currentResultPage, state.resultDoc);
    const pageBoxes = state.boxes
      .map((box, globalIndex) => ({ box, globalIndex }))
      .filter(({ box }) => box.page === page - 1);
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
        const highlight = cssVar("--mask-edge-sel") || "#073e95";
        octx.fillStyle = "rgba(11, 102, 240, 0.16)";
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
  ): Promise<boolean> {
    const generation = ++slot.generation;
    await cancelRenderTask(slot);
    if (generation !== slot.generation) return false;

    wrapEl.classList.toggle("has-rendered-pdf", doc !== null);
    if (!doc) {
      drawEmpty(canvasEl, ctx, emptyText);
      wrapEl.style.width = `${canvasEl.width}px`;
      wrapEl.style.height = `${canvasEl.height}px`;
      return true;
    }

    const safePage = deps.clampPage(pageNum, doc);
    const page = await doc.getPage(safePage);
    if (generation !== slot.generation) return false;
    const viewport = page.getViewport({ scale });
    canvasEl.width = Math.ceil(viewport.width);
    canvasEl.height = Math.ceil(viewport.height);
    wrapEl.style.width = `${canvasEl.width}px`;
    wrapEl.style.height = `${canvasEl.height}px`;
    const renderTask: RenderTask = page.render({ canvasContext: ctx, viewport });
    slot.task = renderTask;
    try {
      await renderTask.promise;
    } catch (error) {
      if (!isPdfRenderCancelled(error)) throw error;
      return false;
    } finally {
      if (slot.task === renderTask) slot.task = null;
    }
    return generation === slot.generation;
  }

  async function renderCompare() {
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
      );
      if (!rendered || generation !== compareGeneration) return;
    } catch {
      if (generation !== compareGeneration) return;
      drawEmpty(deps.origCanvas, deps.origCtx, "원문 렌더 실패");
      state.lastPreviewDiagnostics = "원문 렌더 실패";
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
      );
      if (!rendered || generation !== compareGeneration) return;
    } catch {
      if (generation !== compareGeneration) return;
      drawEmpty(resultCanvas, deps.resultCtx, "수정본 렌더 실패");
      state.lastPreviewDiagnostics = [state.lastPreviewDiagnostics, "수정본 렌더 실패"].filter(Boolean).join(" | ");
    }

    if (generation !== compareGeneration) return;
    redrawOverlay();
    deps.updateMeta();
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
      if (b.page !== page) continue;
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
    state.scale = next;
    await renderCompare();
  }

  async function moveOrigPage(delta: number) {
    const maxPages = state.origDoc?.numPages || 0;
    if (maxPages === 0) return;
    state.currentOrigPage = Math.max(1, Math.min(state.currentOrigPage + delta, maxPages));
    if (state.syncPages) {
      state.currentResultPage = deps.clampPage(state.currentOrigPage, state.resultDoc);
    }
    await renderCompare();
  }

  async function moveResultPage(delta: number) {
    const maxPages = state.resultDoc?.numPages || 0;
    if (maxPages === 0) return;
    state.currentResultPage = Math.max(1, Math.min(state.currentResultPage + delta, maxPages));
    if (state.syncPages) {
      state.currentOrigPage = deps.clampPage(state.currentResultPage, state.origDoc);
    }
    await renderCompare();
  }

  overlay.addEventListener("mousedown", (ev) => {
    if (state.savingInFlight) {
      cancelActiveInteraction();
      return;
    }
    if (!state.resultDoc) return;
    const activeCanvasTool = deps.getActiveCanvasTool();
    const pos = getCanvasPos(ev);

    if (activeCanvasTool === "mask" || activeCanvasTool === "restore") {
      dragStart = pos;
      dragCurrent = pos;
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
    if (!dragStart) return;
    dragCurrent = getCanvasPos(ev);
    redrawOverlay();
  });

  window.addEventListener("mouseup", () => {
    if (state.savingInFlight) {
      cancelActiveInteraction();
      return;
    }
    if (panGesture) {
      panGesture = null;
      overlay.classList.remove("is-panning");
      return;
    }
    if (!dragStart || !dragCurrent || !state.resultDoc) return;
    const x = Math.min(dragStart.x, dragCurrent.x);
    const y = Math.min(dragStart.y, dragCurrent.y);
    const w = Math.abs(dragStart.x - dragCurrent.x);
    const h = Math.abs(dragStart.y - dragCurrent.y);

    dragStart = null;
    dragCurrent = null;

    if (w < 4 || h < 4) {
      redrawOverlay();
      return;
    }

    const p0 = toPdfPoint(x, y);
    const p1 = toPdfPoint(x + w, y + h);
    const page = deps.clampPage(state.currentResultPage, state.resultDoc);
    state.boxes.push({ page: page - 1, x0: p0.x, y0: p0.y, x1: p1.x, y1: p1.y, mode: state.mode, tag: "MANUAL" });
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

  return { renderCompare, redrawOverlay, adjustZoom, moveOrigPage, moveResultPage, cancelActiveInteraction };
}
