export type DashboardSurfaceInput = {
  readonly selectedPath: string;
  readonly documentKind: "pdf" | "";
  readonly batchItems: readonly DashboardDocumentItem[];
  readonly latestDocumentPath: string;
  readonly latestReportPath: string;
  readonly report: unknown;
  readonly keywordCount: number;
  readonly maskBoxCount: number;
  readonly restoreBoxCount: number;
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

export type DashboardSurfaceModel = {
  readonly documentTitle: string;
  readonly health: readonly { readonly label: string; readonly state: "ok" | "warn" }[];
  readonly summary: {
    readonly maskCount: string;
    readonly restoreCount: string;
    readonly keywordCount: string;
    readonly riskCount: string;
    readonly outputLabel: string;
  };
  readonly alerts: readonly { readonly tone: "ok" | "warn" | "review"; readonly title: string; readonly detail: string }[];
  readonly reportActions: readonly { readonly tone: "ok" | "warn"; readonly badge: string; readonly title: string }[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function basename(filePath: string): string {
  const pieces = filePath.replace(/\\/g, "/").split("/").filter(Boolean);
  return pieces.length ? pieces[pieces.length - 1] || filePath : filePath;
}

function countValue(source: unknown, key: string): number {
  return isRecord(source) && typeof source[key] === "number" && Number.isFinite(source[key]) ? source[key] : 0;
}

function reviewItems(report: unknown): readonly unknown[] {
  if (!isRecord(report)) return [];
  const items = report["review_items"];
  return Array.isArray(items) ? items : [];
}

function reportCounts(report: unknown): Record<string, unknown> {
  return isRecord(report) && isRecord(report["counts"]) ? report["counts"] : {};
}

function productChecks(report: unknown): Record<string, unknown> {
  return isRecord(report) && isRecord(report["product_checks"]) ? report["product_checks"] : {};
}

function outputLabel(path: string): string {
  const name = basename(path);
  return name ? (name.length > 34 ? `${name.slice(0, 16)}...${name.slice(-14)}` : name) : "-";
}

function toneForStatus(status: string): "ok" | "warn" | "review" {
  if (status === "applied") return "ok";
  if (status.includes("review") || status.includes("missing") || status.includes("unverified") || status.includes("ocr")) return "warn";
  return "review";
}

function alertFromItem(item: unknown): { readonly tone: "ok" | "warn" | "review"; readonly title: string; readonly detail: string } {
  const source = isRecord(item) ? item : {};
  const tag = typeof source["tag"] === "string" ? source["tag"] : "항목";
  const token = typeof source["display_token"] === "string" ? source["display_token"] : "[MASK]";
  const status = typeof source["status"] === "string" ? source["status"] : "needs_review";
  const count = countValue(source, "count") || 1;
  return {
    tone: toneForStatus(status),
    title: `${tag} ${token}`,
    detail: `${count}건 · ${status}`,
  };
}

export function buildDashboardSurfaceModel(input: DashboardSurfaceInput): DashboardSurfaceModel {
  const checks = productChecks(input.report);
  const qualityPassed = checks["quality_gate_passed"] === true;
  const needsReview = checks["needs_manual_review"] === true;
  const items = reviewItems(input.report);
  const counts = reportCounts(input.report);
  const autoMaskCount = Object.values(counts).reduce<number>((sum, value) => (typeof value === "number" ? sum + value : sum), 0);
  const riskCount = items.length;
  const documentTitle = input.selectedPath ? basename(input.selectedPath) : "문서를 선택하세요";
  const output = input.latestDocumentPath || input.latestReportPath;
  return {
    documentTitle,
    health: [
      { label: input.selectedPath ? input.documentKind.toUpperCase() || "문서" : "문서 대기", state: input.selectedPath ? "ok" : "warn" },
      { label: qualityPassed && !needsReview ? "검증 통과" : riskCount ? `위험 ${riskCount}` : "리포트 대기", state: qualityPassed && !needsReview ? "ok" : "warn" },
      { label: output ? "산출 있음" : "저장 전", state: output ? "ok" : "warn" },
    ],
    summary: {
      maskCount: `${autoMaskCount + input.maskBoxCount}건`,
      restoreCount: `${input.restoreBoxCount}건`,
      keywordCount: `${input.keywordCount}개`,
      riskCount: `${riskCount}건`,
      outputLabel: outputLabel(output),
    },
    alerts: items.length ? items.slice(0, 4).map(alertFromItem) : [{ tone: "review", title: "안전 리포트 대기", detail: "마스킹 실행 후 표시" }],
    reportActions: [
      { tone: riskCount ? "warn" : "ok", badge: riskCount ? `${riskCount}건` : "대기", title: riskCount ? "수동 보정 필요" : "위험 항목 없음" },
      { tone: input.latestReportPath ? "ok" : "warn", badge: input.latestReportPath ? "있음" : "대기", title: "안전 리포트" },
      { tone: output ? "ok" : "warn", badge: output ? "있음" : "대기", title: "결과 문서" },
      { tone: input.restoreBoxCount ? "warn" : "ok", badge: input.restoreBoxCount ? `${input.restoreBoxCount}개` : "없음", title: "수동 복원 사유" },
    ],
  };
}
