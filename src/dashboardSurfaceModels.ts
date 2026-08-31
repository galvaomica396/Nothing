import { canonicalMaskCount, canonicalMaskCounts, canonicalReviewQueue, manualActionOverlapsConfirmedMask } from "./state/maskingSession";
import type { AnalysisManifestV1, AnalysisRegionKind, BoundSafeReport, CanonicalMaskCounts, PdfPointsTopLeftRect, ReviewItemV1 } from "./state/maskingSession";

export type DashboardSurfaceInput = {
  readonly selectedPath: string;
  readonly documentKind: "pdf" | "";
  readonly batchItems: readonly DashboardDocumentItem[];
  readonly latestDocumentPath: string;
  readonly latestReportPath: string;
  readonly report: BoundSafeReport | null;
  readonly geometryDraftReviewId: string;
  readonly reviewFailureById: ReadonlyMap<string, string>;
};

export type DashboardDocumentItem = {
  readonly path: string;
  readonly basename: string;
  readonly kind: "pdf" | "";
  readonly status: "대기" | "처리 중" | "완료" | "실패";
  readonly outputPath?: string;
  readonly reportPath?: string;
  readonly error?: string;
};

export type DashboardReviewItem = {
  readonly reviewId: string;
  readonly reviewIds: readonly string[];
  readonly kind: ReviewItemV1["kind"];
  readonly kindLabel: string;
  readonly regionKind: AnalysisRegionKind | null;
  readonly mergedCount: number;
  readonly pageStart: number;
  readonly pageLabel: string;
  readonly status: ReviewItemV1["status"];
  readonly detail: string;
  readonly scannedGeometryUnavailable: boolean;
};

export type DashboardReviewState =
  | { readonly status: "missing_authority" }
  | { readonly status: "invalid"; readonly reason: "missing_manifest" | "missing_queue" | "queue_divergence" }
  | { readonly status: "valid"; readonly items: readonly DashboardReviewItem[] };

export type DashboardSurfaceModel = {
  readonly documentTitle: string;
  readonly health: readonly { readonly label: string; readonly state: "ok" | "warn" }[];
};

export type DashboardReviewSurfaceCounts = {
  readonly autoMasked: number;
  readonly pending: number;
  readonly resolved: number;
  readonly total: number;
};

export type DashboardMaskCounts = CanonicalMaskCounts;

export type DashboardPageMaskCounts = CanonicalMaskCounts & {
  readonly page: number;
};

const EMPTY_MASK_COUNTS: DashboardMaskCounts = {
  automaticMaskCount: 0,
  manualMaskCount: 0,
  manualRestoreCount: 0,
  effectiveMaskCount: 0,
};

export function dashboardMaskCounts(report: BoundSafeReport | null): DashboardMaskCounts {
  if (!report) return { ...EMPTY_MASK_COUNTS };
  const counts = canonicalMaskCounts(report);
  return counts.ok ? counts.value : { ...EMPTY_MASK_COUNTS };
}

export function dashboardPageMaskCounts(report: BoundSafeReport | null): readonly DashboardPageMaskCounts[] {
  if (!report) return [];
  const canonical = canonicalMaskCounts(report);
  if (!canonical.ok) return [];
  const manifest = report.analysisManifest;
  if (!manifest) return [];
  const manuallyReplaced = new Set(
    manifest.manualActions.flatMap((action) => action.linkedOccurrenceId === null ? [] : [action.linkedOccurrenceId]),
  );
  const pages = new Map<number, DashboardMaskCounts>();
  const ensurePage = (page: number): DashboardMaskCounts => {
    const existing = pages.get(page);
    if (existing) return existing;
    const created: DashboardMaskCounts = { ...EMPTY_MASK_COUNTS };
    pages.set(page, created);
    return created;
  };
  for (const occurrence of manifest.occurrences) {
    if (
      occurrence.proposedAction !== "mask"
      || !["confirmed", "user_confirmed"].includes(occurrence.state)
      || manuallyReplaced.has(occurrence.occurrenceId)
    ) continue;
    const current = ensurePage(occurrence.page);
    const automaticMaskCount = current.automaticMaskCount + 1;
    pages.set(occurrence.page, {
      ...current,
      automaticMaskCount,
      effectiveMaskCount: automaticMaskCount + current.manualMaskCount,
    });
  }
  for (const action of manifest.manualActions) {
    const current = ensurePage(action.page);
    const manualMaskCount = current.manualMaskCount + (action.mode === "mask" ? 1 : 0);
    const manualRestoreCount = current.manualRestoreCount + (action.mode === "restore" ? 1 : 0);
    pages.set(action.page, {
      ...current,
      manualMaskCount,
      manualRestoreCount,
      effectiveMaskCount: current.automaticMaskCount + manualMaskCount,
    });
  }
  return [...pages.entries()]
    .sort(([left], [right]) => left - right)
    .map(([page, counts]) => ({ page, ...counts }));
}

