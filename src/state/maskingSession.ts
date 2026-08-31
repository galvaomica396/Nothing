import { RUNTIME_MASKING_PROFILES } from "../settingsState";
import type { RuntimeMaskingProfile } from "../settingsState";
import { err, isRecord, ok } from "./contracts";
import type { ContractResult, DisplayMode } from "./contracts";

export type BaseMaskingProgress = {
  readonly status: "idle" | "running" | "complete" | "failed";
  readonly percent: number;
  readonly displayMode: DisplayMode;
  readonly currentPage?: number;
  readonly totalPages?: number;
  readonly detectedItems?: number;
};
export type SafeReportProductChecks = {
  readonly quality_gate_passed?: boolean;
  readonly needs_manual_review?: boolean;
  readonly final_submission_allowed?: boolean;
  readonly native_redaction_verified?: boolean;
  readonly native_redaction_status?: string;
  readonly native_redaction_reason_code?: string | null;
  readonly text_surface_verified?: boolean;
  readonly pdf_input?: boolean;
  readonly text_deidentification_final_submission_evidence?: boolean;
  readonly raw_values_saved?: boolean;
};
export type SafeReportRedaction = {
  readonly status?: string;
  readonly missing_targets_count?: number;
  readonly verification?: { readonly residual_hits?: number; readonly verified?: boolean };
};
export type PdfPointsTopLeftRect = { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number };
export type PublicReviewProfile = RuntimeMaskingProfile;
export const ANALYSIS_SEGMENT_KINDS = ["internal_review", "official_dispatch", "attachment", "unknown", "legal", "mixed", "common"] as const;
export const ANALYSIS_SEGMENT_STATES = ["confirmed", "review_required", "user_confirmed"] as const;
export const ANALYSIS_REGION_KINDS = ["approval", "header_meta", "labeled_staff", "recipient_reference", "sender_institution", "approval_staff", "dispatch_metadata", "footer_contact"] as const;
export const ANALYSIS_REGION_STATES = ["confirmed", "review_required", "unconfirmed", "user_confirmed"] as const;
export const REGION_CONFIRMATION_SOURCES = ["automatic", "user"] as const;
export type AnalysisSegmentKind = (typeof ANALYSIS_SEGMENT_KINDS)[number];
export type AnalysisSegmentState = (typeof ANALYSIS_SEGMENT_STATES)[number];
export type AnalysisRegionKind = (typeof ANALYSIS_REGION_KINDS)[number];
export type AnalysisRegionState = (typeof ANALYSIS_REGION_STATES)[number];
export type RegionConfirmationSource = (typeof REGION_CONFIRMATION_SOURCES)[number];
export const REGION_COVERAGE_STATES = ["present", "absent", "indeterminate"] as const;
export type RegionCoverageState = (typeof REGION_COVERAGE_STATES)[number];
export type ApprovalCoverage = {
  readonly approval: RegionCoverageState;
  readonly header_meta: RegionCoverageState;
  readonly labeled_staff: RegionCoverageState;
};
export type RequiredRegionCoverage = {
  readonly recipient_reference: RegionCoverageState;
  readonly sender_institution: RegionCoverageState;
  readonly approval_staff: RegionCoverageState;
  readonly dispatch_metadata: RegionCoverageState;
  readonly footer_contact: RegionCoverageState;
};
export type AnalysisSegmentV1 = { readonly segmentId: string; readonly analysisRevision: number; readonly pageStart: number; readonly pageEnd: number; readonly kind: AnalysisSegmentKind; readonly state: AnalysisSegmentState; readonly commonOnly: boolean; readonly source: string };
export type AnalysisRegionV1 = { readonly regionId: string; readonly segmentId: string; readonly analysisRevision: number; readonly page: number; readonly rects: readonly PdfPointsTopLeftRect[]; readonly kind: AnalysisRegionKind; readonly state: AnalysisRegionState; readonly confirmationSource: RegionConfirmationSource | null; readonly reasonCodes: readonly string[]; readonly source: string };
export const OCCURRENCE_PROPOSED_ACTIONS = ["mask", "exclude", "review"] as const;
export const OCCURRENCE_STATES = ["confirmed", "review_required", "user_confirmed"] as const;
export type OccurrenceProposedAction = (typeof OCCURRENCE_PROPOSED_ACTIONS)[number];
export type OccurrenceState = (typeof OCCURRENCE_STATES)[number];
export const FINAL_APPLIED_MASK_STATES = ["confirmed", "user_confirmed"] as const;
export type FinalAppliedMaskState = (typeof FINAL_APPLIED_MASK_STATES)[number];
export type AnalysisOccurrenceV1 = { readonly occurrenceId: string; readonly segmentId: string; readonly regionId: string | null; readonly analysisRevision: number; readonly page: number; readonly rects: readonly PdfPointsTopLeftRect[]; readonly tag: string; readonly category: string; readonly valueHash: string; readonly expectedTextHash: string; readonly source: string; readonly policy: string; readonly proposedAction: OccurrenceProposedAction; readonly state: OccurrenceState; readonly provenance: string };
export type ReviewKind = "name" | "institution" | "acknowledge" | "boundary" | "ocr" | "region_geometry";
export type ReviewItemV1 = { readonly reviewId: string; readonly analysisRevision: number; readonly kind: ReviewKind; readonly targetId: string; readonly pageStart: number; readonly pageEnd: number; readonly status: "pending" | "resolved"; readonly reasonCodes: readonly string[]; readonly requiresAcknowledgment: boolean; readonly commonOnly: boolean; readonly provenance: string };
export const MANUAL_ACTION_MODES = ["mask", "restore"] as const;
export const MANUAL_ACTION_SOURCE_KINDS = ["text_pdf", "scan"] as const;
export type ManualActionMode = (typeof MANUAL_ACTION_MODES)[number];
export type ManualActionSourceKind = (typeof MANUAL_ACTION_SOURCE_KINDS)[number];
export type ManualActionV1 = { readonly actionId: string; readonly analysisRevision: number; readonly page: number; readonly rects: readonly PdfPointsTopLeftRect[]; readonly protectedNeighborRefs: readonly PdfPointsTopLeftRect[]; readonly mode: ManualActionMode; readonly sourceKind: ManualActionSourceKind; readonly linkedOccurrenceId: string | null; readonly expectedTextHash: string | null; readonly restoreAuthorizationHash: string | null };
export type RestoreAuthorizationSummary = { readonly actionIdHash: string; readonly targetOccurrenceIdHash: string; readonly authorizationEvent: string };
export type ThresholdArtifactV1 = {
  readonly version: string;
  readonly contentHash: string;
  readonly autoMaskThreshold: number;
  readonly reviewThreshold: number;
};
export type AnalysisManifestV1 = { readonly manifestVersion: 1; readonly runId: string; readonly originalDocumentHash: string; readonly analysisRevision: number; readonly manifestHash: string; readonly profile: PublicReviewProfile; readonly policyVersion: string; readonly optionsVersion: string; readonly optionsHash: string; readonly thresholdVersion: string; readonly thresholdHash: string; readonly thresholdArtifact: ThresholdArtifactV1; readonly coordinateSpace: "pdf_points_top_left"; readonly approvalCoverage: ApprovalCoverage; readonly requiredRegionCoverage: RequiredRegionCoverage; readonly segments: readonly AnalysisSegmentV1[]; readonly regions: readonly AnalysisRegionV1[]; readonly occurrences: readonly AnalysisOccurrenceV1[]; readonly reviewItems: readonly ReviewItemV1[]; readonly manualActions: readonly ManualActionV1[] };
export type SafeReport = { readonly product_checks: SafeReportProductChecks; readonly document_redaction?: SafeReportRedaction; readonly pdf_redaction?: SafeReportRedaction; readonly analysisManifest?: AnalysisManifestV1; readonly reviewQueue?: readonly ReviewItemV1[] };
const BOUND_SAFE_REPORT: unique symbol = Symbol("bound-safe-report");
export type MaskingSessionIdentity = {
  readonly runId: string;
  readonly originalDocumentHash: string;
  readonly analysisRevision: number;
  readonly manifestHash: string;
  readonly profile: PublicReviewProfile;
};
export type BoundSafeReport = SafeReport & {
  readonly [BOUND_SAFE_REPORT]: MaskingSessionIdentity;
};

