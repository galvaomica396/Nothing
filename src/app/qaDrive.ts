import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { ApplicationController } from "./applicationController";
import { dashboardMaskCounts } from "../dashboardSurfaceModels";
import { workspaceCanvasMount } from "../features/canvas-workbench/workspaceRuntime";
import { applicationController } from "../state/appControllerRuntime";
import { workspaceStateSnapshot } from "../state/workspaceStore";
import { currentSettings, updateSettings } from "../state/settingsStore";
import { MASKING_PROFILES } from "../settingsState";
import {
  activateQaDriveTrace,
  isQaDriveTimeoutError,
  measureQaDriveStage,
  measureQaDriveStageSync,
  qaDriveCommandTimeoutMs,
  qaDriveCancellationError,
  qaDriveRenderUnavailableError,
  qaDriveTimeoutError,
  traceQaDriveStage,
  withQaDriveTimeout,
  QA_DRIVE_RENDER_CANCEL_TIMEOUT_MS,
  QA_DRIVE_RESPONSE_TIMEOUT_MS,
} from "./qaDriveProtocol";
import type { QaDriveTraceEvent } from "./qaDriveProtocol";
import type { BoxMode } from "../features/document-session/documentSessionController";
import type { CanvasEditorTool } from "../features/manual-adjustment/manualAdjustmentController";
import type { FinalSaveOutcome } from "../features/finalization/finalizationController";

type QaCommand =
  | { readonly kind: "open"; readonly value: string }
  | { readonly kind: "set-profile"; readonly value: string }
  | { readonly kind: "apply-keyword"; readonly value: string }
  | { readonly kind: "set-tool"; readonly value: CanvasEditorTool }
  | { readonly kind: "start-masking" }
  | { readonly kind: "run-masking" }
  | { readonly kind: "wait-idle" }
  | { readonly kind: "render-probe"; readonly value: "on" | "off" | "clear" }
  | { readonly kind: "go-page"; readonly page: number }
  | { readonly kind: "scroll-to"; readonly page?: number; readonly rect: PdfTargetRect }
  | { readonly kind: "inspect-target"; readonly rect: PdfTargetRect }
  | { readonly kind: "set-overlay"; readonly visibility: "hidden" | "visible" }
  | { readonly kind: "resolve-review"; readonly reviewId: string; readonly action: QaReviewAction }
  | { readonly kind: "resolve-geometry"; readonly reviewId: string; readonly rect: { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number } }
  | { readonly kind: "draw-box"; readonly rect: { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number }; readonly mode: BoxMode }
  | { readonly kind: "drag-canvas"; readonly rect: { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number } }
  | { readonly kind: "apply-manual" }
  | { readonly kind: "open-save-dialog" }
  | { readonly kind: "confirm-save" }
  | { readonly kind: "wait-save" }
  | { readonly kind: "close-success-dialog" }
  | { readonly kind: "save-final" }
  | { readonly kind: "dump-state" };

type QaReviewAction = "mask" | "exclude" | "acknowledge" | "confirm_boundary" | "confirm_suggested_geometry" | "confirm_geometry" | "reanalyze";
type PdfTargetRect = { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number };
type QaTargetInspection = {
  readonly page: number;
  readonly rect: PdfTargetRect;
  readonly visible: boolean;
  readonly targetArea: number;
  readonly visibleArea: number;
  readonly visibleRatio: number;
  readonly intersection: { readonly x: number; readonly y: number; readonly width: number; readonly height: number } | null;
  readonly overlay: {
    readonly paintedPixels: number;
    readonly blackPixels: number;
    readonly pendingPixels: number;
    readonly canvasRect: { readonly x: number; readonly y: number; readonly width: number; readonly height: number };
  } | null;
};
type QaCommandResult = {
  readonly targetInspection?: QaTargetInspection;
  readonly saveFinal?: FinalSaveOutcome;
};
type QaDriveEvent = { readonly id: string; readonly command: string };
type QaDriveResponse = {
  readonly id: string;
  readonly ok: boolean;
  readonly state?: ReturnType<typeof stateSnapshot>;
  readonly error?: string;
  readonly trace?: readonly QaDriveTraceEvent[];
};

type QaDriveCommandContext = {
  readonly traceEvents: QaDriveTraceEvent[];
  readonly deadlineAt: number;
  readonly signal: AbortSignal;
  stage: string;
  readonly releaseTrace: () => void;
  readonly cancel: (stage: string) => void;
};

let pendingMaskingRun: Promise<unknown> | null = null;
const activeCommandContexts = new Map<string, QaDriveCommandContext>();

function commandKind(command: string): string {
  return command.trim().split(/\s+/)[0] ?? "";
}

function createCommandContext(command: string, onCancel?: (stage: string) => void): QaDriveCommandContext {
  const traceEvents: QaDriveTraceEvent[] = [];
  const restoreTrace = activateQaDriveTrace((event) => {
    traceEvents.push(event);
  });
  const controller = new AbortController();
  return {
    traceEvents,
    deadlineAt: Date.now() + qaDriveCommandTimeoutMs(commandKind(command)),
    signal: controller.signal,
    stage: "parse_command",
    releaseTrace: restoreTrace,
    cancel: (stage: string) => {
      if (controller.signal.aborted) return;
      controller.abort();
      onCancel?.(stage);
    },
  };
}

function releaseCommandContext(context: QaDriveCommandContext): void {
  context.releaseTrace();
}

async function respond(response: QaDriveResponse, context?: QaDriveCommandContext): Promise<void> {
  if (!context) {
    try {
      await withQaDriveTimeout(
        () => invoke("qa_drive_response", { response }),
        QA_DRIVE_RESPONSE_TIMEOUT_MS,
        "response_ipc",
      );
    } catch {
      // The native bridge has its own response deadline and is authoritative
      // when the WebView cannot service this IPC call.
    }
    return;
  }
  traceQaDriveStage("response_ipc", "start");
  const payload = {
    ...response,
    trace: context.traceEvents.slice(),
  };
  // The native bridge can emit the next command as soon as it receives this
  // IPC response, before this handler's await continuation runs. Stop routing
  // telemetry before awaiting the response so a late event cannot be attached
  // to the next command's trace.
  context.releaseTrace();
  try {
    await withQaDriveTimeout(
      () => invoke("qa_drive_response", { response: payload }),
      QA_DRIVE_RESPONSE_TIMEOUT_MS,
      "response_ipc",
    );
  } catch {
    // The native bridge emits a stage-labelled fallback when this IPC call
    // itself cannot complete.
  }
}

