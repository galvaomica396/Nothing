// Single-document masking-run controller (docs/CODE_REVIEW_2026-07-04.md
// composition root 분리: masking-run 모듈).
//
// Owns the single-document masking orchestration that used to live inline in
// compositionRoot.ts: the maskingRunning guard, option/region validation, base
// masking progress, the run_masking_pipeline result + safe-report adoption
// (parseSafeReport gates the save flow), preview-PDF selection, and text-compare
// hydration. The raw `invoke<MaskingResult>("run_masking_pipeline", …)` IPC call
// stays in the composition root (injected here as `runMaskingPipeline`) so the
// masking-IPC contract remains anchored in the bootstrap module.
//
// composition root destructures `runMaskingForSelectedDocument` into a same-named
// local const, so its call sites (btnRunMasking, btnRunKeywords, document-batch
// controller) stay unchanged.

import { parseRestoreCapability } from "../../services/tauri/maskingContracts";
import type { ApplyManualActionV1Request, LegacyMaskingResult, LegacyMaskingRunOutcome, MaskingOptions, ResolveMaskingReviewRequest, RestoreCapabilityRequest } from "../../services/tauri/maskingContracts";
import { adoptGeneratedPreview } from "../../state/documentProvenance";
import type { ApplicationSessionState } from "../../app/compositionRoot";
import { parseAnalysisManifestV1, parseBoundSafeReport, parseLegacySafeReport } from "../../state/maskingSession";
import type { AnalysisManifestV1, BaseMaskingProgress, BoundSafeReport, PdfPointsTopLeftRect, ReviewItemV1, SafeReport } from "../../state/maskingSession";
import { BUNDLED_PUBLIC_THRESHOLD } from "../../publicThreshold";
import { presentMaskingFailure } from "../save-gate/saveGate";
import { currentSettings } from "../../state/settingsStore";

export type {
  LegacyMaskingReportPayload as MaskingReportPayload,
  LegacyMaskingResult as MaskingResult,
  LegacyMaskingRunOutcome as MaskingRunOutcome,
  MaskingOptions,
} from "../../services/tauri/maskingContracts";

export type MaskingPipelineArgs = {
  readonly inputFile: string;
  readonly originalFile: string;
  readonly outputDir: string;
  readonly opts: MaskingOptions;
};

export type MaskingRunOptions = {
  outputDirOverride?: string;
  statusPrefix?: string;
};

// The slice of the composition root shared `state` object this controller reads and
// mutates. The full closure state object satisfies this structurally.
export type MaskingRunState = Pick<
  ApplicationSessionState,
  | "maskingRunning"
  | "savingInFlight"
  | "documentProvenance"
  | "latestExtractedPath"
  | "latestMaskedPath"
  | "latestMaskedTextPolicy"
  | "latestReport"
  | "latestReportPath"
  | "activeRunKind"
  | "publicRunIdentity"
  | "baseExtractedText"
  | "baseMaskedText"
  | "initialMaskingPreviewPdf"
  | "initialExtractedText"
  | "initialMaskedText"
  | "preManualPreviewPdf"
  | "preManualExtractedText"
  | "preManualMaskedText"
  | "boxes"
  | "geometryDraft"
  | "documentEditRevision"
  | "selectedCanvasBoxIndex"
  | "origDoc"
  | "currentOrigPage"
  | "currentResultPage"
  | "syncPages"
  | "resultDoc"
  | "lastPreviewDiagnostics"
>;

export type MaskingRunDeps = {
  readonly state: MaskingRunState;
  readonly inputPathEl: HTMLInputElement;
  readonly isPdfInput: () => boolean;
  readonly isCustomRegionScope: () => boolean;
  readonly getResultSourcePath: () => string;
  readonly analyzeMaskingRun: (request: { readonly inputFile: string; readonly profile: "internal_review" | "official_dispatch" | "mixed"; readonly options: MaskingOptions }) => Promise<unknown>;
  readonly resolveMaskingReview: (request: ResolveMaskingReviewRequest, report: BoundSafeReport) => Promise<unknown>;
  readonly applyManualActionV1: (request: ApplyManualActionV1Request, report: BoundSafeReport) => Promise<unknown>;
  readonly issueRestoreCapability: (request: RestoreCapabilityRequest, report: BoundSafeReport) => Promise<unknown>;
  readonly readTextFile: (path: string) => Promise<string>;
  readonly ensurePreviewWorkDir: () => Promise<string>;
  readonly collectMaskingOptions: () => MaskingOptions;
  readonly clampPage: (page: number, doc: any | null) => number;
  readonly loadPdfDoc: (path: string) => Promise<any>;
  readonly loadResultPdf: (path: string) => Promise<boolean>;
  readonly renderCompare: () => Promise<void>;
  readonly redrawOverlay?: () => void;
  readonly setCompareMode: (mode: "pdf" | "text") => void;
  readonly setStatus: (message: string, detailTitle?: string) => void;
  readonly setBaseMaskingProgress: (progress: BaseMaskingProgress) => void;
  readonly setTextCompareContents: (extractedText: string, maskedText: string) => void;
  readonly renderFinalState: (report: SafeReport | null) => void;
  readonly renderDocumentReviewSurfaces: () => void;
  readonly resetDerivedArtifacts: () => void;
  readonly updateWorkflowReadiness: () => void;
  readonly updateCanvasControls: () => void;
  readonly cancelGeometryDraft?: () => boolean;
  readonly onReviewResolutionFailure?: (request: ResolveMaskingReviewRequest, error: unknown) => void;
  readonly runMaskingPipeline: (args: MaskingPipelineArgs) => Promise<LegacyMaskingResult>;
};

export type MaskingRunController = {
  readonly runMaskingForSelectedDocument: (options?: MaskingRunOptions) => Promise<LegacyMaskingRunOutcome | null>;
  readonly resolveReview: (request: ResolveMaskingReviewRequest) => Promise<boolean>;
  readonly applyPublicManualMaskActions: (actions: readonly { readonly page: number; readonly rects: readonly PdfPointsTopLeftRect[]; readonly mode: "mask" | "restore"; readonly gestureTrusted?: boolean }[]) => Promise<boolean>;
};

