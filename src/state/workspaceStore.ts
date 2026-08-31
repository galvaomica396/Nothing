import { useSyncExternalStore } from "react";
import { buildDashboardSurfaceModel, dashboardFirstMaskingPage, dashboardReviewState } from "../dashboardSurfaceModels";
import type { DashboardSurfaceInput, DashboardReviewState } from "../dashboardSurfaceModels";
import type { BoundSafeReport } from "./maskingSession";
import { workspaceBoundaryDraft, type WorkspaceBoundaryDraft } from "./workspaceBoundary";
export type { WorkspaceBoundaryDraft } from "./workspaceBoundary";
export type WorkspaceState = {
  readonly selectedPath: string;
  readonly report: BoundSafeReport | null;
  readonly reviewState: DashboardReviewState;
  readonly geometryDraftReviewId: string;
  readonly reviewFailureById: ReadonlyMap<string, string>;
  readonly documentTitle: string;
  readonly health: ReturnType<typeof buildDashboardSurfaceModel>["health"];
  readonly saveSummary: WorkspaceSaveSummary;
  readonly firstMaskingPage: number | null;
  readonly finalSaveDialog: WorkspaceFinalSaveDialog;
  readonly finalizationSuccessDialog: WorkspaceFinalizationSuccessDialog;
  readonly pageThumbnails: ReadonlyMap<number, string>;
  readonly currentCanvasPage: number;
  readonly boundaryDraft: WorkspaceBoundaryDraft | null;
  readonly boundaryDraftApplying: boolean;
};

export type WorkspaceSaveSummary = {
  readonly maskCount: string;
  readonly restoreCount: string;
  readonly automaticMaskCount: string;
  readonly manualMaskCount: string;
  readonly manualRestoreCount: string;
  readonly effectiveMaskCount: string;
  readonly keywordCount: string;
  readonly outputFile: string;
  readonly pdfPolicy: string;
  readonly txtPolicy: string;
};

export type WorkspaceFinalSaveDialog = {
  readonly visible: boolean;
  readonly title: string;
  readonly description: string;
  readonly advisoryTitle: string;
  readonly advisoryCopy: string;
  readonly showAdvisory: boolean;
  readonly cancelLabel: string;
  readonly confirmLabel: string;
  readonly confirmEnabled: boolean;
  readonly stateLabel: string;
  readonly stateTone: "pending" | "ok" | "warn";
  readonly warnings: readonly string[];
  readonly emptyMessage: string;
};

export type WorkspaceFinalSaveConfirmation = Omit<WorkspaceFinalSaveDialog, "visible">;

export type WorkspaceFinalizationSuccessDialog = {
  readonly visible: boolean;
  readonly title: string;
  readonly description: string;
  readonly statusLabel: string;
  readonly statusTone: "ok" | "warn";
  readonly warnings: readonly string[];
  readonly fileName: string;
  readonly meta: string;
  readonly path: string;
  readonly maskCount: string;
  readonly savedAt: string;
};

export type WorkspaceFinalizationSuccess = Omit<WorkspaceFinalizationSuccessDialog, "visible">;

const emptyState: WorkspaceState = {
  selectedPath: "",
  report: null,
  reviewState: { status: "missing_authority" },
  geometryDraftReviewId: "",
  reviewFailureById: new Map(),
  documentTitle: "문서를 선택하세요",
  health: [],
  saveSummary: {
    maskCount: "0개",
    restoreCount: "0개",
    automaticMaskCount: "0건",
    manualMaskCount: "0건(저장 시 적용)",
    manualRestoreCount: "0건",
    effectiveMaskCount: "0건",
    keywordCount: "0개",
    outputFile: "-",
    pdfPolicy: "검정 박스",
    txtPolicy: "저장 안 함",
  },
  firstMaskingPage: null,
  finalSaveDialog: {
    visible: false,
    title: "저장 전 확인",
    description: "검토 결과와 저장 조건을 확인하는 중입니다.",
    advisoryTitle: "저장 조건을 확인하는 중입니다",
    advisoryCopy: "문서 유형과 검토 결과에 따라 저장 조건이 정해집니다.",
    showAdvisory: true,
    cancelLabel: "취소하고 검토하기",
    confirmLabel: "저장 조건 확인 중",
    confirmEnabled: false,
    stateLabel: "저장 조건 확인 중",
    stateTone: "pending",
    warnings: [],
    emptyMessage: "저장 조건을 확인하는 중입니다.",
  },
  finalizationSuccessDialog: {
    visible: false,
    title: "안전 문서로 저장되었습니다",
    description: "개인정보가 마스킹된 안전 문서를 지정한 위치에 저장했습니다.",
    statusLabel: "완전 마스킹본",
    statusTone: "ok",
    warnings: [],
    fileName: "-",
    meta: "-",
    path: "-",
    maskCount: "-",
    savedAt: "-",
  },
  pageThumbnails: new Map(),
  currentCanvasPage: 0,
  boundaryDraft: null,
  boundaryDraftApplying: false,
};