async function runCommandStage<T>(
  context: QaDriveCommandContext,
  stage: string,
  operation: (signal: AbortSignal) => PromiseLike<T> | T,
): Promise<T> {
  context.stage = stage;
  const remaining = context.deadlineAt - Date.now();
  return measureQaDriveStage(
    stage,
    () => {
      if (context.signal.aborted) throw qaDriveCancellationError(stage);
      if (remaining <= 0) {
        context.cancel(stage);
        throw timeoutErrorForStage(stage);
      }
      return withQaDriveTimeout(operation, remaining, stage, {
        signal: context.signal,
        onTimeout: () => context.cancel(stage),
        timeoutError: () => timeoutErrorForStage(stage),
        timeoutGraceMs: /render|canvas|pdfjs|pdf_|waitForRenderSignal/i.test(stage)
          ? QA_DRIVE_RENDER_CANCEL_TIMEOUT_MS
          : 0,
      });
    },
  );
}

function timeoutErrorForStage(stage: string): Error {
  return /render|canvas|pdfjs|pdf_|waitForRenderSignal/i.test(stage)
    ? qaDriveRenderUnavailableError(stage)
    : qaDriveTimeoutError(stage);
}

function parseTargetRect(values: readonly string[]): PdfTargetRect | null {
  if (values.length !== 4) return null;
  const numbers = values.map(Number);
  if (!numbers.every(Number.isFinite)) return null;
  const [x0, y0, x1, y1] = numbers;
  if (x0 === undefined || y0 === undefined || x1 === undefined || y1 === undefined) return null;
  return { x0, y0, x1, y1 };
}

function parseCommand(command: string): QaCommand {
  const [kind, ...rest] = command.trim().split(/\s+/);
  const value = rest.join(" ").trim();
  if (kind === "open" && value) return { kind, value };
  if (kind === "set-profile" && value) return { kind, value };
  if (kind === "apply-keyword" && value) return { kind, value };
  if (kind === "set-tool" && ["mask", "restore", "select", "delete", "pan"].includes(value)) {
    return { kind, value: value as CanvasEditorTool };
  }
  if (kind === "start-masking" && !value) return { kind };
  if (kind === "run-masking" && !value) return { kind };
  if (kind === "wait-idle" && !value) return { kind };
  if (kind === "render-probe" && ["on", "off", "clear"].includes(value)) {
    return { kind, value: value as "on" | "off" | "clear" };
  }
  if (kind === "go-page" && rest.length === 1) {
    const page = Number(rest[0]);
    if (Number.isSafeInteger(page) && page > 0) return { kind, page };
  }
  if (kind === "scroll-to" && (rest.length === 4 || rest.length === 5)) {
    const page = rest.length === 5 ? Number(rest[0]) : undefined;
    const rect = parseTargetRect(rest.length === 5 ? rest.slice(1) : rest);
    if (rect && (page === undefined || (Number.isSafeInteger(page) && page > 0))) {
      return { kind, page, rect };
    }
  }
  if (kind === "inspect-target" && rest.length === 4) {
    const rect = parseTargetRect(rest);
    if (rect) return { kind, rect };
  }
  if (kind === "set-overlay" && (value === "hidden" || value === "visible")) {
    return { kind, visibility: value };
  }
  if (kind === "resolve-review" && rest.length === 2) {
    const [reviewId, requestedAction] = rest;
    const actions: readonly QaReviewAction[] = [
      "mask",
      "exclude",
      "acknowledge",
      "confirm_boundary",
      "confirm_suggested_geometry",
      "confirm_geometry",
      "reanalyze",
    ];
    if (reviewId && actions.includes(requestedAction as QaReviewAction)) {
      return { kind, reviewId, action: requestedAction as QaReviewAction };
    }
  }
  if (kind === "resolve-geometry" && rest.length === 5) {
    const [reviewId, x0, y0, x1, y1] = rest;
    const numbers = [x0, y0, x1, y1].map(Number);
    if (reviewId && numbers.every(Number.isFinite)) {
      const [parsedX0, parsedY0, parsedX1, parsedY1] = numbers;
      if (parsedX0 !== undefined && parsedY0 !== undefined && parsedX1 !== undefined && parsedY1 !== undefined) {
        return { kind, reviewId, rect: { x0: parsedX0, y0: parsedY0, x1: parsedX1, y1: parsedY1 } };
      }
    }
  }
  if (kind === "draw-box" && (rest.length === 4 || rest.length === 5)) {
    const [x0, y0, x1, y1, requestedMode] = rest;
    const numbers = [x0, y0, x1, y1].map(Number);
    const mode = requestedMode ?? "mask";
    if (numbers.every(Number.isFinite) && (mode === "mask" || mode === "restore")) {
      const [parsedX0, parsedY0, parsedX1, parsedY1] = numbers;
      if (parsedX0 !== undefined && parsedY0 !== undefined && parsedX1 !== undefined && parsedY1 !== undefined) {
        return { kind, rect: { x0: parsedX0, y0: parsedY0, x1: parsedX1, y1: parsedY1 }, mode };
      }
    }
  }
  if (kind === "drag-canvas" && rest.length === 4) {
    const [x0, y0, x1, y1] = rest;
    const numbers = [x0, y0, x1, y1].map(Number);
    if (numbers.every(Number.isFinite)) {
      const [parsedX0, parsedY0, parsedX1, parsedY1] = numbers;
      if (parsedX0 !== undefined && parsedY0 !== undefined && parsedX1 !== undefined && parsedY1 !== undefined) {
        return { kind, rect: { x0: parsedX0, y0: parsedY0, x1: parsedX1, y1: parsedY1 } };
      }
    }
  }
  if (kind === "apply-manual" && !value) return { kind };
  if (kind === "open-save-dialog" && !value) return { kind };
  if (kind === "confirm-save" && !value) return { kind };
  if (kind === "wait-save" && !value) return { kind };
  if (kind === "close-success-dialog" && !value) return { kind };
  if (kind === "save-final" && !value) return { kind };
  if (kind === "dump-state" && !value) return { kind };
  throw new Error("QA_DRIVE_COMMAND_INVALID");
}

