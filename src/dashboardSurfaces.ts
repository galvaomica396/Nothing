import { buildDashboardSurfaceModel } from "./dashboardSurfaceModels";
import type { DashboardSurfaceInput } from "./dashboardSurfaceModels";

export { buildDashboardSurfaceModel };

// v4 P2 (REDESIGN_V4_DARK §1): 문서 관제 화면이 통합 "문서" 화면으로 흡수되면서
// 문서 테이블·처리 상태판·메트릭(stage-*, obsidian-*-documents, document rows)은
// 진짜 삭제됐다. 배치 큐 목록은 batchQueueController 가 그린다. 여기서는 통합
// 화면과 상단 바에 남은 표면만 갱신한다:
//  - 상단 바: 현재 문서 제목 · 대상 요약 · 상태 요약(app-health-strip)
//  - 검토 레일: 탐지 항목(obsidian-detection-list)
// v4.1: 안전 리포트 요약 표면(safe-report-preview)은 리포트 내부화와 함께 삭제됐다.
export function renderDashboardSurfaces(root: ParentNode, input: DashboardSurfaceInput): void {
  const model = buildDashboardSurfaceModel(input);
  setText(root, "#current-document-title", model.documentTitle);
  setText(root, "#obsidian-target-summary", targetSummary(input));
  renderHealth(root, model.health);
  renderObsidianDetectionCounts(root, input.report);
}

function setText(root: ParentNode, selector: string, text: string): void {
  const element = root.querySelector(selector);
  if (element instanceof HTMLElement) element.textContent = text;
}

function targetSummary(input: DashboardSurfaceInput): string {
  if (input.batchItems.length) return `${input.batchItems.length}개 파일 선택됨`;
  if (input.selectedPath) return basenameForPath(input.selectedPath);
  return "문서 미선택";
}

function renderHealth(root: ParentNode, items: readonly { readonly label: string; readonly state: "ok" | "warn" }[]): void {
  const container = root.querySelector("#app-health-strip");
  if (!(container instanceof HTMLElement)) return;
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

function renderObsidianDetectionCounts(root: ParentNode, report: unknown): void {
  const container = root.querySelector("#obsidian-detection-list");
  if (!(container instanceof HTMLElement)) return;
  const counts = detectionCounts(report);
  container.textContent = "";
  if (!counts.length) {
    container.append(detectionDot("dot-primary"), cell("strong", "마스킹 실행 후 표시됩니다"), cell("em", "0건"));
    return;
  }
  counts.forEach((item, index) => {
    const row = document.createElement("div");
    row.append(detectionDot(dotClass(index)), cell("strong", labelForDetectionKey(item.key)), cell("em", `${item.count}건`));
    container.appendChild(row);
  });
}

function detectionCounts(report: unknown): readonly { readonly key: string; readonly count: number }[] {
  if (!isRecord(report) || !isRecord(report["counts"])) return [];
  return Object.entries(report["counts"])
    .filter((entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1]) && entry[1] > 0)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 3)
    .map(([key, count]) => ({ key, count }));
}

function detectionDot(className: string): HTMLElement {
  const dot = document.createElement("i");
  dot.className = className;
  return dot;
}

function dotClass(index: number): string {
  if (index === 0) return "dot-danger";
  if (index === 1) return "dot-warning";
  return "dot-primary";
}

function labelForDetectionKey(key: string): string {
  const labels: Record<string, string> = {
    email: "이메일",
    emails: "이메일",
    name: "이름",
    names: "이름",
    phone: "전화번호",
    phones: "전화번호",
    rrn: "주민등록번호",
    resident_registration_number: "주민등록번호",
  };
  return labels[key.toLowerCase()] || key;
}

function basenameForPath(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
