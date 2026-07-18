import type { DeidentificationMode } from "./settingsState";
import type { DisplayMode } from "./state/contracts";

// 문서가 유일한 1급 작업 화면이고 두 설정 화면은 보조 화면이다.
export const WORKFLOW_SCREENS = ["documents", "masking-settings", "settings"] as const;

export type WorkflowScreen = (typeof WORKFLOW_SCREENS)[number];

export type FinalSaveReadinessInput = {
  readonly finalDocumentPath: string;
  readonly safeReportPath: string;
};

export type FinalSaveReadiness = {
  readonly canSave: boolean;
  readonly reason: string;
};

export type FinalSaveConfirmationInput = {
  readonly maskBoxes: number;
  readonly restoreBoxes: number;
  readonly keywords: string;
  readonly outputFileName: string;
  readonly pdfRedaction: boolean;
  readonly displayMode?: DisplayMode;
  readonly maskedTxtExport: boolean;
  readonly maskedTxtRequested: boolean;
  readonly deidentificationMode?: DeidentificationMode;
  readonly safeReportPath: string;
};

export type FinalSaveConfirmationSummary = {
  readonly canConfirm: boolean;
  readonly maskCountLabel: string;
  readonly restoreCountLabel: string;
  readonly keywordCountLabel: string;
  readonly outputFileLabel: string;
  readonly pdfPolicyLabel: string;
  readonly txtPolicyLabel: string;
  readonly securityLabels: readonly string[];
  readonly postSaveActions: readonly string[];
  readonly blockingReason: string;
};

export type SettingsScopeInput = {
  readonly selectedDocumentPath: string;
  readonly currentDocumentName: string;
};

export type SettingsScope = {
  readonly applyLabel: string;
  readonly scopeLabel: string;
};


export type CanvasEntryReadinessInput = {
  readonly documentKind: string;
  readonly standalone: boolean;
};

export type CanvasEntryReadiness = {
  readonly canEnter: boolean;
  readonly reason: string;
};

export type DocumentWorkflowReadinessInput = {
  readonly documentKind: string;
  readonly basePreviewPath: string;
  readonly manualPreviewPath: string;
  readonly safeReportPath: string;
  readonly boxCount: number;
  readonly latestDocumentPath?: string;
  readonly continuationUnavailable?: boolean;
};

export type DocumentWorkflowReadiness = {
  readonly canRunBaseMasking: boolean;
  readonly canApplyManualPreview: boolean;
  readonly canFinalSave: boolean;
  readonly baseMaskingReason: string;
  readonly manualApplyReason: string;
  readonly finalSaveReason: string;
  readonly phaseLabel: string;
};

export function isWorkflowScreen(value: string): value is WorkflowScreen {
  return WORKFLOW_SCREENS.includes(value as WorkflowScreen);
}

export function finalSaveReadiness(input: FinalSaveReadinessInput): FinalSaveReadiness {
  // 최종 저장은 사용자 재량이다. 저장할 마스킹본만 있으면 저장 위치와 파일명은
  // OS 네이티브 저장 다이얼로그에서 한 번에 선택한다.
  if (!input.finalDocumentPath.trim()) {
    return { canSave: false, reason: "저장할 마스킹본이 아직 없습니다. 기본 마스킹을 실행하세요." };
  }
  if (!input.safeReportPath.trim()) {
    return { canSave: true, reason: "자동 검증 전이지만 저장할 수 있습니다." };
  }
  return { canSave: true, reason: "최종 저장 가능" };
}

function keywordCount(keywords: string): number {
  return keywords
    .split(",")
    .map((keyword) => keyword.trim())
    .filter(Boolean).length;
}

function fileNameFromPath(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length === 0 ? "" : parts[parts.length - 1] || "";
}

export function finalSaveDefaultFileName(sourcePath: string): string {
  const sourceName = fileNameFromPath(sourcePath.trim());
  const dotIndex = sourceName.lastIndexOf(".");
  const stem = dotIndex > 0 ? sourceName.slice(0, dotIndex) : sourceName;
  return `${stem || "masked"}_masked`;
}