export const MASKING_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
export const MASKING_HASH = /^[a-f0-9]{64}$/i;
// Native save-target tokens are 32-character hexadecimal values and may begin
// with a digit. Keep the token contract aligned with that issued value.
export const MASKING_TOKEN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
export const MASKING_SAVE_TOKEN = /^[a-f0-9]{32}$/;
export const MASKING_OCCURRENCE_ID = /^occ_[a-f0-9]{24}$/;
const REASON = /^[a-z][a-z0-9_]{0,63}$/;
const ownKeys = (source: Record<string, unknown>, allowed: readonly string[]) => Object.keys(source).every((key) => allowed.includes(key));
export const isMaskingId = (value: unknown): value is string => typeof value === "string" && MASKING_ID.test(value);
export const isMaskingHash = (value: unknown): value is string => typeof value === "string" && MASKING_HASH.test(value);
export const isMaskingToken = (value: unknown): value is string => typeof value === "string" && MASKING_TOKEN.test(value);
export const isMaskingSaveToken = (value: unknown): value is string => typeof value === "string" && MASKING_SAVE_TOKEN.test(value);
export const isMaskingOccurrenceId = (value: unknown): value is string => typeof value === "string" && MASKING_OCCURRENCE_ID.test(value);
export const isNonNegativeInteger = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const id = isMaskingId;
const hash = isMaskingHash;
const token = isMaskingToken;
const count = isNonNegativeInteger;
const finiteNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const reasons = (value: unknown): value is readonly string[] => Array.isArray(value) && value.every((entry) => typeof entry === "string" && REASON.test(entry));
const coverage = <K extends string>(value: unknown, fields: readonly K[]): value is Record<K, RegionCoverageState> =>
  isRecord(value)
  && ownKeys(value, fields)
  && fields.every((field) => REGION_COVERAGE_STATES.includes(value[field] as RegionCoverageState));