function nextAnimationFrame(signal?: AbortSignal, requireActualFrame = false): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let fallback: number | null = null;
    let frame: number | null = null;
    const finish = (): void => {
      if (settled) return;
      settled = true;
      if (fallback !== null) window.clearTimeout(fallback);
      if (frame !== null) window.cancelAnimationFrame(frame);
      signal?.removeEventListener("abort", onAbort);
      resolve();
    };
    const fail = (error: unknown): void => {
      if (settled) return;
      settled = true;
      if (fallback !== null) window.clearTimeout(fallback);
      if (frame !== null) window.cancelAnimationFrame(frame);
      signal?.removeEventListener("abort", onAbort);
      reject(error);
    };
    const settle = (): void => finish();
    const onAbort = (): void => fail(qaDriveCancellationError("render-frame"));
    if (signal?.aborted) {
      onAbort();
      return;
    }
    signal?.addEventListener("abort", onAbort, { once: true });
    if (document.visibilityState === "hidden") {
      if (requireActualFrame) return;
      queueMicrotask(settle);
      return;
    }
    try {
      frame = window.requestAnimationFrame(settle);
    } catch (error) {
      fail(error);
      return;
    }
    // A backgrounded WebView may stop delivering animation frames. Yield to
    // the event loop instead of making the QA drive hang forever.
    if (!requireActualFrame) fallback = window.setTimeout(settle, 0);
  });
}

async function waitForRenderSignal(signal?: AbortSignal, requireActualFrame = false): Promise<void> {
  await nextAnimationFrame(signal, requireActualFrame);
  await nextAnimationFrame(signal, requireActualFrame);
}

function setRenderProbe(value: "on" | "off" | "clear"): void {
  const existing = document.getElementById("qa-render-sentinel");
  if (value === "clear") {
    existing?.remove();
    return;
  }
  const sentinel = existing ?? document.createElement("div");
  sentinel.id = "qa-render-sentinel";
  sentinel.hidden = false;
  Object.assign(sentinel.style, {
    position: "fixed",
    left: "8px",
    top: "8px",
    width: "24px",
    height: "24px",
    zIndex: "2147483647",
    pointerEvents: "none",
    backgroundColor: value === "on" ? "rgb(0, 0, 0)" : "rgb(255, 255, 255)",
    border: "1px solid rgb(127, 127, 127)",
  });
  if (!existing) document.body.appendChild(sentinel);
}

type ClientTargetRect = {
  readonly left: number;
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
};

function intersectRects(
  left: number,
  top: number,
  right: number,
  bottom: number,
  viewport: DOMRect,
): { readonly x: number; readonly y: number; readonly width: number; readonly height: number } | null {
  const x = Math.max(left, viewport.left, 0);
  const y = Math.max(top, viewport.top, 0);
  const rightEdge = Math.min(right, viewport.right, window.innerWidth);
  const bottomEdge = Math.min(bottom, viewport.bottom, window.innerHeight);
  if (rightEdge <= x || bottomEdge <= y) return null;
  return { x, y, width: rightEdge - x, height: bottomEdge - y };
}

function targetClientRect(
  overlay: HTMLCanvasElement,
  targetCanvas: { readonly x: number; readonly y: number; readonly width: number; readonly height: number },
): ClientTargetRect {
  const bounds = overlay.getBoundingClientRect();
  const scaleX = overlay.width > 0 ? bounds.width / overlay.width : 0;
  const scaleY = overlay.height > 0 ? bounds.height / overlay.height : 0;
  return {
    left: bounds.left + targetCanvas.x * scaleX,
    top: bounds.top + targetCanvas.y * scaleY,
    right: bounds.left + (targetCanvas.x + targetCanvas.width) * scaleX,
    bottom: bounds.top + (targetCanvas.y + targetCanvas.height) * scaleY,
  };
}

function scrollableAncestors(element: HTMLElement): HTMLElement[] {
  const ancestors: HTMLElement[] = [];
  const scrollingElement = document.scrollingElement as HTMLElement | null;
  let current = element.parentElement;
  while (current) {
    const style = getComputedStyle(current);
    const scrollX = current.scrollWidth > current.clientWidth + 1
      && style.overflowX !== "visible"
      && style.overflowX !== "clip";
    const scrollY = current.scrollHeight > current.clientHeight + 1
      && style.overflowY !== "visible"
      && style.overflowY !== "clip";
    if (scrollX || scrollY) ancestors.push(current);
    current = current.parentElement;
  }
  if (scrollingElement && !ancestors.includes(scrollingElement) && (
    scrollingElement.scrollWidth > scrollingElement.clientWidth + 1
    || scrollingElement.scrollHeight > scrollingElement.clientHeight + 1
  )) {
    ancestors.push(scrollingElement);
  }
  return ancestors;
}

function scrollViewportFor(element: HTMLElement): DOMRect | null {
  if (element === document.scrollingElement) {
    return new DOMRect(0, 0, window.innerWidth, window.innerHeight);
  }
  const bounds = element.getBoundingClientRect();
  return bounds.width > 0 && bounds.height > 0 ? bounds : null;
}

function adjustScrollForTarget(element: HTMLElement, target: ClientTargetRect): boolean {
  const viewport = scrollViewportFor(element);
  if (!viewport) return false;
  const margin = 2;
  const deltaX = target.left < viewport.left + margin
    ? target.left - viewport.left - margin
    : target.right > viewport.right - margin
      ? target.right - viewport.right + margin
      : 0;
  const deltaY = target.top < viewport.top + margin
    ? target.top - viewport.top - margin
    : target.bottom > viewport.bottom - margin
      ? target.bottom - viewport.bottom + margin
      : 0;
  const maxScrollLeft = Math.max(0, element.scrollWidth - element.clientWidth);
  const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
  const nextLeft = Math.max(0, Math.min(maxScrollLeft, element.scrollLeft + deltaX));
  const nextTop = Math.max(0, Math.min(maxScrollTop, element.scrollTop + deltaY));
  const changed = Math.abs(nextLeft - element.scrollLeft) > 0.5
    || Math.abs(nextTop - element.scrollTop) > 0.5;
  if (changed) {
    element.scrollLeft = nextLeft;
    element.scrollTop = nextTop;
  }
  return changed;
}

function visibleTargetIntersection(
  overlay: HTMLCanvasElement,
  target: ClientTargetRect,
): { readonly x: number; readonly y: number; readonly width: number; readonly height: number } | null {
  let left = 0;
  let top = 0;
  let right = window.innerWidth;
  let bottom = window.innerHeight;
  let current = overlay.parentElement;
  while (current) {
    if (current === document.scrollingElement) break;
    const style = getComputedStyle(current);
    const clipsX = style.overflowX !== "visible";
    const clipsY = style.overflowY !== "visible";
    if (clipsX || clipsY) {
      const bounds = current.getBoundingClientRect();
      if (clipsX) {
        left = Math.max(left, bounds.left);
        right = Math.min(right, bounds.right);
      }
      if (clipsY) {
        top = Math.max(top, bounds.top);
        bottom = Math.min(bottom, bounds.bottom);
      }
    }
    current = current.parentElement;
  }
  return intersectRects(
    target.left,
    target.top,
    target.right,
    target.bottom,
    new DOMRect(left, top, Math.max(0, right - left), Math.max(0, bottom - top)),
  );
}

