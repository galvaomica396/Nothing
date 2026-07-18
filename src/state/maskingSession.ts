import { err, isRecord, ok } from "./contracts";
import type { ContractResult, DisplayMode } from "./contracts";

export type BaseMaskingProgress = {
  readonly status: "idle" | "running" | "complete" | "failed";
  readonly percent: number;
  readonly displayMode: DisplayMode;
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
};

export type SafeReportRedaction = {
  readonly status?: string;
  readonly missing_targets_count?: number;
  readonly targets_requested?: number;
  readonly targets_hit?: number;
  readonly annotations_added?: number;
  readonly rects_from_word_fallback?: number;
  readonly excluded_hits?: number;
  readonly excluded_regions?: number;
  readonly reason_code?: string | null;
  readonly verification?: {
    readonly verified?: boolean;
    readonly residual_hits?: number;
    readonly reason?: string;
  };
};

export type SafeReport = {
  readonly product_checks: SafeReportProductChecks;
  readonly document_redaction?: SafeReportRedaction;
  readonly pdf_redaction?: SafeReportRedaction;
  readonly review_items: readonly unknown[];
  readonly warnings?: readonly unknown[];
  readonly [key: string]: unknown;
};

export function parseSafeReport(value: unknown): ContractResult<SafeReport> {
  if (!isRecord(value)) return err("missing_report", "safeReport");
  if (!isRecord(value["product_checks"])) return err("missing_product_checks", "safeReport.product_checks");
  if (!Array.isArray(value["review_items"])) return err("missing_review_items", "safeReport.review_items");
  const redaction = isRecord(value["document_redaction"]) ? value["document_redaction"] : value["pdf_redaction"];
  if (!isRecord(redaction)) return err("missing_redaction", "safeReport.document_redaction");
  return ok(value as unknown as SafeReport);
}
