export type AcknowledgeReviewResolution = {
  readonly acknowledged: true;
  readonly kind: "acknowledge";
};

export type AnalysisOccurrenceV1 = {
  readonly analysisRevision: number;
  readonly category: string;
  readonly expectedTextHash: string;
  readonly occurrenceId: string;
  readonly page: number;
  readonly policy: string;
  readonly proposedAction: OccurrenceAction;
  readonly provenance: string;
  readonly rects: readonly PdfPointsTopLeftRect[];
  readonly regionId: string | null;
  readonly segmentId: string;
  readonly source: string;
  readonly state: OccurrenceState;
  readonly tag: string;
  readonly valueHash: string;
};

export type AnalysisRegionKind = "approval" | "header_meta" | "labeled_staff" | "recipient_reference" | "sender_institution" | "approval_staff" | "dispatch_metadata" | "footer_contact";

export type AnalysisRegionState = "confirmed" | "review_required" | "unconfirmed" | "user_confirmed";

export type AnalysisRegionV1 = {
  readonly analysisRevision: number;
  readonly confirmationSource: "automatic" | "user" | null;
  readonly kind: AnalysisRegionKind;
  readonly page: number;
  readonly reasonCodes: readonly string[];
  readonly rects: readonly PdfPointsTopLeftRect[];
  readonly regionId: string;
  readonly segmentId: string;
  readonly source: string;
  readonly state: AnalysisRegionState;
};

export type AnalysisSegmentKind = "internal_review" | "official_dispatch" | "attachment" | "unknown" | "legal" | "mixed" | "common";

export type AnalysisSegmentState = "confirmed" | "review_required" | "user_confirmed";

export type AnalysisSegmentV1 = {
  readonly analysisRevision: number;
  readonly commonOnly: boolean;
  readonly kind: AnalysisSegmentKind;
  readonly pageEnd: number;
  readonly pageStart: number;
  readonly segmentId: string;
  readonly source: string;
  readonly state: AnalysisSegmentState;
};

export type ApprovalCoverage = {
  readonly protectedNeighborCount: number;
  readonly schemaVersion: 1;
  readonly signerCount: number;
  readonly state: CoverageState;
};

export type BoundaryReviewResolution = {
  readonly kind: "boundary";
  readonly pageEnd: number;
  readonly pageStart: number;
  readonly segmentKind: BoundarySegmentKind;
};

export type BoundarySegmentKind = "internal_review" | "official_dispatch" | "attachment" | "legal";

export type CoverageState = "present" | "absent" | "indeterminate";

export type FinalizeSaveConfirmation = {
  readonly status: "not_required" | "user_confirmed";
  readonly unresolvedReviews: readonly FinalizeSaveWarning[];
};

export type FinalizeSaveWarning = {
  readonly category: string;
  readonly kind: string;
  readonly pageEnd: number;
  readonly pageStart: number;
  readonly reasonCodes: readonly string[];
  readonly targetId: string | null;
};

export type InstitutionReviewResolution = {
  readonly action: "mask" | "exclude";
  readonly kind: "institution";
};

export type ManualActionV1 = {
  readonly actionId: string;
  readonly analysisRevision: number;
  readonly expectedTextHash: string | null;
  readonly linkedOccurrenceId: string | null;
  readonly mode: "mask" | "restore";
  readonly page: number;
  readonly protectedNeighborRefs: readonly PdfPointsTopLeftRect[];
  readonly rects: readonly PdfPointsTopLeftRect[];
  readonly restoreAuthorizationHash?: string | null;
  readonly sourceKind: "text_pdf" | "scan";
};

export type NameReviewResolution = {
  readonly action: "mask" | "exclude";
  readonly kind: "name";
};

export type OccurrenceAction = "mask" | "exclude" | "review";

export type OccurrenceState = "confirmed" | "review_required" | "user_confirmed";

export type OcrReviewResolution = {
  readonly accepted: boolean;
  readonly kind: "ocr";
};

