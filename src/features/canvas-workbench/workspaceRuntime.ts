import type { ApplicationController } from "../../app/applicationController";

export type WorkspaceCanvasMount = {
  readonly origCanvas: HTMLCanvasElement;
  readonly resultCanvas: HTMLCanvasElement;
  readonly overlay: HTMLCanvasElement;
  readonly origWrap: HTMLDivElement;
  readonly resultWrap: HTMLDivElement;
  readonly pdfCompareView: HTMLDivElement;
  readonly textCompareView: HTMLDivElement;
  readonly extractedTextView: HTMLPreElement;
  readonly maskedTextView: HTMLPreElement;
};

let controller: ApplicationController | null = null;
let canvasMount: WorkspaceCanvasMount | null = null;

export function registerWorkspaceController(next: ApplicationController): void {
  controller = next;
}

export function clearWorkspaceController(next: ApplicationController): void {
  if (controller === next) controller = null;
}

export function workspaceController(): ApplicationController | null {
  return controller;
}

export function registerWorkspaceCanvasMount(next: WorkspaceCanvasMount): void {
  canvasMount = next;
}

export function clearWorkspaceCanvasMount(next: WorkspaceCanvasMount): void {
  if (canvasMount === next) canvasMount = null;
}

export function workspaceCanvasMount(): WorkspaceCanvasMount {
  if (canvasMount === null) throw new Error("Workspace canvas mount is unavailable.");
  return canvasMount;
}
