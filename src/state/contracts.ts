export type DisplayMode = "black" | "label_en" | "label_ko" | "pseudonym";

export type ValidationIssueCode =
  | "schemaVersion_invalid"
  | "missing_id"
  | "missing_document"
  | "missing_outputDir"
  | "missing_baseMasking"
  | "missing_review"
  | "missing_manual"
  | "missing_save"
  | "missing_candidates"
  | "missing_boxes"
  | "missing_page"
  | "missing_bbox"
  | "missing_rect"
  | "missing_tag"
  | "missing_displayToken"
  | "missing_status"
  | "missing_safeContext"
  | "missing_confidence"
  | "missing_displayMode"
  | "missing_template_security"
  | "template_authority_invalid"
  | "forbidden_path_field"
  | "forbidden_raw_text_field"
  | "forbidden_image_field"
  | "rect_out_of_range"
  | "invalid_number"
  | "invalid_status"
  | "invalid_mode"
  | "invalid_kind"
  | "missing_report"
  | "missing_product_checks"
  | "missing_review_items"
  | "missing_redaction";

export type ValidationIssue = {
  readonly code: ValidationIssueCode;
  readonly field: string;
};

export type ContractResult<T> =
  | { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly errors: readonly ValidationIssue[] };

export type BBox = {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
};

export type NormalizedRect = BBox;

const PATH_FIELD_NAMES = new Set(["path", "paths", "sourcepath", "sourcepdfpath", "pdfpath", "filepath", "targetpath", "documentpath", "fullpath", "absolutepath"]);
const RAW_TEXT_FIELD_NAMES = new Set(["rawtext", "raw_text", "ocrtext", "ocr_text", "textcontent", "privatevalue"]);
const IMAGE_FIELD_NAMES = new Set(["image", "images", "pageimage", "pageimages", "thumbnail", "previewimage"]);

export function ok<T>(value: T): ContractResult<T> {
  return { ok: true, value };
}

export function err(code: ValidationIssueCode, field: string): ContractResult<never> {
  return { ok: false, errors: [{ code, field }] };
}

export function prefixContractResult<T>(result: ContractResult<T>, prefix: string): ContractResult<T> {
  if (result.ok) return result;
  return {
    ok: false,
    errors: result.errors.map((issue) => ({
      code: issue.code,
      field: issue.field ? `${prefix}.${issue.field}` : prefix,
    })),
  };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function requiredString(source: Record<string, unknown>, key: string, code: ValidationIssueCode): ContractResult<string> {
  const value = source[key];
  return typeof value === "string" && value.trim() ? ok(value) : err(code, key);
}

export function requiredNumber(source: Record<string, unknown>, key: string, code: ValidationIssueCode): ContractResult<number> {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? ok(value) : err(code, key);
}

export function optionalString(source: Record<string, unknown>, key: string): string | undefined {
  const value = source[key];
  return typeof value === "string" && value.trim() ? value : undefined;
}

export function parseDisplayMode(value: unknown): ContractResult<DisplayMode> {
  if (value === "black" || value === "label_en" || value === "label_ko" || value === "pseudonym") {
    return ok(value);
  }
  return err("missing_displayMode", "displayMode");
}

export function parseBBox(value: unknown, field = "bbox"): ContractResult<BBox> {
  if (!isRecord(value)) return err("missing_bbox", field);
  const x = requiredNumber(value, "x", "invalid_number");
  const y = requiredNumber(value, "y", "invalid_number");
  const width = requiredNumber(value, "width", "invalid_number");
  const height = requiredNumber(value, "height", "invalid_number");
  if (!x.ok) return x;
  if (!y.ok) return y;
  if (!width.ok) return width;
  if (!height.ok) return height;
  if (width.value <= 0 || height.value <= 0) return err("invalid_number", field);
  return ok({ x: x.value, y: y.value, width: width.value, height: height.value });
}

export function parseNormalizedRect(value: unknown): ContractResult<NormalizedRect> {
  const rect = parseBBox(value, "rect");
  if (!rect.ok) return err("missing_rect", "rect");
  const { x, y, width, height } = rect.value;
  if (x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 1 || y + height > 1) {
    return err("rect_out_of_range", "rect");
  }
  return rect;
}

export function forbiddenTemplateIssue(value: unknown, path = "$"): ValidationIssue | null {
  if (Array.isArray(value)) {
    for (const item of value) {
      const issue = forbiddenTemplateIssue(item, path);
      if (issue) return issue;
    }
    return null;
  }
  if (!isRecord(value)) return null;
  for (const [key, nested] of Object.entries(value)) {
    const normalized = key.toLowerCase();
    if (PATH_FIELD_NAMES.has(normalized)) return { code: "forbidden_path_field", field: `${path}.${key}` };
    if (RAW_TEXT_FIELD_NAMES.has(normalized)) return { code: "forbidden_raw_text_field", field: `${path}.${key}` };
    if (IMAGE_FIELD_NAMES.has(normalized)) return { code: "forbidden_image_field", field: `${path}.${key}` };
    const nestedIssue = forbiddenTemplateIssue(nested, `${path}.${key}`);
    if (nestedIssue) return nestedIssue;
  }
  return null;
}

export function isDisplayMode(value: unknown): value is DisplayMode {
  return value === "black" || value === "label_en" || value === "label_ko" || value === "pseudonym";
}