export function dashboardFirstMaskingPage(report: BoundSafeReport | null): number | null {
  if (!report?.analysisManifest) return null;
  const manifest = report.analysisManifest;
  const candidatePages = [
    ...manifest.occurrences
      .filter((occurrence) => occurrence.proposedAction !== "exclude")
      .map((occurrence) => occurrence.page),
    ...manifest.manualActions.map((action) => action.page),
    ...manifest.reviewItems
      .filter((item) => item.status === "pending")
      .map((item) => item.pageStart),
  ];
  return candidatePages.length > 0 ? Math.min(...candidatePages) : null;
}

export function dashboardBlockedRestoreCount(report: BoundSafeReport | null): number {
  const manifest = report?.analysisManifest;
  if (!manifest) return 0;
  return manifest.manualActions.filter((action) => manualActionOverlapsConfirmedMask(manifest, action)).length;
}



export function basenameForDashboardPath(filePath: string): string {
  const pieces = filePath.replace(/\\/g, "/").split("/").filter(Boolean);
  return pieces.length ? pieces[pieces.length - 1] || filePath : filePath;
}

const REVIEW_KIND_LABELS: Record<ReviewItemV1["kind"], string> = {
  name: "이름",
  institution: "기관명",
  acknowledge: "확인 필요",
  boundary: "경계 확인",
  ocr: "OCR 확인",
  region_geometry: "영역 확인",
};

const REVIEW_REASON_LABELS: Readonly<Record<string, string>> = {
  ambiguous_boundary: "문서 경계가 불확실함",
  box_structure_missing: "영역 테두리 구조를 확인할 수 없음",
  candidate_geometry_missing: "탐지 위치 정보가 없음",
  detector_review_required: "탐지 결과 확인 필요",
  geometry_review: "영역 위치 확인 필요",
  institution_address_review_required: "기관 주소 후보 확인 필요",
  institution_label: "기관명 표지 확인 필요",
  institution_pattern: "기관명 형식 확인 필요",
  region_dictionary: "지역명 후보 확인 필요",
  region_label: "지역명 표지 확인 필요",
  hierarchical_region: "지역 계층 확인 필요",
  exact_boundary: "후보 경계 확인 필요",
  footer_position: "하단 시행 정보 위치 확인 필요",
  dispatch_number_format: "시행 문서번호 형식 확인 필요",
  exact_dispatch_label: "시행 표지 확인 필요",
  label_evidence_missing: "영역 표지 확인 필요",
  ocr_confidence_missing: "OCR 신뢰도 정보가 없음",
  ocr_confidence_uncertain: "OCR 신뢰도가 불확실함",
  rectangle_text_unavailable: "영역 텍스트를 확인할 수 없음",
  structural_evidence_missing: "문서 구조 확인 필요",
};

export const REGION_KIND_LABELS: Readonly<Record<AnalysisRegionKind, string>> = {
  approval: "결재선",
  header_meta: "머리말 정보",
  recipient_reference: "수신/참조",
  sender_institution: "발신 기관",
  approval_staff: "결재/담당자",
  dispatch_metadata: "시행 정보",
  footer_contact: "하단 연락처",
  labeled_staff: "담당자 표기",
};

