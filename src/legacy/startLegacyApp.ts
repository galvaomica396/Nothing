import * as pdfjsLib from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { bindLegacyDom } from "./domBindings";
import { createLegacyAppController } from "./legacyAppController";
import { wireCanvasEvents } from "./wiring/canvasWiring";
import { wireDocumentEvents } from "./wiring/documentWiring";
import { wireKeywordEvents } from "./wiring/keywordWiring";
import { wireSettingsEvents } from "./wiring/settingsWiring";

export type { LegacySessionState } from "./legacyAppController";

export function startLegacyApp(): void {
  (pdfjsLib as any).GlobalWorkerOptions.workerSrc = workerSrc;

  const bindings = bindLegacyDom();
  const controller = createLegacyAppController(bindings);

  wireDocumentEvents(bindings, controller);
  wireSettingsEvents(bindings, controller);
  wireKeywordEvents(bindings, controller);
  wireCanvasEvents(bindings, controller);

  controller.initialize();
}
