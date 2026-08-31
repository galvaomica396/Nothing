import { useEffect } from "react";
import { isBoundarySegmentKind } from "../services/tauri/maskingContracts";
import {
  setWorkspaceBoundaryDraft,
  setWorkspaceBoundaryDraftApplying,
  useWorkspaceState,
} from "../state/workspaceStore";
import { clampWorkspaceBoundaryDraft } from "../state/workspaceBoundary";
import { workspaceController } from "../features/canvas-workbench/workspaceRuntime";
import type { BoundarySegmentKind } from "../contracts/generated/analysisManifestV1";
import { dashboardPageMaskCounts } from "../dashboardSurfaceModels";

const SEGMENT_KINDS = [
  { value: "internal_review", label: "내부 검토" },
  { value: "official_dispatch", label: "대내외 시행" },
  { value: "attachment", label: "첨부" },
  { value: "legal", label: "법무" },
] as const satisfies readonly { readonly value: BoundarySegmentKind; readonly label: string }[];

function segmentSummaryLabel(kind: string, state: string): string {
  const kindLabel = kind === "unknown" ? "분류 미확정" : kind;
  const stateLabel = state === "review_required" ? "검토 필요" : state;
  return `${kindLabel}, ${stateLabel}`;
}

function pageRange(segments: readonly { readonly pageEnd: number }[]): readonly number[] {
  const pageCount = segments.reduce((maximum, segment) => Math.max(maximum, segment.pageEnd + 1), 0);
  return Array.from({ length: pageCount }, (_, pageIndex) => pageIndex);
}