function normalizedCoverageMaps(
  approvalValue: unknown,
  requiredValue: unknown,
): { approval: ApprovalCoverage; required: RequiredRegionCoverage } | null {
  if (
    coverage(approvalValue, ["approval", "header_meta", "labeled_staff"])
    && coverage(requiredValue, ["recipient_reference", "sender_institution", "approval_staff", "dispatch_metadata", "footer_contact"])
  ) {
    return { approval: approvalValue, required: requiredValue };
  }
  if (
    !isRecord(approvalValue)
    || !ownKeys(approvalValue, ["schemaVersion", "state", "signerCount", "protectedNeighborCount"])
    || approvalValue.schemaVersion !== 1
    || !REGION_COVERAGE_STATES.includes(approvalValue.state as RegionCoverageState)
    || !count(approvalValue.signerCount)
    || !count(approvalValue.protectedNeighborCount)
    || !isRecord(requiredValue)
    || !ownKeys(requiredValue, ["schemaVersion", "profile", "kinds", "blocking"])
    || requiredValue.schemaVersion !== 1
    || !RUNTIME_MASKING_PROFILES.includes(requiredValue.profile as PublicReviewProfile)
    || !Array.isArray(requiredValue.kinds)
    || typeof requiredValue.blocking !== "boolean"
  ) return null;
  const kinds = new Map<string, RegionCoverageState>();
  for (const item of requiredValue.kinds) {
    if (
      !isRecord(item)
      || !ownKeys(item, ["kind", "state"])
      || typeof item.kind !== "string"
      || !REGION_COVERAGE_STATES.includes(item.state as RegionCoverageState)
      || kinds.has(item.kind)
    ) return null;
    kinds.set(item.kind, item.state as RegionCoverageState);
  }
  const state = approvalValue.state as RegionCoverageState;
  return {
    approval: {
      approval: kinds.get("approval") ?? state,
      header_meta: kinds.get("header_meta") ?? "absent",
      labeled_staff: kinds.get("labeled_staff") ?? "absent",
    },
    required: {
      recipient_reference: kinds.get("recipient_reference") ?? "absent",
      sender_institution: kinds.get("sender_institution") ?? "absent",
      approval_staff: kinds.get("approval_staff") ?? state,
      dispatch_metadata: kinds.get("dispatch_metadata") ?? "absent",
      footer_contact: kinds.get("footer_contact") ?? "absent",
    },
  };
}

export function isPdfPointsTopLeftRect(value: unknown): value is PdfPointsTopLeftRect {
  return isRecord(value)
    && ownKeys(value, ["x0", "y0", "x1", "y1"])
    && finiteNumber(value.x0) && finiteNumber(value.y0) && finiteNumber(value.x1) && finiteNumber(value.y1)
    && value.x1 > value.x0 && value.y1 > value.y0;
}

