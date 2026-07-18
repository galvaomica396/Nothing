// Keyword dialog controller (docs/CODE_REVIEW_2026-07-04.md
// "startLegacyApp 분리": 키워드 다이얼로그 모듈).
//
// Owns the custom-keyword list model (parse / dedupe / write-back into the
// #custom-keywords input), the chip previews, and the keyword dialog open/close
// lifecycle. startLegacyApp wires the injected DOM elements plus the render
// callbacks that must re-run when the keyword set changes.

export type KeywordDialogDeps = {
  readonly customKeywordsEl: HTMLTextAreaElement;
  readonly keywordDialogChipListEl: HTMLElement;
  readonly keywordDialogEl: HTMLElement;
  readonly btnOpenKeywordDialog: HTMLButtonElement;
  readonly setModalVisible: (element: HTMLElement, visible: boolean) => void;
  readonly setStatus: (message: string) => void;
  readonly renderCanvasFinalSaveSummary: () => void;
  readonly renderFinalSaveConfirmation: () => void;
  readonly updateWorkflowReadiness: () => void;
};

export type KeywordDialogController = {
  readonly keywordList: () => string[];
  readonly writeKeywordList: (keywords: string[]) => void;
  readonly syncKeywordDialogChips: () => void;
  readonly openKeywordDialog: () => void;
  readonly closeKeywordDialog: () => void;
};

export function createKeywordDialogController(deps: KeywordDialogDeps): KeywordDialogController {
  function keywordList(): string[] {
    return deps.customKeywordsEl.value
      .split(/[\n,]/)
      .map((keyword) => keyword.trim())
      .filter(Boolean);
  }

  function renderKeywordChipList(container: HTMLElement, keywords: string[]): void {
    container.innerHTML = "";
    if (keywords.length === 0) {
      const empty = document.createElement("span");
      empty.textContent = "등록된 키워드 없음";
      container.appendChild(empty);
      return;
    }
    for (const keyword of keywords) {
      const chip = document.createElement("span");
      chip.textContent = keyword;
      container.appendChild(chip);
    }
  }

  function syncKeywordDialogChips(): void {
    const keywords = keywordList();
    renderKeywordChipList(deps.keywordDialogChipListEl, keywords);
  }

  function writeKeywordList(keywords: string[]): void {
    const deduped = Array.from(new Set(keywords.map((keyword) => keyword.trim()).filter(Boolean)));
    deps.customKeywordsEl.value = deduped.join(", ");
    syncKeywordDialogChips();
    deps.renderCanvasFinalSaveSummary();
    deps.renderFinalSaveConfirmation();
    deps.updateWorkflowReadiness();
  }

  function openKeywordDialog(): void {
    syncKeywordDialogChips();
    deps.setModalVisible(deps.keywordDialogEl, true);
    deps.customKeywordsEl.focus();
  }

  function closeKeywordDialog(): void {
    deps.setModalVisible(deps.keywordDialogEl, false);
    deps.btnOpenKeywordDialog.focus();
  }

  return {
    keywordList,
    writeKeywordList,
    syncKeywordDialogChips,
    openKeywordDialog,
    closeKeywordDialog,
  };
}
