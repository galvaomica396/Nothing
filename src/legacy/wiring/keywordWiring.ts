import type { LegacyDomBindings } from "../domBindings";
import type { LegacyAppController } from "../legacyAppController";

export function wireKeywordEvents(bindings: LegacyDomBindings, controller: LegacyAppController): void {
  const {
    btnRunMasking,
    btnOpenKeywordDialog,
    btnCloseKeywordDialog,
    btnKeywordPolicy,
    customKeywordsEl,
    btnKeywordDialogApply,
  } = bindings;
  const {
    keywordList,
    writeKeywordList,
    state,
    openKeywordDialog,
    closeKeywordDialog,
    syncKeywordDialogChips,
    renderCanvasFinalSaveSummary,
    renderFinalSaveConfirmation,
    setStatus,
  } = controller;

  btnOpenKeywordDialog.addEventListener("click", openKeywordDialog);
  btnCloseKeywordDialog.addEventListener("click", closeKeywordDialog);
  btnKeywordPolicy.addEventListener("click", () => {
    closeKeywordDialog();
    setStatus("마스킹 설정 화면에서 업무명/제목 정책을 확인하세요.");
  });
  customKeywordsEl.addEventListener("input", () => {
    syncKeywordDialogChips();
    renderCanvasFinalSaveSummary();
    renderFinalSaveConfirmation();
  });
  btnKeywordDialogApply.addEventListener("click", () => {
    writeKeywordList(keywordList());
    closeKeywordDialog();
    if (!state.documentProvenance.original.path) {
      setStatus("키워드를 적용했습니다. PDF를 열면 마스킹에 함께 적용됩니다.");
      return;
    }
    btnRunMasking.click();
  });
}
