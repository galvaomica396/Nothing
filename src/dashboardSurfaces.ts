import { basenameForDashboardPath, buildDashboardSurfaceModel, dashboardMaskCounts, dashboardReviewState, dashboardReviewSurfaceCounts } from "./dashboardSurfaceModels";
import type { DashboardReviewItem, DashboardSurfaceInput } from "./dashboardSurfaceModels";
import type { BoundSafeReport } from "./state/maskingSession";

export { buildDashboardSurfaceModel };

export type DashboardSurfaceRenderResult =
  | { readonly ok: true; readonly saveBlocked: false }
  | { readonly ok: false; readonly missingMounts: readonly string[]; readonly saveBlocked: true };

export function reportSessionCounts(report: BoundSafeReport | null): {
  detectedCount: number;
  maskCount: number | null;
  pendingCount: number;
} {
  const reviewState = dashboardReviewState(report);
  const maskCounts = dashboardMaskCounts(report);
  const reviewCounts = dashboardReviewSurfaceCounts(report);
  return {
    detectedCount: reviewState.status === "valid" ? report?.analysisManifest?.occurrences.length ?? 0 : 0,
    maskCount: report ? maskCounts.effectiveMaskCount : null,
    pendingCount: reviewCounts.pending,
  };
}

// v4 P2 (REDESIGN_V4_DARK §1): 문서 관제 화면이 통합 "문서" 화면으로 흡수되면서
// 문서 테이블·처리 상태판·메트릭(stage-*, obsidian-*-documents, document rows)은
// 진짜 삭제됐다. 배치 큐 목록은 batchQueueController 가 그린다. 여기서는 통합
// 화면과 상단 바에 남은 표면만 갱신한다:
//  - 상단 바: 현재 문서 제목 · 대상 요약 · 상태 요약(app-health-strip)
//  - 검토 레일: 탐지 항목(obsidian-detection-list)
// v4.1: 안전 리포트 요약 표면(safe-report-preview)은 리포트 내부화와 함께 삭제됐다.
export function renderDashboardSurfaces(root: ParentNode, input: DashboardSurfaceInput): DashboardSurfaceRenderResult {
  const requiredMounts = [
    "#current-document-title",
    "#obsidian-target-summary",
    "#app-health-strip",
    "#obsidian-detection-list",
    "#final-save-readiness",
    "#btn-save",
  ];
  const missingMounts = requiredMounts.filter((selector) => !(root.querySelector(selector) instanceof HTMLElement));
  if (missingMounts.length) {
    enforceSurfaceMountFailure(root, missingMounts);
    return { ok: false, missingMounts, saveBlocked: true };
  }

  const model = buildDashboardSurfaceModel(input);
  setText(root, "#current-document-title", model.documentTitle);
  setText(root, "#obsidian-target-summary", targetSummary(input));
  renderHealth(root, model.health);
  renderObsidianDetectionRail(root, input);
  return { ok: true, saveBlocked: false };
}

function setText(root: ParentNode, selector: string, text: string): void {
  const element = root.querySelector(selector);
  if (element instanceof HTMLElement) element.textContent = text;
}

function targetSummary(input: DashboardSurfaceInput): string {
  if (input.batchItems.length) return `${input.batchItems.length}개 파일 선택됨`;
  if (input.selectedPath) return basenameForDashboardPath(input.selectedPath);
  return "문서 미선택";
}

function enforceSurfaceMountFailure(root: ParentNode, missingMounts: readonly string[]): void {
  const readiness = root.querySelector("#final-save-readiness");
  const saveButton = root.querySelector("#btn-save");
  if (readiness instanceof HTMLElement) {
    readiness.dataset.state = "blocking";
    readiness.textContent = "검토 표면을 표시할 수 없어 최종 저장을 진행할 수 없습니다. 문서를 다시 열어 확인하세요.";
  } else {
    const host = root instanceof Document ? root.body : root instanceof HTMLElement ? root : null;
    if (host && !host.querySelector("#dashboard-surface-failure")) {
      const alert = document.createElement("p");
      alert.id = "dashboard-surface-failure";
      alert.setAttribute("role", "alert");
      alert.dataset.state = "blocking";
      alert.textContent = "검토 표면을 표시할 수 없어 최종 저장을 진행할 수 없습니다. 문서를 다시 열어 확인하세요.";
      host.prepend(alert);
    }
  }
  if (saveButton instanceof HTMLButtonElement) {
    saveButton.disabled = true;
    saveButton.dataset.safetyBlock = "dashboard-surface-mount";
    saveButton.setAttribute("aria-describedby", "final-save-readiness");
  }
  void missingMounts;
}