function scrollOffsetSnapshot(overlay: HTMLCanvasElement | null): readonly {
  readonly tag: string;
  readonly id: string;
  readonly className: string;
  readonly left: number;
  readonly top: number;
}[] {
  const entries: {
    readonly tag: string;
    readonly id: string;
    readonly className: string;
    readonly left: number;
    readonly top: number;
  }[] = [];
  if (!overlay) return entries;
  let current = overlay.parentElement;
  while (current) {
    if (
      current.scrollLeft !== 0
      || current.scrollTop !== 0
      || current.scrollWidth > current.clientWidth + 1
      || current.scrollHeight > current.clientHeight + 1
    ) {
      entries.push({
        tag: current.tagName,
        id: current.id,
        className: typeof current.className === "string" ? current.className : "",
        left: current.scrollLeft,
        top: current.scrollTop,
      });
    }
    current = current.parentElement;
  }
  return entries;
}

function inspectOverlayPixels(
  overlay: HTMLCanvasElement,
  rect: PdfTargetRect,
  scale: number,
): QaTargetInspection["overlay"] {
  const context = overlay.getContext("2d");
  if (!context) return null;
  const left = Math.max(0, Math.min(overlay.width, Math.floor(Math.min(rect.x0, rect.x1) * scale)));
  const top = Math.max(0, Math.min(overlay.height, Math.floor(Math.min(rect.y0, rect.y1) * scale)));
  const right = Math.max(0, Math.min(overlay.width, Math.ceil(Math.max(rect.x0, rect.x1) * scale)));
  const bottom = Math.max(0, Math.min(overlay.height, Math.ceil(Math.max(rect.y0, rect.y1) * scale)));
  if (right <= left || bottom <= top) {
    return {
      paintedPixels: 0,
      blackPixels: 0,
      pendingPixels: 0,
      canvasRect: { x: left, y: top, width: 0, height: 0 },
    };
  }
  let paintedPixels = 0;
  let blackPixels = 0;
  let pendingPixels = 0;
  const width = right - left;
  const height = bottom - top;
  const area = width * height;
  const stride = area <= MAX_EXACT_OVERLAY_PIXELS
    ? 1
    : Math.max(1, Math.ceil(Math.sqrt(area / MAX_SAMPLED_OVERLAY_PIXELS)), Math.ceil(height / MAX_SAMPLED_ROWS));
  const inspectPixel = (red: number, green: number, blue: number, alpha: number): void => {
    if (alpha === 0) return;
    paintedPixels += 1;
    if (alpha >= 200 && Math.max(red, green, blue) < 64) {
      blackPixels += 1;
    } else if (red > blue + 20 && green > blue + 10 && red >= green && alpha > 20) {
      pendingPixels += 1;
    }
  };
  try {
    if (stride === 1) {
      const pixels = context.getImageData(left, top, width, height).data;
      for (let index = 0; index < pixels.length; index += 4) {
        inspectPixel(
          pixels[index] ?? 0,
          pixels[index + 1] ?? 0,
          pixels[index + 2] ?? 0,
          pixels[index + 3] ?? 0,
        );
      }
    } else {
      for (let y = top; y < bottom; y += stride) {
        const row = context.getImageData(left, y, width, 1).data;
        for (let x = 0; x < width; x += stride) {
          const index = x * 4;
          inspectPixel(
            row[index] ?? 0,
            row[index + 1] ?? 0,
            row[index + 2] ?? 0,
            row[index + 3] ?? 0,
          );
        }
      }
    }
  } catch {
    return null;
  }
  return {
    paintedPixels,
    blackPixels,
    pendingPixels,
    canvasRect: { x: left, y: top, width: right - left, height: bottom - top },
  };
}

async function scrollCanvasTarget(
  controller: ApplicationController,
  rect: PdfTargetRect,
): Promise<QaTargetInspection> {
  const overlay = workspaceCanvasMount().overlay;
  if (!overlay) throw new Error("QA_DRIVE_SCROLL_TARGET_UNAVAILABLE");
  const scale = Number(controller.state.scale);
  const container = overlay.closest(".dm-canvas__scroll") as HTMLElement | null;
  if (!container || !Number.isFinite(scale) || scale <= 0 || overlay.width <= 0 || overlay.height <= 0) {
    throw new Error("QA_DRIVE_SCROLL_TARGET_UNAVAILABLE");
  }
  const leftPdf = Math.min(rect.x0, rect.x1);
  const topPdf = Math.min(rect.y0, rect.y1);
  const rightPdf = Math.max(rect.x0, rect.x1);
  const bottomPdf = Math.max(rect.y0, rect.y1);
  if (![leftPdf, topPdf, rightPdf, bottomPdf].every(Number.isFinite) || rightPdf <= leftPdf || bottomPdf <= topPdf) {
    throw new Error("QA_DRIVE_TARGET_RECT_INVALID");
  }
  const targetCanvas = {
    x: leftPdf * scale,
    y: topPdf * scale,
    width: (rightPdf - leftPdf) * scale,
    height: (bottomPdf - topPdf) * scale,
  };
  const initialOverlayBounds = overlay.getBoundingClientRect();
  if (initialOverlayBounds.width <= 0 || initialOverlayBounds.height <= 0) {
    throw new Error("QA_DRIVE_SCROLL_TARGET_UNAVAILABLE");
  }
  const scrollables = scrollableAncestors(overlay);
  for (let round = 0; round < 4; round += 1) {
    let changed = false;
    for (const scrollable of scrollables) {
      changed = adjustScrollForTarget(scrollable, targetClientRect(overlay, targetCanvas)) || changed;
    }
    if (!changed) break;
    await waitForRenderSignal();
  }
  await waitForRenderSignal();

  return inspectCurrentCanvasTarget(controller, rect);
}

