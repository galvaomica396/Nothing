import { dashboardReviewState, REGION_KIND_LABELS } from "../../dashboardSurfaceModels";
import { isBoundSafeReport } from "../../state/maskingSession";
import type { AnalysisManifestV1, AnalysisRegionKind, BoundSafeReport, RegionCoverageState, ReviewKind, ReviewItemV1, SafeReport } from "../../state/maskingSession";

export type SaveWarningsInput = {
  readonly hasReportPath: boolean;
  readonly report: SafeReport | null;
  readonly restoreRevalidationFailed?: boolean;
};


export type PublicSaveWarningsInput = {
  readonly report: BoundSafeReport | null;
  readonly restoreRevalidationFailed?: boolean;
};

export type FinalSaveWarningPresentation = {
  readonly stateName: "idle" | "pass" | "fail" | "review";
  readonly title: string;
  readonly detail: string;
  readonly warnings: readonly string[];
};

export type FinalSaveGateDecision = {
  readonly eligible: boolean;
  readonly state: "eligible" | "blocked" | "advisory";
  readonly reasonCodes: readonly string[];
};

export type PublicFinalSaveWarningDetail = {
  readonly kind: ReviewKind | "coverage";
  readonly category: string;
  readonly pageStart: number;
  readonly pageEnd: number;
  readonly reasonCodes: readonly string[];
  readonly label: string;
};

export type MaskingFailurePresentation = {
  readonly code: string;
  readonly stage: string;
  readonly hint: string;
  readonly diagnostics: readonly MaskingFailureDiagnostic[];
};

export type MaskingFailureDiagnostic = {
  readonly kind: string;
  readonly reasonCode: string;
  readonly count: number;
  readonly occurrenceId?: string;
  readonly category?: string;
  readonly page?: number;
  readonly rectFingerprint?: string;
  readonly expectedTextHash?: string;
  readonly observedTextHash?: string;
};

type MaskingFailurePayload = {
  readonly code: string;
  readonly stage: string;
  readonly detail?: string;
};

function isMaskingFailurePayload(value: unknown): value is MaskingFailurePayload {
  if (!value || typeof value !== "object") return false;
  const code = Reflect.get(value, "code");
  const stage = Reflect.get(value, "stage");
  return typeof code === "string" && code.length > 0
    && typeof stage === "string" && stage.length > 0;
}

function errorText(value: unknown): string {
  if (value instanceof Error) return value.message;
  return typeof value === "string" ? value : "";
}

