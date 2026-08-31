import { currentSettings, updateSettings } from "../../state/settingsStore";
import { setModalVisibility } from "../../state/shellStore";

export type KeywordDialogDeps = {
  readonly setStatus: (message: string) => void;
  readonly renderCanvasFinalSaveSummary: () => void;
  readonly renderFinalSaveConfirmation: () => void;
  readonly updateWorkflowReadiness: () => void;
  readonly hasSelectedDocument: () => boolean;
  readonly rerunMasking: () => void;
};

export type KeywordDialogController = {
  readonly keywordList: () => string[];
  readonly writeKeywordList: (keywords: readonly string[]) => void;
  readonly openKeywordDialog: () => void;
  readonly closeKeywordDialog: () => void;
  readonly applyKeywords: () => void;
};

export function parseKeywordList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((keyword) => keyword.trim())
    .filter(Boolean);
}

export function appendKeywordValue(existingValue: string, nextKeywordValue: string): string {
  const nextKeyword = nextKeywordValue.trim();
  if (!nextKeyword) return existingValue;
  return Array.from(new Set([...parseKeywordList(existingValue), nextKeyword])).join(", ");
}

export function createKeywordDialogController(deps: KeywordDialogDeps): KeywordDialogController {
  function keywordList(): string[] {
    return parseKeywordList(currentSettings().customKeywords);
  }

  function writeKeywordList(keywords: readonly string[]): void {
    const deduped = Array.from(new Set(keywords.map((keyword) => keyword.trim()).filter(Boolean)));
    updateSettings({ customKeywords: deduped.join(", ") });
    deps.renderCanvasFinalSaveSummary();
    deps.renderFinalSaveConfirmation();
    deps.updateWorkflowReadiness();
  }

  function openKeywordDialog(): void {
    setModalVisibility("keyword-dialog", true);
  }

  function closeKeywordDialog(): void {
    setModalVisibility("keyword-dialog", false);
  }

  function applyKeywords(): void {
    writeKeywordList(keywordList());
    closeKeywordDialog();
    if (!deps.hasSelectedDocument()) {
      deps.setStatus("키워드를 적용했습니다. PDF를 열면 마스킹에 함께 적용됩니다.");
      return;
    }
    deps.rerunMasking();
  }

  return {
    keywordList,
    writeKeywordList,
    openKeywordDialog,
    closeKeywordDialog,
    applyKeywords,
  };
}
