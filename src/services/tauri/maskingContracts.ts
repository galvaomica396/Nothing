import type { DeidentificationMode, MaskingOutputArtifacts } from "../../settingsState";
import type { DisplayMode } from "../../state/contracts";

export type MaskingReportPayload = {
  readonly outputs?: Record<string, unknown>;
  readonly extract?: { readonly engine_used?: string };
  readonly [key: string]: unknown;
};

export type MaskingOptions = {
  readonly rrn: boolean;
  readonly phone: boolean;
  readonly business_reg: boolean;
  readonly name: boolean;
  readonly address: boolean;
  readonly place: boolean;
  readonly legal_party: boolean;
  readonly company: boolean;
  readonly court: boolean;
  readonly case_title: boolean;
  readonly case_number: boolean;
  readonly law_firm: boolean;
  readonly attorney: boolean;
  readonly approval_line: boolean;
  readonly region_context: boolean;
  readonly doc_meta: boolean;
  readonly pdf_redaction: boolean;
  readonly custom_keywords: string;
  readonly extract_engine: string;
  readonly profile: string;
  readonly output_artifacts: MaskingOutputArtifacts;
  readonly display_mode: DisplayMode;
  readonly deidentification_policy: DeidentificationMode;
  readonly region_scope: string;
  readonly custom_regions: string;
  readonly return_text_preview: boolean;
};

export type MaskingResult = {
  readonly extracted_path?: string;
  readonly masked_path?: string;
  readonly report_path?: string;
  readonly report: MaskingReportPayload;
  readonly runtime_manifest?: { readonly outputs?: Record<string, unknown>; readonly [key: string]: unknown };
  readonly extracted_text: string;
  readonly masked_text: string;
};

export type FinalizeResult = {
  readonly final_output_file: string;
  readonly copied_files?: readonly string[];
};
