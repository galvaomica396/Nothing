import { finalSaveGate } from "../../features/save-gate/saveGate";
import { RUNTIME_MASKING_PROFILES } from "../../settingsState";
import type { DeidentificationMode, MaskingOutputArtifacts, MaskingProfile } from "../../settingsState";
import { err, isRecord, ok } from "../../state/contracts";
import type { ContractResult, DisplayMode } from "../../state/contracts";
import {
  boundSafeReportIdentity,
  canonicalMaskCounts,
  canonicalReviewQueue,
  isBoundSafeReport,
  isMaskingHash,
  isMaskingId,
  isMaskingOccurrenceId,
  isMaskingSaveToken,
  isMaskingToken,
  isNonNegativeInteger,
  isPdfPointsTopLeftRect,
} from "../../state/maskingSession";
import type {
  AnalysisManifestV1,
  AnalysisOccurrenceV1,
  AnalysisRegionV1,
  AnalysisSegmentV1,
  BoundSafeReport,
  ManualActionV1,
  PdfPointsTopLeftRect,
  PublicReviewProfile,
  ReviewItemV1,
  ReviewKind,
  RestoreAuthorizationSummary,
} from "../../state/maskingSession";
import type { BoundarySegmentKind } from "../../contracts/generated/analysisManifestV1";

/** Legacy pipeline DTOs are retained only for the pre-analysis compatibility adapter. */
export type LegacyMaskingReportPayload = { readonly outputs?: Record<string, unknown>; readonly extract?: { readonly engine_used?: string }; readonly [key: string]: unknown };
export type LegacyMaskingResult = { readonly extracted_path?: string; readonly masked_path?: string; readonly report_path?: string; readonly report: LegacyMaskingReportPayload; readonly runtime_manifest?: { readonly outputs?: Record<string, unknown>; readonly [key: string]: unknown }; readonly extracted_text: string; readonly masked_text: string };
export type LegacyFinalizeResult = { readonly final_output_file: string; readonly copied_files?: readonly string[] };
export type MaskingOptions = {
  readonly rrn: boolean; readonly phone: boolean; readonly business_reg: boolean; readonly name: boolean;
  readonly address: boolean; readonly place: boolean; readonly legal_party: boolean; readonly company: boolean;
  readonly court: boolean; readonly case_title: boolean; readonly case_number: boolean; readonly law_firm: boolean;
  readonly attorney: boolean; readonly approval_line: boolean; readonly region_context: boolean; readonly doc_meta: boolean;
  readonly email: boolean;
  readonly pdf_redaction: boolean; readonly custom_keywords: string; readonly extract_engine: string;
  readonly profile: MaskingProfile; readonly output_artifacts: MaskingOutputArtifacts; readonly display_mode: DisplayMode;
  readonly deidentification_policy: DeidentificationMode; readonly region_scope: string; readonly custom_regions: string;
  readonly return_text_preview: boolean;
  readonly auto_mask_threshold: number;
  readonly review_threshold: number;
};
export type {
  AnalysisManifestV1,
  AnalysisOccurrenceV1,
  AnalysisRegionV1,
  AnalysisSegmentV1,
  ManualActionV1,
  PdfPointsTopLeftRect,
  PublicReviewProfile,
  ReviewItemV1,
  ReviewKind,
  RestoreAuthorizationSummary,
};
export type CanonicalReviewProfile = PublicReviewProfile | "legal";
export type MaskingAnalysisOutcome = {
  readonly outcome: "analysis";
  readonly manifest: AnalysisManifestV1;
  readonly report?: never;
  readonly runtime_manifest?: never;
  readonly masked_path?: never;
  readonly report_path?: never;
};
export type LegacyMaskingPipelineOutcome = {
  readonly outcome: "masked";
  readonly report: LegacyMaskingReportPayload;
  readonly runtime_manifest?: { readonly outputs?: Record<string, unknown>; readonly [key: string]: unknown };
  readonly extracted_path?: string;
  readonly masked_path?: string;
  readonly report_path?: string;
  readonly extracted_text: string;
  readonly masked_text: string;
};
export type LegacyMaskingRunOutcome = MaskingAnalysisOutcome | LegacyMaskingPipelineOutcome;

