// Document batch queue controller (docs/CODE_REVIEW_2026-07-04.md
// composition root 분리: 배치 큐 모듈).
//
// Owns the batch-queue DOM rendering and per-item lifecycle (load / retry /
// process / open output). Pure queue helpers stay in src/batchQueue.ts; this
// module is the controller layer that the composition root wires with injected DOM
// elements, the shared mutable run state, and orchestration callbacks.

import { appendBatchDocuments, batchActionState, summarizeBatchItems } from "../../batchQueue";
import type { BatchItem, BatchStatus } from "../../batchQueue";
import { latestGeneratedPath } from "../../state/documentProvenance";
import type { ApplicationSessionState } from "../../app/compositionRoot";

// The slice of the composition root shared `state` object this controller reads and
// mutates. The full closure state object satisfies this structurally.
export type DocumentBatchState = Pick<
  ApplicationSessionState,
  | "batchItems"
  | "batchActiveIndex"
  | "batchRunning"
  | "maskingRunning"
  | "outputDir"
  | "documentProvenance"
  | "latestReportPath"
>;

// Result shape the controller reads from a masking run to record queue outputs.
export type DocumentBatchRunResult = {
  readonly report?: { readonly outputs?: Record<string, unknown> } | null;
  readonly runtime_manifest?: { readonly outputs?: Record<string, unknown> } | null;
  readonly masked_path?: string;
  readonly report_path?: string;
};

export type DocumentBatchRunOptions = {
  readonly outputDirOverride?: string;
  readonly statusPrefix?: string;
};

export type DocumentBatchDeps = {
  readonly state: DocumentBatchState;
  readonly batchSummaryEl: HTMLElement;
  readonly batchQueueEl: HTMLElement;
  readonly btnRunBatch: HTMLButtonElement;
  readonly compactPath: (path: string) => string;
  readonly openPath: (path: string) => Promise<void>;
  readonly setStatus: (message: string) => void;
  readonly renderDocumentReviewSurfaces: () => void;
  readonly loadOriginalDocument: (path: string) => Promise<void>;
  readonly runMaskingForSelectedDocument: (options?: DocumentBatchRunOptions) => Promise<DocumentBatchRunResult | null>;
};

export type DocumentBatchController = {
  readonly renderBatchQueue: () => void;
  readonly addBatchDocuments: (paths: string[]) => Promise<void>;
  readonly processBatchItem: (item: BatchItem, index: number) => Promise<void>;
};

function queueCell(className: string, text: string): HTMLSpanElement {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = text;
  span.title = text;
  return span;
}

function basenameOnly(path: string): string {
  const normalized = String(path || "").trim().replace(/[\\/]+$/, "");
  if (!normalized) return "";
  const parts = normalized.split(/[\\/]+/).filter(Boolean);
  return parts[parts.length - 1] ?? "";
}

function queueSafeTitle(item: BatchItem, compactPath: (path: string) => string): string {
  if (item.error) return item.error;
  const visiblePath = item.outputPath || item.path;
  return basenameOnly(visiblePath) || compactPath(visiblePath) || item.basename;
}

