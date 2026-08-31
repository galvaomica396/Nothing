import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

export function geometryReviewManifestForQa({
  revision = 1,
  manifestHash = "a".repeat(64),
  resolvedRegionIds = [],
} = {}) {
  const segmentId = `geometry-segment-r${revision}`;
  const regions = [
    { rects: [{ x0: 72, y0: 60, x1: 200, y1: 96 }], reasonCodes: ["geometry_review", "box_structure_missing"] },
    { rects: [{ x0: 140, y0: 78, x1: 236, y1: 120 }], reasonCodes: ["geometry_review"] },
  ].map((region, index) => {
    const regionId = `geometry-region-${index + 1}-r${revision}`;
    const resolved = resolvedRegionIds.includes(regionId);
    return {
      ...region,
      regionId,
      segmentId,
      analysisRevision: revision,
      page: 1,
      kind: "approval",
      state: "confirmed",
      confirmationSource: "automatic",
      source: "qa",
    };
  });
  return {
    manifestVersion: 1, runId: "qa-public-run-1", originalDocumentHash: "1".repeat(64),
    analysisRevision: revision, manifestHash,
    profile: "official_dispatch", policyVersion: "official-v1", optionsVersion: "options-v1",
    optionsHash: "2".repeat(64), thresholdVersion: "thresholds-v2",
    thresholdHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
    thresholdArtifact: {
      version: "thresholds-v2",
      contentHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
      autoMaskThreshold: 0.85,
      reviewThreshold: 0.5,
    },
    coordinateSpace: "pdf_points_top_left",
    approvalCoverage: { approval: "absent", header_meta: "absent", labeled_staff: "absent" },
    requiredRegionCoverage: {
      recipient_reference: "absent",
      sender_institution: "absent",
      approval_staff: "absent",
      dispatch_metadata: "absent",
      footer_contact: "absent",
    },
    segments: [{ segmentId, analysisRevision: revision, pageStart: 0, pageEnd: 1, kind: "official_dispatch", state: "confirmed", commonOnly: false, source: "qa" }],
    regions,
    occurrences: regions.map((region, index) => ({
      occurrenceId: `occ_${revision.toString(16).padStart(22, "0")}${(index + 1).toString(16).padStart(2, "0")}`,
      segmentId,
      regionId: region.regionId,
      analysisRevision: revision,
      page: region.page,
      rects: index === 0 ? [{ x0: 100, y0: 70, x1: 160, y1: 88 }] : [{ x0: 164, y0: 88, x1: 208, y1: 106 }],
      tag: "STAFF",
      category: "staff",
      valueHash: String(index + 3).repeat(64),
      expectedTextHash: String(index + 6).repeat(64),
      source: "qa",
      policy: "token",
      proposedAction: "mask",
      state: "confirmed",
      provenance: "qa",
    })),
    reviewItems: [],
    manualActions: [],
  };
}

export function unresolvedGeometryManifestForQa({
  revision = 1,
  manifestHash = "a".repeat(64),
} = {}) {
  const manifest = geometryReviewManifestForQa({ revision, manifestHash });
  const region = manifest.regions[0];
  const occurrence = manifest.occurrences[0];
  if (!region || !occurrence) throw new Error("QA_UNRESOLVED_GEOMETRY_FIXTURE_INVALID");
  return {
    ...manifest,
    regions: manifest.regions.map((item) => item.regionId === region.regionId
      ? { ...item, state: "review_required", confirmationSource: null }
      : item),
    occurrences: manifest.occurrences.map((item) => item.occurrenceId === occurrence.occurrenceId
      ? { ...item, proposedAction: "review", state: "review_required" }
      : item),
    reviewItems: [
      { reviewId: `geometry-review-${revision}`, analysisRevision: revision, kind: "region_geometry", targetId: region.regionId, pageStart: region.page, pageEnd: region.page, status: "pending", reasonCodes: ["geometry_review"], requiresAcknowledgment: false, commonOnly: false, provenance: "qa" },
      { reviewId: `occurrence-review-${revision}`, analysisRevision: revision, kind: "name", targetId: occurrence.occurrenceId, pageStart: occurrence.page, pageEnd: occurrence.page, status: "pending", reasonCodes: ["geometry_review"], requiresAcknowledgment: false, commonOnly: false, provenance: "qa" },
    ],
  };
}

export function publicManualMaskManifestForQa({
  revision = 1,
  manifestHash = "a".repeat(64),
  resolved = false,
  keywordDetected = false,
  manualActions = [],
} = {}) {
  return {
    manifestVersion: 1, runId: "qa-public-run-1", originalDocumentHash: "1".repeat(64),
    analysisRevision: revision, manifestHash,
    profile: "official_dispatch", policyVersion: "official-v1", optionsVersion: "options-v1",
    optionsHash: "2".repeat(64), thresholdVersion: "thresholds-v2",
    thresholdHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
    thresholdArtifact: {
      version: "thresholds-v2",
      contentHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
      autoMaskThreshold: 0.85,
      reviewThreshold: 0.5,
    },
    coordinateSpace: "pdf_points_top_left",
    approvalCoverage: { approval: "absent", header_meta: "absent", labeled_staff: "absent" },
    requiredRegionCoverage: {
      recipient_reference: "absent",
      sender_institution: "absent",
      approval_staff: "absent",
      dispatch_metadata: "absent",
      footer_contact: "absent",
    },
    segments: [{ segmentId: "segment-1", analysisRevision: revision, pageStart: 0, pageEnd: 0, kind: "official_dispatch", state: "confirmed", commonOnly: false, source: "qa" }],
    regions: [],
    occurrences: [
      { occurrenceId: "occ_000000000000000000000001", segmentId: "segment-1", regionId: null, analysisRevision: revision, page: 0, rects: [{ x0: 72, y0: 60, x1: 200, y1: 78 }], tag: "NAME", category: "name", valueHash: "4".repeat(64), expectedTextHash: "5".repeat(64), source: "text_pdf", policy: "mask", proposedAction: resolved ? "mask" : "review", state: resolved ? "confirmed" : "review_required", provenance: "qa" },
      { occurrenceId: "occ_000000000000000000000002", segmentId: "segment-1", regionId: null, analysisRevision: revision, page: 0, rects: [{ x0: 280, y0: 60, x1: 408, y1: 82 }], tag: "PHONE", category: "phone", valueHash: "6".repeat(64), expectedTextHash: "7".repeat(64), source: "text_pdf", policy: "mask", proposedAction: "mask", state: "confirmed", provenance: "qa" },
      ...(keywordDetected ? [{ occurrenceId: "occ_000000000000000000000003", segmentId: "segment-1", regionId: null, analysisRevision: revision, page: 0, rects: [{ x0: 72, y0: 108, x1: 200, y1: 130 }], tag: "KEYWORD", category: "custom_keyword", valueHash: "8".repeat(64), expectedTextHash: "9".repeat(64), source: "custom_keyword", policy: "masking-policy-v1", proposedAction: "mask", state: "confirmed", provenance: "custom_keyword" }] : []),
    ],
    reviewItems: [{ reviewId: "review-1", analysisRevision: revision, kind: "name", targetId: "occ_000000000000000000000001", pageStart: 0, pageEnd: 0, status: resolved ? "resolved" : "pending", reasonCodes: ["requires_review"], requiresAcknowledgment: false, commonOnly: false, provenance: "qa" }],
    manualActions: manualActions.map((action, index) => ({
      ...action,
      actionId: `manual-${revision}-${index + 1}`,
      analysisRevision: revision,
    })),
  };
}