function renderHealth(root: ParentNode, items: readonly { readonly label: string; readonly state: "ok" | "warn" }[]): void {
  const container = root.querySelector("#app-health-strip");
  if (!(container instanceof HTMLElement)) {
    throw new Error("Dashboard health mount is missing after preflight.");
  }
  container.textContent = "";
  for (const item of items) {
    const span = document.createElement("span");
    span.classList.toggle("health-warn", item.state === "warn");
    span.textContent = item.label;
    container.appendChild(span);
  }
}

function cell(tagName: "span" | "strong" | "em", text: string, state?: string): HTMLElement {
  const element = document.createElement(tagName);
  if (state) element.dataset.state = state;
  element.textContent = text;
  element.title = text;
  return element;
}

function renderObsidianDetectionRail(root: ParentNode, input: DashboardSurfaceInput): void {
  const container = root.querySelector("#obsidian-detection-list");
  if (!(container instanceof HTMLElement)) return;
  const reviewState = dashboardReviewState(input.report);
  renderReviewCounters(root, input.report);
  if (reviewState.status === "valid") {
    renderAuthoritativeReviewItems(
      container,
      reviewState.items.filter((item) => item.kind !== "region_geometry"),
      input.geometryDraftReviewId,
      input.reviewFailureById,
    );
    return;
  }
  if (reviewState.status === "invalid") {
    renderInvalidReviewState(container);
    return;
  }
  renderPreRunOrAbsentReviewState(container);
}

function renderReviewCounters(root: ParentNode, report: DashboardSurfaceInput["report"]): void {
  const counts = dashboardReviewSurfaceCounts(report);
  const maskCounts = dashboardMaskCounts(report);
  const summary = `자동 ${maskCounts.automaticMaskCount}건 · 수동 ${maskCounts.manualMaskCount}건(저장 시 적용) · 검토 필요 ${counts.pending}건`;
  setText(root, "#review-total-count", `${counts.total}건`);
  setText(root, "#review-filter-all-count", String(counts.total));
  setText(root, "#review-filter-pending-count", String(counts.pending));
  setText(root, "#review-filter-resolved-count", String(counts.resolved));
  setText(root, "#review-progress-summary", `${counts.total}건 중 ${counts.resolved}건 확인 완료`);
  setText(root, "#review-summary-banner", summary);
  const explanation = root.querySelector("#review-summary-explanation");
  if (explanation instanceof HTMLElement) {
    explanation.hidden = !(report
      && counts.autoMasked === 0
      && maskCounts.manualMaskCount === 0
      && maskCounts.manualRestoreCount === 0);
  }
}

function renderInvalidReviewState(container: HTMLElement): void {
  container.textContent = "";
  const row = document.createElement("div");
  row.dataset.state = "blocking";
  row.append(
    detectionDot("dot-danger"),
    cell("strong", "안전 리포트 계약 오류"),
    cell("em", "저장 차단"),
  );
  const detail = document.createElement("span");
  detail.textContent = "분석 매니페스트와 검토 대기열이 일치하지 않습니다. 문서를 다시 분석하세요.";
  row.appendChild(detail);
  container.appendChild(row);
}
function renderPreRunOrAbsentReviewState(container: HTMLElement): void {
  container.textContent = "";
  const row = document.createElement("div");
  row.dataset.state = "blocking";
  row.append(
    detectionDot("dot-danger"),
    cell("strong", "검토 정보 확인 필요"),
    cell("em", "저장 차단"),
  );
  const detail = document.createElement("span");
  detail.textContent = "마스킹 실행 전이거나 서버 검토 정보가 없습니다. 다시 분석한 뒤 저장하세요.";
  row.appendChild(detail);
  container.appendChild(row);
}