function inspectCurrentCanvasTarget(
  controller: ApplicationController,
  rect: PdfTargetRect,
): QaTargetInspection {
  const overlay = workspaceCanvasMount().overlay;
  if (!overlay) throw new Error("QA_DRIVE_SCROLL_TARGET_UNAVAILABLE");
  const scale = Number(controller.state.scale);
  if (!Number.isFinite(scale) || scale <= 0 || overlay.width <= 0 || overlay.height <= 0) {
    throw new Error("QA_DRIVE_SCROLL_TARGET_UNAVAILABLE");
  }
  const leftPdf = Math.min(rect.x0, rect.x1);
  const topPdf = Math.min(rect.y0, rect.y1);
  const rightPdf = Math.max(rect.x0, rect.x1);
  const bottomPdf = Math.max(rect.y0, rect.y1);
  if (![leftPdf, topPdf, rightPdf, bottomPdf].every(Number.isFinite) || rightPdf <= leftPdf || bottomPdf <= topPdf) {
    throw new Error("QA_DRIVE_TARGET_RECT_INVALID");
  }
  const targetCanvas = {
    x: leftPdf * scale,
    y: topPdf * scale,
    width: (rightPdf - leftPdf) * scale,
    height: (bottomPdf - topPdf) * scale,
  };
  const targetBounds = targetClientRect(overlay, targetCanvas);
  const targetLeft = targetBounds.left;
  const targetTop = targetBounds.top;
  const targetRight = targetBounds.right;
  const targetBottom = targetBounds.bottom;
  const targetArea = Math.max(0, targetRight - targetLeft) * Math.max(0, targetBottom - targetTop);
  const intersection = visibleTargetIntersection(overlay, targetBounds);
  const visibleArea = intersection ? intersection.width * intersection.height : 0;
  return {
    page: Math.max(0, (controller.state.currentResultPage || 1) - 1),
    rect,
    visible: visibleArea > 0,
    targetArea,
    visibleArea,
    visibleRatio: targetArea > 0 ? visibleArea / targetArea : 0,
    intersection,
    overlay: inspectOverlayPixels(overlay, rect, scale),
  };
}

type PaintedOverlaySummary = {
  readonly pixelCount: number;
  readonly bounds: { readonly x: number; readonly y: number; readonly width: number; readonly height: number } | null;
};

const MAX_EXACT_OVERLAY_PIXELS = 1_000_000;
const MAX_SAMPLED_OVERLAY_PIXELS = 500_000;
const MAX_SAMPLED_ROWS = 2_048;

function inspectPaintedOverlay(overlay: HTMLCanvasElement | null): PaintedOverlaySummary {
  if (!overlay || overlay.width <= 0 || overlay.height <= 0) {
    return { pixelCount: 0, bounds: null };
  }
  const context = overlay.getContext("2d");
  if (!context) return { pixelCount: 0, bounds: null };
  const exact = overlay.width * overlay.height <= MAX_EXACT_OVERLAY_PIXELS;
  const stride = exact
    ? 1
    : Math.max(
      1,
      Math.ceil(Math.sqrt((overlay.width * overlay.height) / MAX_SAMPLED_OVERLAY_PIXELS)),
      Math.ceil(overlay.height / MAX_SAMPLED_ROWS),
    );
  let pixelCount = 0;
  let left = overlay.width;
  let top = overlay.height;
  let right = -1;
  let bottom = -1;
  const include = (x: number, y: number, cellWidth: number, cellHeight: number): void => {
    pixelCount += 1;
    left = Math.min(left, x);
    top = Math.min(top, y);
    right = Math.max(right, Math.min(overlay.width, x + cellWidth) - 1);
    bottom = Math.max(bottom, Math.min(overlay.height, y + cellHeight) - 1);
  };
  try {
    if (exact) {
      const pixels = context.getImageData(0, 0, overlay.width, overlay.height).data;
      for (let index = 3; index < pixels.length; index += 4) {
        if (pixels[index] === 0) continue;
        const pixel = (index - 3) / 4;
        include(pixel % overlay.width, Math.floor(pixel / overlay.width), 1, 1);
      }
    } else {
      for (let y = 0; y < overlay.height; y += stride) {
        const row = context.getImageData(0, y, overlay.width, 1).data;
        for (let x = 0; x < overlay.width; x += stride) {
          if (row[x * 4 + 3] === 0) continue;
          include(x, y, stride, stride);
        }
      }
    }
  } catch {
    return { pixelCount: 0, bounds: null };
  }
  return {
    pixelCount,
    bounds: right >= left && bottom >= top
      ? { x: left, y: top, width: right - left + 1, height: bottom - top + 1 }
      : null,
  };
}

