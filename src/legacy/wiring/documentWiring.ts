import { invoke } from "@tauri-apps/api/core";
import type { LegacyDomBindings } from "../domBindings";
import type { LegacyAppController } from "../legacyAppController";

export function wireDocumentEvents(bindings: LegacyDomBindings, controller: LegacyAppController): void {
  const {
    btnPickPdf,
    btnPickBatch,
    btnCanvasHeroBatch,
    btnRunMasking,
    btnRunBatch,
    btnNewDocument,
    btnCloseNewDocumentDialog,
    btnCancelNewDocument,
    btnConfirmNewDocument,
    syncPagesEl,
    customRegionsEl,
    btnPrevOrig,
    btnNextOrig,
    btnPrevResult,
    btnNextResult,
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
    moveResultPage,
    saveFinalOutput,
    setCompareMode,
  } = controller;

  btnPickPdf.addEventListener("click", () => {
    void pickInputDocument();
  });

  btnPickBatch.addEventListener("click", async () => {
    const selected = await invoke<string[] | null>("pick_input_documents");
    if (!selected || selected.length === 0) return;
    if (!(await prepareForDocumentReplacement())) return;
    try {
      await maybeApplyDefaultOutputDir(selected.slice(0, 1));
      await addBatchDocuments(selected);
      setStatus(`일괄 처리 큐 추가 완료: ${selected.length}개 선택`);
    } catch {
      setStatus("일괄 처리 큐 추가 실패");
    }
  });
  // 빈 상태 히어로의 "여러 PDF" 보조 액션 → 상단 바 배치 선택으로 위임.
  btnCanvasHeroBatch?.addEventListener("click", () => btnPickBatch.click());


  btnRunMasking.addEventListener("click", async () => {
    await runMaskingForSelectedDocument();
  });

  btnRunBatch.addEventListener("click", async () => {
    if (state.batchItems.length === 0) {
      setStatus("일괄 처리할 문서를 먼저 추가하세요.");
      return;
    }
    if (!state.outputDir) {
      setStatus("일괄 처리 작업 폴더를 준비하지 못했습니다.");
      return;
    }
    if (isCustomRegionScope() && !customRegionsEl.value.trim()) {
      setStatus("사용자 지정 지역을 선택했으면 지역명을 입력하세요.");
      customRegionsEl.focus();
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

  btnNewDocument.addEventListener("click", () => {
    void resetDocumentSession();
  });
  btnCloseNewDocumentDialog.addEventListener("click", () => resolveDiscardConfirmation(false));
  btnCancelNewDocument.addEventListener("click", () => resolveDiscardConfirmation(false));
  btnConfirmNewDocument.addEventListener("click", () => resolveDiscardConfirmation(true));

  syncPagesEl.addEventListener("change", async () => {
    state.syncPages = syncPagesEl.checked;
    if (state.syncPages) {
      state.currentResultPage = clampPage(state.currentOrigPage, state.resultDoc);
      await renderCompare();
    }
  });

  btnPrevOrig.addEventListener("click", async () => moveOrigPage(-1));
  btnNextOrig.addEventListener("click", async () => moveOrigPage(1));
  btnPrevResult.addEventListener("click", async () => moveResultPage(-1));
  btnNextResult.addEventListener("click", async () => moveResultPage(1));

  btnSave.addEventListener("click", async () => {
    await saveFinalOutput();
  });

  compareModePdf.addEventListener("click", () => setCompareMode("pdf"));
  compareModeText.addEventListener("click", () => setCompareMode("text"));
}
