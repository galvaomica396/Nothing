import { err, isRecord, ok, requiredString } from "../../state/contracts";
import type { ContractResult } from "../../state/contracts";

export type DefaultOutputDirForDocumentPayload = {
  readonly documentPath: string;
};

export type DefaultOutputDirForDocumentResult = {
  readonly status: "ok";
  readonly outputDir: string;
};

export function parseDefaultOutputDirForDocumentPayload(value: unknown): ContractResult<DefaultOutputDirForDocumentPayload> {
  if (!isRecord(value)) return err("missing_document", "payload");
  const documentPath = requiredString(value, "documentPath", "missing_document");
  if (!documentPath.ok) return documentPath;
  return ok({ documentPath: documentPath.value });
}

export function serializeDefaultOutputDirForDocumentPayload(
  value: unknown,
): ContractResult<DefaultOutputDirForDocumentPayload> {
  return parseDefaultOutputDirForDocumentPayload(value);
}

export function parseDefaultOutputDirForDocumentResult(value: unknown): ContractResult<DefaultOutputDirForDocumentResult> {
  if (typeof value === "string" && value.trim()) {
    return ok({ status: "ok", outputDir: value.trim() });
  }
  if (!isRecord(value) || value["status"] !== "ok") return err("invalid_status", "status");
  const outputDir = requiredString(value, "outputDir", "missing_outputDir");
  if (!outputDir.ok) return outputDir;
  return ok({ status: "ok", outputDir: outputDir.value });
}