function stateSnapshot(controller: ApplicationController, commandResult: QaCommandResult | null = null) {
  return measureQaDriveStageSync("stateSnapshot", () => {
    const overlay = workspaceCanvasMount().overlay;
    const workspace = workspaceStateSnapshot();
    const reviewItems = workspace.reviewState.status === "valid" ? workspace.reviewState.items : [];
    const regions = new Map(workspace.report?.analysisManifest?.regions.map((region) => [region.regionId, region]) ?? []);
    const manifest = workspace.report?.analysisManifest;
    const occurrenceById = new Map(manifest?.occurrences.map((occurrence) => [occurrence.occurrenceId, occurrence]) ?? []);
    const pendingReviews = workspace.report?.reviewQueue?.filter((review) => review.status === "pending").map((review) => ({
      reviewId: review.reviewId,
      kind: review.kind,
      page: review.pageStart + 1,
      regionKind: review.kind === "region_geometry" ? regions.get(review.targetId)?.kind ?? null : null,
    })) ?? [];
    const pendingTargets = manifest?.reviewItems
      .filter((review) => review.status === "pending")
      .flatMap((review) => {
        const occurrence = occurrenceById.get(review.targetId);
        const region = regions.get(review.targetId);
        const rects = occurrence?.rects ?? region?.rects ?? [];
        return rects.length > 0
          ? [{ reviewId: review.reviewId, kind: review.kind, page: review.pageStart, rects }]
          : [];
      }) ?? [];
    const manifestMaskCounts = dashboardMaskCounts(workspace.report);
    const scale = Number(controller.state.scale);
    const overlayWidth = overlay?.width ?? 0;
    const overlayHeight = overlay?.height ?? 0;
    const pageSize = Number.isFinite(scale) && scale > 0 && overlayWidth > 0 && overlayHeight > 0
      ? { width: overlayWidth / scale, height: overlayHeight / scale }
      : null;
    const finiteOrNull = (value: number): number | null => Number.isFinite(value) ? value : null;
    const bounds = overlay?.getBoundingClientRect();
    const scrollContainer = overlay?.closest(".dm-canvas__scroll") as HTMLElement | null;
    const scrollBounds = scrollContainer?.getBoundingClientRect();
    const screen = window.screen as Screen & { left?: number; top?: number; availLeft?: number; availTop?: number };
    const paintedOverlay = inspectPaintedOverlay(overlay);
    const overlayPaintedPixelCount = paintedOverlay.pixelCount;
    const paintedBounds = paintedOverlay.bounds;
    return {
    targetInspection: commandResult?.targetInspection ?? null,
    saveFinal: commandResult?.saveFinal ?? null,
    finalizationSuccess: {
      visible: workspace.finalizationSuccessDialog.visible,
      statusLabel: workspace.finalizationSuccessDialog.statusLabel,
      statusTone: workspace.finalizationSuccessDialog.statusTone,
      warnings: workspace.finalizationSuccessDialog.warnings,
    },
    documentLoaded: Boolean(controller.state.documentProvenance.original.path),
    profile: currentSettings().profile,
    activeRunKind: controller.state.activeRunKind,
    maskingRunning: controller.state.maskingRunning,
    status: document.getElementById("status")?.textContent ?? "",
    analysisRevision: workspace.report?.analysisManifest?.analysisRevision ?? null,
    occurrences: manifest?.occurrences.map((occurrence) => ({
      occurrenceId: occurrence.occurrenceId,
      page: occurrence.page,
      rects: occurrence.rects,
      category: occurrence.category,
      proposedAction: occurrence.proposedAction,
      state: occurrence.state,
      expectedTextHash: occurrence.expectedTextHash,
    })) ?? [],
    pendingOccurrenceCount: manifest?.occurrences.filter((occurrence) =>
      occurrence.proposedAction === "review" && occurrence.state === "review_required",
    ).length ?? 0,
    excludedOccurrenceCount: manifest?.occurrences.filter((occurrence) => occurrence.proposedAction === "exclude").length ?? 0,
    pendingTargets,
    reviewCardCount: reviewItems.filter((item) => item.kind !== "region_geometry").length,
    advancedGeometryCount: reviewItems.filter((item) => item.kind === "region_geometry" && item.status === "pending").length,
    pendingReviews,
    boxes: controller.state.boxes.map((box) => ({ page: box.page, mode: box.mode, tag: box.tag ?? "MANUAL" })),
    lastDragRejection: controller.state.lastDragRejection,
    manualActionModes: workspace.report?.analysisManifest?.manualActions.map((action) => action.mode) ?? [],
    manualActions: manifest?.manualActions.map((action) => ({
      actionId: action.actionId,
      analysisRevision: action.analysisRevision,
      page: action.page,
      rects: action.rects,
      mode: action.mode,
      sourceKind: action.sourceKind,
      linkedOccurrenceId: action.linkedOccurrenceId,
      expectedTextHash: action.expectedTextHash,
      restoreAuthorizationHash: action.restoreAuthorizationHash,
    })) ?? [],
    maskCounts: {
      automaticMaskCount: manifestMaskCounts.automaticMaskCount,
      manualMaskCount: manifestMaskCounts.manualMaskCount,
      manualRestoreCount: manifestMaskCounts.manualRestoreCount,
      effectiveMaskCount: manifestMaskCounts.effectiveMaskCount,
    },
    firstMaskingPage: workspace.firstMaskingPage === null ? null : workspace.firstMaskingPage + 1,
    currentPage: workspace.currentCanvasPage + 1,
    stagedOverlay: {
      maskCount: Number(overlay?.dataset.stagedMaskCount ?? 0),
      restoreCount: Number(overlay?.dataset.stagedRestoreCount ?? 0),
      blockedRestoreCount: Number(overlay?.dataset.blockedRestoreCount ?? 0),
      style: overlay?.dataset.stagedOverlayStyle ?? "none",
      restoreState: overlay?.dataset.stagedRestoreState ?? "none",
    },
    overlayPaintedPixelCount,
    overlayBounds: bounds ? { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height } : null,
    scrollViewport: scrollBounds
      ? { x: scrollBounds.x, y: scrollBounds.y, width: scrollBounds.width, height: scrollBounds.height }
      : null,
    scrollPosition: scrollContainer
      ? { left: scrollContainer.scrollLeft, top: scrollContainer.scrollTop }
      : null,
    documentScroll: document.scrollingElement
      ? { left: document.scrollingElement.scrollLeft, top: document.scrollingElement.scrollTop }
      : null,
    scrollOffsets: scrollOffsetSnapshot(overlay),
    paintedBounds,
    overlayPixels: overlay ? { width: overlay.width, height: overlay.height } : null,
    overlayVisibility: overlay?.dataset.qaOverlayVisibility ?? "visible",
    renderedPdf: Boolean(
      controller.state.resultDoc
      && overlay
      && overlay.width > 0
      && overlay.height > 0,
    ),
    pageCount: controller.state.resultDoc?.numPages ?? controller.state.origDoc?.numPages ?? null,
    currentResultPage: controller.state.currentResultPage,
    currentOrigPage: controller.state.currentOrigPage,
    scale: finiteOrNull(scale),
    pageSize,
    screenOrigin: { x: finiteOrNull(window.screenX), y: finiteOrNull(window.screenY) },
    contentScreenOrigin: {
      x: finiteOrNull(window.screenX + Math.max(0, (window.outerWidth - window.innerWidth) / 2)),
      y: finiteOrNull(window.screenY + Math.max(0, window.outerHeight - window.innerHeight)),
    },
    screenBounds: {
      x: finiteOrNull(screen.left ?? screen.availLeft ?? 0),
      y: finiteOrNull(screen.top ?? screen.availTop ?? 0),
      width: finiteOrNull(screen.width),
      height: finiteOrNull(screen.height),
    },
    screenSize: { width: finiteOrNull(window.screen.width), height: finiteOrNull(window.screen.height) },
    devicePixelRatio: finiteOrNull(window.devicePixelRatio),
    saveGateState: document.getElementById("final-save-readiness")?.getAttribute("data-state") ?? null,
    finalSaveButtonBounds: (() => {
      const button = document.getElementById("btn-dialog-save-all");
      if (!(button instanceof HTMLElement) || button.hidden || button.getClientRects().length === 0) return null;
      const bounds = button.getBoundingClientRect();
      return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height };
    })(),
      viewport: { width: window.innerWidth, height: window.innerHeight },
    };
  });
}

function dispatchCanvasMouseEvent(overlay: HTMLCanvasElement, type: "mousedown" | "mousemove" | "mouseup", x: number, y: number): void {
  const bounds = overlay.getBoundingClientRect();
  const scaleX = overlay.width > 0 && bounds.width > 0 ? bounds.width / overlay.width : 1;
  const scaleY = overlay.height > 0 && bounds.height > 0 ? bounds.height / overlay.height : 1;
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    view: window,
    button: 0,
    buttons: type === "mouseup" ? 0 : 1,
    clientX: bounds.left + x * scaleX,
    clientY: bounds.top + y * scaleY,
  });
  (type === "mouseup" ? window : overlay).dispatchEvent(event);
}