export type AnalyzeMaskingRunRequest = {
  readonly [Profile in PublicReviewProfile]: {
    readonly inputFile: string;
    readonly profile: Profile;
    readonly options: MaskingOptions & { readonly profile: Profile };
  };
}[PublicReviewProfile];
export type GetMaskingRunStateRequest = { readonly runId: string };
export type NameOrInstitutionResolution = { readonly kind: "name" | "institution"; readonly action: "mask" | "exclude" };
export type AcknowledgeResolution = { readonly kind: "acknowledge"; readonly acknowledged: true };
export type BoundaryResolution = { readonly kind: "boundary"; readonly pageStart: number; readonly pageEnd: number; readonly segmentKind: BoundarySegmentKind };
export type OcrResolution = { readonly kind: "ocr"; readonly accepted: boolean };
export type RegionGeometryResolution = { readonly kind: "region_geometry"; readonly rects: readonly PdfPointsTopLeftRect[] };
export type ReviewResolution = NameOrInstitutionResolution | AcknowledgeResolution | BoundaryResolution | OcrResolution | RegionGeometryResolution;
export type ResolveMaskingReviewRequest = {
  readonly runId: string;
  readonly analysisRevision: number;
  readonly manifestHash: string;
  readonly reviewId: string;
  readonly resolution: ReviewResolution;
};
export type ApplyManualActionV1Request = {
  readonly runId: string;
  readonly analysisRevision: number;
  readonly manifestHash: string;
  readonly page: number;
  readonly rects: readonly PdfPointsTopLeftRect[];
  readonly mode: "mask" | "restore";
  readonly sourceKind: "text_pdf" | "scan";
  readonly linkedOccurrenceId: string | null;
  readonly targetRegionId: null;
  readonly expectedTextHash: string | null;
  readonly protectedNeighborRefs: readonly PdfPointsTopLeftRect[];
  readonly restoreCapability: string | null;
};
export type RestoreCapabilityRequest = {
  readonly runId: string;
  readonly analysisRevision: number;
  readonly manifestHash: string;
  readonly occurrenceId: string;
  readonly rects: readonly PdfPointsTopLeftRect[];
  readonly expectedTextHash: string;
};
export type RestoreCapability = {
  readonly capability: string;
};
type FinalizeMaskingRunRequest = {
  readonly runId: string;
  readonly analysisRevision: number;
  readonly manifestHash: string;
  readonly destination: string;
  readonly saveToken: string;
  readonly warningsConfirmed: boolean;
};
const PREPARED_FINALIZE: unique symbol = Symbol("prepared-finalize");
export type PreparedFinalizeMaskingRun = {
  readonly [PREPARED_FINALIZE]: true;
  readonly request: FinalizeMaskingRunRequest;
  readonly identity: ReturnType<typeof boundSafeReportIdentity>;
  readonly expectedOccurrenceCount: number;
  readonly expectedManualMaskCount: number;
  readonly expectedRestoreCount: number;
};
export type FinalizeMaskingRunResult = {
  readonly runId: string;
  readonly analysisRevision: number;
  readonly manifestHash: string;
  readonly finalPath: string;
  readonly finalHash: string;
  readonly finalHashAttested: true;
  readonly occurrenceCount: number;
  readonly appliedMaskCount: number;
  readonly manualMaskCount: number;
  readonly restoreCount: number;
  readonly effectiveMaskCount: number;
  readonly restoreAuthorization: RestoreAuthorizationSummary;
  readonly saveConfirmation: FinalizeSaveConfirmation;
  readonly status: "promoted";
};
export type FinalizeSaveWarning = {
  readonly kind: string;
  readonly targetId: string | null;
  readonly category: string;
  readonly pageStart: number;
  readonly pageEnd: number;
  readonly reasonCodes: readonly string[];
};
export type FinalizeSaveConfirmation = {
  readonly status: "not_required" | "user_confirmed";
  readonly unresolvedReviews: readonly FinalizeSaveWarning[];
};

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
const path = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 4096;
const nonNegativeInteger = isNonNegativeInteger;
const exactKeys = (value: Record<string, unknown>, fields: readonly string[]) => Object.keys(value).length === fields.length && Object.keys(value).every((key) => fields.includes(key));
const BOUNDARY_SEGMENT_KINDS = ["internal_review", "official_dispatch", "attachment", "legal"] as const satisfies readonly BoundarySegmentKind[];
export const isBoundarySegmentKind = (value: unknown): value is BoundarySegmentKind =>
  typeof value === "string" && BOUNDARY_SEGMENT_KINDS.some((kind) => kind === value);

