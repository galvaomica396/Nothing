import { isBoundarySegmentKind } from "../services/tauri/maskingContracts";
import type { BoundarySegmentKind } from "../contracts/generated/analysisManifestV1";
import type { DashboardReviewState } from "../dashboardSurfaceModels";
import type { BoundSafeReport } from "./maskingSession";

export type WorkspaceBoundaryDraft = {
  readonly reviewId: string;
  readonly segmentId: string;
  readonly pageStart: number;
  readonly pageEnd: number;
  readonly segmentKind: BoundarySegmentKind;
};

export function clampWorkspaceBoundaryDraft(
  draft: WorkspaceBoundaryDraft,
  pageIndex: number,
  edge: "start" | "end",
  segmentPageStart: number,
  segmentPageEnd: number,
): WorkspaceBoundaryDraft {
  const boundedPage = Math.max(segmentPageStart, Math.min(segmentPageEnd, pageIndex));
  const boundedStart = Math.max(segmentPageStart, Math.min(segmentPageEnd, draft.pageStart));
  const boundedEnd = Math.max(segmentPageStart, Math.min(segmentPageEnd, draft.pageEnd));
  return edge === "start"
    ? { ...draft, pageStart: Math.min(boundedPage, boundedEnd), pageEnd: Math.max(boundedPage, boundedEnd) }
    : { ...draft, pageStart: Math.min(boundedPage, boundedStart), pageEnd: Math.max(boundedPage, boundedStart) };
}

export function workspaceBoundaryDraft(
  report: BoundSafeReport | null,
  reviewState: DashboardReviewState,
): WorkspaceBoundaryDraft | null {
  const boundaryReview = reviewState.status === "valid"
    ? report?.reviewQueue?.find((item) => item.kind === "boundary" && item.status === "pending")
    : undefined;
  const boundarySegment = boundaryReview === undefined
    ? undefined
    : report?.analysisManifest?.segments.find((segment) => segment.segmentId === boundaryReview.targetId);
  if (boundaryReview === undefined || boundarySegment === undefined) return null;
  return {
    reviewId: boundaryReview.reviewId,
    segmentId: boundarySegment.segmentId,
    pageStart: boundarySegment.pageStart,
    pageEnd: boundarySegment.pageEnd,
    segmentKind: isBoundarySegmentKind(boundarySegment.kind) ? boundarySegment.kind : "official_dispatch",
  };
}