function dispatchCanvasDrag(rect: { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number }): void {
  const overlay = workspaceCanvasMount().overlay;
  dispatchCanvasMouseEvent(overlay, "mousedown", rect.x0, rect.y0);
  for (const progress of [0.2, 0.4, 0.6, 0.8, 1]) {
    dispatchCanvasMouseEvent(
      overlay,
      "mousemove",
      rect.x0 + (rect.x1 - rect.x0) * progress,
      rect.y0 + (rect.y1 - rect.y0) * progress,
    );
  }
  dispatchCanvasMouseEvent(overlay, "mouseup", rect.x1, rect.y1);
}

function qaErrorCode(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return message.match(/[A-Z][A-Z0-9_]{2,}/)?.[0] ?? fallback;
}

async function attachFinalOutputStat(outcome: FinalSaveOutcome): Promise<FinalSaveOutcome> {
  if (outcome.status !== "ok" || !outcome.finalPath) return outcome;
  try {
    const fileStat = await invoke<{ readonly exists: boolean; readonly size: number }>("qa_stat_final_output", {
      path: outcome.finalPath,
    });
    if (fileStat.exists !== true || !Number.isSafeInteger(fileStat.size) || fileStat.size <= 0) {
      return {
        ...outcome,
        status: "failed",
        stage: "file-stat",
        errorCode: outcome.errorCode ?? "QA_DRIVE_FINAL_OUTPUT_STAT_FAILED",
        errorField: "file-stat",
        steps: {
          ...outcome.steps,
          fileStat: { status: "failed", code: "QA_DRIVE_FINAL_OUTPUT_STAT_FAILED" },
        },
      };
    }
    return {
      ...outcome,
      stage: outcome.status === "ok" ? "complete" : outcome.stage,
      steps: {
        ...outcome.steps,
        fileStat: { status: "ok", code: null },
      },
    };
  } catch (error) {
    const code = qaErrorCode(error, "QA_DRIVE_FINAL_OUTPUT_STAT_FAILED");
    return {
      ...outcome,
      status: "failed",
      stage: "file-stat",
      errorCode: outcome.errorCode ?? code,
      errorField: "file-stat",
      steps: {
        ...outcome.steps,
        fileStat: { status: "failed", code },
      },
    };
  }
}