export function SegmentThumbnailStrip() {
  const workspace = useWorkspaceState();
  const manifest = workspace.report?.analysisManifest;
  const segments = manifest?.segments ?? [];
  const pages = pageRange(segments);
  const pageMaskCounts = new Map(dashboardPageMaskCounts(workspace.report).map((counts) => [counts.page, counts]));
  const draft = workspace.boundaryDraft;
  const targetSegment = draft === null ? undefined : segments.find((segment) => segment.segmentId === draft.segmentId);
  const canEdit = draft !== null && targetSegment !== undefined && workspace.reviewState.status === "valid";

  useEffect(() => {
    if (pages.length === 0) return;
    const eagerPages = pages.length < 30
      ? pages
      : [workspace.currentCanvasPage, workspace.currentCanvasPage + 1].filter((page) => page < pages.length);
    void workspaceController()?.loadPageThumbnails(eagerPages);
  }, [pages.length, workspace.currentCanvasPage, workspace.selectedPath]);

  if (segments.length === 0) return null;

  const updateDraftRange = (pageIndex: number, edge: "start" | "end"): void => {
    if (draft === null || targetSegment === undefined) return;
    setWorkspaceBoundaryDraft(clampWorkspaceBoundaryDraft(
      draft, pageIndex, edge, targetSegment.pageStart, targetSegment.pageEnd,
    ));
  };
  const moveBoundaryWithKey = (event: React.KeyboardEvent<HTMLButtonElement>, edge: "start" | "end"): void => {
    if (draft === null || targetSegment === undefined || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const page = event.key === "Home"
      ? targetSegment.pageStart
      : event.key === "End"
        ? targetSegment.pageEnd
        : Math.max(targetSegment.pageStart, Math.min(targetSegment.pageEnd, (edge === "start" ? draft.pageStart : draft.pageEnd) + (event.key === "ArrowLeft" ? -1 : 1)));
    updateDraftRange(page, edge);
  };
  const moveBoundaryWithPointer = (event: React.PointerEvent<HTMLButtonElement>, edge: "start" | "end"): void => {
    if (draft === null) return;
    const page = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-segment-page]")?.dataset.segmentPage;
    if (page === undefined) return;
    const pageIndex = Number(page);
    if (Number.isSafeInteger(pageIndex)) updateDraftRange(pageIndex, edge);
  };
  const applyBoundaryDraft = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (draft === null) return;
    const controller = workspaceController();
    if (controller === null) return;
    setWorkspaceBoundaryDraftApplying(true);
    try {
      await controller.resolveBoundaryReviewFromStrip(draft.reviewId, {
        kind: "boundary",
        pageStart: draft.pageStart,
        pageEnd: draft.pageEnd,
        segmentKind: draft.segmentKind,
      });
    } finally {
      setWorkspaceBoundaryDraftApplying(false);
    }
  };

  return (
    <section id="segment-thumbnail-strip" className="dm-segment-strip" aria-label="페이지 구간 썸네일">
      <header className="dm-segment-strip__head">
        <div>
          <span className="dm-section-label">문서 구간</span>
          <strong>{canEdit ? "경계 검토" : "자동 분석 결과"}</strong>
        </div>
        <span className="dm-segment-strip__summary">{pages.length}쪽 · {segments.length}개 구간</span>
      </header>
      <div className="dm-segment-strip__reel" role="list" aria-label="페이지별 문서 구간">
        {pages.map((pageIndex) => {
          const segment = segments.find((candidate) => pageIndex >= candidate.pageStart && pageIndex <= candidate.pageEnd);
          if (segment === undefined) return null;
          const selected = draft?.segmentId === segment.segmentId;
          const thumbnail = workspace.pageThumbnails.get(pageIndex);
          const maskCounts = pageMaskCounts.get(pageIndex);
          const manualMaskCount = maskCounts?.manualMaskCount ?? 0;
          const manualRestoreCount = maskCounts?.manualRestoreCount ?? 0;
          const effectiveMaskCount = maskCounts?.effectiveMaskCount ?? 0;
          const scanned = segment.source === "scanned_geometry_unavailable";
          const summary = `${pageIndex + 1}쪽, ${segmentSummaryLabel(segment.kind, segment.state)}${segment.commonOnly ? ", 공통 전용" : ""}${scanned ? ", 스캔 자동 탐지 불가" : ""}, 마스킹 ${effectiveMaskCount}건${manualMaskCount > 0 ? `, 수동 ${manualMaskCount}건 저장 시 적용` : ""}`;
          return (
            <article className="dm-segment-strip__page" data-segment-page={pageIndex} data-kind={segment.kind} data-state={segment.state} data-common-only={segment.commonOnly || undefined} data-scanned={scanned || undefined} data-mask-count={effectiveMaskCount} data-manual-mask-count={manualMaskCount || undefined} data-manual-restore-count={manualRestoreCount || undefined} key={pageIndex} role="listitem">
              <button
                className="dm-segment-strip__thumbnail"
                type="button"
                aria-current={workspace.currentCanvasPage === pageIndex ? "page" : undefined}
                aria-label={`${summary} 페이지로 이동`}
                onClick={() => { void workspaceController()?.loadPageThumbnails([pageIndex]); void workspaceController()?.goToReviewPage(pageIndex); }}
                onMouseEnter={() => void workspaceController()?.loadPageThumbnails([pageIndex])}
              >
                {thumbnail === undefined ? <span className="dm-segment-strip__placeholder">{pageIndex + 1}</span> : <img alt="" src={thumbnail} />}
                <span className="dm-segment-strip__bar" aria-hidden="true" />
                <span className="dm-segment-strip__page-number" aria-hidden="true">{pageIndex + 1}</span>
              </button>
              <span className="dm-segment-strip__mask-count">{effectiveMaskCount}건 마스킹</span>
              <span className="dm-segment-strip__manual-count" aria-hidden={manualMaskCount === 0 || undefined}>
                {manualMaskCount > 0 ? `수동 ${manualMaskCount}건 · 저장 시 적용` : " "}
              </span>
              <span className="dm-segment-strip__restore-count" aria-hidden={manualRestoreCount === 0 || undefined}>
                {manualRestoreCount > 0 ? `복원 ${manualRestoreCount}건` : " "}
              </span>
              {canEdit && selected && (
                <div className="dm-segment-strip__handles" aria-label={`${pageIndex + 1}쪽 구간 경계 편집`}>
                  {pageIndex === draft.pageStart && <button className="dm-segment-strip__handle" type="button" data-boundary-handle="start" aria-label={`구간 시작 경계, ${summary}`} onKeyDown={(event) => moveBoundaryWithKey(event, "start")} onPointerDown={(event) => event.currentTarget.setPointerCapture(event.pointerId)} onPointerMove={(event) => moveBoundaryWithPointer(event, "start")}><span aria-hidden="true" /></button>}
                  {pageIndex === draft.pageEnd && <button className="dm-segment-strip__handle" type="button" data-boundary-handle="end" aria-label={`구간 끝 경계, ${summary}`} onKeyDown={(event) => moveBoundaryWithKey(event, "end")} onPointerDown={(event) => event.currentTarget.setPointerCapture(event.pointerId)} onPointerMove={(event) => moveBoundaryWithPointer(event, "end")}><span aria-hidden="true" /></button>}
                  <button className="dm-segment-strip__start" type="button" onClick={() => updateDraftRange(pageIndex, "start")} onContextMenu={(event) => { event.preventDefault(); updateDraftRange(pageIndex, "start"); }}>여기서 시작</button>
                </div>
              )}
            </article>
          );
        })}
      </div>
      {canEdit && draft !== null ? (
        <form className="dm-segment-strip__editor" onSubmit={(event) => void applyBoundaryDraft(event)}>
          <label htmlFor="segment-boundary-kind">구간 유형</label>
          <select id="segment-boundary-kind" className="dm-input" value={draft.segmentKind} onChange={(event) => { if (isBoundarySegmentKind(event.currentTarget.value)) setWorkspaceBoundaryDraft({ ...draft, segmentKind: event.currentTarget.value }); }}>
            {SEGMENT_KINDS.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
          </select>
          <span className="dm-segment-strip__range">{draft.pageStart + 1}–{draft.pageEnd + 1}쪽</span>
          <button id="btn-apply-segment-boundary" className="dm-btn dm-btn--primary" type="submit" disabled={workspace.boundaryDraftApplying}>경계 적용</button>
          {workspace.reviewFailureById.get(draft.reviewId) && <span className="dm-detect__feedback" data-state="failure" role="alert">처리 실패 ({workspace.reviewFailureById.get(draft.reviewId)})</span>}
        </form>
      ) : <p className="dm-segment-strip__readonly">경계 검토 항목이 있을 때만 구간을 편집할 수 있습니다.</p>}
    </section>
  );
}