const MASKING_OPTION_FIELDS = [
  "rrn", "phone", "business_reg", "name", "address", "place", "legal_party", "company",
  "court", "case_title", "case_number", "law_firm", "attorney", "approval_line",
  "region_context", "doc_meta", "email", "pdf_redaction", "custom_keywords", "extract_engine",
  "profile", "output_artifacts", "display_mode", "deidentification_policy", "region_scope",
  "custom_regions", "return_text_preview", "auto_mask_threshold", "review_threshold",
] as const;
const MASKING_BOOLEAN_FIELDS = [
  "rrn", "phone", "business_reg", "name", "address", "place", "legal_party", "company",
  "court", "case_title", "case_number", "law_firm", "attorney", "approval_line",
  "region_context", "doc_meta", "email", "pdf_redaction",
] as const;

function validAnalyzeMaskingRunRequest(value: unknown): value is AnalyzeMaskingRunRequest {
  if (!isRecord(value) || !exactKeys(value, ["inputFile", "profile", "options"])
    || !path(value.inputFile) || !RUNTIME_MASKING_PROFILES.includes(value.profile as PublicReviewProfile)
    || !isRecord(value.options)) {
    return false;
  }
  const options = value.options;
  if (!exactKeys(options, MASKING_OPTION_FIELDS)
    || options.profile !== value.profile
    || MASKING_BOOLEAN_FIELDS.some((field) => typeof options[field] !== "boolean")
    || typeof options.custom_keywords !== "string" || options.custom_keywords.length > 50_000
    || typeof options.custom_regions !== "string" || options.custom_regions.length > 10_000
    || !["auto", "marker", "paddle", "pymupdf", "pypdf"].includes(options.extract_engine as string)
    || !["pdf_safe_report", "pdf_masked_txt_safe_report"].includes(options.output_artifacts as string)
    || !["black", "label_en", "label_ko", "pseudonym"].includes(options.display_mode as string)
    || !["token", "partial", "pseudonym"].includes(options.deidentification_policy as string)
    || !["national", "seoul", "custom"].includes(options.region_scope as string)
    || options.return_text_preview !== false
    || typeof options.auto_mask_threshold !== "number" || !Number.isFinite(options.auto_mask_threshold)
    || typeof options.review_threshold !== "number" || !Number.isFinite(options.review_threshold)
    || options.review_threshold < 0 || options.auto_mask_threshold > 1
    || options.review_threshold > options.auto_mask_threshold) {
    return false;
  }
  const customRegions = options.custom_regions.trim();
  return options.region_scope === "custom" ? customRegions.length > 0 : customRegions.length === 0;
}
function matchesIdentity(request: { readonly runId: string; readonly analysisRevision: number; readonly manifestHash: string }, identity: ReturnType<typeof boundSafeReportIdentity>): boolean {
  return request.runId === identity.runId
    && request.analysisRevision === identity.analysisRevision
    && request.manifestHash === identity.manifestHash;
}

function sameRects(
  left: readonly PdfPointsTopLeftRect[],
  right: readonly PdfPointsTopLeftRect[],
): boolean {
  return left.length === right.length
    && left.every((rect, index) => {
      const candidate = right[index];
      return candidate !== undefined
        && rect.x0 === candidate.x0
        && rect.y0 === candidate.y0
        && rect.x1 === candidate.x1
        && rect.y1 === candidate.y1;
    });
}

function parseRestoreAuthorizationSummary(value: unknown): ContractResult<RestoreAuthorizationSummary> {
  if (!isRecord(value)
    || !exactKeys(value, ["actionIdHash", "targetOccurrenceIdHash", "authorizationEvent"])
    || !isMaskingHash(value.actionIdHash)
    || !isMaskingHash(value.targetOccurrenceIdHash)
    || !isMaskingToken(value.authorizationEvent)) {
    return err("invalid_status", "finalize_result.restoreAuthorization");
  }
  return ok({
    actionIdHash: value.actionIdHash,
    targetOccurrenceIdHash: value.targetOccurrenceIdHash,
    authorizationEvent: value.authorizationEvent,
  });
}