function parseRect(value: unknown): ContractResult<PdfPointsTopLeftRect> {
  if (!isPdfPointsTopLeftRect(value)) return err("invalid_number", "rect");
  return ok({ x0: value.x0, y0: value.y0, x1: value.x1, y1: value.y1 });
}
function parseRects(value: unknown, options: { readonly allowEmpty?: boolean } = {}): ContractResult<readonly PdfPointsTopLeftRect[]> {
  if (!Array.isArray(value) || (!options.allowEmpty && value.length === 0)) return err("missing_rect", "rects");
  const rects: PdfPointsTopLeftRect[] = [];
  for (const candidate of value) { const parsed = parseRect(candidate); if (!parsed.ok) return parsed; rects.push(parsed.value); }
  return ok(rects);
}
function parseSegment(value: unknown): ContractResult<AnalysisSegmentV1> {
  if (!isRecord(value) || !ownKeys(value, ["segmentId", "analysisRevision", "pageStart", "pageEnd", "kind", "state", "commonOnly", "source"]) || !id(value.segmentId) || !count(value.analysisRevision) || !count(value.pageStart) || !count(value.pageEnd) || value.pageStart > value.pageEnd || !ANALYSIS_SEGMENT_KINDS.includes(value.kind as AnalysisSegmentKind) || !ANALYSIS_SEGMENT_STATES.includes(value.state as AnalysisSegmentState) || typeof value.commonOnly !== "boolean" || !token(value.source)) return err("invalid_kind", "segment");
  return ok({ segmentId: value.segmentId, analysisRevision: value.analysisRevision, pageStart: value.pageStart, pageEnd: value.pageEnd, kind: value.kind as AnalysisSegmentKind, state: value.state as AnalysisSegmentState, commonOnly: value.commonOnly, source: value.source });
}
function parseRegion(value: unknown): ContractResult<AnalysisRegionV1> {
  if (!isRecord(value) || !ownKeys(value, ["regionId", "segmentId", "analysisRevision", "page", "rects", "kind", "state", "confirmationSource", "reasonCodes", "source"]) || !id(value.regionId) || !id(value.segmentId) || !count(value.analysisRevision) || !count(value.page) || !ANALYSIS_REGION_KINDS.includes(value.kind as AnalysisRegionKind) || !ANALYSIS_REGION_STATES.includes(value.state as AnalysisRegionState) || (value.confirmationSource !== null && !REGION_CONFIRMATION_SOURCES.includes(value.confirmationSource as RegionConfirmationSource)) || !reasons(value.reasonCodes) || !token(value.source)) return err("invalid_kind", "region");
  const rects = parseRects(value.rects); if (!rects.ok) return rects;
  return ok({ regionId: value.regionId, segmentId: value.segmentId, analysisRevision: value.analysisRevision, page: value.page, rects: rects.value, kind: value.kind as AnalysisRegionKind, state: value.state as AnalysisRegionState, confirmationSource: value.confirmationSource as RegionConfirmationSource | null, reasonCodes: value.reasonCodes, source: value.source });
}
function parseOccurrence(value: unknown): ContractResult<AnalysisOccurrenceV1> {
  const fields = ["occurrenceId", "segmentId", "regionId", "analysisRevision", "page", "rects", "tag", "category", "valueHash", "expectedTextHash", "source", "policy", "proposedAction", "state", "provenance"];
  if (!isRecord(value) || !ownKeys(value, fields) || !isMaskingOccurrenceId(value.occurrenceId) || !id(value.segmentId) || (value.regionId !== null && !id(value.regionId)) || !count(value.analysisRevision) || !count(value.page) || !token(value.tag) || !token(value.category) || !hash(value.valueHash) || !hash(value.expectedTextHash) || !token(value.source) || !token(value.policy) || !OCCURRENCE_PROPOSED_ACTIONS.includes(value.proposedAction as OccurrenceProposedAction) || !OCCURRENCE_STATES.includes(value.state as OccurrenceState) || !token(value.provenance)) return err("invalid_kind", "occurrence");
  const rects = parseRects(value.rects); if (!rects.ok) return rects;
  return ok({ occurrenceId: value.occurrenceId, segmentId: value.segmentId, regionId: value.regionId, analysisRevision: value.analysisRevision, page: value.page, rects: rects.value, tag: value.tag, category: value.category, valueHash: value.valueHash, expectedTextHash: value.expectedTextHash, source: value.source, policy: value.policy, proposedAction: value.proposedAction as OccurrenceProposedAction, state: value.state as OccurrenceState, provenance: value.provenance });
}
function parseReview(value: unknown): ContractResult<ReviewItemV1> {
  const fields = ["reviewId", "analysisRevision", "kind", "targetId", "pageStart", "pageEnd", "status", "reasonCodes", "requiresAcknowledgment", "commonOnly", "provenance"];
  const validKind = value && isRecord(value) && ["name", "institution", "acknowledge", "boundary", "ocr", "region_geometry"].includes(value.kind as string);
  if (!isRecord(value) || !ownKeys(value, fields) || !id(value.reviewId) || !count(value.analysisRevision) || !validKind || !id(value.targetId) || !count(value.pageStart) || !count(value.pageEnd) || value.pageStart > value.pageEnd || (value.status !== "pending" && value.status !== "resolved") || !reasons(value.reasonCodes) || typeof value.requiresAcknowledgment !== "boolean" || typeof value.commonOnly !== "boolean" || !token(value.provenance)) return err("invalid_status", "review_item");
  return ok({ reviewId: value.reviewId, analysisRevision: value.analysisRevision, kind: value.kind as ReviewKind, targetId: value.targetId, pageStart: value.pageStart, pageEnd: value.pageEnd, status: value.status, reasonCodes: value.reasonCodes, requiresAcknowledgment: value.requiresAcknowledgment, commonOnly: value.commonOnly, provenance: value.provenance });
}
function parseManualAction(value: unknown): ContractResult<ManualActionV1> {
  const fields = ["actionId", "analysisRevision", "page", "rects", "protectedNeighborRefs", "mode", "sourceKind", "linkedOccurrenceId", "expectedTextHash"];
  if (!isRecord(value) || !ownKeys(value, [...fields, "restoreAuthorizationHash"]) || !id(value.actionId) || !count(value.analysisRevision) || !count(value.page) || !MANUAL_ACTION_MODES.includes(value.mode as ManualActionMode) || !MANUAL_ACTION_SOURCE_KINDS.includes(value.sourceKind as ManualActionSourceKind) || (value.linkedOccurrenceId !== null && !isMaskingOccurrenceId(value.linkedOccurrenceId)) || (value.expectedTextHash !== null && !hash(value.expectedTextHash)) || (value.restoreAuthorizationHash !== null && value.restoreAuthorizationHash !== undefined && !hash(value.restoreAuthorizationHash))) return err("invalid_status", "manual_action");
  const rects = parseRects(value.rects); if (!rects.ok) return rects;
  const protectedNeighborRefs = parseRects(value.protectedNeighborRefs, { allowEmpty: value.sourceKind === "scan" || value.mode === "restore" }); if (!protectedNeighborRefs.ok) return protectedNeighborRefs;
  return ok({ actionId: value.actionId, analysisRevision: value.analysisRevision, page: value.page, rects: rects.value, protectedNeighborRefs: protectedNeighborRefs.value, mode: value.mode as ManualActionMode, sourceKind: value.sourceKind as ManualActionSourceKind, linkedOccurrenceId: value.linkedOccurrenceId, expectedTextHash: value.expectedTextHash, restoreAuthorizationHash: value.restoreAuthorizationHash ?? null });
}
function parseThresholdArtifact(value: unknown): ContractResult<ThresholdArtifactV1> {
  if (
    !isRecord(value)
    || !ownKeys(value, ["version", "contentHash", "autoMaskThreshold", "reviewThreshold"])
    || !token(value.version)
    || !hash(value.contentHash)
    || !finiteNumber(value.autoMaskThreshold)
    || !finiteNumber(value.reviewThreshold)
    || value.reviewThreshold < 0
    || value.autoMaskThreshold > 1
    || value.reviewThreshold > value.autoMaskThreshold
  ) return err("invalid_status", "threshold_artifact");
  return ok({
    version: value.version,
    contentHash: value.contentHash,
    autoMaskThreshold: value.autoMaskThreshold,
    reviewThreshold: value.reviewThreshold,
  });
}

