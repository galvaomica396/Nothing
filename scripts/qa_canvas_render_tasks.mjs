import assert from "node:assert/strict";

import { createCanvasRenderController } from "../src/features/canvas-workbench/canvasRenderController.ts";

const listeners = new Map();
globalThis.window = {
  innerWidth: 1400,
  addEventListener(type, listener) {
    listeners.set(type, listener);
  },
};
globalThis.document = { documentElement: {} };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });

function element() {
  return {
    width: 0,
    height: 0,
    style: {},
    parentElement: null,
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    closest() { return null; },
    getBoundingClientRect() { return { left: 0, top: 0 }; },
  };
}

function context() {
  return {
    renderedPage: 0,
    clearRect() {},
    fillRect() {},
    strokeRect() {},
    fillText() {},
    setLineDash() {},
  };
}

function deferredRender(pageNumber, renderContext, tasks) {
  let resolvePromise;
  let rejectPromise;
  const promise = new Promise((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  const task = {
    promise,
    cancelCalls: 0,
    cancel() {
      this.cancelCalls += 1;
      const error = new Error("cancelled");
      error.name = "RenderingCancelledException";
      rejectPromise(error);
    },
    finish() {
      renderContext.renderedPage = pageNumber;
      resolvePromise();
    },
  };
  tasks.push(task);
  return task;
}

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  throw new Error("render task was not created");
}

const origCanvas = element();
const resultCanvas = element();
const overlay = element();
const origWrap = element();
const resultWrap = element();
const pdfCompareView = element();
const origCtx = context();
const resultCtx = context();
const overlayCtx = context();
const tasks = [];
let metaUpdates = 0;

const origDoc = {
  numPages: 2,
  async getPage(pageNumber) {
    return {
      getViewport: ({ scale }) => ({ width: pageNumber * 100 * scale, height: pageNumber * 120 * scale }),
      render: ({ canvasContext }) => deferredRender(pageNumber, canvasContext, tasks),
    };
  },
};
const state = {
  scale: 1,
  boxes: [],
  mode: "mask",
  currentOrigPage: 1,
  currentResultPage: 1,
  origDoc,
  resultDoc: null,
  selectedCanvasBoxIndex: -1,
  lastPreviewDiagnostics: "",
  syncPages: false,
};
const controller = createCanvasRenderController({
  state,
  origCanvas,
  resultCanvas,
  overlay,
  origWrap,
  resultWrap,
  pdfCompareView,
  origCtx,
  resultCtx,
  octx: overlayCtx,
  clampPage: (page) => Math.max(1, Math.min(page, 2)),
  updateMeta: () => { metaUpdates += 1; },
  getActiveCanvasTool: () => "select",
});

const first = controller.renderCompare();
await waitFor(() => tasks.length === 1);
state.currentOrigPage = 2;
state.scale = 2;
const second = controller.renderCompare();
await waitFor(() => tasks.length === 2 || tasks[0].cancelCalls === 1);
assert.equal(tasks[0].cancelCalls, 1, "a newer render must cancel the previous task");
await waitFor(() => tasks.length === 2);
tasks[1].finish();
await Promise.all([first, second]);

assert.equal(origCtx.renderedPage, 2, "the last requested page must own the final pixels");
assert.equal(origCanvas.width, 400, "the last requested scale must own the canvas dimensions");
assert.equal(metaUpdates, 1, "only the latest render generation may update metadata");
console.log("[qa:canvas-render] cancellation and latest-request ownership passed");
