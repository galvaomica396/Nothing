// Final-save advisory warnings — pure decision logic (docs/CODE_REVIEW_2026-07-04.md
// "startLegacyApp 분리": save-gate 모듈).
//
// v4.2.0 정책 전환: 검증 결과는 저장을 "차단"하지 않는다. 최종 저장은 항상 사용자
// 재량이며, 저장할 마스킹본이 존재하기만 하면 저장 버튼은 활성이다. 이 모듈은 더
// 이상 "차단 사유"를 만들지 않고, 저장 직전 확인 1회에 띄울 "권고형 경고 목록"을
// 산출한다. 문제가 없으면 경고는 비어 있고 추가 확인 없이 저장이 진행된다.
//
// 노출 원칙(가르치지 말고 치워라 / 개인정보 비노출): 경고 문구에는 건수만 담고,
// 개인정보 원문·좌표·내부 코드는 절대 담지 않는다. 리포트 파싱 결과(잔존/누락/품질
// 게이트/수동 검토)와 복원 재검증 실패 플래그를 입력으로 받는다.

import type { SafeReport } from "../../state/maskingSession";

export type SaveWarningsInput = {
  readonly hasReportPath: boolean;
  readonly report: SafeReport | null;
  // 복원(un-mask) 반영 후 재검증이 통과하지 못한 상태. 저장을 막지는 않지만
  // 복원 영역이 마스킹을 다시 노출할 수 있음을 알린다.
  readonly restoreRevalidationFailed?: boolean;
};

export type FinalSaveWarningPresentation = {
  readonly stateName: "idle" | "pass" | "fail" | "review";
  readonly title: string;
  readonly detail: string;
  readonly warnings: readonly string[];
};

type SaveWarningFacts = {
  readonly qualityPassed: boolean;
  readonly needsReview: boolean;
  readonly residualHits: number;
  readonly missingTargets: number;
};

function saveWarningFacts(report: SafeReport | null): SaveWarningFacts {
  const checks = report?.product_checks ?? {};
  const redaction = report?.document_redaction ?? report?.pdf_redaction;
  return {
    qualityPassed: checks.quality_gate_passed === true,
    needsReview: checks.needs_manual_review === true,
    residualHits: typeof redaction?.verification?.residual_hits === "number" ? redaction.verification.residual_hits : 0,
    missingTargets: typeof redaction?.missing_targets_count === "number" ? redaction.missing_targets_count : 0,
  };
}

function residualWarning(count: number): string {
  return `잔존 개인정보 후보 ${count}건이 남아 있습니다. 보정 화면에서 확인하는 것을 권장합니다.`;
}

function missingTargetsWarning(count: number): string {
  return `마스킹되지 않은 대상 ${count}건이 있습니다. 보정 화면에서 확인하는 것을 권장합니다.`;
}

function qualityWarning(): string {
  return "자동 검증을 통과하지 못했습니다. 보정 화면에서 확인하는 것을 권장합니다.";
}

// 저장을 막지 않는 권고형 경고 목록을 산출한다. 순서는 잔존 → 누락 → 자동 검증
// 미통과 → 수동 검토 권장 → 복원 재검증 미통과. 각 항목은 건수만 노출한다.
export function finalSaveWarnings(input: SaveWarningsInput): readonly string[] {
  const warnings: string[] = [];
  if (input.hasReportPath && input.report) {
    const facts = saveWarningFacts(input.report);
    if (facts.residualHits > 0) {
      warnings.push(residualWarning(facts.residualHits));
    }
    if (facts.missingTargets > 0) {
      warnings.push(missingTargetsWarning(facts.missingTargets));
    }
    if (!facts.qualityPassed) {
      warnings.push(qualityWarning());
    }
    if (facts.needsReview && input.report.product_checks?.final_submission_allowed !== true) {
      warnings.push("수동 검토가 권장되는 항목이 있습니다. 보정 화면에서 확인하는 것을 권장합니다.");
    }
  }
  if (input.restoreRevalidationFailed) {
    warnings.push("복원 영역이 마스킹을 다시 노출할 수 있습니다. 보정 화면에서 확인하는 것을 권장합니다.");
  }
  return warnings;
}

export function finalSaveWarningPresentation(input: SaveWarningsInput): FinalSaveWarningPresentation {
  const warnings = finalSaveWarnings(input);
  const reportWarning = input.report?.warnings?.find((warning) => Boolean(warning));
  if (!input.report) {
    return {
      stateName: "idle",
      title: "대기 중",
      detail: "문서를 선택하고 마스킹을 실행하세요.",
      warnings,
    };
  }

  const facts = saveWarningFacts(input.report);
  const base = facts.qualityPassed && !facts.needsReview
    ? { stateName: "pass" as const, title: "자동 검증 통과", detail: "문서 마스킹 자동 검증을 통과했습니다. 최종 저장할 수 있습니다." }
    : facts.residualHits > 0
      ? { stateName: "fail" as const, title: "잔존 개인정보 후보 있음", detail: residualWarning(facts.residualHits) }
      : { stateName: "review" as const, title: "확인 권장", detail: missingTargetsWarning(facts.missingTargets) };
  return {
    ...base,
    detail: reportWarning ? `${base.detail} · ${reportWarning}` : base.detail,
    warnings,
  };
}