function validateUniqueIds(manifest: { readonly segments: readonly AnalysisSegmentV1[]; readonly regions: readonly AnalysisRegionV1[]; readonly occurrences: readonly AnalysisOccurrenceV1[]; readonly reviewItems: readonly ReviewItemV1[]; readonly manualActions: readonly ManualActionV1[] }): ContractResult<void> {
  const allIds = [
    ...manifest.segments.map((item) => item.segmentId),
    ...manifest.regions.map((item) => item.regionId),
    ...manifest.occurrences.map((item) => item.occurrenceId),
    ...manifest.reviewItems.map((item) => item.reviewId),
    ...manifest.manualActions.map((item) => item.actionId),
  ];
  return new Set(allIds).size === allIds.length ? ok(undefined) : err("invalid_status", "analysis_manifest.ids");
}
function validateSegmentRevisions(manifest: AnalysisManifestV1): ContractResult<void> {
  return manifest.segments.every((segment) => segment.analysisRevision === manifest.analysisRevision)
    ? ok(undefined)
    : err("invalid_status", "segment.revision");
}
function validateRegionReferences(manifest: AnalysisManifestV1, segments: ReadonlyMap<string, AnalysisSegmentV1>): ContractResult<void> {
  for (const region of manifest.regions) {
    const segment = segments.get(region.segmentId);
    if (!segment || region.analysisRevision !== manifest.analysisRevision || region.page < segment.pageStart || region.page > segment.pageEnd) return err("invalid_status", "region.reference");
  }
  return ok(undefined);
}
function validateOccurrenceReferences(manifest: AnalysisManifestV1, segments: ReadonlyMap<string, AnalysisSegmentV1>, regions: ReadonlyMap<string, AnalysisRegionV1>): ContractResult<void> {
  for (const occurrence of manifest.occurrences) {
    const segment = segments.get(occurrence.segmentId);
    const region = occurrence.regionId === null ? undefined : regions.get(occurrence.regionId);
    if (!segment || occurrence.analysisRevision !== manifest.analysisRevision || occurrence.page < segment.pageStart || occurrence.page > segment.pageEnd || (occurrence.regionId !== null && (!region || region.segmentId !== occurrence.segmentId || region.page !== occurrence.page))) return err("invalid_status", "occurrence.reference");
  }
  return ok(undefined);
}
function validateReviewReferences(manifest: AnalysisManifestV1, segments: ReadonlyMap<string, AnalysisSegmentV1>, regions: ReadonlyMap<string, AnalysisRegionV1>, occurrences: ReadonlyMap<string, AnalysisOccurrenceV1>): ContractResult<void> {
  const pendingFor = (kinds: readonly ReviewKind[], targetId: string): boolean => manifest.reviewItems.some((review) =>
    review.status === "pending" && review.targetId === targetId && kinds.includes(review.kind));
  for (const review of manifest.reviewItems) {
    const target = review.kind === "name" || review.kind === "institution"
      ? occurrences.get(review.targetId)
      : review.kind === "region_geometry"
        ? regions.get(review.targetId)
        : segments.get(review.targetId);
    if (!target || review.analysisRevision !== manifest.analysisRevision) return err("invalid_status", "review_item.reference");
    const start = "pageStart" in target ? target.pageStart : target.page;
    const end = "pageEnd" in target ? target.pageEnd : target.page;
    if (review.pageStart < start || review.pageEnd > end) return err("invalid_status", "review_item.page");
  }
  if (manifest.segments.some((segment) => segment.state === "review_required"
    && !pendingFor(["acknowledge", "boundary", "ocr"], segment.segmentId))) {
    return err("invalid_status", "segment.review_coverage");
  }
  if (manifest.regions.some((region) => region.state !== "confirmed" && region.state !== "user_confirmed"
    && !pendingFor(["region_geometry"], region.regionId))) {
    return err("invalid_status", "region.review_coverage");
  }
  if (manifest.occurrences.some((occurrence) => occurrence.state === "review_required"
    && !pendingFor(["name", "institution"], occurrence.occurrenceId))) {
    return err("invalid_status", "occurrence.review_coverage");
  }
  return ok(undefined);
}
function validateManualActionReferences(manifest: AnalysisManifestV1, occurrences: ReadonlyMap<string, AnalysisOccurrenceV1>): ContractResult<void> {
  return manifest.manualActions.every((action) => action.analysisRevision === manifest.analysisRevision
    && (action.linkedOccurrenceId === null || occurrences.has(action.linkedOccurrenceId))
    && (action.mode === "restore"
      ? (action.sourceKind === "text_pdf"
        && action.linkedOccurrenceId !== null
        && action.expectedTextHash !== null
        && action.restoreAuthorizationHash !== null
        && action.protectedNeighborRefs.length === 0)
        || (action.sourceKind === "scan"
          && action.linkedOccurrenceId === null
          && action.expectedTextHash === null
          && action.restoreAuthorizationHash === null
          && action.protectedNeighborRefs.length === 0)
      : action.restoreAuthorizationHash === null))
    ? ok(undefined)
    : err("invalid_status", "manual_action.reference");
}
function validateManifestReferences(manifest: AnalysisManifestV1): ContractResult<void> {
  const segments = new Map(manifest.segments.map((item) => [item.segmentId, item]));
  const regions = new Map(manifest.regions.map((item) => [item.regionId, item]));
  const occurrences = new Map(manifest.occurrences.map((item) => [item.occurrenceId, item]));
  const checks = [
    validateSegmentRevisions(manifest),
    validateRegionReferences(manifest, segments),
    validateOccurrenceReferences(manifest, segments, regions),
    validateReviewReferences(manifest, segments, regions, occurrences),
    validateManualActionReferences(manifest, occurrences),
  ];
  return checks.find((check) => !check.ok) ?? ok(undefined);
}