export async function installTauriQaMocks(page, options) {
  await page.addInitScript(
    ({ fixturePathValue, outputDirValue, fixtureBytes, failBatchItemOnce, analyzeDelayMs, codedAnalyzeFailure, malformedPublicManualMaskSuccessor, reviewScenario, realManifestValue, geometryManifestValue }) => {
      window.confirm = () => true;
      window.__QA_INVOKES__ = [];
      let selectedFinalTarget = null;
      let saveTokenCounter = 0;
      const failedBatchInputs = new Set();
      const publicSession = {
        runId: "qa-public-run-1",
        revision: 1,
        manifestHash: "a".repeat(64),
        profile: "official_dispatch",
        analyzed: false,
        resolved: false,
        reviewDecision: null,
        excludedOccurrenceId: null,
        keywordDetected: false,
        saveToken: null,
        destination: null,
        destinationIdentity: null,
        destinationConsumed: false,
        geometryResolvedIndexes: [],
        geometryResolvedRectsByIndex: {},
        boundaryResolved: false,
        boundaryKind: "unknown",
        resolvedCoverageKinds: [],
        coverageRectsByKind: {},
        manualActions: [],
      };
      const hashPath = (value) => {
        let hash = 5381;
        for (const char of String(value || "")) hash = ((hash << 5) + hash) ^ char.charCodeAt(0);
        return `qa-${(hash >>> 0).toString(16)}`;
      };
      const publicManifest = () => {
        if (reviewScenario === "indeterminate_coverage") {
          const suffix = `r${publicSession.revision}`;
          const segmentId = `coverage-segment-${suffix}`;
          const coverageRegions = [
            { kind: "header_meta", page: 0 },
            { kind: "dispatch_metadata", page: 0 },
          ].map(({ kind, page }) => {
            const resolved = publicSession.resolvedCoverageKinds.includes(kind);
            return {
              regionId: `coverage-region-${kind}-${suffix}`,
              segmentId,
              analysisRevision: publicSession.revision,
              page,
              rects: publicSession.coverageRectsByKind[kind] ?? [{ x0: 72, y0: 60, x1: 200, y1: 96 }],
              kind,
              state: resolved ? "user_confirmed" : "review_required",
              confirmationSource: resolved ? "user" : null,
              reasonCodes: ["geometry_review"],
              source: "qa",
            };
          });
          return {
            manifestVersion: 1, runId: publicSession.runId, originalDocumentHash: "1".repeat(64),
            analysisRevision: publicSession.revision, manifestHash: publicSession.manifestHash,
            profile: "mixed", policyVersion: "official-v1", optionsVersion: "options-v1",
            optionsHash: "2".repeat(64), thresholdVersion: "thresholds-v2",
            thresholdHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
            thresholdArtifact: {
              version: "thresholds-v2",
              contentHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
              autoMaskThreshold: 0.85,
              reviewThreshold: 0.5,
            },
            coordinateSpace: "pdf_points_top_left",
            approvalCoverage: {
              approval: "absent",
              header_meta: publicSession.resolvedCoverageKinds.includes("header_meta") ? "absent" : "indeterminate",
              labeled_staff: "absent",
            },
            requiredRegionCoverage: {
              recipient_reference: "absent",
              sender_institution: "absent",
              approval_staff: "absent",
              dispatch_metadata: publicSession.resolvedCoverageKinds.includes("dispatch_metadata") ? "absent" : "indeterminate",
              footer_contact: "absent",
            },
            segments: [{ segmentId, analysisRevision: publicSession.revision, pageStart: 0, pageEnd: 0, kind: "mixed", state: "confirmed", commonOnly: false, source: "qa" }],
            regions: coverageRegions,
            occurrences: [],
            reviewItems: coverageRegions.map((region) => ({
              reviewId: `coverage-review-${region.kind}-${suffix}`,
              analysisRevision: publicSession.revision,
              kind: "region_geometry",
              targetId: region.regionId,
              pageStart: region.page,
              pageEnd: region.page,
              status: publicSession.resolvedCoverageKinds.includes(region.kind) ? "resolved" : "pending",
              reasonCodes: ["geometry_review"],
              requiresAcknowledgment: false,
              commonOnly: false,
              provenance: "qa",
            })),
            manualActions: [],
          };
        }
        if (reviewScenario === "geometry") {
          const manifest = structuredClone(geometryManifestValue);
          const revision = publicSession.revision;
          const segmentId = `geometry-segment-r${revision}`;
          const regionIdFor = (index) => `geometry-region-${index + 1}-r${revision}`;
          const occurrenceIdFor = (index) => `occ_${revision.toString(16).padStart(22, "0")}${(index + 1).toString(16).padStart(2, "0")}`;
          const resolved = (index) => publicSession.geometryResolvedIndexes.includes(index);
          const regions = manifest.regions.map((region, index) => ({
            ...region,
            regionId: regionIdFor(index),
            segmentId,
            analysisRevision: revision,
            rects: publicSession.geometryResolvedRectsByIndex[index] ?? region.rects,
            state: resolved(index) ? "user_confirmed" : region.state,
            confirmationSource: resolved(index) ? "user" : region.confirmationSource,
          }));
          return {
            ...manifest,
            runId: publicSession.runId,
            analysisRevision: revision,
            manifestHash: publicSession.manifestHash,
            segments: manifest.segments.map((segment) => ({ ...segment, segmentId, analysisRevision: revision })),
            regions,
            occurrences: manifest.occurrences.map((occurrence, index) => ({
              ...occurrence,
              occurrenceId: occurrenceIdFor(index),
              segmentId,
              regionId: regionIdFor(index),
              analysisRevision: revision,
            })),
            reviewItems: manifest.reviewItems.map((review, index) => ({
              ...review,
              reviewId: `geometry-review-${index + 1}-r${revision}`,
              targetId: regionIdFor(index),
              analysisRevision: revision,
              status: resolved(index) ? "resolved" : "pending",
            })),
          };
        }
        if (reviewScenario === "boundary") {
          const suffix = `r${publicSession.revision}`;
          const segmentId = `boundary-segment-${suffix}`;
          return {
            manifestVersion: 1, runId: publicSession.runId, originalDocumentHash: "1".repeat(64),
            analysisRevision: publicSession.revision, manifestHash: publicSession.manifestHash,
            profile: "official_dispatch", policyVersion: "official-v1", optionsVersion: "options-v1",
            optionsHash: "2".repeat(64), thresholdVersion: "thresholds-v2",
            thresholdHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
            thresholdArtifact: {
              version: "thresholds-v2",
              contentHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
              autoMaskThreshold: 0.85,
              reviewThreshold: 0.5,
            },
            coordinateSpace: "pdf_points_top_left",
            approvalCoverage: { approval: "absent", header_meta: "absent", labeled_staff: "absent" },
            requiredRegionCoverage: {
              recipient_reference: "absent",
              sender_institution: "absent",
              approval_staff: "absent",
              dispatch_metadata: "absent",
              footer_contact: "absent",
            },
            segments: [
              { segmentId, analysisRevision: publicSession.revision, pageStart: 0, pageEnd: 0, kind: publicSession.boundaryKind, state: publicSession.boundaryResolved ? "user_confirmed" : "review_required", commonOnly: false, source: "qa" },
              { segmentId: `boundary-neighbor-${suffix}`, analysisRevision: publicSession.revision, pageStart: 1, pageEnd: 1, kind: "official_dispatch", state: "confirmed", commonOnly: false, source: "qa" },
            ],
            regions: [],
            occurrences: [],
            reviewItems: [{
              reviewId: `boundary-review-${suffix}`,
              analysisRevision: publicSession.revision,
              kind: "boundary",
              targetId: segmentId,
              pageStart: 0,
              pageEnd: 0,
              status: publicSession.boundaryResolved ? "resolved" : "pending",
              reasonCodes: ["ambiguous_boundary"],
              requiresAcknowledgment: true,
              commonOnly: false,
              provenance: "qa",
            }],
            manualActions: [],
          };
        }
        return {
        manifestVersion: 1, runId: publicSession.runId, originalDocumentHash: "1".repeat(64),
        analysisRevision: publicSession.revision, manifestHash: publicSession.manifestHash,
        profile: publicSession.profile, policyVersion: "official-v1", optionsVersion: "options-v1",
        optionsHash: "2".repeat(64), thresholdVersion: "thresholds-v2",
        thresholdHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
        thresholdArtifact: {
          version: "thresholds-v2",
          contentHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
          autoMaskThreshold: 0.85,
          reviewThreshold: 0.5,
        },
        coordinateSpace: "pdf_points_top_left",
        approvalCoverage: {
          approval: "absent",
          header_meta: "absent",
          labeled_staff: "absent",
        },
        requiredRegionCoverage: {
          recipient_reference: "absent",
          sender_institution: "absent",
          approval_staff: "absent",
          dispatch_metadata: "absent",
          footer_contact: "absent",
        },
        segments: [{ segmentId: "segment-1", analysisRevision: publicSession.revision, pageStart: 0, pageEnd: 0, kind: publicSession.profile, state: "confirmed", commonOnly: false, source: "qa" }],
        regions: [],
        occurrences: [
          { occurrenceId: "occ_000000000000000000000001", segmentId: "segment-1", regionId: null, analysisRevision: publicSession.revision, page: 0, rects: [{ x0: 72, y0: 60, x1: 200, y1: 78 }], tag: "NAME", category: "name", valueHash: "4".repeat(64), expectedTextHash: "5".repeat(64), source: "text_pdf", policy: "mask", proposedAction: publicSession.resolved ? (publicSession.reviewDecision === "exclude" ? "exclude" : "mask") : "review", state: publicSession.resolved ? "confirmed" : "review_required", provenance: "qa" },
          { occurrenceId: "occ_000000000000000000000002", segmentId: "segment-1", regionId: null, analysisRevision: publicSession.revision, page: 0, rects: [{ x0: 280, y0: 60, x1: 408, y1: 82 }], tag: "PHONE", category: "phone", valueHash: "6".repeat(64), expectedTextHash: "7".repeat(64), source: "text_pdf", policy: "mask", proposedAction: "mask", state: "confirmed", provenance: "qa" },
          ...(publicSession.keywordDetected ? [{ occurrenceId: "occ_000000000000000000000003", segmentId: "segment-1", regionId: null, analysisRevision: publicSession.revision, page: 0, rects: [{ x0: 72, y0: 108, x1: 200, y1: 130 }], tag: "KEYWORD", category: "custom_keyword", valueHash: "8".repeat(64), expectedTextHash: "9".repeat(64), source: "custom_keyword", policy: "masking-policy-v1", proposedAction: "mask", state: "confirmed", provenance: "custom_keyword" }] : []),
        ],
        reviewItems: [{ reviewId: "review-1", analysisRevision: publicSession.revision, kind: "name", targetId: "occ_000000000000000000000001", pageStart: 0, pageEnd: 0, status: publicSession.resolved ? "resolved" : "pending", reasonCodes: ["requires_review"], requiresAcknowledgment: false, commonOnly: false, provenance: "qa" }],
          manualActions: publicSession.manualActions.map((action, index) => ({
            ...action,
            actionId: `manual-${publicSession.revision}-${index + 1}`,
            analysisRevision: publicSession.revision,
          })),
        };
      };
      // This is a browser UI-wiring fixture, not a native authority emulator.
      // It validates only the minimal request shapes and UI lifecycle ordering.
      // Packaged-app receipts own authorization, bypass, and replay assertions.
      window.__TAURI_INTERNALS__ = {
        plugins: { path: { sep: "/", delimiter: ":" } },
        transformCallback: () => 1,
        unregisterCallback: () => {},
        invoke: async (cmd, payload = {}) => {
          const auditedPayload = cmd === "run_masking_pipeline"
            ? {
                opts: {
                  output_artifacts: payload.opts?.output_artifacts,
                  deidentification_policy: payload.opts?.deidentification_policy,
                  display_mode: payload.opts?.display_mode,
                  return_text_preview: payload.opts?.return_text_preview,
                  // Non-sensitive boolean rule flags + profile/scope for rule-toggle QA.
                  rrn: payload.opts?.rrn,
                  phone: payload.opts?.phone,
                  business_reg: payload.opts?.business_reg,
                  name: payload.opts?.name,
                  address: payload.opts?.address,
                  place: payload.opts?.place,
                  legal_party: payload.opts?.legal_party,
                  company: payload.opts?.company,
                  court: payload.opts?.court,
                  case_title: payload.opts?.case_title,
                  case_number: payload.opts?.case_number,
                  law_firm: payload.opts?.law_firm,
                  attorney: payload.opts?.attorney,
                  approval_line: payload.opts?.approval_line,
                  region_context: payload.opts?.region_context,
                  doc_meta: payload.opts?.doc_meta,
                  profile: payload.opts?.profile,
                  region_scope: payload.opts?.region_scope,
                },
                }
              : cmd === "analyze_masking_run"
              ? {
                  request: {
                    profile: payload.request?.profile,
                    optionsProfile: payload.request?.options?.profile,
                    hasInputFile: typeof payload.request?.inputFile === "string" && payload.request.inputFile.length > 0,
                    hasCustomKeyword: payload.request?.options?.custom_keywords === "서울시청",
                  },
                }
                : cmd === "plugin:opener|open_path"
                ? {
                    path: {
                      hasValue: typeof payload.path === "string" && payload.path.length > 0,
                      hash: hashPath(payload.path),
                    },
                  }
                : cmd === "resolve_masking_review"
                  ? {
                      request: {
                        reviewId: payload.request?.reviewId,
                        resolutionKind: payload.request?.resolution?.kind,
                        segmentKind: payload.request?.resolution?.segmentKind,
                        pageStart: payload.request?.resolution?.pageStart,
                        pageEnd: payload.request?.resolution?.pageEnd,
                        rectCount: Array.isArray(payload.request?.resolution?.rects) ? payload.request.resolution.rects.length : 0,
                      },
                    }
                  : cmd === "apply_manual_action_v1"
                    ? {
                        request: {
                          analysisRevision: payload.request?.analysisRevision,
                          page: payload.request?.page,
                          rectCount: Array.isArray(payload.request?.rects) ? payload.request.rects.length : 0,
                          mode: payload.request?.mode,
                          sourceKind: payload.request?.sourceKind,
                          linkedOccurrenceId: payload.request?.linkedOccurrenceId,
                          expectedTextHash: payload.request?.expectedTextHash,
                          restoreCapability: payload.request?.restoreCapability ? "present" : null,
                          protectedNeighborRefCount: Array.isArray(payload.request?.protectedNeighborRefs)
                            ? payload.request.protectedNeighborRefs.length
                            : -1,
                        },
                      }
                    : cmd === "finalize_masking_run"
                      ? {
                          request: {
                            warningsConfirmed: payload.request?.warningsConfirmed,
                          },
                        }
                  : {};
          window.__QA_INVOKES__.push({ cmd, payload: auditedPayload });
          if (["pick_input_document", "pick_input_pdf"].includes(cmd)) return fixturePathValue;
          if (cmd === "pick_input_documents") return [fixturePathValue, fixturePathValue.replace(/\.pdf$/i, "-batch.pdf")];
          if (cmd === "default_output_dir_for_document") return outputDirValue;
          if (cmd === "pick_output_dir" || cmd === "get_preview_workdir") return outputDirValue;
          if (cmd === "choose_final_pdf_path") {
            const publicSave = publicSession.analyzed;
            if (publicSave && (
              publicSession.destination
              || publicSession.destinationConsumed
              || typeof payload.defaultFileName !== "string"
              || payload.defaultFileName.length === 0
              || payload.runId !== publicSession.runId
              || payload.analysisRevision !== publicSession.revision
              || payload.manifestHash !== publicSession.manifestHash
            )) throw new Error("QA_PUBLIC_DESTINATION_REQUEST_REJECTED");
            selectedFinalTarget = {
              outputPath: `${outputDirValue}/${String(payload.defaultFileName || "masked")}.pdf`,
              saveToken: (++saveTokenCounter).toString(16).padStart(32, "0"),
            };
            if (publicSave) {
              publicSession.destination = selectedFinalTarget.outputPath;
              publicSession.saveToken = selectedFinalTarget.saveToken;
              publicSession.destinationIdentity = {
                runId: payload.runId,
                analysisRevision: payload.analysisRevision,
                manifestHash: payload.manifestHash,
              };
            }
            return { ...selectedFinalTarget };
          }
          if (cmd === "read_pdf_bytes") return fixtureBytes;
          if (cmd === "read_text_file" && String(payload.path || "").includes("manual_revalidation.safe_report.json")) {
            return JSON.stringify({
              raw_values_saved: false,
              raw_text_returned: false,
              manual_revalidation: { status: "passed", verified: true, output_file_saved_in_report: false, mask_boxes_applied: 1, restore_boxes_applied: 0, skipped_boxes: 0 },
              product_checks: { quality_gate_passed: true, needs_manual_review: false, final_submission_allowed: true },
              document_redaction: { status: "manual_revalidated", missing_targets_count: 0, verification: { verified: true, residual_hits: 0, reason: "수동 보정 PDF 재검증 완료" } },
              review_items: [],
            });
          }
          if (cmd === "read_text_file") return "원문 ALPHA REVIEW TOKEN\n마스킹 [KEYWORD]";
          if (cmd === "apply_manual_action_v1") {
            const request = payload.request;
            if (
              reviewScenario !== "default"
              || !publicSession.analyzed
              || !request
              || request.runId !== publicSession.runId
              || request.analysisRevision !== publicSession.revision
              || request.manifestHash !== publicSession.manifestHash
              || request.page !== 0
              || !["mask", "restore"].includes(request.mode)
              || !Array.isArray(request.rects)
              || request.rects.length !== 1
              || request.targetRegionId !== null
              || !Array.isArray(request.protectedNeighborRefs)
              || request.protectedNeighborRefs.length !== 0
              || (request.mode === "mask" && (
                request.sourceKind !== "scan"
                || request.linkedOccurrenceId !== null
                || request.expectedTextHash !== null
                || request.restoreCapability !== null
              ))
              || request.mode === "restore"
            ) throw new Error("QA_PUBLIC_MANUAL_ACTION_REQUEST_REJECTED");
            if (malformedPublicManualMaskSuccessor) return {};
            publicSession.revision += 1;
            publicSession.manifestHash = "b".repeat(64);
            publicSession.manualActions.push({
              actionId: `manual-${publicSession.revision}`,
              analysisRevision: publicSession.revision,
              page: request.page,
              rects: request.rects,
              protectedNeighborRefs: [],
              mode: request.mode,
              sourceKind: request.sourceKind,
              linkedOccurrenceId: request.linkedOccurrenceId,
              expectedTextHash: request.expectedTextHash,
              restoreAuthorizationHash: request.mode === "restore" ? "a".repeat(64) : null,
            });
            return publicManifest();
          }
          if (cmd === "resolve_masking_review") {
            const request = payload.request;
            if (reviewScenario === "indeterminate_coverage") {
              const nextKind = ["header_meta", "dispatch_metadata"].find((kind) => !publicSession.resolvedCoverageKinds.includes(kind));
              if (
                !publicSession.analyzed
                || !nextKind
                || !request
                || request.runId !== publicSession.runId
                || request.analysisRevision !== publicSession.revision
                || request.manifestHash !== publicSession.manifestHash
                || request.reviewId !== `coverage-review-${nextKind}-r${publicSession.revision}`
                || request.resolution?.kind !== "region_geometry"
                || !Array.isArray(request.resolution.rects)
                || request.resolution.rects.length === 0
              ) throw new Error("QA_INDETERMINATE_COVERAGE_RESOLVE_REQUEST_REJECTED");
              publicSession.resolvedCoverageKinds.push(nextKind);
              publicSession.coverageRectsByKind[nextKind] = request.resolution.rects;
              publicSession.revision += 1;
              publicSession.manifestHash = String.fromCharCode(97 + publicSession.revision).repeat(64);
              return publicManifest();
            }
            if (reviewScenario === "geometry") {
              const geometryManifest = publicManifest();
              const requestedReview = geometryManifest.reviewItems.find((candidate) => candidate.reviewId === request?.reviewId);
              const requestedIndex = requestedReview ? geometryManifest.reviewItems.indexOf(requestedReview) : -1;
              const resolutionRects = Array.isArray(request?.resolution?.rects) ? request.resolution.rects : [];
              const linkedOccurrenceRects = geometryManifest.occurrences.flatMap((occurrence) => occurrence.rects);
              const coversLinkedOccurrences = linkedOccurrenceRects.every((occurrenceRect) => resolutionRects.some((rect) => (
                Math.min(rect.x0, rect.x1) <= Math.min(occurrenceRect.x0, occurrenceRect.x1)
                && Math.min(rect.y0, rect.y1) <= Math.min(occurrenceRect.y0, occurrenceRect.y1)
                && Math.max(rect.x0, rect.x1) >= Math.max(occurrenceRect.x0, occurrenceRect.x1)
                && Math.max(rect.y0, rect.y1) >= Math.max(occurrenceRect.y0, occurrenceRect.y1)
              )));
              if (
                !publicSession.analyzed
                || !request
                || request.runId !== publicSession.runId
                || request.analysisRevision !== publicSession.revision
                || request.manifestHash !== publicSession.manifestHash
                || !requestedReview
                || requestedIndex < 0
                || publicSession.geometryResolvedIndexes.includes(requestedIndex)
                || request.resolution?.kind !== "region_geometry"
                || resolutionRects.length === 0
                || !coversLinkedOccurrences
              ) throw new Error("QA_GEOMETRY_RESOLVE_REQUEST_REJECTED");
              publicSession.geometryResolvedIndexes.push(requestedIndex);
              publicSession.geometryResolvedRectsByIndex[requestedIndex] = resolutionRects;
              publicSession.resolved = publicSession.geometryResolvedIndexes.length === geometryManifest.reviewItems.length;
              publicSession.revision += 1;
              publicSession.manifestHash = String.fromCharCode(97 + publicSession.revision).repeat(64);
              return publicManifest();
            }
            if (reviewScenario === "boundary") {
              if (
                !publicSession.analyzed
                || publicSession.boundaryResolved
                || !request
                || request.runId !== publicSession.runId
                || request.analysisRevision !== publicSession.revision
                || request.manifestHash !== publicSession.manifestHash
                || request.reviewId !== `boundary-review-r${publicSession.revision}`
                || request.resolution?.kind !== "boundary"
                || request.resolution.pageStart !== 0
                || request.resolution.pageEnd !== 0
                || !["internal_review", "official_dispatch", "attachment", "legal"].includes(request.resolution.segmentKind)
              ) throw new Error("QA_BOUNDARY_RESOLVE_REQUEST_REJECTED");
              publicSession.boundaryKind = request.resolution.segmentKind;
              publicSession.boundaryResolved = true;
              publicSession.revision += 1;
              publicSession.manifestHash = "b".repeat(64);
              return publicManifest();
            }
            if (
              !publicSession.analyzed
              || publicSession.resolved
              || !request
              || request.runId !== publicSession.runId
              || request.analysisRevision !== publicSession.revision
              || request.manifestHash !== publicSession.manifestHash
              || request.reviewId !== "review-1"
              || request.resolution?.kind !== "name"
              || !["mask", "exclude"].includes(request.resolution?.action)
            ) throw new Error("QA_PUBLIC_RESOLVE_REQUEST_REJECTED");
            publicSession.resolved = true;
            const decision = request.resolution.action;
            const currentManifest = publicManifest();
            const reviewTarget = currentManifest.reviewItems.find((item) => item.reviewId === request.reviewId)?.targetId;
            publicSession.reviewDecision = decision;
            publicSession.excludedOccurrenceId = decision === "exclude" ? reviewTarget : null;
            // Name decisions remain within the current analysis revision; only boundary or
            // region-geometry reanalysis may mint a successor revision.
            publicSession.manifestHash = "b".repeat(64);
            return publicManifest();
          }
          if (cmd === "finalize_masking_run") {
            const request = payload.request;
            const publicReviewsResolved = reviewScenario === "indeterminate_coverage"
              ? publicSession.resolvedCoverageKinds.length === 2
              : publicSession.resolved;
            if (
              !publicSession.analyzed
              || (!publicReviewsResolved && request?.warningsConfirmed !== true)
              || !publicSession.destination
              || !publicSession.saveToken
              || publicSession.destinationConsumed
              || !publicSession.destinationIdentity
              || !request
              || request.runId !== publicSession.runId
              || request.analysisRevision !== publicSession.revision
              || request.manifestHash !== publicSession.manifestHash
              || request.runId !== publicSession.destinationIdentity.runId
              || request.analysisRevision !== publicSession.destinationIdentity.analysisRevision
              || request.manifestHash !== publicSession.destinationIdentity.manifestHash
              || request.destination !== publicSession.destination
              || request.saveToken !== publicSession.saveToken
              || typeof request.warningsConfirmed !== "boolean"
            ) throw new Error("QA_PUBLIC_FINALIZE_REQUEST_REJECTED");
            publicSession.destinationConsumed = true;
            publicSession.destination = null;
            publicSession.saveToken = null;
            publicSession.destinationIdentity = null;
            // The finalize receipt must reflect the CURRENT session identity the
            // mock issued (latest analyze/resolve manifest) and counts consistent
            // with that manifest, or the frontend's strict result parser rejects
            // response. Recompute from the live manifest instead of constants.
            const finalizedManifest = publicManifest();
            const linkedOccurrenceIds = new Set(finalizedManifest.manualActions
              .map((action) => action.linkedOccurrenceId)
              .filter((value) => typeof value === "string"));
            const finalizedMaskCount = finalizedManifest.occurrences.filter(
              (occurrence) => occurrence.proposedAction === "mask"
                && (occurrence.state === "confirmed" || occurrence.state === "user_confirmed")
                && !linkedOccurrenceIds.has(occurrence.occurrenceId),
            ).length + finalizedManifest.manualActions.filter((action) => action.mode === "mask").length;
            const manualMaskCount = finalizedManifest.manualActions.filter((action) => action.mode === "mask").length;
            const restoreCount = finalizedManifest.manualActions.filter((action) => action.mode === "restore").length;
            const unresolvedReviews = finalizedManifest.reviewItems
              .filter((item) => item.status === "pending")
              .map((item) => {
                const occurrence = finalizedManifest.occurrences.find((candidate) => candidate.occurrenceId === item.targetId);
                const region = finalizedManifest.regions.find((candidate) => candidate.regionId === item.targetId);
                return {
                  kind: item.kind,
                  targetId: item.targetId,
                  category: occurrence?.category ?? region?.kind ?? item.kind,
                  pageStart: item.pageStart,
                  pageEnd: item.pageEnd,
                  reasonCodes: item.reasonCodes,
                };
              });
            return {
              runId: finalizedManifest.runId,
              analysisRevision: finalizedManifest.analysisRevision,
              manifestHash: finalizedManifest.manifestHash,
              finalPath: request.destination,
              finalHash: "6".repeat(64),
              finalHashAttested: true,
              occurrenceCount: finalizedMaskCount,
              appliedMaskCount: finalizedMaskCount,
              manualMaskCount,
              restoreCount,
              effectiveMaskCount: finalizedMaskCount,
              restoreAuthorization: {
                actionIdHash: restoreCount > 0 ? "c".repeat(64) : "0".repeat(64),
                targetOccurrenceIdHash: restoreCount > 0 ? "d".repeat(64) : "0".repeat(64),
                authorizationEvent: "none",
              },
              saveConfirmation: {
                status: unresolvedReviews.length === 0 ? "not_required" : "user_confirmed",
                unresolvedReviews,
              },
              status: "promoted",
            };
          }
          if (cmd === "analyze_masking_run") {
            if (realManifestValue) {
              const rm = JSON.parse(JSON.stringify(realManifestValue));
              rm.runId = publicSession.runId; rm.analysisRevision = publicSession.revision;
              rm.manifestHash = publicSession.manifestHash; rm.manifestVersion = 1;
              publicSession.analyzed = true;
              return rm;
            }
            if (analyzeDelayMs > 0) {
              await new Promise((resolve) => setTimeout(resolve, analyzeDelayMs));
            }
            const request = payload.request;
            if (codedAnalyzeFailure && request?.profile === "official_dispatch") {
              throw {
                code: "MASKING_SESSION_ANALYZER_UNAVAILABLE",
                stage: "spawn",
                detail: "io_kind=NotFound",
              };
            }
            // Batch documents route through the per-input batch branch below
            // (fail-once + input-keyed manifest) regardless of profile. The public
            // gate is single-document: once publicSession.analyzed flips, a later
            // batch analyze (e.g. the retry) would be rejected and never complete.
            const inputFile = String(request?.inputFile || "");
            const isBatchDocument = inputFile.endsWith("-batch.pdf");
            if (!isBatchDocument && ["internal_review", "official_dispatch", "mixed"].includes(request?.profile)) {
              const isKeywordRedetection = publicSession.analyzed
                && request.options?.custom_keywords?.includes("서울시청")
                && !publicSession.keywordDetected;
              if (
                (publicSession.analyzed && !isKeywordRedetection)
                || typeof request.inputFile !== "string"
                || request.inputFile.length === 0
                || !request.options
                || typeof request.options !== "object"
                || request.options.profile !== request.profile
              ) throw new Error("QA_PUBLIC_ANALYZE_REQUEST_REJECTED");
              publicSession.profile = request.profile;
              publicSession.analyzed = true;
              if (isKeywordRedetection) {
                publicSession.keywordDetected = true;
                publicSession.manifestHash = "c".repeat(64);
              }
              return publicManifest();
            }
            if (!inputFile) throw new Error("ANALYZE_REQUEST_REJECTED");
            if (failBatchItemOnce && inputFile.endsWith("-batch.pdf") && !failedBatchInputs.has(inputFile)) {
              failedBatchInputs.add(inputFile);
              throw new Error("QA_BATCH_FAIL_ONCE");
            }
            const inputKey = inputFile.endsWith("-batch.pdf") ? "b" : "a";
            return {
              manifestVersion: 1, runId: `qa-run-${inputKey}`, originalDocumentHash: inputKey.repeat(64), analysisRevision: 1,
              manifestHash: (inputKey === "a" ? "c" : "d").repeat(64), profile: request.profile ?? "mixed",
              policyVersion: "official-v1", optionsVersion: "options-v1", optionsHash: "e".repeat(64),
              thresholdVersion: "thresholds-v2",
              thresholdHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
              thresholdArtifact: {
                version: "thresholds-v2",
                contentHash: "535b7b2b8a3abee3a6dd3c541e15226fd5a01d38aaa4e94663eb61b4a831b59e",
                autoMaskThreshold: 0.85,
                reviewThreshold: 0.5,
              },
              coordinateSpace: "pdf_points_top_left",
              approvalCoverage: {
                approval: "absent",
                header_meta: "absent",
                labeled_staff: "absent",
              },
              requiredRegionCoverage: {
                recipient_reference: "absent",
                sender_institution: "absent",
                approval_staff: "absent",
                dispatch_metadata: "absent",
                footer_contact: "absent",
              },
              segments: [], regions: [], occurrences: [], reviewItems: [], manualActions: [],
            };
          }
          if (cmd === "run_masking_pipeline") {
            const inputFile = String(payload.inputFile || "");
            if (failBatchItemOnce && inputFile.endsWith("-batch.pdf") && !failedBatchInputs.has(inputFile)) {
              failedBatchInputs.add(inputFile);
              throw new Error("QA_BATCH_FAIL_ONCE");
            }
            const exportMaskedText = payload.opts?.output_artifacts === "pdf_masked_txt_safe_report";
            const policy = payload.opts?.deidentification_policy ?? "token";
            const maskedTextByPolicy = {
              token: "연락처 [PHONE]",
              partial: "연락처 010-****-5678",
              pseudonym: "연락처 010-0000-0001",
            };
            const maskedPath = exportMaskedText ? `${outputDirValue}/masked.txt` : "";
            return {
              extracted_path: "",
              masked_path: maskedPath,
              report_path: `${outputDirValue}/safe_report.json`,
              extracted_text: "",
              masked_text: exportMaskedText ? maskedTextByPolicy[policy] ?? maskedTextByPolicy.token : "",
              runtime_manifest: {
                outputs: { preview_pdf_source_file: fixturePathValue, masked_pdf_file: `${outputDirValue}/masked.pdf`, safe_report_path: `${outputDirValue}/safe_report.json`, extracted_file: null, masked_file: maskedPath || null },
                review_items: [
                  { tag: "KEYWORD", display_token: "[KEYWORD]", status: "needs_review", count: 1, page: 0, bbox: { x: 72, y: 60, width: 128, height: 18 } },
                  { tag: "ADDRESS", display_token: "[ADDRESS]", status: "needs_review", count: 1, page: 0, bbox: { x: 72, y: 96, width: 144, height: 18 } },
                ],
              },
              report: {
                extract: { engine_used: payload.opts?.extract_engine ?? "auto" },
                outputs: { preview_pdf_source_file: null, masked_pdf_file: null, safe_report_path: null, extracted_file: null, masked_file: null },
                text_deidentification: { policy },
                product_checks: { quality_gate_passed: true, needs_manual_review: true, final_submission_allowed: true },
                pdf_redaction: { status: "ok", verification: { residual_hits: 0 }, missing_targets_count: 0 },
                review_items: [
                  { tag: "KEYWORD", display_token: "[KEYWORD]", status: "needs_review", count: 1, raw_value_saved: false },
                  { tag: "ADDRESS", display_token: "[ADDRESS]", status: "needs_review", count: 1, raw_value_saved: false },
                ],
                warnings: [],
              },
            };
          }
          if (cmd === "apply_manual_boxes") {
            const boxes = payload.boxes ?? [];
            const restoreApplied = boxes.filter((box) => box.mode === "restore").length;
            // Fixed contract: revalidation only when a restore was applied (risk
            // increases). Mask-only additions leave the base report untouched, so
            // no revalidation report is attached and the save gate stays as-is.
            const requiresRevalidation = restoreApplied > 0;
            const result = {
              status: "applied",
              output_file: `${outputDirValue}/manual_preview.pdf`,
              mask_count: boxes.filter((box) => box.mode === "mask").length,
              restore_count: restoreApplied,
              applied_count: boxes.length,
              mask_boxes_applied: boxes.filter((box) => box.mode === "mask").length,
              unmask_boxes_applied: restoreApplied,
              skipped_boxes: 0,
              warnings: [],
              requires_revalidation: requiresRevalidation,
            };
            if (requiresRevalidation) {
              result.revalidation_report = `${outputDirValue}/manual_preview.manual_revalidation.safe_report.json`;
              result.revalidation_status = "passed";
            }
            return result;
          }
          if (cmd === "finalize_manual_output" || cmd === "finalize_manual_output_to_selected_path") {
            // v4.2.0: Rust finalize_manual_output 은 리포트 내용(잔존·누락·품질·재검증
            // 실패·리포트 부재/파싱 실패)으로는 절대 실패하지 않는다 — report_allows_
            // final_save 거부 술어는 폐기됐다. 최종 저장은 사용자 재량이며, 이 mock 도
            // finalize 거부 로직 없이 항상 성공을 반환한다.
            // v4.1: Rust finalize copy_report 기본값 false, 프론트는 항상
            // copyReport:false 로 호출한다. 안전 리포트는 시스템 임시폴더 하위 내부
            // 세션 디렉터리에만 존재하고 사용자 산출 폴더에는 마스킹 PDF 만 남으므로
            // copied_files 에 safe_report 가 포함되면 안 된다.
            const normalizePath = (value) => {
              const parts = [];
              for (const part of String(value || "").split("/")) {
                if (!part || part === ".") continue;
                if (part === "..") parts.pop();
                else parts.push(part);
              }
              return `/${parts.join("/")}`;
            };
            const previewPdf = normalizePath(payload.previewPdf);
            const registeredOutputDir = normalizePath(outputDirValue);
            const previewIsRegistered = previewPdf.startsWith(`${registeredOutputDir}/`);
            let finalOutput = `${outputDirValue}/final_masked.pdf`;
            if (!previewIsRegistered) {
              throw new Error("SAVE_SOURCE_REJECTED: 저장 원본을 확인할 수 없습니다.");
            }
          if (cmd === "finalize_manual_output_to_selected_path") {
              const confirmedTarget = selectedFinalTarget;
              selectedFinalTarget = null;
              if (
                !confirmedTarget
                || normalizePath(payload.outputPath) !== normalizePath(confirmedTarget.outputPath)
                || String(payload.saveToken || "") !== confirmedTarget.saveToken
              ) {
                throw new Error("SAVE_OUTPUT_PATH_REJECTED: 저장 경로를 확인할 수 없습니다.");
              }
              finalOutput = confirmedTarget.outputPath;
            } else if (normalizePath(payload.outputDir) !== registeredOutputDir) {
              throw new Error("SAVE_OUTPUT_DIR_REJECTED: 저장 폴더를 확인할 수 없습니다.");
            }
            const copiedFiles = payload.copyReport === true ? [`${outputDirValue}/safe_report.json`] : [];
            return {
              final_output_file: finalOutput,
              copied_files: copiedFiles,
            };
          }
          if (cmd === "plugin:opener|open_path" || cmd === "open_mask_canvas_window" || cmd === "create_canvas_launch_token") return "ok";
          throw new Error(`QA_UNKNOWN_IPC:${cmd}`);
        },
      };
    },
    {
      fixturePathValue: options.fixturePath,
      outputDirValue: options.outputDir,
      fixtureBytes: options.pdfBytes,
      failBatchItemOnce: options.failBatchItemOnce === true,
      analyzeDelayMs: options.analyzeDelayMs ?? 0,
      codedAnalyzeFailure: options.codedAnalyzeFailure === true,
      malformedPublicManualMaskSuccessor: options.malformedPublicManualMaskSuccessor === true,
      reviewScenario: options.reviewScenario ?? "default",
      realManifestValue: options.realManifest ?? null,
      geometryManifestValue: geometryReviewManifestForQa(),
    },
  );
}