export function createMaskingRunController(deps: MaskingRunDeps): MaskingRunController {
  const { state } = deps;
  let maskingRunGeneration = 0;
  const documentPageCount = (): number => {
    const pages = Number(state.origDoc?.numPages ?? 0);
    return Number.isSafeInteger(pages) && pages > 0 ? pages : 0;
  };
  const progressFor = (
    status: BaseMaskingProgress["status"],
    percent: number,
    details: Pick<BaseMaskingProgress, "currentPage" | "totalPages" | "detectedItems"> = {},
  ): BaseMaskingProgress => ({
    status,
    percent,
    displayMode: currentSettings().displayMode,
    totalPages: details.totalPages ?? documentPageCount(),
    currentPage: details.currentPage ?? 0,
    detectedItems: details.detectedItems ?? 0,
  });
  function bindPublicManifest(
    manifest: AnalysisManifestV1,
    identity = state.publicRunIdentity,
  ): BoundSafeReport | null {
    if (!identity
      || identity.runId !== manifest.runId
      || identity.originalDocumentHash !== manifest.originalDocumentHash
      || identity.analysisRevision !== manifest.analysisRevision
      || identity.manifestHash !== manifest.manifestHash
      || identity.profile !== manifest.profile) {
      return null;
    }
    const parsed = parseBoundSafeReport({
      product_checks: {},
      analysisManifest: manifest,
      reviewQueue: manifest.reviewItems,
    }, identity);
    return parsed.ok ? parsed.value : null;
  }
  function identityForManifest(manifest: AnalysisManifestV1): NonNullable<MaskingRunState["publicRunIdentity"]> {
    return Object.freeze({
      runId: manifest.runId,
      originalDocumentHash: manifest.originalDocumentHash,
      analysisRevision: manifest.analysisRevision,
      manifestHash: manifest.manifestHash,
      profile: manifest.profile,
    });
  }
  const cancelGeometryDraft = (): boolean => {
    if (!state.geometryDraft) return true;
    if (deps.cancelGeometryDraft) return deps.cancelGeometryDraft();
    state.geometryDraft = null;
    return true;
  };

  function safeFailureCode(error: unknown): string {
    return presentMaskingFailure(error).code;
  }

  async function hydrateTextFromPaths(reportOutputs: any, currentExtracted: string, currentMasked: string, extractedPathHint = "", maskedPathHint = "") {
    let extracted = currentExtracted || "";
    let masked = currentMasked || "";
    const extractedPath = reportOutputs?.extracted_file || extractedPathHint || "";
    const maskedPath = reportOutputs?.masked_file || maskedPathHint || "";

    // 읽기 실패를 "옵션 꺼짐(경로 없음)"과 구분해 별도 진단으로 보고한다(M-6).
    const loadErrors: string[] = [];
    if (!extracted && extractedPath) {
      try {
        extracted = await deps.readTextFile(extractedPath);
      } catch (error) {
        loadErrors.push(`추출본(${safeFailureCode(error)})`);
      }
    }
    if (!masked && maskedPath) {
      try {
        masked = await deps.readTextFile(maskedPath);
      } catch (error) {
        loadErrors.push(`마스킹본(${safeFailureCode(error)})`);
      }
    }
    return { extracted, masked, loadError: loadErrors.join(" | ") };
  }

  async function runMaskingForSelectedDocument(options: MaskingRunOptions = {}): Promise<LegacyMaskingRunOutcome | null> {
    const statusPrefix = options.statusPrefix || "마스킹";
    if (state.maskingRunning) {
      deps.setStatus("마스킹이 이미 실행 중입니다. 완료 후 다시 시도하세요.");
      return null;
    }
    if (state.savingInFlight) {
      deps.setStatus("최종 저장 중에는 마스킹을 다시 실행할 수 없습니다.");
      return null;
    }
    if (!state.documentProvenance.original.path) {
      deps.setStatus("먼저 문서를 선택하세요.");
      return null;
    }
    if (state.documentProvenance.continuation?.state === "unavailable") {
      deps.setStatus("저장된 PDF를 작업공간에서 다시 열 수 없습니다. PDF를 다시 선택하거나 열어주세요.");
      return null;
    }
    if (deps.isCustomRegionScope() && !currentSettings().customRegions.trim()) {
      deps.setStatus("사용자 지정 지역을 선택했으면 지역명을 입력하세요.");
      return null;
    }
    const originalPdfForRestore = state.documentProvenance.original.path;
    const inputPdfForRun = deps.isPdfInput() ? deps.getResultSourcePath() || originalPdfForRestore : originalPdfForRestore;
    const opts = deps.collectMaskingOptions();
    const generation = ++maskingRunGeneration;
    const runSnapshot = { ...state };
    const restoreRunSnapshot = (): void => {
      const mutableState = state as Record<string, unknown>;
      for (const key of Object.keys(mutableState)) {
        if (!(key in runSnapshot)) delete mutableState[key];
      }
      Object.assign(state, runSnapshot);
    };
    let runProvenance = state.documentProvenance;
    const sessionIsCurrent = () => generation === maskingRunGeneration && state.documentProvenance === runProvenance;
    deps.setStatus(`${statusPrefix} 실행 중...`);
    state.maskingRunning = true;
    deps.updateWorkflowReadiness();
    deps.updateCanvasControls();
    try {
      deps.setBaseMaskingProgress(progressFor("running", 0));
      if (opts.profile !== "legal") {
        if (!cancelGeometryDraft()) throw new Error("invalid_geometry_draft");
        const analyzed = await deps.analyzeMaskingRun({
          inputFile: inputPdfForRun,
          profile: opts.profile,
          options: opts,
        });
        deps.setBaseMaskingProgress(progressFor("running", 45));
        let analysisValue: unknown = analyzed;
        if (typeof analyzed === "string") {
          try {
            analysisValue = JSON.parse(analyzed);
          } catch {
            throw new Error("invalid_analysis_manifest");
          }
        }
        const manifest = parseAnalysisManifestV1(analysisValue);
        if (!sessionIsCurrent()) return null;
        if (!manifest.ok) {
          throw new Error("invalid_analysis_manifest");
        }
        if (
          manifest.value.thresholdVersion !== BUNDLED_PUBLIC_THRESHOLD.version
          || manifest.value.thresholdHash !== BUNDLED_PUBLIC_THRESHOLD.contentHash
          || manifest.value.thresholdArtifact.autoMaskThreshold !== opts.auto_mask_threshold
          || manifest.value.thresholdArtifact.reviewThreshold !== opts.review_threshold
        ) {
          throw new Error("invalid_threshold_authority");
        }
        const nextIdentity = identityForManifest(manifest.value);
        const authoritativeReport = bindPublicManifest(manifest.value, nextIdentity);
        if (!authoritativeReport) {
          throw new Error("invalid_analysis_authority");
        }
        const manifestPageCount = manifest.value.segments.reduce(
          (maximum, segment) => Math.max(maximum, segment.pageEnd + 1),
          0,
        );
        const totalPages = Math.max(documentPageCount(), manifestPageCount);
        deps.setBaseMaskingProgress(progressFor("running", 80, {
          currentPage: totalPages,
          totalPages,
          detectedItems: manifest.value.occurrences.length,
        }));
        // resetDerivedArtifacts clears the rendered result document, but
        // analysis completion must not steal the page the user was viewing.
        // Capture the live values immediately before reset so a page change
        // made while the analysis ran is also preserved.
        const pagesBeforeAnalysisReset = {
          original: state.currentOrigPage,
          result: state.currentResultPage,
        };
        deps.resetDerivedArtifacts();
        runProvenance = state.documentProvenance;
        state.resultDoc = state.origDoc;
        state.currentOrigPage = deps.clampPage(pagesBeforeAnalysisReset.original, state.origDoc);
        state.currentResultPage = state.syncPages
          ? deps.clampPage(state.currentOrigPage, state.resultDoc)
          : deps.clampPage(pagesBeforeAnalysisReset.result, state.resultDoc);
        deps.setCompareMode("pdf");
        await deps.renderCompare();
        if (!sessionIsCurrent()) return null;
        state.publicRunIdentity = nextIdentity;
        state.latestReport = authoritativeReport;
        state.activeRunKind = "public";
        state.latestReportPath = "";
        state.latestExtractedPath = "";
        state.latestMaskedPath = "";
        state.latestMaskedTextPolicy = "";
        state.baseExtractedText = "";
        state.baseMaskedText = "";
        state.boxes = [];
        state.documentEditRevision = (state.documentEditRevision || 0) + 1;
        state.selectedCanvasBoxIndex = -1;
        deps.redrawOverlay?.();
        deps.setBaseMaskingProgress(progressFor("complete", 100, {
          currentPage: totalPages,
          totalPages,
          detectedItems: manifest.value.occurrences.length,
        }));
        deps.renderFinalState(state.latestReport);
        deps.renderDocumentReviewSurfaces();
        deps.setStatus(`${statusPrefix} 분석 완료: 검토 항목 ${manifest.value.reviewItems.length}건`);
        deps.updateWorkflowReadiness();
        return { outcome: "analysis", manifest: manifest.value };
      }
      const runOutdir = options.outputDirOverride || (await deps.ensurePreviewWorkDir());
      const result = await deps.runMaskingPipeline({
        inputFile: inputPdfForRun,
        originalFile: deps.isPdfInput() ? originalPdfForRestore : "",
        outputDir: runOutdir,
        opts,
      });
      if (!sessionIsCurrent()) return null;
      deps.setBaseMaskingProgress(progressFor("running", 70));
      const pagesBeforePreviewReset = {
        original: state.currentOrigPage,
        result: state.currentResultPage,
      };
      deps.resetDerivedArtifacts();
      runProvenance = state.documentProvenance;
      state.currentOrigPage = pagesBeforePreviewReset.original;
      state.currentResultPage = state.syncPages
        ? pagesBeforePreviewReset.original
        : pagesBeforePreviewReset.result;
      deps.inputPathEl.value = originalPdfForRestore;
      deps.setStatus(`${statusPrefix} 실행 완료: 결과 파싱 성공`);

      const reportOutputs: Record<string, any> = result.runtime_manifest?.outputs ?? result.report?.outputs ?? {};
      state.latestExtractedPath = reportOutputs?.extracted_file || result.extracted_path || "";
      state.latestMaskedPath = reportOutputs?.masked_file || result.masked_path || "";
      state.latestMaskedTextPolicy = state.latestMaskedPath ? opts.deidentification_policy : "";
      const artifactPath = reportOutputs?.masked_pdf_file || reportOutputs?.preview_pdf_source_file || "";
      runProvenance = state.documentProvenance;
      // F-2: run_masking_pipeline 응답의 report를 최소 스키마로 검증 후 채택한다.
      // 검증 실패 시 리포트/경로를 무효화한다(저장은 막지 않고, 경고 산출 입력에서 제외).
      const adoptedReport = parseLegacySafeReport(result.report);
      if (adoptedReport.ok) {
        state.latestReport = adoptedReport.value;
        state.latestReportPath = reportOutputs?.safe_report_path || result.report_path || "";
        state.activeRunKind = "legal";
      } else {
        state.latestReport = null;
        state.latestReportPath = "";
        state.activeRunKind = "none";
        state.lastPreviewDiagnostics = "마스킹 보고서 검증 실패 (invalid)";
      }
      deps.renderFinalState(state.latestReport);
      const hydratedTexts = await hydrateTextFromPaths(
        reportOutputs,
        result.extracted_text || "",
        result.masked_text || "",
        result.extracted_path || "",
        result.masked_path || "",
      );
      if (!sessionIsCurrent()) return null;
      state.baseExtractedText = hydratedTexts.extracted || "";
      state.baseMaskedText = hydratedTexts.masked || "";
      deps.setBaseMaskingProgress(progressFor("complete", 100, {
        currentPage: documentPageCount(),
      }));
      deps.setTextCompareContents(state.baseExtractedText, state.baseMaskedText);
      deps.renderDocumentReviewSurfaces();

      const previewCandidates = deps.isPdfInput()
        ? [
            reportOutputs.preview_pdf_source_file,
            reportOutputs.masked_pdf_file,
            typeof result.masked_path === "string" && result.masked_path.toLowerCase().endsWith(".pdf") ? result.masked_path : "",
          ].filter((candidate) => candidate && candidate !== originalPdfForRestore)
        : [];
      let previewCandidate = "";
      for (const candidate of previewCandidates) {
        if (!candidate) continue;
        try {
          if (!(await deps.loadResultPdf(candidate))) throw new Error("invalid_preview_pdf");
          if (!sessionIsCurrent()) return null;
          previewCandidate = candidate;
          break;
        } catch (error) {
          if (!sessionIsCurrent()) return null;
          state.lastPreviewDiagnostics = `수정본 PDF 로드 실패 (${safeFailureCode(error)})`;
        }
      }
      if (!previewCandidate) {
        throw new Error("invalid_preview_pdf");
      }

      state.documentProvenance = adoptGeneratedPreview(state.documentProvenance, previewCandidate, artifactPath);
      runProvenance = state.documentProvenance;
      state.initialMaskingPreviewPdf = previewCandidate;
      state.initialExtractedText = state.baseExtractedText || "";
      state.initialMaskedText = state.baseMaskedText || "";
      state.preManualPreviewPdf = previewCandidate;
      state.preManualExtractedText = state.baseExtractedText || "";
      state.preManualMaskedText = state.baseMaskedText || "";
      state.boxes = [];
      state.documentEditRevision = (state.documentEditRevision || 0) + 1;
      state.selectedCanvasBoxIndex = -1;
      if (!state.origDoc) {
        try {
          const originalDoc = await deps.loadPdfDoc(originalPdfForRestore);
          if (!sessionIsCurrent()) return null;
          state.origDoc = originalDoc;
        } catch (error) {
          if (!sessionIsCurrent()) return null;
          state.lastPreviewDiagnostics = `원본 미리보기를 복구하지 못했습니다 (${safeFailureCode(error)}).`;
        }
      }
      state.currentOrigPage = deps.clampPage(pagesBeforePreviewReset.original, state.origDoc);
      state.currentResultPage = state.syncPages
        ? deps.clampPage(state.currentOrigPage, state.resultDoc)
        : deps.clampPage(pagesBeforePreviewReset.result, state.resultDoc);
      if (!sessionIsCurrent()) return null;
      deps.setCompareMode("pdf");
      deps.redrawOverlay?.();
      const pdfMsg = "pdf 미리보기 생성";
      const textMsg = hydratedTexts.loadError
        ? `미리보기 텍스트 로드 실패: ${hydratedTexts.loadError}`
        : hydratedTexts.extracted || hydratedTexts.masked
          ? "텍스트 비교 채움"
          : "텍스트 비교 비어있음";
      deps.setStatus(
        `${statusPrefix} 완료(${pdfMsg}, ${textMsg})`,
      );
      deps.updateWorkflowReadiness();
      return { outcome: "masked", ...result };
    } catch (error) {
      if (sessionIsCurrent()) {
        const failure = presentMaskingFailure(error);
        restoreRunSnapshot();
        deps.setBaseMaskingProgress(progressFor("failed", 0));
        deps.setStatus(`${statusPrefix} 실패 (${failure.code} · ${failure.hint}): 작업을 완료하지 못했습니다.`);
        deps.updateWorkflowReadiness();
      }
      return null;
    } finally {
      if (sessionIsCurrent()) {
        state.maskingRunning = false;
        deps.updateWorkflowReadiness();
        deps.updateCanvasControls();
      }
    }
  }
  const sameRects = (left: readonly { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number }[], right: readonly { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number }[]): boolean =>
    left.length === right.length && left.every((rect, index) => rect.x0 === right[index]?.x0 && rect.y0 === right[index]?.y0 && rect.x1 === right[index]?.x1 && rect.y1 === right[index]?.y1);
  const rectsOverlap = (
    left: { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number },
    right: { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number },
  ): boolean =>
    left.x0 < right.x1 && left.x1 > right.x0 && left.y0 < right.y1 && left.y1 > right.y0;
  const sameReasons = (left: readonly string[], right: readonly string[]): boolean =>
    left.length === right.length && left.every((reason, index) => reason === right[index]);
  const sameCoverage = (left: Readonly<Record<string, string>>, right: Readonly<Record<string, string>>): boolean => {
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length && keys.every((key) => left[key] === right[key]);
  };
  const coverageChangesResolveOnlyTarget = (
    current: AnalysisManifestV1,
    next: AnalysisManifestV1,
    targetKind: AnalysisManifestV1["regions"][number]["kind"],
  ): boolean => {
    const changes = [
      { key: "approval", current: current.approvalCoverage.approval, next: next.approvalCoverage.approval },
      { key: "header_meta", current: current.approvalCoverage.header_meta, next: next.approvalCoverage.header_meta },
      { key: "labeled_staff", current: current.approvalCoverage.labeled_staff, next: next.approvalCoverage.labeled_staff },
      { key: "recipient_reference", current: current.requiredRegionCoverage.recipient_reference, next: next.requiredRegionCoverage.recipient_reference },
      { key: "sender_institution", current: current.requiredRegionCoverage.sender_institution, next: next.requiredRegionCoverage.sender_institution },
      { key: "approval_staff", current: current.requiredRegionCoverage.approval_staff, next: next.requiredRegionCoverage.approval_staff },
      { key: "dispatch_metadata", current: current.requiredRegionCoverage.dispatch_metadata, next: next.requiredRegionCoverage.dispatch_metadata },
      { key: "footer_contact", current: current.requiredRegionCoverage.footer_contact, next: next.requiredRegionCoverage.footer_contact },
    ].filter((entry) => entry.current !== entry.next);
    return changes.length === 0 || (changes.length === 1
      && changes[0]?.key === targetKind
      && changes[0].current === "indeterminate"
      && changes[0].next !== "indeterminate");
  };
  const sameSegment = (left: AnalysisManifestV1["segments"][number], right: AnalysisManifestV1["segments"][number], includeState = true): boolean =>
    left.pageStart === right.pageStart && left.pageEnd === right.pageEnd && left.kind === right.kind
      && left.commonOnly === right.commonOnly && left.source === right.source && (!includeState || left.state === right.state);
  const sameRegion = (left: AnalysisManifestV1["regions"][number], right: AnalysisManifestV1["regions"][number], includeGeometry = true): boolean =>
    left.page === right.page && left.kind === right.kind && left.state === right.state
      && left.confirmationSource === right.confirmationSource && left.source === right.source
      && sameReasons(left.reasonCodes, right.reasonCodes) && (!includeGeometry || sameRects(left.rects, right.rects));
  const sameOccurrence = (left: AnalysisManifestV1["occurrences"][number], right: AnalysisManifestV1["occurrences"][number], includeResolution = true): boolean =>
    left.page === right.page && left.tag === right.tag && left.category === right.category
      && left.valueHash === right.valueHash && left.expectedTextHash === right.expectedTextHash
      && left.source === right.source && left.policy === right.policy && left.provenance === right.provenance
      && sameRects(left.rects, right.rects)
      && (!includeResolution || (left.proposedAction === right.proposedAction && left.state === right.state));
  const uniqueMatch = <T>(items: readonly T[], predicate: (item: T) => boolean): T | null => {
    const matches = items.filter(predicate);
    return matches.length === 1 ? matches[0]! : null;
  };
  function sameReviewBasis(left: ReviewItemV1, right: ReviewItemV1): boolean {
    return left.kind === right.kind
      && left.pageStart === right.pageStart
      && left.pageEnd === right.pageEnd
      && left.requiresAcknowledgment === right.requiresAcknowledgment
      && left.commonOnly === right.commonOnly
      && left.provenance === right.provenance
      && sameReasons(left.reasonCodes, right.reasonCodes);
  }
  function sameReviewTarget(
    current: AnalysisManifestV1,
    next: AnalysisManifestV1,
    left: ReviewItemV1,
    right: ReviewItemV1,
  ): boolean {
    if (left.kind !== right.kind) return false;
    if (left.kind === "name" || left.kind === "institution") {
      const target = current.occurrences.find((item) => item.occurrenceId === left.targetId);
      const successor = next.occurrences.find((item) => item.occurrenceId === right.targetId);
      return !!target && !!successor && sameOccurrence(target, successor);
    }
    if (left.kind === "region_geometry") {
      const target = current.regions.find((item) => item.regionId === left.targetId);
      const successor = next.regions.find((item) => item.regionId === right.targetId);
      return !!target && !!successor && sameRegion(target, successor);
    }
    const target = current.segments.find((item) => item.segmentId === left.targetId);
    const successor = next.segments.find((item) => item.segmentId === right.targetId);
    return !!target && !!successor && sameSegment(target, successor);
  }
  function exactManualAction(left: AnalysisManifestV1["manualActions"][number], right: AnalysisManifestV1["manualActions"][number]): boolean {
    return left.actionId === right.actionId && left.analysisRevision === right.analysisRevision
      && left.page === right.page && left.mode === right.mode && left.sourceKind === right.sourceKind
      && left.linkedOccurrenceId === right.linkedOccurrenceId && left.expectedTextHash === right.expectedTextHash
      && (left.restoreAuthorizationHash ?? null) === (right.restoreAuthorizationHash ?? null)
      && sameRects(left.rects, right.rects) && sameRects(left.protectedNeighborRefs, right.protectedNeighborRefs);
  }
  function manualActionsPreserved(current: AnalysisManifestV1, next: AnalysisManifestV1, createsRevision: boolean): boolean {
    if (next.manualActions.length !== current.manualActions.length) return false;
    const matched = new Set<AnalysisManifestV1["manualActions"][number]>();
    for (const action of current.manualActions) {
      const successor = uniqueMatch(next.manualActions, (candidate) => {
        if (createsRevision) {
          if (action.page !== candidate.page || action.mode !== candidate.mode || action.sourceKind !== candidate.sourceKind
            || action.expectedTextHash !== candidate.expectedTextHash || !sameRects(action.rects, candidate.rects)
            || !sameRects(action.protectedNeighborRefs, candidate.protectedNeighborRefs)
            || (action.restoreAuthorizationHash ?? null) !== (candidate.restoreAuthorizationHash ?? null)) return false;
          if (action.linkedOccurrenceId === null || candidate.linkedOccurrenceId === null) {
            return action.linkedOccurrenceId === candidate.linkedOccurrenceId;
          }
          const occurrence = current.occurrences.find((item) => item.occurrenceId === action.linkedOccurrenceId);
          const occurrenceSuccessor = next.occurrences.find((item) => item.occurrenceId === candidate.linkedOccurrenceId);
          return !!occurrence && !!occurrenceSuccessor && sameOccurrence(occurrence, occurrenceSuccessor);
        }
        return exactManualAction(action, candidate);
      });
      if (!successor || matched.has(successor)) return false;
      matched.add(successor);
    }
    return matched.size === current.manualActions.length;
  }
  function revisionScopedIdsAreDisjoint(current: AnalysisManifestV1, next: AnalysisManifestV1): boolean {
    const currentIds = new Set([
      ...current.segments.map((item) => item.segmentId),
      ...current.regions.map((item) => item.regionId),
      ...current.occurrences.map((item) => item.occurrenceId),
      ...current.reviewItems.map((item) => item.reviewId),
      ...current.manualActions.map((item) => item.actionId),
    ]);
    return ![
      ...next.segments.map((item) => item.segmentId),
      ...next.regions.map((item) => item.regionId),
      ...next.occurrences.map((item) => item.occurrenceId),
      ...next.reviewItems.map((item) => item.reviewId),
      ...next.manualActions.map((item) => item.actionId),
    ].some((id) => currentIds.has(id));
  }
  function revisionReferencesFollowSemanticSuccessors(
    current: AnalysisManifestV1,
    next: AnalysisManifestV1,
    targetSegment: AnalysisManifestV1["segments"][number] | null,
    successorSegment: AnalysisManifestV1["segments"][number] | null,
    targetRegion: AnalysisManifestV1["regions"][number] | null,
    successorRegion: AnalysisManifestV1["regions"][number] | null,
  ): boolean {
    const segmentSuccessor = (segment: AnalysisManifestV1["segments"][number]) => segment === targetSegment
      ? successorSegment
      : uniqueMatch(next.segments, (candidate) => sameSegment(segment, candidate));
    const regionSuccessor = (region: AnalysisManifestV1["regions"][number]) => region === targetRegion
      ? successorRegion
      : uniqueMatch(next.regions, (candidate) => sameRegion(region, candidate));
    for (const region of current.regions) {
      const successor = regionSuccessor(region);
      const segment = current.segments.find((item) => item.segmentId === region.segmentId);
      const successorSegmentForRegion = segment && segmentSuccessor(segment);
      if (!successor || !successorSegmentForRegion || successor.segmentId !== successorSegmentForRegion.segmentId) return false;
    }
    for (const occurrence of current.occurrences) {
      const successor = uniqueMatch(next.occurrences, (candidate) => sameOccurrence(occurrence, candidate));
      const segment = current.segments.find((item) => item.segmentId === occurrence.segmentId);
      const successorSegmentForOccurrence = segment && segmentSuccessor(segment);
      const region = occurrence.regionId === null ? null : current.regions.find((item) => item.regionId === occurrence.regionId);
      if (occurrence.regionId !== null && !region) return false;
      const successorRegionForOccurrence = region ? regionSuccessor(region) : null;
      if (!successor || !successorSegmentForOccurrence || successor.segmentId !== successorSegmentForOccurrence.segmentId
        || successor.regionId !== (successorRegionForOccurrence?.regionId ?? null)) return false;
    }
    return true;
  }
  function reviewsOnlyResolveTarget(
    current: AnalysisManifestV1,
    next: AnalysisManifestV1,
    prior: ReviewItemV1,
    successor: ReviewItemV1,
    createsRevision: boolean,
  ): boolean {
    if (next.reviewItems.length !== current.reviewItems.length || successor.status !== "resolved") return false;
    const matched = new Set<ReviewItemV1>();
    for (const review of current.reviewItems) {
      if (review === prior) continue;
      const candidate = uniqueMatch(next.reviewItems, (item) => item !== successor
        && item.status === review.status
        && sameReviewBasis(review, item)
        && (createsRevision
          ? sameReviewTarget(current, next, review, item)
          : item.reviewId === review.reviewId && item.targetId === review.targetId));
      if (!candidate || matched.has(candidate)) return false;
      matched.add(candidate);
    }
    return matched.size === current.reviewItems.length - 1
      && next.reviewItems.filter((item) => item.status === "resolved"
        && sameReviewBasis(prior, item) && item.targetId === successor.targetId).length === 1;
  }
  function validReviewTransition(
    current: AnalysisManifestV1,
    next: AnalysisManifestV1,
    request: ResolveMaskingReviewRequest,
  ): boolean {
    const resolution = request.resolution;
    const createsRevision = resolution.kind === "boundary" || resolution.kind === "region_geometry";
    if (
      next.runId !== current.runId
      || next.analysisRevision !== current.analysisRevision + (createsRevision ? 1 : 0)
      || next.manifestHash === current.manifestHash || next.originalDocumentHash !== current.originalDocumentHash
      || next.profile !== current.profile || next.policyVersion !== current.policyVersion
      || next.optionsVersion !== current.optionsVersion || next.optionsHash !== current.optionsHash
      || next.thresholdVersion !== current.thresholdVersion || next.thresholdHash !== current.thresholdHash
      || next.thresholdArtifact.version !== current.thresholdArtifact.version
      || next.thresholdArtifact.contentHash !== current.thresholdArtifact.contentHash
      || next.thresholdArtifact.autoMaskThreshold !== current.thresholdArtifact.autoMaskThreshold
      || next.thresholdArtifact.reviewThreshold !== current.thresholdArtifact.reviewThreshold
      || next.coordinateSpace !== current.coordinateSpace
      || (createsRevision && !revisionScopedIdsAreDisjoint(current, next))
      || !manualActionsPreserved(current, next, createsRevision)
    ) return false;
    const prior = current.reviewItems.find((item) => item.reviewId === request.reviewId);
    if (!prior || prior.kind !== resolution.kind || prior.status !== "pending" || resolution.kind === "ocr") return false;
    if (resolution.kind !== "region_geometry"
      && (!sameCoverage(next.approvalCoverage, current.approvalCoverage)
        || !sameCoverage(next.requiredRegionCoverage, current.requiredRegionCoverage))) return false;

    if (resolution.kind === "name" || resolution.kind === "institution") {
      const expectedAction = resolution.action;
      const target = current.occurrences.find((item) => item.occurrenceId === prior.targetId);
      const successor = target && uniqueMatch(next.occurrences, (item) => sameOccurrence(target, item, false)
        && item.proposedAction === expectedAction && item.state === "confirmed");
      if (!target || !successor
        || current.occurrences.some((item) => item !== target && !uniqueMatch(next.occurrences, (candidate) => sameOccurrence(item, candidate)))
        || next.occurrences.length !== current.occurrences.length
        || next.segments.length !== current.segments.length || next.regions.length !== current.regions.length
        || !next.segments.every((item) => uniqueMatch(current.segments, (candidate) => sameSegment(item, candidate)))
        || !next.regions.every((item) => uniqueMatch(current.regions, (candidate) => sameRegion(item, candidate)))) return false;
      const review = uniqueMatch(next.reviewItems, (item) => sameReviewBasis(prior, item) && item.targetId === successor.occurrenceId);
      return !!review && reviewsOnlyResolveTarget(current, next, prior, review, false);
    }

    if (resolution.kind === "acknowledge") {
      const target = current.segments.find((item) => item.segmentId === prior.targetId);
      const successor = target && uniqueMatch(next.segments, (item) => sameSegment(target, item, false)
        && item.state === "user_confirmed");
      if (!target || !successor || next.segments.length !== current.segments.length
        || current.segments.some((item) => item !== target && !uniqueMatch(next.segments, (candidate) => sameSegment(item, candidate)))
        || next.occurrences.length !== current.occurrences.length || next.regions.length !== current.regions.length
        || !next.occurrences.every((item) => uniqueMatch(current.occurrences, (candidate) => sameOccurrence(item, candidate)))
        || !next.regions.every((item) => uniqueMatch(current.regions, (candidate) => sameRegion(item, candidate)))) return false;
      const review = uniqueMatch(next.reviewItems, (item) => sameReviewBasis(prior, item) && item.targetId === successor.segmentId);
      return !!review && reviewsOnlyResolveTarget(current, next, prior, review, false);
    }

    if (resolution.kind === "boundary") {
      const { pageStart, pageEnd, segmentKind } = resolution;
      const target = current.segments.find((item) => item.segmentId === prior.targetId);
      const successor = target && uniqueMatch(next.segments, (item) => item.pageStart === pageStart
        && item.pageEnd === pageEnd && item.kind === segmentKind
        && item.state === "user_confirmed" && item.commonOnly === target.commonOnly && item.source === target.source);
      if (!target || !successor || next.segments.length !== current.segments.length
        || current.segments.some((item) => item !== target && !uniqueMatch(next.segments, (candidate) => sameSegment(item, candidate)))
        || next.occurrences.length !== current.occurrences.length || next.regions.length !== current.regions.length
        || !next.occurrences.every((item) => uniqueMatch(current.occurrences, (candidate) => sameOccurrence(item, candidate)))
        || !next.regions.every((item) => uniqueMatch(current.regions, (candidate) => sameRegion(item, candidate)))
        || !revisionReferencesFollowSemanticSuccessors(current, next, target, successor, null, null)) return false;
      const review = uniqueMatch(next.reviewItems, (item) => sameReviewBasis(prior, item) && item.targetId === successor.segmentId);
      return !!review && reviewsOnlyResolveTarget(current, next, prior, review, true);
    }

    if (resolution.kind !== "region_geometry") return false;
    const target = current.regions.find((item) => item.regionId === prior.targetId);
    const successor = target && uniqueMatch(next.regions, (item) => item.page === target.page && item.kind === target.kind
      && item.source === target.source && sameReasons(item.reasonCodes, target.reasonCodes)
      && sameRects(item.rects, resolution.rects) && item.state === "user_confirmed"
      && item.confirmationSource === "user");
    if (!target || !successor || !coverageChangesResolveOnlyTarget(current, next, target.kind)
      || next.regions.length !== current.regions.length
      || current.regions.some((item) => item !== target && !uniqueMatch(next.regions, (candidate) => sameRegion(item, candidate)))
      || next.segments.length !== current.segments.length || next.occurrences.length !== current.occurrences.length
      || !next.segments.every((item) => uniqueMatch(current.segments, (candidate) => sameSegment(item, candidate)))
      || !next.occurrences.every((item) => uniqueMatch(current.occurrences, (candidate) => sameOccurrence(item, candidate)))
      || !revisionReferencesFollowSemanticSuccessors(current, next, null, null, target, successor)) return false;
    const review = uniqueMatch(next.reviewItems, (item) => sameReviewBasis(prior, item) && item.targetId === successor.regionId);
    return !!review && reviewsOnlyResolveTarget(current, next, prior, review, true);
  }

  async function resolveReview(request: ResolveMaskingReviewRequest): Promise<boolean> {
    const cancelRequestedGeometryDraft = (): boolean => state.geometryDraft?.owner === request.reviewId
      ? (deps.cancelGeometryDraft?.() ?? false)
      : false;
    const reject = (message: string): false => {
      cancelRequestedGeometryDraft();
      deps.setStatus?.(message);
      return false;
    };
    if (state.savingInFlight) return reject("최종 저장 중에는 검토 항목을 변경할 수 없습니다.");
    const report = state.latestReport;
    const current = report?.analysisManifest;
    const target = current?.reviewItems.find((item) => item.reviewId === request.reviewId);
    if (
      !current
      || !target
      || target.status !== "pending"
      || target.analysisRevision !== current.analysisRevision
      || request.runId !== current.runId
      || request.analysisRevision !== current.analysisRevision
      || request.manifestHash !== current.manifestHash
    ) {
      return reject("현재 검토 세션과 일치하지 않는 검토 요청입니다.");
    }
    const currentIdentity = state.publicRunIdentity;
    const currentBound = report ? bindPublicManifest(current, currentIdentity) : null;
    if (!currentBound || !currentIdentity) return reject("현재 서버 검토 세션을 검증하지 못했습니다.");
    let response: unknown;
    try {
      response = await deps.resolveMaskingReview(request, currentBound);
    } catch (error) {
      deps.onReviewResolutionFailure?.(request, error);
      return reject(`검토 항목을 서버에 반영하지 못했습니다 (${safeFailureCode(error)}). 현재 검토 세션은 유지됩니다.`);
    }
    const resolved = parseAnalysisManifestV1(response);
    const latest = state.latestReport?.analysisManifest;
    if (latest !== current) return false;
    if (
      !resolved.ok
      || latest.runId !== request.runId
      || latest.analysisRevision !== request.analysisRevision
      || latest.manifestHash !== request.manifestHash
      || !validReviewTransition(current, resolved.value, request)
    ) {
      return reject("서버 검토 응답을 검증하지 못했습니다. 현재 검토 세션은 유지됩니다.");
    }
    const nextIdentity = identityForManifest(resolved.value);
    const adoptedReport = bindPublicManifest(resolved.value, nextIdentity);
    if (!adoptedReport) return reject("서버 검토 응답의 세션 권위를 검증하지 못했습니다. 현재 검토 세션은 유지됩니다.");
    state.publicRunIdentity = nextIdentity;
    state.latestReport = adoptedReport;
    state.latestReportPath = "";
    deps.redrawOverlay?.();
    deps.renderFinalState(state.latestReport);
    deps.renderDocumentReviewSurfaces();
    deps.updateWorkflowReadiness();
    return true;
  }

  async function applyPublicManualMaskActions(actions: readonly { readonly page: number; readonly rects: readonly PdfPointsTopLeftRect[]; readonly mode: "mask" | "restore"; readonly gestureTrusted?: boolean }[]): Promise<boolean> {
    if (actions.length === 0 || state.savingInFlight) return false;
    for (const action of actions) {
      const current = state.latestReport?.analysisManifest;
      const currentIdentity = state.publicRunIdentity;
      const currentBound = current ? bindPublicManifest(current, currentIdentity) : null;
      if (!current || !currentIdentity || !currentBound || action.rects.length === 0) {
        deps.setStatus("공공 검토 세션에서 수동 보정을 반영할 수 없습니다.");
        return false;
      }
      let response: unknown;
      try {
        let request: ApplyManualActionV1Request = {
          runId: current.runId,
          analysisRevision: current.analysisRevision,
          manifestHash: current.manifestHash,
          page: action.page,
          rects: action.rects,
          mode: action.mode,
          sourceKind: "scan",
          linkedOccurrenceId: null,
          targetRegionId: null,
          expectedTextHash: null,
          protectedNeighborRefs: [],
          restoreCapability: null,
        };
        if (action.mode === "restore") {
          if (action.gestureTrusted !== true) {
            deps.setStatus("복원은 실제 캔버스 드래그로 선택한 확정 마스크에서만 허용됩니다.");
            return false;
          }
          const restoredOccurrenceIds = new Set(
            current.manualActions
              .filter((candidate) => candidate.mode === "restore" && candidate.linkedOccurrenceId !== null)
              .map((candidate) => candidate.linkedOccurrenceId),
          );
          const candidates = current.occurrences.filter((occurrence) =>
            occurrence.page === action.page
              && occurrence.proposedAction === "mask"
              && ["confirmed", "user_confirmed"].includes(occurrence.state)
              && !restoredOccurrenceIds.has(occurrence.occurrenceId)
              && occurrence.rects.some((occurrenceRect) =>
                action.rects.some((restoreRect) => rectsOverlap(occurrenceRect, restoreRect))),
          );
          if (candidates.length !== 1) {
            deps.setStatus("복원 대상 확정 마스크를 하나만 선택하세요.");
            return false;
          }
          const target = candidates[0];
          if (!target) return false;
          const capability = parseRestoreCapability(await deps.issueRestoreCapability({
            runId: current.runId,
            analysisRevision: current.analysisRevision,
            manifestHash: current.manifestHash,
            occurrenceId: target.occurrenceId,
            rects: target.rects,
            expectedTextHash: target.expectedTextHash,
          }, currentBound));
          if (!capability.ok) {
            deps.setStatus("복원 의도 권한을 발급받지 못했습니다. 확정 마스크를 다시 선택하세요.");
            return false;
          }
          request = {
            ...request,
            page: target.page,
            rects: target.rects,
            sourceKind: "text_pdf",
            linkedOccurrenceId: target.occurrenceId,
            expectedTextHash: target.expectedTextHash,
            restoreCapability: capability.value.capability,
          };
        }
        response = await deps.applyManualActionV1(request, currentBound);
      } catch (error) {
        deps.setStatus(`공공 수동 보정을 서버에 반영하지 못했습니다 (${safeFailureCode(error)}).`);
        return false;
      }
      const next = parseAnalysisManifestV1(response);
      if (!next.ok
        || next.value.runId !== current.runId
        || next.value.analysisRevision !== current.analysisRevision + 1
        || next.value.manualActions.length !== current.manualActions.length + 1
        || !next.value.manualActions.some((candidate) => candidate.analysisRevision === next.value.analysisRevision
          && candidate.page === action.page && candidate.mode === action.mode
          && (action.mode === "restore"
            ? candidate.sourceKind === "text_pdf"
              && candidate.linkedOccurrenceId !== null
              && candidate.restoreAuthorizationHash !== null
            : candidate.sourceKind === "scan"))) {
        deps.setStatus("공공 수동 보정 응답을 검증하지 못했습니다. 현재 검토 세션은 유지됩니다.");
        return false;
      }
      const nextIdentity = identityForManifest(next.value);
      const adopted = bindPublicManifest(next.value, nextIdentity);
      if (!adopted) {
        deps.setStatus("공공 수동 보정 세션 권위를 검증하지 못했습니다. 현재 검토 세션은 유지됩니다.");
        return false;
      }
      state.publicRunIdentity = nextIdentity;
      state.latestReport = adopted;
      state.latestReportPath = "";
    }
    deps.redrawOverlay?.();
    deps.renderFinalState(state.latestReport);
    deps.renderDocumentReviewSurfaces();
    deps.updateWorkflowReadiness();
    return true;
  }


  return { runMaskingForSelectedDocument, resolveReview, applyPublicManualMaskActions };
}
