import type { ApplicationController } from "../app/applicationController";

let controller: ApplicationController | null = null;

export function registerApplicationController(next: ApplicationController): void {
  controller = next;
}

export function clearApplicationController(next: ApplicationController): void {
  if (controller === next) controller = null;
}

export function applicationController(): ApplicationController | null {
  return controller;
}
