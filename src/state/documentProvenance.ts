import type { DocumentKind } from "../batchQueue";

export interface OriginalDocument {
  readonly path: string;
  readonly kind: DocumentKind;
}

export interface GeneratedMaskedPreview {
  readonly path: string;
  readonly artifactPath: string;
}

export interface ManualAdjustedPreview {
  readonly path: string;
}

export interface FinalOutput {
  readonly path: string;
}

export interface PostSaveContinuation {
  readonly state: "ready" | "unavailable";
  readonly path: string;
}

/**
 * Document roles are deliberately separate: derived resets never change the
 * original, generated paths never alias the original, and manual is committed
 * only after its PDF loads. A final snapshot becomes a continuation baseline
 * only after load verification; later manual previews supersede that baseline
 * without mutating or forgetting the immutable final. `artifactPath` preserves
 * the backend-reported artifact hint independently from the load-verified
 * generated preview.
 */
export interface DocumentProvenance {
  readonly original: OriginalDocument;
  readonly generated: GeneratedMaskedPreview;
  readonly manual: ManualAdjustedPreview;
  readonly final: FinalOutput;
  readonly continuation: PostSaveContinuation | null;
}

const EMPTY_GENERATED: GeneratedMaskedPreview = { path: "", artifactPath: "" };
const EMPTY_MANUAL: ManualAdjustedPreview = { path: "" };
const EMPTY_FINAL: FinalOutput = { path: "" };
const EMPTY_CONTINUATION: PostSaveContinuation | null = null;

export function emptyDocumentProvenance(): DocumentProvenance {
  return {
    original: { path: "", kind: "" },
    generated: EMPTY_GENERATED,
    manual: EMPTY_MANUAL,
    final: EMPTY_FINAL,
    continuation: EMPTY_CONTINUATION,
  };
}

export function selectOriginalDocument(
  provenance: DocumentProvenance,
  path: string,
  kind: DocumentKind,
): DocumentProvenance {
  return resetDerivedProvenance({
    ...provenance,
    original: { path, kind },
  });
}

export function resetDerivedProvenance(provenance: DocumentProvenance): DocumentProvenance {
  return {
    original: provenance.original,
    generated: EMPTY_GENERATED,
    manual: EMPTY_MANUAL,
    final: EMPTY_FINAL,
    continuation: EMPTY_CONTINUATION,
  };
}

export function adoptGeneratedPreview(
  provenance: DocumentProvenance,
  path: string,
  artifactPath = "",
): DocumentProvenance {
  const originalPath = provenance.original.path;
  return {
    original: provenance.original,
    generated: {
      path: path && path !== originalPath ? path : "",
      artifactPath: artifactPath && artifactPath !== originalPath ? artifactPath : "",
    },
    manual: EMPTY_MANUAL,
    final: EMPTY_FINAL,
    continuation: EMPTY_CONTINUATION,
  };
}

export function adoptManualPreview(provenance: DocumentProvenance, path: string): DocumentProvenance {
  return {
    ...provenance,
    manual: { path },
  };
}

export function adoptLoadVerifiedFinalContinuation(
  provenance: DocumentProvenance,
  path: string,
): DocumentProvenance {
  return {
    original: provenance.original,
    generated: EMPTY_GENERATED,
    manual: EMPTY_MANUAL,
    final: { path },
    continuation: { state: "ready", path },
  };
}

export function adoptUnavailableFinalContinuation(
  provenance: DocumentProvenance,
  path: string,
): DocumentProvenance {
  return {
    original: provenance.original,
    generated: EMPTY_GENERATED,
    manual: EMPTY_MANUAL,
    final: { path },
    continuation: { state: "unavailable", path },
  };
}

export function resultSourcePath(provenance: DocumentProvenance): string {
  if (provenance.continuation?.state === "unavailable") return "";
  return provenance.manual.path
    || provenance.generated.path
    || provenance.continuation?.path
    || "";
}

export function finalSaveSourcePath(provenance: DocumentProvenance): string {
  return resultSourcePath(provenance);
}

export function canvasWindowTargetPath(provenance: DocumentProvenance): string {
  if (provenance.original.kind !== "pdf") return "";
  return resultSourcePath(provenance)
    || provenance.continuation?.path
    || provenance.original.path;
}

export function canvasWindowTargetCandidates(provenance: DocumentProvenance): string[] {
  if (provenance.original.kind !== "pdf") return [];
  if (provenance.continuation) {
    const currentPath = resultSourcePath(provenance) || provenance.continuation.path;
    return currentPath ? [currentPath] : [];
  }
  return Array.from(
    new Set(
      [provenance.manual.path, provenance.generated.path, provenance.original.path].filter(Boolean),
    ),
  );
}

export function latestGeneratedPath(provenance: DocumentProvenance): string {
  return provenance.generated.path || provenance.generated.artifactPath;
}

export function hasMaskedArtifact(provenance: DocumentProvenance): boolean {
  return Boolean(
    (provenance.continuation?.state === "ready" && provenance.continuation.path)
    || provenance.manual.path
    || provenance.generated.path
    || provenance.generated.artifactPath,
  );
}

export function statusSourcePath(provenance: DocumentProvenance): string {
  return resultSourcePath(provenance)
    || provenance.continuation?.path
    || provenance.generated.artifactPath
    || provenance.original.path;
}