function renderAuthoritativeReviewItems(
  container: HTMLElement,
  items: readonly DashboardReviewItem[],
  geometryDraftReviewId: string,
  reviewFailureById: ReadonlyMap<string, string>,
): void {
  container.textContent = "";
  if (!items.length) {
    const row = document.createElement("div");
    row.append(detectionDot("dot-primary"), cell("strong", "검토 항목이 없습니다"), cell("em", "0건"));
    container.appendChild(row);
    return;
  }

  for (const item of items) {
    const row = document.createElement("div");
    row.className = "dm-detect__item";
    row.dataset.reviewId = item.reviewId;
    row.dataset.state = item.status;
    row.append(
      detectionDot(item.status === "resolved" ? "dot-primary" : "dot-warning"),
      cell("strong", item.kindLabel),
      cell("em", `${item.pageLabel}쪽 · ${item.status === "pending" ? "검토 대기" : "해결됨"}`),
    );

    const detail = document.createElement("span");
    detail.className = "dm-detect__detail";
    detail.id = `review-detail-${item.reviewId}`;
    detail.textContent = item.detail;
    row.appendChild(detail);

    const failureCode = reviewFailureById.get(item.reviewId);
    if (failureCode) {
      const failure = document.createElement("span");
      failure.className = "dm-detect__feedback";
      failure.dataset.state = "failure";
      failure.setAttribute("role", "alert");
      failure.textContent = `처리 실패 (${failureCode})`;
      row.appendChild(failure);
    }

    if (item.status === "pending") {
      appendReviewControls(row, item, detail.id, item.reviewId === geometryDraftReviewId);
    } else {
      const resolved = document.createElement("span");
      resolved.className = "dm-detect__feedback";
      resolved.dataset.state = "resolved";
      resolved.textContent = "이 항목을 반영했습니다";
      row.appendChild(resolved);
    }
    container.appendChild(row);
  }
}

function appendReviewControls(row: HTMLElement, item: DashboardReviewItem, detailId: string, geometryDraftActive: boolean): void {
  const actions = document.createElement("div");
  actions.className = "dm-detect__actions";
  if (item.kind === "name" || item.kind === "institution") {
    actions.append(actionButton(item, detailId, "mask", "마스킹 적용"), actionButton(item, detailId, "exclude", "제외"));
  } else if (item.kind === "acknowledge") {
    actions.append(actionButton(item, detailId, "acknowledge", "확인"));
  } else if (item.kind === "boundary") {
    actions.append(actionButton(item, detailId, "confirm_boundary", "자동 경계 확인"));
  } else if (item.kind === "region_geometry") {
    if (geometryDraftActive) {
      const guidance = document.createElement("p");
      guidance.className = "dm-detect__geometry-guidance";
      guidance.textContent = "표시된 영역을 모두 덮도록 드래그한 뒤 [영역 확정]을 누르세요.";
      row.appendChild(guidance);
    }
    actions.append(
      actionButton(item, detailId, "confirm_suggested_geometry", "제안 영역 확정"),
      actionButton(item, detailId, "confirm_geometry", geometryDraftActive ? "영역 확정" : "영역 편집"),
    );
  } else if (item.kind === "ocr") {
    actions.append(actionButton(item, detailId, "reanalyze", "다시 분석"));
  }
  if (actions.childElementCount > 0) {
    row.append(actions);
    return;
  }

  const blocking = document.createElement("span");
  blocking.textContent = "값을 확인해 해결해야 합니다";
  blocking.dataset.state = "blocking";
  row.appendChild(blocking);
}

function actionButton(item: DashboardReviewItem, detailId: string, action: "mask" | "exclude" | "acknowledge" | "confirm_boundary" | "confirm_suggested_geometry" | "confirm_geometry" | "reanalyze", label: string): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = action === "mask" || action === "confirm_suggested_geometry"
    ? "dm-btn dm-btn--compact dm-btn--primary dm-detect__action dm-detect__action--primary"
    : "dm-btn dm-btn--compact dm-detect__action";
  button.dataset.reviewId = item.reviewId;
  button.dataset.reviewAction = action;
  button.textContent = label;
  button.setAttribute("aria-label", `${item.kindLabel} ${item.pageLabel}쪽 검토 항목 ${label}`);
  button.setAttribute("aria-describedby", detailId);
  return button;
}
function detectionDot(className: string): HTMLElement {
  const dot = document.createElement("i");
  dot.className = className;
  return dot;
}
