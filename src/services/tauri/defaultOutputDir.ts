import {
  parseDefaultOutputDirForDocumentResult,
  serializeDefaultOutputDirForDocumentPayload,
} from "./contracts";

export type DefaultOutputDirSelectionInput = {
  readonly currentOutputDir: string;
  readonly selectedDocumentPaths: readonly string[];
};

export type DefaultOutputDirSelectionPlan =
  | {
      readonly kind: "preserve";
      readonly outputDir: string;
      readonly reason: "existing_output_dir" | "empty_selection" | "multiple_selection";
    }
  | {
      readonly kind: "resolve";
      readonly outputDir: string;
      readonly documentPath: string;
      readonly reason: "single_selection";
    };

export type TauriInvokeLike = <T>(command: string, payload?: Record<string, unknown>) => Promise<T>;

export function planDefaultOutputDirSelection(input: DefaultOutputDirSelectionInput): DefaultOutputDirSelectionPlan {
  const currentOutputDir = input.currentOutputDir.trim();
  if (currentOutputDir) {
    return { kind: "preserve", outputDir: currentOutputDir, reason: "existing_output_dir" };
  }
  if (input.selectedDocumentPaths.length === 0) {
    return { kind: "preserve", outputDir: "", reason: "empty_selection" };
  }
  if (input.selectedDocumentPaths.length !== 1) {
    return { kind: "preserve", outputDir: "", reason: "multiple_selection" };
  }
  return {
    kind: "resolve",
    outputDir: "",
    documentPath: input.selectedDocumentPaths[0] || "",
    reason: "single_selection",
  };
}

export async function defaultOutputDirForSelection(
  invokeCommand: TauriInvokeLike,
  input: DefaultOutputDirSelectionInput,
): Promise<string> {
  const plan = planDefaultOutputDirSelection(input);
  if (plan.kind !== "resolve") return plan.outputDir;
  const payload = serializeDefaultOutputDirForDocumentPayload({ documentPath: plan.documentPath });
  if (!payload.ok) return "";
  const resolved = await invokeCommand<unknown>("default_output_dir_for_document", payload.value);
  const parsed = parseDefaultOutputDirForDocumentResult(resolved);
  return parsed.ok ? parsed.value.outputDir.trim() : "";
}