let state = emptyState;
const listeners = new Set<() => void>();

export function publishWorkspaceSurface(input: DashboardSurfaceInput): void {
  const model = buildDashboardSurfaceModel(input);
  const reviewState = dashboardReviewState(input.report);
  const boundaryDraft = workspaceBoundaryDraft(input.report, reviewState);
  const documentChanged = input.selectedPath !== state.selectedPath;
  state = {
    selectedPath: input.selectedPath,
    report: input.report,
    reviewState,
    geometryDraftReviewId: input.geometryDraftReviewId,
    reviewFailureById: new Map(input.reviewFailureById),
    documentTitle: model.documentTitle,
    health: model.health,
    saveSummary: state.saveSummary,
    firstMaskingPage: dashboardFirstMaskingPage(input.report),
    finalSaveDialog: state.finalSaveDialog,
    finalizationSuccessDialog: state.finalizationSuccessDialog,
    pageThumbnails: documentChanged ? new Map() : state.pageThumbnails,
    currentCanvasPage: documentChanged ? 0 : state.currentCanvasPage,
    boundaryDraft,
    boundaryDraftApplying: false,
  };
  for (const listener of listeners) listener();
}

function publish(next: WorkspaceState): void {
  state = next;
  for (const listener of listeners) listener();
}

export function publishWorkspaceCanvasSummary(summary: Pick<WorkspaceSaveSummary, "maskCount" | "restoreCount" | "automaticMaskCount" | "manualMaskCount" | "manualRestoreCount" | "effectiveMaskCount" | "keywordCount" | "outputFile">): void {
  publish({ ...state, saveSummary: { ...state.saveSummary, ...summary } });
}

export function publishWorkspaceFinalSaveSummary(summary: WorkspaceSaveSummary): void {
  publish({ ...state, saveSummary: summary });
}

export function publishWorkspaceFinalSaveDialog(confirmation: WorkspaceFinalSaveConfirmation): void {
  publish({ ...state, finalSaveDialog: { ...state.finalSaveDialog, ...confirmation } });
}

export function setWorkspaceFinalSaveDialogVisible(visible: boolean): void {
  if (state.finalSaveDialog.visible === visible) return;
  publish({ ...state, finalSaveDialog: { ...state.finalSaveDialog, visible } });
}

export function publishWorkspaceFinalizationSuccessDialog(success: WorkspaceFinalizationSuccess): void {
  publish({ ...state, finalizationSuccessDialog: { ...state.finalizationSuccessDialog, ...success } });
}

export function setWorkspaceFinalizationSuccessDialogVisible(visible: boolean): void {
  if (state.finalizationSuccessDialog.visible === visible) return;
  publish({ ...state, finalizationSuccessDialog: { ...state.finalizationSuccessDialog, visible } });
}

export function publishWorkspacePageThumbnails(thumbnails: readonly { readonly pageIndex: number; readonly src: string }[]): void {
  const pageThumbnails = new Map(state.pageThumbnails);
  for (const thumbnail of thumbnails) pageThumbnails.set(thumbnail.pageIndex, thumbnail.src);
  publish({ ...state, pageThumbnails });
}

export function setWorkspaceCurrentCanvasPage(pageIndex: number): void {
  if (state.currentCanvasPage === pageIndex) return;
  publish({ ...state, currentCanvasPage: pageIndex });
}

export function setWorkspaceBoundaryDraft(draft: WorkspaceBoundaryDraft): void {
  publish({ ...state, boundaryDraft: draft });
}

export function setWorkspaceBoundaryDraftApplying(applying: boolean): void {
  if (state.boundaryDraftApplying === applying) return;
  publish({ ...state, boundaryDraftApplying: applying });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot(): WorkspaceState {
  return state;
}

export function workspaceStateSnapshot(): WorkspaceState {
  return snapshot();
}

export function useWorkspaceState(): WorkspaceState {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