function buildNonAuthoritativeArtifact() {
  return {
    schemaVersion: 1,
    status: "non_authoritative",
    contract: "ui-mock-only",
    reasonCode: "UI_MOCK_NOT_NATIVE_EVIDENCE",
    evidenceAuthority: "none",
  };
}

function parseCliArgs(argv) {
  const args = { contract: "", output: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag !== "--contract" && flag !== "--output") throw new Error("QA_CLI_UNKNOWN_ARGUMENT");
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error("QA_CLI_MISSING_VALUE");
    if (flag === "--contract") {
      if (args.contract) throw new Error("QA_CLI_DUPLICATE_ARGUMENT");
      args.contract = value;
    } else {
      if (args.output) throw new Error("QA_CLI_DUPLICATE_ARGUMENT");
      args.output = value;
    }
    index += 1;
  }
  if (!args.contract || !args.output) throw new Error("QA_CLI_REQUIRED_ARGUMENT");
  return args;
}

function assertOwnedArtifactPath(outputPath) {
  const artifactRoot = path.join(repoRoot, "artifacts");
  const absoluteOutputPath = path.resolve(repoRoot, outputPath);
  const relative = path.relative(artifactRoot, absoluteOutputPath);
  if (
    !relative
    || relative.startsWith("..")
    || path.isAbsolute(relative)
    || path.dirname(relative) !== "."
  ) {
    throw new Error("QA_ARTIFACT_PATH_REJECTED");
  }

  try {
    fs.mkdirSync(artifactRoot, { mode: 0o700 });
  } catch (error) {
    if (error && typeof error === "object" && error.code === "EEXIST") {
      throw new Error("QA_ARTIFACT_ROOT_REJECTED");
    }
    throw error;
  }

  const rootStat = fs.lstatSync(artifactRoot);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink() || (rootStat.mode & 0o077) !== 0) {
    throw new Error("QA_ARTIFACT_ROOT_REJECTED");
  }
  return absoluteOutputPath;
}