function parseMaskingFailurePayload(error: unknown): MaskingFailurePayload | null {
  if (isMaskingFailurePayload(error)) return error;
  const message = errorText(error).trim();
  if (!message) return null;
  try {
    const parsed: unknown = JSON.parse(message);
    return isMaskingFailurePayload(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function parseFailureDiagnostics(detail: string | undefined): readonly MaskingFailureDiagnostic[] {
  if (typeof detail !== "string") return [];
  const marker = ";diagnostics=";
  const markerIndex = detail.indexOf(marker);
  if (markerIndex < 0) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(detail.slice(markerIndex + marker.length));
  } catch {
    return [];
  }
  if (!Array.isArray(parsed) || parsed.length > 16) return [];
  const diagnostics: MaskingFailureDiagnostic[] = [];
  for (const value of parsed) {
    if (!value || typeof value !== "object" || Array.isArray(value)) continue;
    const keys = Object.keys(value);
    const allowed = new Set([
      "kind",
      "reason_code",
      "count",
      "occurrence_id",
      "category",
      "page",
      "rect_fingerprint",
      "expected_text_hash",
      "observed_text_hash",
    ]);
    if (keys.some((key) => !allowed.has(key))) continue;
    const kind = Reflect.get(value, "kind");
    const reasonCode = Reflect.get(value, "reason_code");
    const count = Reflect.get(value, "count");
    if (
      typeof kind !== "string"
      || !/^[a-z][a-z0-9_]{0,63}$/.test(kind)
      || typeof reasonCode !== "string"
      || !/^[a-z][a-z0-9_]{0,63}$/.test(reasonCode)
      || !Number.isInteger(count)
      || count < 1
      || count > 10_000
    ) continue;
    const diagnostic: {
      kind: string;
      reasonCode: string;
      count: number;
      occurrenceId?: string;
      category?: string;
      page?: number;
      rectFingerprint?: string;
      expectedTextHash?: string;
      observedTextHash?: string;
    } = { kind, reasonCode, count };
    const occurrenceId = Reflect.get(value, "occurrence_id");
    if (occurrenceId !== undefined) {
      if (typeof occurrenceId !== "string" || !/^occ_[0-9a-f]{24}$/.test(occurrenceId)) continue;
      diagnostic.occurrenceId = occurrenceId;
    }
    const category = Reflect.get(value, "category");
    if (category !== undefined) {
      if (typeof category !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(category)) continue;
      diagnostic.category = category;
    }
    const page = Reflect.get(value, "page");
    if (page !== undefined) {
      if (!Number.isInteger(page) || page < 0 || page > 2_000) continue;
      diagnostic.page = page;
    }
    for (const [sourceKey, targetKey] of [
      ["rect_fingerprint", "rectFingerprint"],
      ["expected_text_hash", "expectedTextHash"],
      ["observed_text_hash", "observedTextHash"],
    ] as const) {
      const hash = Reflect.get(value, sourceKey);
      if (hash === undefined) continue;
      if (typeof hash !== "string" || !/^[0-9a-fA-F]{64}$/.test(hash)) continue;
      diagnostic[targetKey] = hash.toLowerCase();
    }
    diagnostics.push(diagnostic);
  }
  return diagnostics;
}

function rawMaskingFailureCode(error: unknown): string | null {
  const matched = errorText(error).match(/(?:MASKING|PROCESS|SAVE)_[A-Z0-9_]+/);
  return matched?.[0] ?? null;
}

function maskingFailureHint(code: string, stage: string): string {
  switch (stage) {
    case "spawn": return "실행 파일 없음";
    case "timeout": return "처리 시간 초과";
    case "nonzero_exit": return "마스킹 엔진 종료 오류";
    case "output_parse": return "엔진 응답 형식 오류";
    case "pipeline_failure_code": return "마스킹 엔진 오류";
    case "session_guard":
      if (code.includes("ACCESS_DENIED") || code.includes("DESTINATION_REJECTED")) return "경로 접근이 거부됨";
      if (code.includes("SESSION_") || code.includes("STALE_") || code.includes("REVIEW_")) return "검토 세션 확인 필요";
      if (code.includes("UNAVAILABLE") || code.includes("RUNTIME")) return "실행 환경 확인 필요";
      return "마스킹 작업 확인 필요";
    default: return stage;
  }
}

export function presentMaskingFailure(error: unknown): MaskingFailurePresentation {
  const payload = parseMaskingFailurePayload(error);
  const code = payload?.code ?? rawMaskingFailureCode(error) ?? "unknown";
  const stage = payload?.stage ?? (code === "unknown" ? "unknown" : "session_guard");
  return {
    code,
    stage,
    hint: maskingFailureHint(code, stage),
    diagnostics: parseFailureDiagnostics(payload?.detail),
  };
}

type RelevantCoverageEntry = {
  readonly kind: AnalysisRegionKind;
  readonly state: RegionCoverageState;
};

function relevantCoverageEntries(manifest: AnalysisManifestV1): readonly RelevantCoverageEntry[] {
  const approval: readonly RelevantCoverageEntry[] = [
    { kind: "approval", state: manifest.approvalCoverage.approval },
    { kind: "header_meta", state: manifest.approvalCoverage.header_meta },
    { kind: "labeled_staff", state: manifest.approvalCoverage.labeled_staff },
  ];
  const dispatch: readonly RelevantCoverageEntry[] = [
    { kind: "recipient_reference", state: manifest.requiredRegionCoverage.recipient_reference },
    { kind: "sender_institution", state: manifest.requiredRegionCoverage.sender_institution },
    { kind: "approval_staff", state: manifest.requiredRegionCoverage.approval_staff },
    { kind: "dispatch_metadata", state: manifest.requiredRegionCoverage.dispatch_metadata },
    { kind: "footer_contact", state: manifest.requiredRegionCoverage.footer_contact },
  ];
  switch (manifest.profile) {
    case "internal_review": return approval;
    case "official_dispatch": return dispatch;
    case "mixed": return [...approval, ...dispatch];
    default: return [];
  }
}

function indeterminateCoverageReason(report: BoundSafeReport): "indeterminate_coverage" | "indeterminate_coverage_requires_reanalysis" | null {
  const manifest = report.analysisManifest;
  if (!manifest) return null;
  const indeterminateKinds = relevantCoverageEntries(manifest)
    .filter((entry) => entry.state === "indeterminate")
    .map((entry) => entry.kind);
  if (indeterminateKinds.length === 0) return null;
  const reviewableKinds = new Set(
    manifest.regions
      .filter((region) => indeterminateKinds.includes(region.kind))
      .filter((region) => report.reviewQueue?.some((review) => review.status === "pending"
        && review.kind === "region_geometry" && review.targetId === region.regionId))
      .map((region) => region.kind),
  );
  return indeterminateKinds.every((kind) => reviewableKinds.has(kind))
    ? "indeterminate_coverage"
    : "indeterminate_coverage_requires_reanalysis";
}

// The server-confirmed manifest remains authoritative for integrity. Review
// findings are warn-and-confirm; malformed or stale sessions remain hard blocks.
export function finalSaveGate(input: PublicSaveWarningsInput): FinalSaveGateDecision {
  if (!input.report || !isBoundSafeReport(input.report)) return { eligible: false, state: "blocked", reasonCodes: ["missing_current_session"] };
  if (input.restoreRevalidationFailed) return { eligible: false, state: "blocked", reasonCodes: ["restore_revalidation_failed"] };
  const reviewState = dashboardReviewState(input.report);
  if (reviewState.status !== "valid" || !input.report.analysisManifest) {
    return { eligible: false, state: "blocked", reasonCodes: ["stale_or_tampered_session"] };
  }
  const unresolved = input.report.analysisManifest.reviewItems.filter((item) => item.status === "pending");
  const pendingCards = reviewState.items.filter((item) => item.status === "pending");
  if ((unresolved.length === 0) !== (pendingCards.length === 0)) {
    return { eligible: false, state: "blocked", reasonCodes: ["stale_or_tampered_session"] };
  }
  const pendingGeometryReview = unresolved.some((item) => item.kind === "region_geometry");
  const coverageReason = pendingGeometryReview ? null : indeterminateCoverageReason(input.report);
  const reasonCodes = [...new Set([
    ...(coverageReason ? [coverageReason] : []),
    ...unresolved.flatMap((item) => item.kind === "region_geometry" ? ["geometry_review"] : item.reasonCodes),
  ])];
  if (unresolved.length > 0 && reasonCodes.length === 0) reasonCodes.push("unresolved_review_items");
  if (reasonCodes.length > 0) {
    return {
      eligible: false,
      state: "advisory",
      reasonCodes,
    };
  }

  return { eligible: true, state: "eligible", reasonCodes: [] };
}

const PUBLIC_SAVE_REASON_LABELS: Readonly<Record<string, string>> = {
  ambiguous_boundary: "문서 유형 경계를 확인해야 합니다.",
  box_structure_missing: "고정 영역의 박스 구조를 확인해야 합니다.",
  candidate_geometry_missing: "탐지 대상의 마스킹 위치를 확인할 수 없습니다.",
  conflicting_region_evidence: "영역 증거가 상충하여 확인이 필요합니다.",
  detector_review_required: "이름 또는 기관 탐지 결과를 확인해야 합니다.",
  geometry_review: "결재란 영역 자동확인 미완료 — 확인하고 저장",
  institution_address_review_required: "기관 주소 후보를 확인해야 합니다.",
  institution_label: "기관명 표지를 확인해야 합니다.",
  institution_pattern: "기관명 형식을 확인해야 합니다.",
  region_dictionary: "지역명 후보를 확인해야 합니다.",
  region_label: "지역명 표지를 확인해야 합니다.",
  hierarchical_region: "지역 계층 정보를 확인해야 합니다.",
  exact_boundary: "후보 경계를 확인해야 합니다.",
  footer_position: "하단 시행 정보 위치를 확인해야 합니다.",
  dispatch_number_format: "시행 문서번호 형식을 확인해야 합니다.",
  exact_dispatch_label: "시행 표지를 확인해야 합니다.",
  indeterminate_coverage: "고정 영역 확인이 완료되지 않았습니다.",
  indeterminate_coverage_requires_reanalysis: "고정 영역 확인이 완료되지 않았습니다. 확인 후 저장할 수 있습니다.",
  label_evidence_missing: "고정 영역의 표지를 확인해야 합니다.",
  label_value_distance_missing: "라벨과 값 사이 거리를 확인할 수 없습니다.",
  label_value_distance_out_of_range: "라벨과 값 사이 거리가 기준을 벗어났습니다.",
  layout_structure_missing: "고정 영역의 박스 구조를 확인해야 합니다.",
  missing_current_session: "현재 서버 검토 세션이 없습니다. 문서를 다시 분석하세요.",
  ocr_confidence_missing: "OCR 신뢰도 정보가 없어 다시 분석해야 합니다.",
  ocr_confidence_uncertain: "OCR 결과가 불확실해 다시 분석해야 합니다.",
  profile_rectangle_text_unavailable: "마스킹 영역의 텍스트를 확인할 수 없습니다.",
  rectangle_text_unavailable: "마스킹 영역의 텍스트를 확인할 수 없습니다.",
  restore_revalidation_failed: "복원 영역 때문에 개인정보가 다시 노출될 수 있습니다.",
  scanned_geometry_unavailable: "자동 탐지가 되지 않아 수동 확인이 필요합니다.",
  stale_or_tampered_session: "검토 목록과 분석 정보가 일치하지 않습니다. 문서를 다시 분석하세요.",
  structural_evidence_missing: "공문서 고정 형식을 확인할 수 없습니다.",
  unconfirmed_region_candidate: "미확정 영역의 후보 값을 확인해야 합니다.",
  unresolved_review_items: "해결되지 않은 검토 항목이 있습니다.",
};

const REVIEW_KIND_SAVE_REASON: Readonly<Record<ReviewKind, string>> = {
  name: "detector_review_required",
  institution: "detector_review_required",
  acknowledge: "unresolved_review_items",
  boundary: "ambiguous_boundary",
  ocr: "ocr_confidence_uncertain",
  region_geometry: "geometry_review",
};

const UNRESOLVED_REVIEW_ITEMS_LABEL = "해결되지 않은 검토 항목이 있습니다.";

function mappedKindSaveReasonLabel(report: BoundSafeReport | null, code: string): string {
  const item: ReviewItemV1 | undefined = report?.analysisManifest?.reviewItems.find((candidate) =>
    candidate.status === "pending" && candidate.reasonCodes.includes(code));
  if (!item) return UNRESOLVED_REVIEW_ITEMS_LABEL;
  return PUBLIC_SAVE_REASON_LABELS[REVIEW_KIND_SAVE_REASON[item.kind]] ?? UNRESOLVED_REVIEW_ITEMS_LABEL;
}

const REVIEW_CATEGORY_LABELS: Readonly<Record<string, string>> = {
  name: "이름",
  institution: "기관명",
  region_name: "지역명",
  institution_value: "기관명",
  institution_address: "기관 주소",
  email: "이메일",
  dispatch_metadata: "시행 정보",
  acknowledge: "검토 항목",
  boundary: "문서 경계",
  ocr: "OCR",
  region_geometry: "영역",
  coverage: "고정 영역",
};

function pageRangeLabel(pageStart: number, pageEnd: number): string {
  return pageStart === pageEnd
    ? `${pageStart + 1}`
    : `${pageStart + 1}–${pageEnd + 1}`;
}

function reviewCategoryLabel(manifest: AnalysisManifestV1, review: ReviewItemV1): string {
  if (review.kind === "name" || review.kind === "institution") {
    const occurrence = manifest.occurrences.find((candidate) => candidate.occurrenceId === review.targetId);
    const category = occurrence?.category;
    if (category && category !== "name" && category !== "institution") return category;
  }
  if (review.kind === "region_geometry") {
    const region = manifest.regions.find((candidate) => candidate.regionId === review.targetId);
    if (region) return REGION_KIND_LABELS[region.kind];
  }
  return REVIEW_CATEGORY_LABELS[review.kind] ?? review.kind;
}

function reasonLabel(reasonCodes: readonly string[]): string {
  const labels = reasonCodes
    .map((code) => PUBLIC_SAVE_REASON_LABELS[code] ?? mappedKindSaveReasonLabel(null, code))
    .filter((label) => label !== UNRESOLVED_REVIEW_ITEMS_LABEL);
  return labels[0] ?? "확인되지 않은 검토 항목";
}

export function formatPublicFinalSaveWarning(detail: Pick<PublicFinalSaveWarningDetail, "category" | "pageStart" | "pageEnd" | "reasonCodes">): string {
  return `미가림 가능성: ${detail.category} · ${pageRangeLabel(detail.pageStart, detail.pageEnd)}쪽 — ${reasonLabel(detail.reasonCodes)}`;
}

export function coveragePageRange(manifest: AnalysisManifestV1, kind: AnalysisRegionKind): { pageStart: number; pageEnd: number } {
  const regionPages = manifest.regions
    .filter((region) => region.kind === kind)
    .map((region) => region.page);
  if (regionPages.length > 0) {
    return { pageStart: Math.min(...regionPages), pageEnd: Math.max(...regionPages) };
  }
  const segmentPages = manifest.segments.flatMap((segment) => [segment.pageStart, segment.pageEnd]);
  return segmentPages.length > 0
    ? { pageStart: Math.min(...segmentPages), pageEnd: Math.max(...segmentPages) }
    : { pageStart: 0, pageEnd: 0 };
}

export function publicFinalSaveWarningDetails(input: PublicSaveWarningsInput): readonly PublicFinalSaveWarningDetail[] {
  const manifest = input.report?.analysisManifest;
  if (!manifest) return [];
  const pending = manifest.reviewItems.filter((item) => item.status === "pending");
  const details: PublicFinalSaveWarningDetail[] = pending.map((item) => {
    const reasons = Array.from(new Set([
      ...item.reasonCodes,
      REVIEW_KIND_SAVE_REASON[item.kind],
    ]));
    return {
      kind: item.kind,
      category: reviewCategoryLabel(manifest, item),
      pageStart: item.pageStart,
      pageEnd: item.pageEnd,
      reasonCodes: reasons,
      label: formatPublicFinalSaveWarning({
        category: reviewCategoryLabel(manifest, item),
        pageStart: item.pageStart,
        pageEnd: item.pageEnd,
        reasonCodes: reasons,
      }),
    };
  });
  const pendingRegionKinds = new Set(
    pending
      .filter((item) => item.kind === "region_geometry")
      .map((item) => manifest.regions.find((region) => region.regionId === item.targetId)?.kind)
      .filter((kind): kind is AnalysisRegionKind => kind !== undefined),
  );
  for (const entry of relevantCoverageEntries(manifest)) {
    if (entry.state !== "indeterminate" || pendingRegionKinds.has(entry.kind)) continue;
    const pages = coveragePageRange(manifest, entry.kind);
    details.push({
      kind: "coverage",
      category: REGION_KIND_LABELS[entry.kind],
      pageStart: pages.pageStart,
      pageEnd: pages.pageEnd,
      reasonCodes: ["indeterminate_coverage"],
      label: `미가림 가능성: ${REGION_KIND_LABELS[entry.kind]} · ${pageRangeLabel(pages.pageStart, pages.pageEnd)}쪽 — 고정 영역 확인이 완료되지 않았습니다.`,
    });
  }
  return details;
}

export function publicFinalSaveWarnings(input: PublicSaveWarningsInput): readonly string[] {
  const decision = finalSaveGate(input);
  const details = publicFinalSaveWarningDetails(input);
  const detailWarnings = details.map((detail) => detail.label);
  const representedReasonCodes = new Set(details.flatMap((detail) => detail.reasonCodes));
  const otherWarnings = decision.reasonCodes
    .filter((code) => !representedReasonCodes.has(code))
    .map((code) => PUBLIC_SAVE_REASON_LABELS[code] ?? mappedKindSaveReasonLabel(input.report, code));
  return Array.from(new Set(
    decision.state === "blocked"
      ? [...otherWarnings, ...detailWarnings]
      : [...detailWarnings, ...otherWarnings],
  ));
}

export function publicFinalSavePresentation(input: PublicSaveWarningsInput): FinalSaveWarningPresentation {
  const decision = finalSaveGate(input);
  const warnings = publicFinalSaveWarnings(input);
  if (decision.state === "blocked") {
    return {
      stateName: "fail",
      title: "최종 저장 차단",
      detail: warnings[0] ?? "현재 서버 검토 세션을 다시 확인해야 합니다.",
      warnings,
    };
  }
  if (warnings.length === 0) {
    return {
      stateName: "pass",
      title: "서버 검토 완료",
      detail: "모든 검토 항목이 해결되어 최종 저장할 수 있습니다.",
      warnings,
    };
  }
  return {
    stateName: "review",
    title: "사용자 확인 필요",
    detail: warnings[0] ?? "검토 항목을 확인하고 저장할 수 있습니다.",
    warnings,
  };
}

// Legal-report compatibility is deliberately isolated. Legal reports retain their
// established advisory presentation until their server lifecycle is migrated.
export function legalCompatibilityFinalSaveGate(input: SaveWarningsInput): FinalSaveGateDecision {
  const reasons: string[] = [];
  if (!input.report) reasons.push("missing_legal_report");
  if (!input.hasReportPath) reasons.push("missing_legal_report_path");
  if (input.report) {
    const facts = saveWarningFacts(input.report);
    if (facts.residualHits > 0) reasons.push("legal_residual_hits");
    if (facts.missingTargets > 0) reasons.push("legal_missing_targets");
    if (!facts.qualityPassed) reasons.push("legal_quality_gate_failed");
    if (facts.needsReview && input.report.product_checks.final_submission_allowed !== true) reasons.push("legal_manual_review_recommended");
  }
  if (input.restoreRevalidationFailed) reasons.push("legal_restore_revalidation_failed");
  if (!input.report || !input.hasReportPath) return { eligible: false, state: "blocked", reasonCodes: reasons };
  return reasons.length === 0
    ? { eligible: true, state: "eligible", reasonCodes: [] }
    : { eligible: true, state: "advisory", reasonCodes: reasons };
}

function saveWarningFacts(report: SafeReport | null): { qualityPassed: boolean; needsReview: boolean; residualHits: number; missingTargets: number } {
  const checks = report?.product_checks ?? {};
  const redaction = report?.document_redaction ?? report?.pdf_redaction;
  return {
    qualityPassed: checks.quality_gate_passed === true,
    needsReview: checks.needs_manual_review === true,
    residualHits: typeof redaction?.verification?.residual_hits === "number" ? redaction.verification.residual_hits : 0,
    missingTargets: typeof redaction?.missing_targets_count === "number" ? redaction.missing_targets_count : 0,
  };
}

// Compatibility-only legal-report presentation derives from the same decision
// projection as the legal save gate; it is not public-document authorization.
function legalWarnings(input: SaveWarningsInput, decision: FinalSaveGateDecision): readonly string[] {
  const facts = saveWarningFacts(input.report);
  return Array.from(new Set(decision.reasonCodes.map((code) => {
    switch (code) {
      case "missing_legal_report":
        return "법률 보고서가 없습니다. 마스킹을 다시 실행하세요.";
      case "missing_legal_report_path":
        return "법률 보고서 저장 경로가 없습니다. 마스킹을 다시 실행하세요.";
      case "legal_residual_hits":
        return `잔존 개인정보 후보 ${facts.residualHits}건이 남아 있습니다. 보정 화면에서 확인하는 것을 권장합니다.`;
      case "legal_missing_targets":
        return `마스킹되지 않은 대상 ${facts.missingTargets}건이 있습니다. 보정 화면에서 확인하는 것을 권장합니다.`;
      case "legal_quality_gate_failed":
        return "자동 검증을 통과하지 못했습니다. 보정 화면에서 확인하는 것을 권장합니다.";
      case "legal_manual_review_recommended":
        return "수동 검토가 권장되는 항목이 있습니다. 보정 화면에서 확인하는 것을 권장합니다.";
      case "legal_restore_revalidation_failed":
        return "복원 영역이 마스킹을 다시 노출할 수 있습니다. 보정 화면에서 확인하는 것을 권장합니다.";
      default:
        return "법률 보고서를 확인해야 합니다.";
    }
  })));
}

export function finalSaveWarnings(input: SaveWarningsInput): readonly string[] {
  const decision = legalCompatibilityFinalSaveGate(input);
  return legalWarnings(input, decision);
}

export function finalSaveWarningPresentation(input: SaveWarningsInput): FinalSaveWarningPresentation {
  const decision = legalCompatibilityFinalSaveGate(input);
  const warnings = legalWarnings(input, decision);
  if (decision.state === "eligible") {
    return { stateName: "pass", title: "자동 검증 통과", detail: "법률 보고서용 자동 검증을 통과했습니다.", warnings };
  }
  if (decision.state === "blocked") {
    return {
      stateName: input.report ? "fail" : "idle",
      title: input.report ? "최종 저장 차단" : "대기 중",
      detail: warnings[0] ?? "법률 보고서를 다시 확인하세요.",
      warnings,
    };
  }
  return { stateName: "review", title: "확인 권장", detail: warnings[0] ?? "법률 보고서의 검토 항목을 확인하세요.", warnings };
}