function rectsIntersect(left: PdfPointsTopLeftRect, right: PdfPointsTopLeftRect): boolean {
  return Math.max(Math.min(left.x0, left.x1), Math.min(right.x0, right.x1))
    < Math.min(Math.max(left.x0, left.x1), Math.max(right.x0, right.x1))
    && Math.max(Math.min(left.y0, left.y1), Math.min(right.y0, right.y1))
      < Math.min(Math.max(left.y0, left.y1), Math.max(right.y0, right.y1));
}

function regionRectsIntersect(left: readonly PdfPointsTopLeftRect[], right: readonly PdfPointsTopLeftRect[]): boolean {
  return left.some((leftRect) => right.some((rightRect) => rectsIntersect(leftRect, rightRect)));
}

export function geometryReviewCluster(manifest: AnalysisManifestV1, seed: ReviewItemV1): readonly ReviewItemV1[] {
  if (seed.kind !== "region_geometry" || seed.status !== "pending") return [seed];
  const regions = new Map(manifest.regions.map((region) => [region.regionId, region]));
  const seedRegion = regions.get(seed.targetId);
  if (!seedRegion) return [seed];
  const candidates = manifest.reviewItems.filter((review) => {
    if (review.kind !== "region_geometry" || review.status !== "pending" || review.pageStart !== seed.pageStart || review.pageEnd !== seed.pageEnd) return false;
    const region = regions.get(review.targetId);
    return region?.kind === seedRegion.kind;
  });
  const cluster = new Map([[seed.reviewId, seed]]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const candidate of candidates) {
      if (cluster.has(candidate.reviewId)) continue;
      const candidateRegion = regions.get(candidate.targetId);
      if (!candidateRegion) continue;
      const intersectsCluster = [...cluster.values()].some((member) => {
        const memberRegion = regions.get(member.targetId);
        return memberRegion !== undefined && regionRectsIntersect(memberRegion.rects, candidateRegion.rects);
      });
      if (intersectsCluster) {
        cluster.set(candidate.reviewId, candidate);
        changed = true;
      }
    }
  }
  return [...cluster.values()];
}

function presentReviewItem(item: ReviewItemV1, manifest: AnalysisManifestV1, groupedReviews: readonly ReviewItemV1[] = [item]): DashboardReviewItem {
  const pageStart = item.pageStart + 1;
  const pageEnd = item.pageEnd + 1;
  const region = item.kind === "region_geometry" ? manifest.regions.find((candidate) => candidate.regionId === item.targetId) : undefined;
  const reasonCodes = groupedReviews.flatMap((review) => {
    const targetRegion = review.kind === "region_geometry"
      ? manifest.regions.find((candidate) => candidate.regionId === review.targetId)
      : undefined;
    return [...review.reasonCodes, ...(targetRegion?.reasonCodes ?? [])];
  });
  const reasonLabels = Array.from(new Set(reasonCodes.map((code) => REVIEW_REASON_LABELS[code] ?? "검토 사유 확인 필요"))).slice(0, 3);
  const warnings = [
    item.commonOnly ? "공통 항목만 적용" : "",
    item.requiresAcknowledgment ? "사용자 확인 필요" : "",
  ].filter(Boolean);
  const scannedGeometryUnavailable = reasonCodes.includes("scanned_geometry_unavailable");
  return {
    reviewId: item.reviewId,
    reviewIds: groupedReviews.map((review) => review.reviewId),
    kind: item.kind,
    kindLabel: region
      ? `${REGION_KIND_LABELS[region.kind]} 영역${groupedReviews.length > 1 ? ` ${groupedReviews.length}건 통합` : ""}`
      : REVIEW_KIND_LABELS[item.kind],
    regionKind: region?.kind ?? null,
    mergedCount: groupedReviews.length,
    pageStart: item.pageStart,
    pageLabel: pageStart === pageEnd ? `${pageStart}` : `${pageStart}–${pageEnd}`,
    status: item.status,
    detail: scannedGeometryUnavailable
      ? `스캔 페이지 ${pageStart === pageEnd ? `${pageStart}` : `${pageStart}–${pageEnd}`}쪽: 자동 탐지 불가 — 수동 마스킹으로 가린 뒤 확인하세요.`
      : [...reasonLabels, ...warnings].join(" · ") || "검토 사유 확인 필요",
    scannedGeometryUnavailable,
  };
}