function validResolution(value: unknown, item: ReviewItemV1): value is ReviewResolution {
  if (!isRecord(value) || typeof value.kind !== "string" || value.kind !== item.kind) return false;
  if ((value.kind === "name" || value.kind === "institution") && exactKeys(value, ["kind", "action"])) return value.action === "mask" || value.action === "exclude";
  if (value.kind === "acknowledge" && exactKeys(value, ["kind", "acknowledged"])) return value.acknowledged === true;
  if (value.kind === "ocr" && exactKeys(value, ["kind", "accepted"])) return typeof value.accepted === "boolean";
  if (value.kind === "boundary" && exactKeys(value, ["kind", "pageStart", "pageEnd", "segmentKind"])) {
    return nonNegativeInteger(value.pageStart) && nonNegativeInteger(value.pageEnd) && value.pageStart <= value.pageEnd
      && isBoundarySegmentKind(value.segmentKind);
  }
  if (value.kind === "region_geometry" && exactKeys(value, ["kind", "rects"]) && Array.isArray(value.rects) && value.rects.length > 0) {
    return value.rects.every(isPdfPointsTopLeftRect);
  }
  return false;
}

function parseFinalizeSaveConfirmation(value: unknown): ContractResult<FinalizeSaveConfirmation> {
  if (!isRecord(value) || !exactKeys(value, ["status", "unresolvedReviews"])
    || (value.status !== "not_required" && value.status !== "user_confirmed")
    || !Array.isArray(value.unresolvedReviews)) {
    return err("invalid_status", "finalize_result.saveConfirmation");
  }
  const unresolvedReviews: FinalizeSaveWarning[] = [];
  for (const warning of value.unresolvedReviews) {
    if (!isRecord(warning)
      || !exactKeys(warning, ["kind", "targetId", "category", "pageStart", "pageEnd", "reasonCodes"])
      || !isMaskingToken(warning.kind)
      || (warning.targetId !== null && !isMaskingId(warning.targetId))
      || !isMaskingToken(warning.category)
      || !nonNegativeInteger(warning.pageStart)
      || !nonNegativeInteger(warning.pageEnd)
      || warning.pageStart > warning.pageEnd
      || !Array.isArray(warning.reasonCodes)
      || warning.reasonCodes.length === 0
      || warning.reasonCodes.some((reason) => typeof reason !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(reason))) {
      return err("invalid_status", "finalize_result.saveConfirmation.unresolvedReviews");
    }
    unresolvedReviews.push({
      kind: warning.kind,
      targetId: warning.targetId,
      category: warning.category,
      pageStart: warning.pageStart,
      pageEnd: warning.pageEnd,
      reasonCodes: warning.reasonCodes,
    });
  }
  if (value.status === "not_required" && unresolvedReviews.length > 0) {
    return err("invalid_status", "finalize_result.saveConfirmation.status");
  }
  if (value.status === "user_confirmed" && unresolvedReviews.length === 0) {
    return err("invalid_status", "finalize_result.saveConfirmation.status");
  }
  return ok({
    status: value.status,
    unresolvedReviews,
  });
}

export function prepareResolveMaskingReview(value: unknown, report: BoundSafeReport): ContractResult<ResolveMaskingReviewRequest> {
  if (!isRecord(value) || !exactKeys(value, ["runId", "analysisRevision", "manifestHash", "reviewId", "resolution"])
    || !isMaskingId(value.runId) || !nonNegativeInteger(value.analysisRevision)
    || !isMaskingHash(value.manifestHash) || !isMaskingId(value.reviewId) || !isBoundSafeReport(report)) {
    return err("invalid_status", "review_request");
  }
  const identity = boundSafeReportIdentity(report);
  if (!matchesIdentity(value as ResolveMaskingReviewRequest, identity)) return err("invalid_status", "review_request.identity");
  const item = report.reviewQueue?.find((candidate) => candidate.reviewId === value.reviewId);
  if (!item || item.status !== "pending" || !validResolution(value.resolution, item)) return err("invalid_status", "review_request.resolution");
  return ok(value as ResolveMaskingReviewRequest);
}