async function execute(
  context: QaDriveCommandContext,
  controller: ApplicationController,
  command: QaCommand,
): Promise<QaCommandResult | undefined> {
  switch (command.kind) {
    case "open": {
      try {
        const path = await runCommandStage(
          context,
          "qa_register_input_document",
          () => invoke<string>("qa_register_input_document", { path: command.value }),
        );
        const loaded = await runCommandStage(
          context,
          "canvas-render",
          // The transactional loader remains the single open path:
          // controller.loadCanvasWorkspacePdf(path)
          (signal) => controller.loadCanvasWorkspacePdf(path, undefined, signal),
        );
        if (!loaded || controller.state.documentProvenance.original.path !== path) {
          throw new Error("QA_DRIVE_DOCUMENT_LOAD_FAILED");
        }
        await runCommandStage(
          context,
          "maybeApplyDefaultOutputDir",
          () => controller.maybeApplyDefaultOutputDir([path]),
        );
        if (controller.state.documentProvenance.original.path !== path) {
          throw new Error("QA_DRIVE_DOCUMENT_STALE");
        }
        await runCommandStage(context, "activateAppScreen", () => controller.activateAppScreen("documents"));
        await runCommandStage(
          context,
          "setCanvasMode",
          () => controller.setCanvasMode(true, { allowEmptyCanvas: true }),
        );
        await runCommandStage(context, "setStatus", () => controller.setStatus("원문 PDF 로드 완료"));
        await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      } catch (error) {
        if (error instanceof Error && error.message === "QA_DRIVE_DOCUMENT_STALE") throw error;
        if (isQaDriveTimeoutError(error)) {
          const message = error instanceof Error ? error.message : String(error);
          if (message.includes(":stage=")) throw error;
          throw new Error(`${message}:stage=${context.stage}`);
        }
        throw new Error(`${qaErrorCode(error, "QA_DRIVE_DOCUMENT_LOAD_FAILED")}:stage=${context.stage}`);
      }
      return;
    }
    case "set-profile": {
      await runCommandStage(context, "set-profile", () => {
        const profile = MASKING_PROFILES.find((candidate) => candidate === command.value);
        if (profile === undefined) {
          throw new Error("QA_DRIVE_PROFILE_INVALID");
        }
        updateSettings({ profile });
      });
      return;
    }
    case "apply-keyword":
      controller.writeKeywordList([command.value]);
      await runCommandStage(
        context,
        "runMaskingForSelectedDocument",
        () => controller.runMaskingForSelectedDocument(),
      );
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return;
    case "set-tool":
      await runCommandStage(context, "set-tool", () => controller.setActiveCanvasTool(command.value));
      return;
    case "run-masking":
      await runCommandStage(
        context,
        "runMaskingForSelectedDocument",
        () => controller.runMaskingForSelectedDocument(),
      );
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return;
    case "start-masking":
      await runCommandStage(context, "startMasking", () => {
        pendingMaskingRun = controller.runMaskingForSelectedDocument();
        void pendingMaskingRun.catch(() => undefined);
      });
      return;
    case "wait-idle":
      if (pendingMaskingRun !== null) {
        const run = pendingMaskingRun;
        pendingMaskingRun = null;
        await runCommandStage(context, "pendingMaskingRun", () => run);
        await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
        return;
      }
      if (controller.state.maskingRunning || controller.state.savingInFlight) throw new Error("QA_DRIVE_NOT_IDLE");
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return;
    case "render-probe":
      await runCommandStage(
        context,
        "render-probe-frame",
        (signal) => {
          setRenderProbe(command.value);
          return waitForRenderSignal(signal, command.value !== "clear");
        },
      );
      return;
    case "go-page":
      await runCommandStage(context, "goToReviewPage", () => controller.goToReviewPage(command.page - 1));
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return;
    case "scroll-to":
      const targetPage = command.page;
      if (targetPage !== undefined) {
        await runCommandStage(context, "goToReviewPage", () => controller.goToReviewPage(targetPage - 1));
      }
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return {
        targetInspection: await runCommandStage(
          context,
          "scrollCanvasTarget",
          () => scrollCanvasTarget(controller, command.rect),
        ),
      };
    case "inspect-target":
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return {
        targetInspection: await runCommandStage(
          context,
          "inspectCanvasTarget",
          () => inspectCurrentCanvasTarget(controller, command.rect),
        ),
      };
    case "set-overlay": {
      await runCommandStage(context, "set-overlay", () => {
        const overlay = workspaceCanvasMount().overlay;
        overlay.style.visibility = command.visibility;
        overlay.dataset.qaOverlayVisibility = command.visibility;
      });
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return;
    }
    case "resolve-review":
      await runCommandStage(
        context,
        "resolveReviewFromRail",
        () => controller.resolveReviewFromRail(command.reviewId, command.action, null),
      );
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return;
    case "resolve-geometry": {
      const workspace = await runCommandStage(context, "workspaceStateSnapshot", () => workspaceStateSnapshot());
      const items = workspace.reviewState.status === "valid" ? workspace.reviewState.items : [];
      const review = command.reviewId === "first"
        ? items.find((item) => item.kind === "region_geometry" && item.status === "pending")
        : items.find((item) => item.reviewId === command.reviewId && item.kind === "region_geometry" && item.status === "pending");
      if (!review) throw new Error("QA_DRIVE_GEOMETRY_REVIEW_NOT_FOUND");
      await runCommandStage(
        context,
        "resolveReviewFromRail",
        () => controller.resolveReviewFromRail(review.reviewId, "confirm_geometry", null),
      );
      const draft = controller.state.geometryDraft;
      if (!draft) throw new Error("QA_DRIVE_GEOMETRY_DRAFT_NOT_STARTED");
      controller.state.boxes.push({ page: draft.page, ...command.rect, mode: "mask", tag: draft.owner });
      await runCommandStage(
        context,
        "resolveReviewFromRail",
        () => controller.resolveReviewFromRail(review.reviewId, "confirm_geometry", null),
      );
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return;
    }
    case "draw-box": {
      await runCommandStage(context, "draw-box", () => {
        const page = Math.max(0, (controller.state.currentResultPage || 1) - 1);
        const tag = command.mode === "mask" && controller.state.geometryDraft
          ? controller.state.geometryDraft.owner
          : "MANUAL";
        controller.state.boxes.push({ page, ...command.rect, mode: command.mode, tag });
        controller.state.documentEditRevision += 1;
        controller.state.selectedCanvasBoxIndex = controller.state.boxes.length - 1;
      });
      return;
    }
    case "drag-canvas":
      await runCommandStage(context, "drag-canvas", () => dispatchCanvasDrag(command.rect));
      return;
    case "apply-manual":
      await runCommandStage(
        context,
        "applyPendingManualBoxes",
        () => controller.applyPendingManualBoxes("수동마스킹실행"),
      );
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return;
    case "open-save-dialog":
      await runCommandStage(context, "openFinalSaveDialog", () => controller.openFinalSaveDialog());
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return;
    case "confirm-save": {
      await runCommandStage(context, "confirm-save-dialog", () => {
        const dialog = document.getElementById("final-save-dialog");
        const button = document.getElementById("btn-dialog-save-all") as HTMLButtonElement | null;
        if (!dialog || dialog.hidden || !button || button.disabled) throw new Error("QA_DRIVE_SAVE_DIALOG_NOT_CONFIRMABLE");
        controller.closeFinalSaveDialog();
      });
      const saveFinal = await runCommandStage(
        context,
        "saveFinalOutput",
        () => controller.saveFinalOutput({ warningsConfirmed: true }),
      );
      return {
        saveFinal: await runCommandStage(
          context,
          "qa_stat_final_output",
          () => attachFinalOutputStat(saveFinal),
        ),
      };
    }
    case "wait-save": {
      const saveFinal = await runCommandStage(context, "waitForSaveResult", async (signal) => {
        while (!signal.aborted && (controller.state.savingInFlight || controller.lastFinalSaveOutcome() === null)) {
          await new Promise<void>((resolve) => window.setTimeout(resolve, 25));
        }
        if (signal.aborted) throw qaDriveCancellationError("waitForSaveResult");
        const outcome = controller.lastFinalSaveOutcome();
        if (!outcome) throw new Error("QA_DRIVE_SAVE_RESULT_UNAVAILABLE");
        return outcome;
      });
      return {
        saveFinal: await runCommandStage(
          context,
          "qa_stat_final_output",
          () => attachFinalOutputStat(saveFinal),
        ),
      };
    }
    case "close-success-dialog":
      await runCommandStage(context, "closeFinalizationSuccess", () => controller.closeFinalizationSuccess());
      await runCommandStage(context, "waitForRenderSignal", waitForRenderSignal);
      return;
    case "save-final": {
      const readiness = document.getElementById("final-save-readiness")?.getAttribute("data-state");
      if (readiness !== "ready") {
        throw new Error("QA_DRIVE_SAVE_FINAL_REQUIRES_CONFIRM_SAVE");
      }
      const saveFinal = await runCommandStage(
        context,
        "saveFinalOutput",
        () => controller.saveFinalOutput({ warningsConfirmed: true }),
      );
      return {
        saveFinal: await runCommandStage(
          context,
          "qa_stat_final_output",
          () => attachFinalOutputStat(saveFinal),
        ),
      };
    }
    case "dump-state":
      return;
  }
}

let installation: Promise<void> | null = null;

function currentController(): ApplicationController {
  const controller = applicationController();
  if (controller === null) throw new Error("QA_DRIVE_FRONTEND_UNAVAILABLE");
  return controller;
}

export function installQaDrive(): void {
  if (installation !== null) return;
  const commandListener = listen<QaDriveEvent>("qa-drive-command", async (event) => {
    const { id, command } = event.payload;
    const context = createCommandContext(command, () => {
      applicationController()?.invalidateLifecycle();
    });
    activeCommandContexts.set(id, context);
    try {
      const parsedCommand = await runCommandStage(context, "parse_command", () => parseCommand(command));
      const controller = await runCommandStage(context, "currentController", () => currentController());
      const commandResult = await execute(context, controller, parsedCommand);
      const state = await runCommandStage(
        context,
        "stateSnapshot",
        () => stateSnapshot(controller, commandResult ?? null),
      );
      await respond({ id, ok: true, state }, context);
    } catch (error) {
      const message = error instanceof Error ? error.message : "QA_DRIVE_COMMAND_FAILED";
      await respond({ id, ok: false, error: message }, context);
    } finally {
      if (activeCommandContexts.get(id) === context) activeCommandContexts.delete(id);
      releaseCommandContext(context);
    }
  });
  const cancelListener = listen<QaDriveEvent>("qa-drive-cancel", (event) => {
    const context = activeCommandContexts.get(event.payload.id);
    context?.cancel("native_timeout");
  });
  installation = Promise.all([commandListener, cancelListener]).then(async () => {
    await respond({ id: "ready", ok: true });
  }).catch(() => undefined).then(() => undefined);
}