export function createDocumentBatchController(deps: DocumentBatchDeps): DocumentBatchController {
  const { state } = deps;

  function batchActionButton(label: string, enabled: boolean, handler: () => Promise<void>): HTMLButtonElement {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.disabled = !enabled;
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!enabled) return;
      await handler();
    });
    return button;
  }

  async function openBatchPath(path: string, label: string): Promise<void> {
    if (!path) {
      deps.setStatus(`${label} 열기 실패: 경로가 없습니다.`);
      return;
    }
    try {
      await deps.openPath(path);
      deps.setStatus(`${label} 열기 완료`);
    } catch {
      deps.setStatus(`${label} 열기 실패`);
    }
  }

  async function loadBatchItem(item: BatchItem, index: number): Promise<void> {
    state.batchActiveIndex = index;
    renderBatchQueue();
    try {
      await deps.loadOriginalDocument(item.path);
      deps.setStatus("일괄 큐 문서 로드 완료");
    } catch {
      deps.setStatus("일괄 큐 문서 로드 실패");
    }
  }

  async function retryBatchItem(item: BatchItem, index: number): Promise<void> {
    if (!state.outputDir) {
      deps.setStatus("일괄 처리 작업 폴더를 준비하지 못했습니다.");
      return;
    }
    await processBatchItem(item, index);
  }

  function updateBatchItem(item: BatchItem, status: BatchStatus, patch: Partial<BatchItem> = {}): void {
    Object.assign(item, patch, { status });
    renderBatchQueue();
    deps.renderDocumentReviewSurfaces();
  }

  async function processBatchItem(item: BatchItem, index: number): Promise<void> {
    state.batchActiveIndex = index;
    updateBatchItem(item, "처리 중", { error: "" });
    try {
      await deps.loadOriginalDocument(item.path);
      const result = await deps.runMaskingForSelectedDocument({
        outputDirOverride: state.outputDir,
        statusPrefix: `일괄 ${index + 1}/${state.batchItems.length}`,
      });
      if (!result) {
        updateBatchItem(item, "실패", { error: "마스킹 실행이 완료되지 않았습니다." });
        return;
      }
      const reportOutputs = (result.runtime_manifest?.outputs ?? result.report?.outputs ?? {}) as Record<string, string | undefined>;
      updateBatchItem(item, "완료", {
        outputPath: reportOutputs.masked_pdf_file || reportOutputs.preview_pdf_source_file || result.masked_path || latestGeneratedPath(state.documentProvenance),
        reportPath: reportOutputs.safe_report_path || result.report_path || state.latestReportPath,
      });
    } catch {
      updateBatchItem(item, "실패", { error: "마스킹 실행 중 오류가 발생했습니다." });
    }
  }

  async function addBatchDocuments(paths: string[]): Promise<void> {
    state.batchItems = appendBatchDocuments(state.batchItems, paths);
    if (state.batchActiveIndex === -1 && state.batchItems.length > 0) {
      state.batchActiveIndex = 0;
      await deps.loadOriginalDocument(state.batchItems[0].path);
    }
    renderBatchQueue();
  }

  function renderBatchQueue(): void {
    const summary = summarizeBatchItems(state.batchItems);
    const batchDisclosure = deps.batchQueueEl.closest<HTMLDetailsElement>(".dm-canvas__batch");
    if (batchDisclosure) batchDisclosure.hidden = summary.total === 0;
    deps.batchSummaryEl.textContent = `${summary.total}개 · 대기 ${summary.pending} · 완료 ${summary.done}`;
    deps.btnRunBatch.hidden = summary.pending === 0;
    deps.btnRunBatch.disabled = summary.pending === 0 || state.batchRunning || state.maskingRunning;
    const runLabel = deps.btnRunBatch.querySelector("span");
    if (runLabel) runLabel.textContent = `대기 ${summary.pending}개 모두 마스킹`;

    if (!summary.total) {
      deps.batchQueueEl.innerHTML = `<div class="batch-empty">큐에 문서가 없습니다.</div>`;
      deps.renderDocumentReviewSurfaces();
      return;
    }

    deps.batchQueueEl.innerHTML = "";
    state.batchItems.forEach((item, index) => {
      const actions = batchActionState(item, state.batchRunning);
      const row = document.createElement("div");
      row.role = "button";
      row.tabIndex = actions.canLoad ? 0 : -1;
      row.className = `batch-item status-${item.status.replace(/\s/g, "-")}`;
      row.classList.toggle("is-active", index === state.batchActiveIndex);
      row.title = queueSafeTitle(item, deps.compactPath);
      row.setAttribute("aria-label", `${item.basename} ${item.status}${actions.canRetry ? " 재실행 가능" : ""}`);
      row.append(
        queueCell("batch-name", item.basename),
        queueCell("batch-kind", item.kind ? item.kind.toUpperCase() : "문서"),
        queueCell("batch-state", item.status),
        queueCell("batch-output", item.error || deps.compactPath(item.outputPath || item.path)),
      );
      const actionsEl = document.createElement("div");
      actionsEl.className = "batch-actions";
      actionsEl.append(batchActionButton("불러오기", actions.canLoad, async () => loadBatchItem(item, index)));
      if (actions.canRetry) {
        actionsEl.append(batchActionButton("재실행", true, async () => retryBatchItem(item, index)));
      }
      actionsEl.append(batchActionButton("결과 열기", actions.canOpenOutput, async () => openBatchPath(item.outputPath || "", "결과")));
      row.append(actionsEl);
      row.addEventListener("click", async () => {
        if (!actions.canLoad) return;
        await loadBatchItem(item, index);
      });
      row.addEventListener("keydown", async (event) => {
        if (!actions.canLoad || (event.key !== "Enter" && event.key !== " ")) return;
        event.preventDefault();
        await loadBatchItem(item, index);
      });
      deps.batchQueueEl.appendChild(row);
    });
    deps.renderDocumentReviewSurfaces();
  }

  return { renderBatchQueue, addBatchDocuments, processBatchItem };
}
