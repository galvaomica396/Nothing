export type DocumentKind = "pdf" | "";
export type BatchStatus = "대기" | "처리 중" | "완료" | "실패";

export type BatchItem = {
  id: string;
  path: string;
  basename: string;
  kind: DocumentKind;
  status: BatchStatus;
  outputPath?: string;
  reportPath?: string;
  error?: string;
};

export type BatchSummary = {
  total: number;
  pending: number;
  done: number;
  failed: number;
};

export type BatchActionState = {
  canLoad: boolean;
  canRetry: boolean;
  canOpenOutput: boolean;
  canOpenReport: boolean;
};

export function basenameForPath(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

export function documentKindForPath(path: string): DocumentKind {
  const lowered = path.toLowerCase();
  if (lowered.endsWith(".pdf")) return "pdf";
  return "";
}

export function summarizeBatchItems(items: readonly BatchItem[]): BatchSummary {
  return {
    total: items.length,
    pending: items.filter((item) => item.status === "대기").length,
    done: items.filter((item) => item.status === "완료").length,
    failed: items.filter((item) => item.status === "실패").length,
  };
}

export function batchActionState(item: BatchItem, batchRunning: boolean): BatchActionState {
  const hasOutput = Boolean(item.outputPath);
  const hasReport = Boolean(item.reportPath);
  return {
    canLoad: !batchRunning,
    canRetry: !batchRunning && item.status === "실패",
    canOpenOutput: hasOutput && (!batchRunning || item.status === "완료"),
    canOpenReport: hasReport && (!batchRunning || item.status === "완료"),
  };
}

export function appendBatchDocuments(
  currentItems: readonly BatchItem[],
  paths: readonly string[],
  idSeed = Date.now(),
): BatchItem[] {
  const existing = new Set(currentItems.map((item) => item.path));
  const next = [...currentItems];
  for (const path of paths) {
    if (existing.has(path)) continue;
    const kind = documentKindForPath(path);
    if (!kind) continue;
    next.push({
      id: `${idSeed}-${next.length}-${path}`,
      path,
      basename: basenameForPath(path),
      kind,
      status: "대기",
    });
    existing.add(path);
  }
  return next;
}
