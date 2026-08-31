import { invoke } from "@tauri-apps/api/core";
import type { ApplicationDomBindings } from "../domBindings";
import type { ApplicationController } from "../applicationController";
import { currentSettings } from "../../state/settingsStore";
type BatchPickerFailureCode = "PICK_INPUT_DOCUMENTS_IPC_FAILED" | "PICK_INPUT_DOCUMENTS_FAILED";

function batchPickerFailureCode(error: unknown): BatchPickerFailureCode {
  const message = error instanceof Error ? error.message.toLowerCase() : "";
  return message.includes("ipc") || message.includes("invoke") || message.includes("permission")
    ? "PICK_INPUT_DOCUMENTS_IPC_FAILED"
    : "PICK_INPUT_DOCUMENTS_FAILED";
}


export function wireDocumentEvents(bindings: ApplicationDomBindings, controller: ApplicationController): () => void {
  const {
    btnPickPdf,
    btnPickBatch,
    btnRunMasking,
    btnRunBatch,
    btnNewDocument,
    btnCloseNewDocumentDialog,
    btnCancelNewDocument,
    btnConfirmNewDocument,
    syncPagesEl,
    btnPrevOrig,
    btnNextOrig,
    btnSave,
    compareModePdf,
    compareModeText,
  } = bindings;
  const {
    state,
    pickInputDocument,
    addBatchDocuments,
    maybeApplyDefaultOutputDir,
    setStatus,
    runMaskingForSelectedDocument,
    isCustomRegionScope,
    renderBatchQueue,
    updateWorkflowReadiness,
    processBatchItem,
    prepareForDocumentReplacement,
    resetDocumentSession,
    resolveDiscardConfirmation,
    clampPage,
    renderCompare,
    moveOrigPage,
    saveFinalOutput,
    setCompareMode,
  } = controller;
  const abortController = new AbortController();
  const listen = (target: EventTarget, type: string, listener: EventListener) => {
    target.addEventListener(type, listener, { signal: abortController.signal });
  };
  let disposed = false;
  let unregister = () => {};
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    abortController.abort();
    unregister();
  };
  unregister = controller.registerDisposer(dispose);

  listen(btnPickPdf, "click", () => {
    void pickInputDocument();
  });

  listen(btnPickBatch, "click", async () => {
    try {
      const selected = await invoke<string[] | null>("pick_input_documents");
      if (!selected || selected.length === 0) return;
      if (!(await prepareForDocumentReplacement())) return;
      await maybeApplyDefaultOutputDir(selected.slice(0, 1));
      await addBatchDocuments(selected);
      setStatus(`일괄 처리 큐 추가 완료: ${selected.length}개 선택`);
    } catch (error) {
      console.error("pick_input_documents failed.", error);
      setStatus(`일괄 처리 문서 선택 실패 (${batchPickerFailureCode(error)})`);
    }
  });


  listen(btnRunMasking, "click", async () => {
    await runMaskingForSelectedDocument();
  });

  listen(btnRunBatch, "click", async () => {
    if (state.batchItems.length === 0) {
      setStatus("일괄 처리할 문서를 먼저 추가하세요.");
      return;
    }
    if (!state.outputDir) {
      setStatus("일괄 처리 작업 폴더를 준비하지 못했습니다.");
      return;
    }
    if (isCustomRegionScope() && !currentSettings().customRegions.trim()) {
      setStatus("사용자 지정 지역을 선택했으면 지역명을 입력하세요.");
      return;
    }
    state.batchRunning = true;
    renderBatchQueue();
    updateWorkflowReadiness();
    try {
      const runnableItems = state.batchItems.filter((item) => item.status === "대기");
      for (const item of runnableItems) {
        await processBatchItem(item, state.batchItems.indexOf(item));
      }
    } finally {
      state.batchRunning = false;
      renderBatchQueue();
      updateWorkflowReadiness();
    }
    setStatus("일괄 마스킹 실행 완료: 큐 상태를 확인하세요.");
  });

  listen(btnNewDocument, "click", () => {
    void resetDocumentSession();
  });
  listen(btnCloseNewDocumentDialog, "click", () => resolveDiscardConfirmation(false));
  listen(btnCancelNewDocument, "click", () => resolveDiscardConfirmation(false));
  listen(btnConfirmNewDocument, "click", () => resolveDiscardConfirmation(true));

  listen(syncPagesEl, "change", async () => {
    state.syncPages = syncPagesEl.checked;
    if (state.syncPages) {
      state.currentResultPage = clampPage(state.currentOrigPage, state.resultDoc);
      await renderCompare();
    }
  });

  listen(btnPrevOrig, "click", async () => moveOrigPage(-1));
  listen(btnNextOrig, "click", async () => moveOrigPage(1));

  listen(btnSave, "click", async () => {
    await saveFinalOutput();
  });

  listen(compareModePdf, "click", () => setCompareMode("pdf"));
  listen(compareModeText, "click", () => setCompareMode("text"));
  return dispose;
}