export function finalSaveConfirmationSummary(input: FinalSaveConfirmationInput): FinalSaveConfirmationSummary {
  const hasOutputFile = Boolean(input.outputFileName.trim());
  const hasReport = Boolean(input.safeReportPath.trim());
  const deidentificationMode = input.deidentificationMode || "token";
  const deidentificationLabel =
    deidentificationMode === "partial"
      ? "비식별 TXT: 부분 마스킹"
      : deidentificationMode === "pseudonym"
        ? "비식별 TXT: 일관 가명"
        : "비식별 TXT: 완전 치환(유형 토큰)";
  const pdfDisplayLabel =
    input.displayMode === "label_en"
      ? "영문 유형 라벨"
      : input.displayMode === "label_ko"
        ? "한글 유형 라벨"
        : input.displayMode === "pseudonym"
          ? "가명 표시"
          : "검정 박스";
  const txtPolicyLabel = input.maskedTxtExport
    ? deidentificationLabel.replace("비식별 TXT: ", "TXT: ")
    : input.maskedTxtRequested
      ? "TXT: 설정 변경 후 다시 실행 필요"
      : "TXT: 저장 안 함";
  const canConfirm = hasOutputFile;
  const blockingReason = hasOutputFile ? "" : "기본 파일명을 준비하지 못했습니다.";

  return {
    canConfirm,
    maskCountLabel: `마스킹 박스 수 ${input.maskBoxes}개`,
    restoreCountLabel: `복원 박스 수 ${input.restoreBoxes}개`,
    keywordCountLabel: `키워드 수 ${keywordCount(input.keywords)}개`,
    outputFileLabel: input.outputFileName.trim() || "-",
    pdfPolicyLabel: input.pdfRedaction ? `PDF: ${pdfDisplayLabel}` : "PDF: 레닥션 꺼짐",
    txtPolicyLabel,
    securityLabels: [
      input.pdfRedaction ? "PDF 레닥션 적용됨" : "PDF 레닥션 꺼짐",
      input.maskedTxtExport
        ? deidentificationLabel
        : input.maskedTxtRequested
          ? "비식별 TXT: 설정 변경 후 다시 실행 필요"
          : "비식별 TXT 저장 안 함",
      hasReport ? "자동 검증 완료" : "자동 검증 전",
    ],
    postSaveActions: ["결과 열기", "폴더 열기"],
    blockingReason,
  };
}

export function settingsScopeStatus(input: SettingsScopeInput): SettingsScope {
  const hasCurrentDocument = Boolean(input.selectedDocumentPath.trim() || input.currentDocumentName.trim());
  return {
    applyLabel: hasCurrentDocument ? "저장하면 현재 작업에도 적용됨" : "앱 기본값으로 저장됨",
    scopeLabel: hasCurrentDocument ? "현재 문서 작업값" : "앱 기본값",
  };
}


export function documentWorkflowReadiness(input: DocumentWorkflowReadinessInput): DocumentWorkflowReadiness {
  const documentKind = input.documentKind.trim();
  const hasDocument = documentKind === "pdf";
  const basePreviewPath = input.basePreviewPath.trim();
  const manualPreviewPath = input.manualPreviewPath.trim();
  const safeReportPath = input.safeReportPath.trim();
  const latestDocumentPath = (input.latestDocumentPath || "").trim();
  const editablePdfSourcePath = documentKind === "pdf" ? basePreviewPath || latestDocumentPath : "";
  const finalDocumentPath = manualPreviewPath || editablePdfSourcePath;
  const hasPendingManualBoxes = documentKind === "pdf" && input.boxCount > 0;
  const finalSave = finalSaveReadiness({
    finalDocumentPath,
    safeReportPath,
  });
  const finalSaveReason = hasPendingManualBoxes && finalSave.canSave ? "최종 저장 시 수동 보정 박스를 자동 반영합니다." : finalSave.reason;

  const continuationUnavailable = input.continuationUnavailable === true;
  const canRunBaseMasking = hasDocument && !continuationUnavailable;
  const baseMaskingReason = continuationUnavailable
    ? "저장된 PDF를 작업공간에서 다시 열 수 없습니다. PDF를 다시 선택하거나 열어주세요."
    : canRunBaseMasking
      ? "기본 마스킹 실행 가능"
      : "문서를 먼저 선택하세요.";

  let canApplyManualPreview = false;
  let manualApplyReason = "기본 마스킹 후 수동 박스를 추가하세요.";
  if (documentKind !== "pdf") {
    manualApplyReason = "PDF 문서를 선택하면 수동 보정을 사용할 수 있습니다.";
  } else if (!editablePdfSourcePath) {
    manualApplyReason = "PDF 문서를 먼저 불러오세요.";
  } else if (input.boxCount <= 0) {
    manualApplyReason = "반영할 수동 마스킹/복원 박스가 없습니다.";
  } else {
    canApplyManualPreview = true;
    manualApplyReason = "수동 보정 미리보기 반영 가능";
  }

  let phaseLabel = "문서 대기";
  if (hasDocument && !basePreviewPath && !latestDocumentPath) {
    phaseLabel = "기본 마스킹 대기";
  } else if (canApplyManualPreview) {
    phaseLabel = "수동 보정 대기";
  } else if (finalSave.canSave) {
    phaseLabel = "최종 저장 준비";
  } else if (hasDocument) {
    phaseLabel = "기본 마스킹 대기";
  }

  return {
    canRunBaseMasking,
    canApplyManualPreview,
    canFinalSave: finalSave.canSave,
    baseMaskingReason,
    manualApplyReason,
    finalSaveReason,
    phaseLabel,
  };
}

export function canvasEntryReadiness(input: CanvasEntryReadinessInput): CanvasEntryReadiness {
  if (input.documentKind === "pdf") {
    return { canEnter: true, reason: "PDF 캔버스 보정 가능" };
  }
  if (input.standalone && input.documentKind === "") {
    return { canEnter: true, reason: "독립 작업창에서 PDF를 불러올 수 있습니다." };
  }
  return { canEnter: false, reason: "PDF 문서를 선택하면 캔버스를 열 수 있습니다." };
}