export function parseAnalysisManifestV1(value: unknown): ContractResult<AnalysisManifestV1> {
  const fields = ["manifestVersion", "runId", "originalDocumentHash", "analysisRevision", "manifestHash", "profile", "policyVersion", "optionsVersion", "optionsHash", "thresholdVersion", "thresholdHash", "thresholdArtifact", "coordinateSpace", "approvalCoverage", "requiredRegionCoverage", "segments", "regions", "occurrences", "reviewItems", "manualActions"];
  if (!isRecord(value) || !ownKeys(value, fields) || value.manifestVersion !== 1 || !id(value.runId) || !hash(value.originalDocumentHash) || !count(value.analysisRevision) || !hash(value.manifestHash) || !RUNTIME_MASKING_PROFILES.includes(value.profile as PublicReviewProfile) || !token(value.policyVersion) || !token(value.optionsVersion) || !hash(value.optionsHash) || !token(value.thresholdVersion) || !hash(value.thresholdHash) || value.coordinateSpace !== "pdf_points_top_left"
    || !Array.isArray(value.segments) || !Array.isArray(value.regions) || !Array.isArray(value.occurrences) || !Array.isArray(value.reviewItems) || !Array.isArray(value.manualActions)) return err("invalid_status", "analysis_manifest");
  const coverageMaps = normalizedCoverageMaps(value.approvalCoverage, value.requiredRegionCoverage);
  if (!coverageMaps) return err("invalid_status", "analysis_manifest.regionCoverage");
  const parseAll = <T>(items: unknown[], parser: (item: unknown) => ContractResult<T>): ContractResult<T[]> => { const parsed: T[] = []; for (const item of items) { const result = parser(item); if (!result.ok) return result; parsed.push(result.value); } return ok(parsed); };
  const segments = parseAll(value.segments, parseSegment); if (!segments.ok) return segments;
  const regions = parseAll(value.regions, parseRegion); if (!regions.ok) return regions;
  const occurrences = parseAll(value.occurrences, parseOccurrence); if (!occurrences.ok) return occurrences;
  const reviewItems = parseAll(value.reviewItems, parseReview); if (!reviewItems.ok) return reviewItems;
  const manualActions = parseAll(value.manualActions, parseManualAction); if (!manualActions.ok) return manualActions;
  const thresholdArtifact = parseThresholdArtifact(value.thresholdArtifact); if (!thresholdArtifact.ok) return thresholdArtifact;
  const manifest: AnalysisManifestV1 = { manifestVersion: 1, runId: value.runId, originalDocumentHash: value.originalDocumentHash, analysisRevision: value.analysisRevision, manifestHash: value.manifestHash, profile: value.profile as PublicReviewProfile, policyVersion: value.policyVersion, optionsVersion: value.optionsVersion, optionsHash: value.optionsHash, thresholdVersion: value.thresholdVersion, thresholdHash: value.thresholdHash, thresholdArtifact: thresholdArtifact.value, coordinateSpace: "pdf_points_top_left", approvalCoverage: coverageMaps.approval, requiredRegionCoverage: coverageMaps.required, segments: segments.value, regions: regions.value, occurrences: occurrences.value, reviewItems: reviewItems.value, manualActions: manualActions.value };
  if (manifest.thresholdVersion !== manifest.thresholdArtifact.version || manifest.thresholdHash !== manifest.thresholdArtifact.contentHash) return err("invalid_status", "threshold_artifact.identity");
  const unique = validateUniqueIds(manifest); if (!unique.ok) return unique;
  const references = validateManifestReferences(manifest); if (!references.ok) return references;
  return ok(manifest);
}

function equalReviewItem(left: ReviewItemV1, right: ReviewItemV1): boolean {
  return left.reviewId === right.reviewId && left.analysisRevision === right.analysisRevision && left.kind === right.kind && left.targetId === right.targetId && left.pageStart === right.pageStart && left.pageEnd === right.pageEnd && left.status === right.status && left.requiresAcknowledgment === right.requiresAcknowledgment && left.commonOnly === right.commonOnly && left.provenance === right.provenance && left.reasonCodes.length === right.reasonCodes.length && left.reasonCodes.every((code, index) => code === right.reasonCodes[index]);
}
export function canonicalReviewQueue(report: BoundSafeReport): ContractResult<readonly ReviewItemV1[]> {
  const manifest = report.analysisManifest;
  if (!manifest) return err("missing_review_items", "analysisManifest");
  if (!coverage(manifest.approvalCoverage, ["approval", "header_meta", "labeled_staff"])
    || !coverage(manifest.requiredRegionCoverage, ["recipient_reference", "sender_institution", "approval_staff", "dispatch_metadata", "footer_contact"])) {
    return err("invalid_status", "analysisManifest.regionCoverage");
  }
  const queue = report.reviewQueue;
  if (!queue) return err("missing_review_items", "reviewQueue.absent");
  if (queue.length !== manifest.reviewItems.length) return err("invalid_status", "reviewQueue.divergence");
  return manifest.reviewItems.every((item, index) => equalReviewItem(item, queue[index]!))
    ? ok(manifest.reviewItems)
    : err("invalid_status", "reviewQueue.divergence");
}

export type CanonicalMaskCounts = {
  /**
   * Confirmed automatic mask occurrences that are still effective after
   * linked manual actions replace their occurrence.
   */
  readonly automaticMaskCount: number;
  /** Manual actions that add a mask at final-save time. */
  readonly manualMaskCount: number;
  /** Manual restore actions, including actions that currently block saving. */
  readonly manualRestoreCount: number;
  /**
   * Number of mask-producing entries expected in the saved document:
   * effective automatic masks plus manual mask actions. Restore actions do not
   * reduce this value because an overlapping restore is rejected by the save
   * gate and a disjoint restore does not remove an automatic mask.
   */
  readonly effectiveMaskCount: number;
};

function manuallyReplacedOccurrenceIds(manifest: AnalysisManifestV1): ReadonlySet<string> {
  return new Set(
    manifest.manualActions.flatMap((action) => action.linkedOccurrenceId === null ? [] : [action.linkedOccurrenceId]),
  );
}

function finalizedAutomaticMaskOccurrences(manifest: AnalysisManifestV1): readonly AnalysisOccurrenceV1[] {
  const manuallyReplaced = manuallyReplacedOccurrenceIds(manifest);
  return manifest.occurrences.filter((occurrence) =>
    occurrence.proposedAction === "mask"
      && FINAL_APPLIED_MASK_STATES.includes(occurrence.state as FinalAppliedMaskState)
      && !manuallyReplaced.has(occurrence.occurrenceId),
  );
}

