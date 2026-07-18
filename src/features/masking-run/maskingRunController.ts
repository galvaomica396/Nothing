// Single-document masking-run controller (docs/CODE_REVIEW_2026-07-04.md
// "startLegacyApp 분리": masking-run 모듈).
//
// Owns the single-document masking orchestration that used to live inline in
// startLegacyApp.ts: the maskingRunning guard, option/region validation, base
// masking progress, the run_masking_pipeline result + safe-report adoption
// (parseSafeReport gates the save flow), preview-PDF selection, and text-compare
// hydration. The raw `invoke<MaskingResult>("run_masking_pipeline", …)` IPC call
// stays in startLegacyApp (injected here as `runMaskingPipeline`) so the legacy
// masking-IPC contract remains anchored in the bootstrap module.
//
// startLegacyApp destructures `runMaskingForSelectedDocument` into a same-named
// local const, so its call sites (btnRunMasking, btnRunKeywords, document-batch
// controller) stay unchanged.

import { invoke } from "@tauri-apps/api/core";
import type { MaskingOptions, MaskingResult } from "../../services/tauri/maskingContracts";
import { adoptGeneratedPreview } from "../../state/documentProvenance";
import type { LegacySessionState } from "../../legacy/startLegacyApp";
import { parseSafeReport } from "../../state/maskingSession";
import type { BaseMaskingProgress, SafeReport } from "../../state/maskingSession";

export type { MaskingOptions, MaskingReportPayload, MaskingResult } from "../../services/tauri/maskingContracts";

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

// The slice of startLegacyApp's shared `state` object this controller reads and
// mutates. The full closure state object satisfies this structurally.
export type MaskingRunState = Pick<
  LegacySessionState,
  | "maskingRunning"
  | "savingInFlight"
  | "documentProvenance"
  | "latestExtractedPath"
  | "latestMaskedPath"
  | "latestMaskedTextPolicy"
  | "latestReport"
  | "latestReportPath"
  | "baseExtractedText"
  | "baseMaskedText"
  | "initialMaskingPreviewPdf"
  | "initialExtractedText"
  | "initialMaskedText"
  | "preManualPreviewPdf"
  | "preManualExtractedText"
  | "preManualMaskedText"
  | "boxes"
  | "documentEditRevision"
  | "selectedCanvasBoxIndex"
  | "origDoc"
  | "currentOrigPage"
  | "currentResultPage"
  | "resultDoc"
  | "lastPreviewDiagnostics"
>;

export type MaskingRunDeps = {
  readonly state: MaskingRunState;
  readonly customRegionsEl: HTMLInputElement;
  readonly displayModeEl: HTMLSelectElement;
  readonly inputPathEl: HTMLInputElement;
  readonly isPdfInput: () => boolean;
  readonly isCustomRegionScope: () => boolean;
  readonly getResultSourcePath: () => string;
  readonly ensurePreviewWorkDir: () => Promise<string>;
  readonly collectMaskingOptions: () => MaskingOptions;
  readonly clampPage: (page: number, doc: any | null) => number;
  readonly loadPdfDoc: (path: string) => Promise<any>;
  readonly loadResultPdf: (path: string, fallbackPath?: string, isCurrent?: () => boolean) => Promise<void>;
  readonly renderCompare: () => Promise<void>;
  readonly setCompareMode: (mode: "pdf" | "text") => void;
  readonly setStatus: (message: string, detailTitle?: string) => void;
  readonly setBaseMaskingProgress: (progress: BaseMaskingProgress) => void;
  readonly setTextCompareContents: (extractedText: string, maskedText: string) => void;
  readonly renderFinalState: (report: SafeReport | null) => void;
  readonly renderDocumentReviewSurfaces: () => void;
  readonly resetDerivedArtifacts: () => void;
  readonly updateWorkflowReadiness: () => void;
  readonly updateCanvasControls: () => void;
  readonly runMaskingPipeline: (args: MaskingPipelineArgs) => Promise<MaskingResult>;
};

export type MaskingRunController = {
  readonly runMaskingForSelectedDocument: (options?: MaskingRunOptions) => Promise<MaskingResult | null>;
};

