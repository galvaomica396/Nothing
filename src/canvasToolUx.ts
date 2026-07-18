export type CanvasToolReadinessInput = {
  readonly hasPdf: boolean;
  readonly hasResultDoc: boolean;
  readonly hasFinalDocument: boolean;
};

export type CanvasToolReadiness = {
  readonly canEdit: boolean;
  readonly canApply: boolean;
  readonly canSave: boolean;
  readonly editReason: string;
  readonly saveReason: string;
};

export type CanvasFinalSaveSummaryInput = {
  readonly maskBoxes: number;
  readonly restoreBoxes: number;
  readonly keywords: string;
  readonly hasFinalDocument: boolean;
};

export type CanvasFinalSaveSummary = {
  readonly maskLabel: string;
  readonly restoreLabel: string;
  readonly keywordLabel: string;
  readonly saveLabel: string;
};

export function canvasToolReadinessText(input: CanvasToolReadinessInput): CanvasToolReadiness {
  const canEdit = input.hasPdf && input.hasResultDoc;
  const canApply = canEdit;
  const canSave = input.hasFinalDocument;
  const editReason = canEdit ? "PDF 수정본에 박스를 그릴 수 있습니다." : "PDF를 불러오고 기본 마스킹 미리보기를 만든 뒤 편집할 수 있습니다.";
  const saveReason = canSave ? "최종 저장 준비 완료" : "최종 저장 전 미리보기 문서가 필요합니다.";

  return { canEdit, canApply, canSave, editReason, saveReason };
}

export function canvasFinalSaveSummary(input: CanvasFinalSaveSummaryInput): CanvasFinalSaveSummary {
  const keywordCount = input.keywords
    .split(",")
    .map((keyword) => keyword.trim())
    .filter(Boolean).length;
  const saveLabel = input.hasFinalDocument ? "최종 저장 가능" : "최종 저장 전 확인 필요";

  return {
    maskLabel: `마스킹 박스 ${input.maskBoxes}개`,
    restoreLabel: `복원 박스 ${input.restoreBoxes}개`,
    keywordLabel: `키워드 ${keywordCount}개`,
    saveLabel,
  };
}