export function canonicalMaskCounts(report: BoundSafeReport): ContractResult<CanonicalMaskCounts> {
  const queue = canonicalReviewQueue(report);
  if (!queue.ok) return { ok: false, errors: queue.errors };
  const manifest = report.analysisManifest!;
  const automaticMaskCount = finalizedAutomaticMaskOccurrences(manifest).length;
  const manualMaskCount = manifest.manualActions.filter((action) => action.mode === "mask").length;
  const manualRestoreCount = manifest.manualActions.filter((action) => action.mode === "restore").length;
  return ok({
    automaticMaskCount,
    manualMaskCount,
    manualRestoreCount,
    effectiveMaskCount: automaticMaskCount + manualMaskCount,
  });
}

export function canonicalMaskCount(report: BoundSafeReport): ContractResult<number> {
  const counts = canonicalMaskCounts(report);
  if (!counts.ok) return { ok: false, errors: counts.errors };
  // Protocol operation count is intentionally distinct from the effective
  // mask count: a restore is audited as an operation but does not produce a
  // black mask in the final document.
  return ok(counts.value.automaticMaskCount + counts.value.manualMaskCount + counts.value.manualRestoreCount);
}

function rectsOverlap(
  left: PdfPointsTopLeftRect,
  right: PdfPointsTopLeftRect,
): boolean {
  const leftX0 = Math.min(left.x0, left.x1);
  const leftX1 = Math.max(left.x0, left.x1);
  const leftY0 = Math.min(left.y0, left.y1);
  const leftY1 = Math.max(left.y0, left.y1);
  const rightX0 = Math.min(right.x0, right.x1);
  const rightX1 = Math.max(right.x0, right.x1);
  const rightY0 = Math.min(right.y0, right.y1);
  const rightY1 = Math.max(right.y0, right.y1);
  return leftX0 < rightX1 && leftX1 > rightX0 && leftY0 < rightY1 && leftY1 > rightY0;
}

export function manualActionOverlapsConfirmedMask(
  manifest: Pick<AnalysisManifestV1, "occurrences">,
  action: ManualActionV1,
): boolean {
  if (action.mode !== "restore" || (action.linkedOccurrenceId !== null && action.restoreAuthorizationHash !== null)) return false;
  const confirmedMasks = manifest.occurrences.filter((occurrence) =>
    occurrence.proposedAction === "mask"
      && FINAL_APPLIED_MASK_STATES.includes(occurrence.state as FinalAppliedMaskState),
  );
  return confirmedMasks.some((occurrence) =>
    occurrence.page === action.page
      && action.rects.some((restore) => occurrence.rects.some((mask) => rectsOverlap(restore, mask))),
  );
}

export function hasRestoreReexposure(manifest: AnalysisManifestV1): boolean {
  return manifest.manualActions.some((action) => manualActionOverlapsConfirmedMask(manifest, action));
}
function parseSafeRedaction(value: unknown): ContractResult<SafeReportRedaction> {
  if (!isRecord(value) || !ownKeys(value, ["status", "missing_targets_count", "verification"])) {
    return err("invalid_status", "safeReport.redaction");
  }

  const status = value.status;
  if (status !== undefined && !token(status)) return err("invalid_status", "safeReport.redaction.status");

  const missingTargetsCount = value.missing_targets_count;
  if (missingTargetsCount !== undefined && !count(missingTargetsCount)) {
    return err("invalid_status", "safeReport.redaction.missing_targets_count");
  }

  const verification = value.verification;
  if (verification === undefined) {
    return ok({
      ...(status === undefined ? {} : { status }),
      ...(missingTargetsCount === undefined ? {} : { missing_targets_count: missingTargetsCount }),
    });
  }
  if (!isRecord(verification) || !ownKeys(verification, ["residual_hits", "verified"])) {
    return err("invalid_status", "safeReport.redaction.verification");
  }

  const residualHits = verification.residual_hits;
  if (residualHits !== undefined && !count(residualHits)) {
    return err("invalid_status", "safeReport.redaction.verification.residual_hits");
  }
  const verified = verification.verified;
  if (verified !== undefined && typeof verified !== "boolean") {
    return err("invalid_status", "safeReport.redaction.verification.verified");
  }

  return ok({
    ...(status === undefined ? {} : { status }),
    ...(missingTargetsCount === undefined ? {} : { missing_targets_count: missingTargetsCount }),
    verification: {
      ...(residualHits === undefined ? {} : { residual_hits: residualHits }),
      ...(verified === undefined ? {} : { verified }),
    },
  });
}