export type PdfPointsTopLeftRect = {
  readonly x0: number;
  readonly x1: number;
  readonly y0: number;
  readonly y1: number;
};

export type PublicReviewProfile = "internal_review" | "official_dispatch" | "mixed";

export type RegionGeometryReviewResolution = {
  readonly kind: "region_geometry";
  readonly rects: readonly PdfPointsTopLeftRect[];
};

export type RequiredRegionCoverage = {
  readonly blocking: boolean;
  readonly kinds: readonly RequiredRegionCoverageKind[];
  readonly profile: PublicReviewProfile;
  readonly schemaVersion: 1;
};

export type RequiredRegionCoverageKind = {
  readonly kind: AnalysisRegionKind;
  readonly state: CoverageState;
};

export type RestoreAuthorizationSummary = {
  readonly actionIdHash: string;
  readonly authorizationEvent: string;
  readonly targetOccurrenceIdHash: string;
};

export type ReviewItemV1 = {
  readonly analysisRevision: number;
  readonly commonOnly: boolean;
  readonly kind: ReviewKind;
  readonly pageEnd: number;
  readonly pageStart: number;
  readonly provenance: string;
  readonly reasonCodes: readonly string[];
  readonly requiresAcknowledgment: boolean;
  readonly reviewId: string;
  readonly status: ReviewStatus;
  readonly targetId: string;
};

export type ReviewKind = "name" | "institution" | "acknowledge" | "boundary" | "ocr" | "region_geometry";

export type ReviewStatus = "pending" | "resolved";

export type ThresholdArtifactV1 = {
  readonly autoMaskThreshold: number;
  readonly contentHash: string;
  readonly reviewThreshold: number;
  readonly version: string;
};

export type AnalysisManifestV1 = {
  readonly analysisRevision: number;
  readonly approvalCoverage: ApprovalCoverage;
  readonly coordinateSpace: "pdf_points_top_left";
  readonly manifestHash: string;
  readonly manifestVersion: 1;
  readonly manualActions: readonly ManualActionV1[];
  readonly occurrences: readonly AnalysisOccurrenceV1[];
  readonly optionsHash: string;
  readonly optionsVersion: string;
  readonly originalDocumentHash: string;
  readonly policyVersion: string;
  readonly profile: PublicReviewProfile;
  readonly regions: readonly AnalysisRegionV1[];
  readonly requiredRegionCoverage: RequiredRegionCoverage;
  readonly reviewItems: readonly ReviewItemV1[];
  readonly runId: string;
  readonly segments: readonly AnalysisSegmentV1[];
  readonly thresholdArtifact: ThresholdArtifactV1;
  readonly thresholdHash: string;
  readonly thresholdVersion: string;
};

export type FinalizeMaskingRunResult = {
  readonly analysisRevision: number;
  readonly appliedMaskCount: number;
  readonly effectiveMaskCount: number;
  readonly finalHash: string;
  readonly finalHashAttested: true;
  readonly finalPath: string;
  readonly manifestHash: string;
  readonly manualMaskCount: number;
  readonly occurrenceCount: number;
  readonly restoreAuthorization: RestoreAuthorizationSummary;
  readonly restoreCount: number;
  readonly runId: string;
  readonly saveConfirmation: FinalizeSaveConfirmation;
  readonly status: "promoted";
};

export type ResolveMaskingReviewRequest = {
  readonly analysisRevision: number;
  readonly manifestHash: string;
  readonly resolution: NameReviewResolution | InstitutionReviewResolution | AcknowledgeReviewResolution | BoundaryReviewResolution | OcrReviewResolution | RegionGeometryReviewResolution;
  readonly reviewId: string;
  readonly runId: string;
};

export type ReviewResolution = NameReviewResolution | InstitutionReviewResolution | AcknowledgeReviewResolution | BoundaryReviewResolution | OcrReviewResolution | RegionGeometryReviewResolution;