export function createMaskingRunController(deps: MaskingRunDeps): MaskingRunController {
  const { state, displayModeEl } = deps;

  async function pickFirstLoadablePdf(candidates: string[]): Promise<{ path: string; failureCount: number }> {
    let failureCount = 0;
    for (const candidate of candidates) {
      if (!candidate) continue;
      try {
        await deps.loadPdfDoc(candidate);
        return { path: candidate, failureCount };
      } catch {
        failureCount += 1;
      }
    }
    return { path: "", failureCount };
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
        extracted = await invoke<string>("read_text_file", { path: extractedPath });
      } catch {
        loadErrors.push("추출본 1건");
      }
    }
    if (!masked && maskedPath) {
      try {
        masked = await invoke<string>("read_text_file", { path: maskedPath });
      } catch {
        loadErrors.push("마스킹본 1건");
      }
    }
    return { extracted, masked, loadError: loadErrors.join(" | ") };
  }

  async function runMaskingForSelectedDocument(options: MaskingRunOptions = {}): Promise<MaskingResult | null> {
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
    if (deps.isCustomRegionScope() && !deps.customRegionsEl.value.trim()) {
      deps.setStatus("사용자 지정 지역을 선택했으면 지역명을 입력하세요.");
      deps.customRegionsEl.focus();
      return null;
    }
    const originalPdfForRestore = state.documentProvenance.original.path;
    const inputPdfForRun = deps.isPdfInput() ? deps.getResultSourcePath() || originalPdfForRestore : originalPdfForRestore;
    const runOutdir = options.outputDirOverride || (await deps.ensurePreviewWorkDir());
    const opts = deps.collectMaskingOptions();
    deps.resetDerivedArtifacts();
    let runProvenance = state.documentProvenance;
    const sessionIsCurrent = () => state.documentProvenance === runProvenance;
    deps.setStatus(`${statusPrefix} 실행 중...`);
    state.maskingRunning = true;
    deps.updateWorkflowReadiness();
    try {
      deps.setBaseMaskingProgress({ status: "running", percent: 0, displayMode: displayModeEl.value as BaseMaskingProgress["displayMode"] });
      const result = await deps.runMaskingPipeline({
        inputFile: inputPdfForRun,
        originalFile: deps.isPdfInput() ? originalPdfForRestore : "",
        outputDir: runOutdir,
        opts,
      });
      if (!sessionIsCurrent()) return null;
      deps.inputPathEl.value = originalPdfForRestore;
      deps.setStatus(`${statusPrefix} 실행 완료: 결과 파싱 성공`);

      const reportOutputs: Record<string, any> = result.runtime_manifest?.outputs ?? result.report?.outputs ?? {};
      state.latestExtractedPath = reportOutputs?.extracted_file || result.extracted_path || "";
      state.latestMaskedPath = reportOutputs?.masked_file || result.masked_path || "";
      state.latestMaskedTextPolicy = state.latestMaskedPath ? opts.deidentification_policy : "";
      const artifactPath = reportOutputs?.masked_pdf_file || reportOutputs?.preview_pdf_source_file || "";
      state.documentProvenance = adoptGeneratedPreview(state.documentProvenance, "", artifactPath);
      runProvenance = state.documentProvenance;
      // F-2: run_masking_pipeline 응답의 report를 최소 스키마로 검증 후 채택한다.
      // 검증 실패 시 리포트/경로를 무효화한다(저장은 막지 않고, 경고 산출 입력에서 제외).
      const adoptedReport = parseSafeReport(result.report);
      if (adoptedReport.ok) {
        state.latestReport = adoptedReport.value;
        state.latestReportPath = reportOutputs?.safe_report_path || result.report_path || "";
      } else {
        state.latestReport = null;
        state.latestReportPath = "";
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
      deps.setBaseMaskingProgress({ status: "complete", percent: 100, displayMode: displayModeEl.value as BaseMaskingProgress["displayMode"] });
      deps.setTextCompareContents(state.baseExtractedText, state.baseMaskedText);
      deps.renderDocumentReviewSurfaces();

      const previewCandidates = deps.isPdfInput()
        ? [
            reportOutputs.preview_pdf_source_file,
            reportOutputs.masked_pdf_file,
            typeof result.masked_path === "string" && result.masked_path.toLowerCase().endsWith(".pdf") ? result.masked_path : "",
          ].filter((candidate) => candidate && candidate !== originalPdfForRestore)
        : [];
      const preview = await pickFirstLoadablePdf(previewCandidates);
      const previewCandidate = preview.path;

      if (previewCandidate) {
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
            state.origDoc = await deps.loadPdfDoc(originalPdfForRestore);
          } catch {
            state.lastPreviewDiagnostics = "원본 미리보기를 복구하지 못했습니다.";
          }
        }
        state.currentOrigPage = deps.clampPage(state.currentOrigPage, state.origDoc);
        state.currentResultPage = 1;
        try {
          await deps.loadResultPdf(previewCandidate, inputPdfForRun, sessionIsCurrent);
          if (!sessionIsCurrent()) return null;
          deps.setCompareMode("pdf");
        } catch {
          deps.setCompareMode(hydratedTexts.extracted || hydratedTexts.masked ? "text" : "pdf");
        }
      } else {
        state.resultDoc = null;
        await deps.renderCompare();
        if (!sessionIsCurrent()) return null;
        if (hydratedTexts.extracted || hydratedTexts.masked) {
          deps.setCompareMode("text");
        }
      }

      const pdfMsg = previewCandidate
        ? "pdf 미리보기 생성"
        : `pdf 미리보기 실패(${preview.failureCount}건)`;
      const textMsg = hydratedTexts.loadError
        ? `미리보기 텍스트 로드 실패: ${hydratedTexts.loadError}`
        : hydratedTexts.extracted || hydratedTexts.masked
          ? "텍스트 비교 채움"
          : "텍스트 비교 비어있음";
      deps.setStatus(
        `${statusPrefix} 완료(${pdfMsg}, ${textMsg})`,
      );
      deps.updateWorkflowReadiness();
      return result;
    } catch {
      if (sessionIsCurrent()) {
        deps.setBaseMaskingProgress({ status: "failed", percent: 0, displayMode: displayModeEl.value as BaseMaskingProgress["displayMode"] });
        deps.setStatus(`${statusPrefix} 실패: 작업을 완료하지 못했습니다.`);
        deps.updateWorkflowReadiness();
      }
      return null;
    } finally {
      state.maskingRunning = false;
      deps.updateWorkflowReadiness();
      deps.updateCanvasControls();
    }
  }

  return { runMaskingForSelectedDocument };
}