export function prepareFinalizeMaskingRun(value: unknown, report: BoundSafeReport, restoreRevalidationFailed = false): ContractResult<PreparedFinalizeMaskingRun> {
  if (!isBoundSafeReport(report)) return err("invalid_status", "finalize_request.report");
  const decision = finalSaveGate({ report, restoreRevalidationFailed });
  if (decision.state === "blocked") return err("invalid_status", "finalize_request.gate");
  if (!isRecord(value)) return err("invalid_status", "finalize_request");
  if (!exactKeys(value, ["runId", "analysisRevision", "manifestHash", "destination", "saveToken", "warningsConfirmed"])) {
    return err("invalid_status", "finalize_request.keys");
  }
  if (!isMaskingId(value.runId)) return err("invalid_status", "finalize_request.runId");
  if (!nonNegativeInteger(value.analysisRevision)) return err("invalid_status", "finalize_request.analysisRevision");
  if (!isMaskingHash(value.manifestHash)) return err("invalid_status", "finalize_request.manifestHash");
  if (!path(value.destination)) return err("invalid_status", "finalize_request.destination");
  if (!isMaskingSaveToken(value.saveToken)) return err("invalid_status", "finalize_request.saveToken");
  if (typeof value.warningsConfirmed !== "boolean") return err("invalid_status", "finalize_request.warningsConfirmed");
  if (decision.state === "advisory" && value.warningsConfirmed !== true) {
    return err("invalid_status", "finalize_request.warningsConfirmed");
  }
  const identity = boundSafeReportIdentity(report);
  if (!matchesIdentity(value as FinalizeMaskingRunRequest, identity)) return err("invalid_status", "finalize_request.identity");
  const queue = canonicalReviewQueue(report);
  if (!queue.ok) return err("invalid_status", "finalize_request.review_queue");
  const expectedCounts = canonicalMaskCounts(report);
  if (!expectedCounts.ok) return { ok: false, errors: expectedCounts.errors };
  const prepared: PreparedFinalizeMaskingRun = {
    [PREPARED_FINALIZE]: true,
    request: Object.freeze({ ...value } as FinalizeMaskingRunRequest),
    identity,
    expectedOccurrenceCount: expectedCounts.value.effectiveMaskCount,
    expectedManualMaskCount: expectedCounts.value.manualMaskCount,
    expectedRestoreCount: expectedCounts.value.manualRestoreCount,
  };
  return ok(Object.freeze(prepared));
}

export function parseFinalizeMaskingRunResult(value: unknown, prepared: PreparedFinalizeMaskingRun | undefined): ContractResult<FinalizeMaskingRunResult> {
  if (!prepared || prepared[PREPARED_FINALIZE] !== true || !matchesIdentity(prepared.request, prepared.identity)) {
    return err("invalid_status", "finalize_request.identity");
  }
  if (!isRecord(value)
    || !exactKeys(value, ["runId", "analysisRevision", "manifestHash", "finalPath", "finalHash", "finalHashAttested", "occurrenceCount", "appliedMaskCount", "manualMaskCount", "restoreCount", "effectiveMaskCount", "restoreAuthorization", "saveConfirmation", "status"])
    || value.runId !== prepared.identity.runId || value.analysisRevision !== prepared.identity.analysisRevision
    || value.manifestHash !== prepared.identity.manifestHash || !path(value.finalPath)
    || value.finalPath !== prepared.request.destination || !isMaskingHash(value.finalHash)
    || value.finalHashAttested !== true || !nonNegativeInteger(value.occurrenceCount)
    || !nonNegativeInteger(value.appliedMaskCount)
    || !nonNegativeInteger(value.manualMaskCount)
    || !nonNegativeInteger(value.restoreCount)
    || !nonNegativeInteger(value.effectiveMaskCount)
    || value.occurrenceCount !== prepared.expectedOccurrenceCount
    || value.appliedMaskCount !== prepared.expectedOccurrenceCount
    || value.manualMaskCount !== prepared.expectedManualMaskCount
    || value.restoreCount !== prepared.expectedRestoreCount
    || value.effectiveMaskCount !== prepared.expectedOccurrenceCount
    || value.status !== "promoted") {
    return err("invalid_status", "finalize_result");
  }
  const restoreAuthorization = parseRestoreAuthorizationSummary(value.restoreAuthorization);
  if (!restoreAuthorization.ok) return restoreAuthorization;
  const saveConfirmation = parseFinalizeSaveConfirmation(value.saveConfirmation);
  if (!saveConfirmation.ok) return saveConfirmation;
  return ok({
    runId: value.runId,
    analysisRevision: value.analysisRevision,
    manifestHash: value.manifestHash,
    finalPath: value.finalPath,
    finalHash: value.finalHash,
    finalHashAttested: true,
    occurrenceCount: value.occurrenceCount,
    appliedMaskCount: value.appliedMaskCount,
    manualMaskCount: value.manualMaskCount,
    restoreCount: value.restoreCount,
    effectiveMaskCount: value.effectiveMaskCount,
    restoreAuthorization: restoreAuthorization.value,
    saveConfirmation: saveConfirmation.value,
    status: "promoted",
  });
}

