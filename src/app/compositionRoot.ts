import * as pdfjsLib from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { bindApplicationDom } from "./domBindings";
import { createApplicationController } from "./applicationController";
import { wireDocumentEvents } from "./wiring/documentWiring";
import { installQaDrive } from "./qaDrive";
import { clearWorkspaceController, registerWorkspaceController } from "../features/canvas-workbench/workspaceRuntime";
import { clearApplicationController, registerApplicationController } from "../state/appControllerRuntime";

export type { ApplicationSessionState, DragRejection } from "./applicationController";

export function startApplicationComposition(): () => void;
export function startApplicationComposition(): void;
export function startApplicationComposition(): () => void {
  (pdfjsLib as any).GlobalWorkerOptions.workerSrc = workerSrc;

  const bindings = bindApplicationDom();
  const controller = createApplicationController(bindings);
  registerApplicationController(controller);
  registerWorkspaceController(controller);

  const cleanups = [
    wireDocumentEvents(bindings, controller),
  ];

  controller.initialize();
  installQaDrive();

  let disposed = false;
  return () => {
    if (disposed) return;
    disposed = true;
    for (const cleanup of cleanups) cleanup();
    controller.dispose();
    clearApplicationController(controller);
    clearWorkspaceController(controller);
  };
}