export function parseSafeReport(value: unknown): ContractResult<SafeReport> {
  const fields = ["product_checks", "document_redaction", "pdf_redaction", "analysisManifest", "reviewQueue"];
  if (!isRecord(value) || !ownKeys(value, fields) || !isRecord(value.product_checks)) return err("missing_report", "safeReport");
  const checks = value.product_checks;
  const checkFields = ["quality_gate_passed", "needs_manual_review", "final_submission_allowed", "native_redaction_verified", "native_redaction_status", "native_redaction_reason_code", "text_surface_verified", "pdf_input", "text_deidentification_final_submission_evidence", "raw_values_saved"];
  const booleanChecks = checkFields.filter((key) => key !== "native_redaction_status" && key !== "native_redaction_reason_code");
  if (!ownKeys(checks, checkFields)
    || booleanChecks.some((key) => checks[key] !== undefined && typeof checks[key] !== "boolean")
    || (checks.native_redaction_status !== undefined && !token(checks.native_redaction_status))
    || (checks.native_redaction_reason_code !== undefined
      && checks.native_redaction_reason_code !== null
      && (typeof checks.native_redaction_reason_code !== "string" || !REASON.test(checks.native_redaction_reason_code)))) {
    return err("invalid_status", "safeReport.product_checks");
  }
  const documentRedaction = value.document_redaction === undefined ? undefined : parseSafeRedaction(value.document_redaction); if (documentRedaction && !documentRedaction.ok) return documentRedaction;
  const pdfRedaction = value.pdf_redaction === undefined ? undefined : parseSafeRedaction(value.pdf_redaction); if (pdfRedaction && !pdfRedaction.ok) return pdfRedaction;
  if (value.analysisManifest === undefined || value.reviewQueue === undefined || !Array.isArray(value.reviewQueue)) {
    return err("missing_review_items", value.analysisManifest === undefined ? "analysisManifest" : "reviewQueue.absent");
  }
  const manifest = parseAnalysisManifestV1(value.analysisManifest); if (!manifest.ok) return manifest;
  const queue: ReviewItemV1[] = [];
  for (const item of value.reviewQueue) {
    const result = parseReview(item);
    if (!result.ok) return result;
    queue.push(result.value);
  }
  if (manifest.value.reviewItems.length !== queue.length || !manifest.value.reviewItems.every((item, index) => equalReviewItem(item, queue[index]!))) {
    return err("invalid_status", "safeReport.reviewQueue.divergence");
  }
  return ok({
    product_checks: checks as SafeReportProductChecks,
    ...(documentRedaction ? { document_redaction: documentRedaction.value } : {}),
    ...(pdfRedaction ? { pdf_redaction: pdfRedaction.value } : {}),
    analysisManifest: manifest.value,
    reviewQueue: manifest.value.reviewItems,
  });
}


export function parseLegacySafeReport(value: unknown): ContractResult<SafeReport> {
  if (!isRecord(value) || !isRecord(value.product_checks)) return err("missing_report", "legacySafeReport");
  const checks = value.product_checks;
  const checkFields = ["quality_gate_passed", "needs_manual_review", "final_submission_allowed", "native_redaction_verified", "native_redaction_status", "native_redaction_reason_code", "text_surface_verified", "pdf_input", "text_deidentification_final_submission_evidence", "raw_values_saved"];
  const booleanChecks = checkFields.filter((key) => key !== "native_redaction_status" && key !== "native_redaction_reason_code");
  if (!ownKeys(checks, checkFields)
    || booleanChecks.some((key) => checks[key] !== undefined && typeof checks[key] !== "boolean")
    || (checks.native_redaction_status !== undefined && !token(checks.native_redaction_status))
    || (checks.native_redaction_reason_code !== undefined
      && checks.native_redaction_reason_code !== null
      && (typeof checks.native_redaction_reason_code !== "string" || !REASON.test(checks.native_redaction_reason_code)))) {
    return err("invalid_status", "legacySafeReport.product_checks");
  }
  const documentRedaction = value.document_redaction === undefined ? undefined : parseSafeRedaction(value.document_redaction);
  if (documentRedaction && !documentRedaction.ok) return documentRedaction;
  const pdfRedaction = value.pdf_redaction === undefined ? undefined : parseSafeRedaction(value.pdf_redaction);
  if (pdfRedaction && !pdfRedaction.ok) return pdfRedaction;
  if (!documentRedaction && !pdfRedaction) return err("missing_redaction", "legacySafeReport.redaction");
  return ok({
    product_checks: checks as SafeReportProductChecks,
    ...(documentRedaction ? { document_redaction: documentRedaction.value } : {}),
    ...(pdfRedaction ? { pdf_redaction: pdfRedaction.value } : {}),
  });
}

function validIdentity(value: unknown): value is MaskingSessionIdentity {
  return isRecord(value)
    && id(value.runId)
    && hash(value.originalDocumentHash)
    && count(value.analysisRevision)
    && hash(value.manifestHash)
    && RUNTIME_MASKING_PROFILES.includes(value.profile as PublicReviewProfile);
}

function snapshotIdentity(identity: MaskingSessionIdentity): MaskingSessionIdentity {
  return Object.freeze({ ...identity });
}
function freezeDeep<T>(value: T): T {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value as Record<string, unknown>)) freezeDeep(nested);
    Object.freeze(value);
  }
  return value;
}


export function boundSafeReportIdentity(report: BoundSafeReport): MaskingSessionIdentity {
  const identity = report[BOUND_SAFE_REPORT];
  if (!validIdentity(identity)) throw new Error("Invalid bound masking report.");
  return snapshotIdentity(identity);
}
export function isBoundSafeReport(report: unknown): report is BoundSafeReport {
  if (!isRecord(report) || !Object.isFrozen(report)) return false;
  try {
    const identity = boundSafeReportIdentity(report as BoundSafeReport);
    const manifest = (report as BoundSafeReport).analysisManifest;
    return manifest !== undefined
      && manifest.runId === identity.runId
      && manifest.originalDocumentHash === identity.originalDocumentHash
      && manifest.analysisRevision === identity.analysisRevision
      && manifest.manifestHash === identity.manifestHash
      && manifest.profile === identity.profile;
  } catch {
    return false;
  }
}

export function parseBoundSafeReport(value: unknown, expected: MaskingSessionIdentity): ContractResult<BoundSafeReport> {
  if (!validIdentity(expected)) return err("invalid_status", "safeReport.expectedIdentity");
  const report = parseSafeReport(value);
  if (!report.ok) return report;
  const manifest = report.value.analysisManifest!;
  if (manifest.runId !== expected.runId
    || manifest.originalDocumentHash !== expected.originalDocumentHash
    || manifest.analysisRevision !== expected.analysisRevision
    || manifest.manifestHash !== expected.manifestHash
    || manifest.profile !== expected.profile) {
    return err("invalid_status", "safeReport.identity");
  }
  return ok(freezeDeep({ ...report.value, [BOUND_SAFE_REPORT]: snapshotIdentity(expected) }));
}