export function dashboardReviewState(report: BoundSafeReport | null): DashboardReviewState {
  if (!report) return { status: "missing_authority" };
  const queue = canonicalReviewQueue(report);
  if (!queue.ok) {
    const field = queue.errors[0]?.field;
    return {
      status: "invalid",
      reason: field === "analysisManifest" ? "missing_manifest" : field === "reviewQueue.absent" ? "missing_queue" : "queue_divergence",
    };
  }
  const manifest = report.analysisManifest;
  if (!manifest) return { status: "invalid", reason: "missing_manifest" };
  const groupedReviewIds = new Set<string>();
  const items: DashboardReviewItem[] = [];
  for (const item of queue.value) {
    if (item.kind !== "region_geometry" || item.status !== "pending") {
      items.push(presentReviewItem(item, manifest));
      continue;
    }
    if (groupedReviewIds.has(item.reviewId)) continue;
    const cluster = geometryReviewCluster(manifest, item);
    for (const grouped of cluster) groupedReviewIds.add(grouped.reviewId);
    items.push(presentReviewItem(item, manifest, cluster));
  }
  return { status: "valid", items };
}

export function dashboardReviewSurfaceCounts(report: BoundSafeReport | null): DashboardReviewSurfaceCounts {
  const reviewState = dashboardReviewState(report);
  const items = reviewState.status === "valid"
    ? reviewState.items.filter((item) => item.kind !== "region_geometry")
    : [];
  const maskCounts = dashboardMaskCounts(report);
  const pending = items.filter((item) => item.status === "pending").length;
  const resolved = items.length - pending;
  return {
    autoMasked: maskCounts.automaticMaskCount,
    pending,
    resolved,
    total: items.length,
  };
}





export function buildDashboardSurfaceModel(input: DashboardSurfaceInput): DashboardSurfaceModel {
  const checks = input.report?.product_checks ?? {};
  const qualityPassed = checks["quality_gate_passed"] === true;
  const needsReview = checks["needs_manual_review"] === true;
  const authoritativeState = dashboardReviewState(input.report);
  const finalizedMaskCount = input.report && authoritativeState.status === "valid"
    ? canonicalMaskCount(input.report)
    : null;
  const hasInvalidMaskCount = finalizedMaskCount !== null && !finalizedMaskCount.ok;
  const riskCount = dashboardReviewSurfaceCounts(input.report).pending;
  const documentTitle = input.selectedPath ? basenameForDashboardPath(input.selectedPath) : "문서를 선택하세요";
  const hasDocument = Boolean(input.latestDocumentPath);
  const hasReport = Boolean(input.latestReportPath);
  return {
    documentTitle,
    health: [
      { label: input.selectedPath ? input.documentKind.toUpperCase() || "문서" : "문서 대기", state: input.selectedPath ? "ok" : "warn" },
      {
        label: hasInvalidMaskCount
          ? "마스킹 건수 계약 오류"
          : authoritativeState.status === "invalid"
            ? "리포트 계약 오류"
            : qualityPassed && !needsReview
              ? "검증 통과"
              : riskCount
                ? `위험 ${riskCount}`
                : "리포트 대기",
        state: qualityPassed && !needsReview && authoritativeState.status !== "invalid" && !hasInvalidMaskCount ? "ok" : "warn",
      },
      { label: hasDocument ? "결과 문서 있음" : "결과 문서 대기", state: hasDocument ? "ok" : "warn" },
      { label: hasReport ? "안전 리포트 있음" : "안전 리포트 대기", state: hasReport ? "ok" : "warn" },
    ],
  };
}