function writeArtifact(outputPath, payload) {
  const absoluteOutputPath = assertOwnedArtifactPath(outputPath);
  const descriptor = fs.openSync(
    absoluteOutputPath,
    fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW,
    0o600,
  );
  try {
    fs.writeFileSync(descriptor, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
    fs.fsyncSync(descriptor);
  } finally {
    fs.closeSync(descriptor);
  }
}

async function main() {
  const args = parseCliArgs(process.argv.slice(2));
  if (args.contract !== "safe-report") throw new Error("QA_CONTRACT_REJECTED");
  const artifact = buildNonAuthoritativeArtifact();
  writeArtifact(args.output, artifact);
  process.stdout.write(`${JSON.stringify(artifact)}\n`);
  process.exitCode = 1;
}

const entryPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (entryPath === fileURLToPath(import.meta.url)) {
  try {
    await main();
  } catch (error) {
    const message = error instanceof Error ? error.message : "";
    const filesystemCode = error && typeof error === "object" && "code" in error ? error.code : "";
    const reasonCode = /^QA_[A-Z_]+$/.test(message)
      ? message
      : filesystemCode === "EEXIST"
        ? "QA_ARTIFACT_EXISTS"
        : filesystemCode === "EACCES" || filesystemCode === "EPERM"
          ? "QA_ARTIFACT_WRITE_REJECTED"
          : "QA_MOCK_INTERNAL_ERROR";
    process.stdout.write(`${JSON.stringify({ schemaVersion: 1, status: "failed", reasonCode })}\n`);
    process.stderr.write(`[qa-tauri-mock] ${reasonCode}\n`);
    process.exitCode = 1;
  }
}