export function analyzeMaskingRun(invoke: Invoke, request: AnalyzeMaskingRunRequest): Promise<unknown> {
  const candidate: unknown = request;
  if (!validAnalyzeMaskingRunRequest(candidate)) {
    throw new Error(
      isRecord(candidate)
        && RUNTIME_MASKING_PROFILES.includes(candidate.profile as PublicReviewProfile)
        && isRecord(candidate.options)
        && candidate.profile === candidate.options.profile
        ? "Invalid masking analysis options."
        : "Invalid masking analysis profile.",
    );
  }
  return invoke("analyze_masking_run", { request: candidate });
}
export function getMaskingRunState(invoke: Invoke, request: GetMaskingRunStateRequest): Promise<unknown> { return invoke("get_masking_run_state", request); }
export function resolveMaskingReview(invoke: Invoke, request: unknown, report: BoundSafeReport): Promise<unknown> {
  const prepared = prepareResolveMaskingReview(request, report);
  if (!prepared.ok) return Promise.reject(new Error("Invalid masking review resolution."));
  return invoke("resolve_masking_review", { request: prepared.value });
}

export function issueRestoreCapability(
  invoke: Invoke,
  request: unknown,
  report: BoundSafeReport,
): Promise<unknown> {
  if (!isBoundSafeReport(report) || !isRecord(request)
    || !exactKeys(request, ["runId", "analysisRevision", "manifestHash", "occurrenceId", "rects", "expectedTextHash"])
    || !isMaskingId(request.runId)
    || !nonNegativeInteger(request.analysisRevision)
    || !isMaskingHash(request.manifestHash)
    || !isMaskingOccurrenceId(request.occurrenceId)
    || !isMaskingHash(request.expectedTextHash)
    || !Array.isArray(request.rects)
    || !request.rects.every(isPdfPointsTopLeftRect)) {
    return Promise.reject(new Error("Invalid restore capability request."));
  }
  const identity = boundSafeReportIdentity(report);
  if (!matchesIdentity(request as RestoreCapabilityRequest, identity)) {
    return Promise.reject(new Error("Invalid restore capability identity."));
  }
  const occurrence = report.analysisManifest?.occurrences.find((candidate) => candidate.occurrenceId === request.occurrenceId);
  if (!occurrence
    || occurrence.proposedAction !== "mask"
    || !["confirmed", "user_confirmed"].includes(occurrence.state)
    || occurrence.expectedTextHash !== request.expectedTextHash
    || !sameRects(occurrence.rects, request.rects)) {
    return Promise.reject(new Error("Invalid restore capability target."));
  }
  return invoke("issue_restore_capability", { request });
}

export function parseRestoreCapability(value: unknown): ContractResult<RestoreCapability> {
  if (!isRecord(value) || !exactKeys(value, ["capability"]) || !isMaskingToken(value.capability)) {
    return err("invalid_status", "restore_capability");
  }
  return ok({ capability: value.capability });
}

export function applyManualActionV1(invoke: Invoke, request: ApplyManualActionV1Request, report: BoundSafeReport): Promise<unknown> {
  const identity = boundSafeReportIdentity(report);
  const validMaskRequest = request.mode === "mask"
    && request.sourceKind === "scan"
    && request.linkedOccurrenceId === null
    && request.expectedTextHash === null
    && request.restoreCapability === null
    && request.protectedNeighborRefs.length === 0;
  const validRestoreRequest = request.mode === "restore"
    && request.sourceKind === "text_pdf"
    && isMaskingOccurrenceId(request.linkedOccurrenceId)
    && isMaskingHash(request.expectedTextHash)
    && isMaskingToken(request.restoreCapability)
    && request.protectedNeighborRefs.length === 0;
  const validRequest = request.runId === identity.runId
    && request.analysisRevision === identity.analysisRevision
    && request.manifestHash === identity.manifestHash
    && Number.isSafeInteger(request.page)
    && request.page >= 0
    && (validMaskRequest || validRestoreRequest)
    && request.targetRegionId === null
    && request.rects.length > 0
    && request.rects.every(isPdfPointsTopLeftRect);
  if (!validRequest) return Promise.reject(new Error("Invalid public manual action."));
  return invoke("apply_manual_action_v1", { request });
}
export function finalizeMaskingRun(invoke: Invoke, prepared: PreparedFinalizeMaskingRun): Promise<unknown> {
  if (prepared[PREPARED_FINALIZE] !== true || !matchesIdentity(prepared.request, prepared.identity)) {
    return Promise.reject(new Error("Invalid prepared masking finalization."));
  }
  return invoke("finalize_masking_run", { request: prepared.request });
}
