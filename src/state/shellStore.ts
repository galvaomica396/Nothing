import { useSyncExternalStore } from "react";
import type { SettingsTheme } from "../settingsState";

const SHELL_SCREENS = ["desk", "documents", "review-queue", "storage", "settings", "masking-settings"] as const;
const SHELL_MODAL_IDS = [
  "keyword-dialog",
  "new-document-dialog",
  "final-save-dialog",
  "masking-progress-dialog",
  "finalization-success-dialog",
] as const;

export type ShellScreen = (typeof SHELL_SCREENS)[number];
export type ShellPanel = Exclude<ShellScreen, "review-queue">;
export type ShellModalId = (typeof SHELL_MODAL_IDS)[number];

export type ShellState = {
  readonly activeScreen: ShellScreen;
  readonly activePanel: ShellPanel;
  readonly inspectorCollapsed: boolean;
  readonly reviewQueueActivationTick: number;
  readonly theme: SettingsTheme;
  readonly modalVisibility: Readonly<Record<ShellModalId, boolean>>;
};

type ShellListener = () => void;

const hiddenModals: Readonly<Record<ShellModalId, boolean>> = {
  "keyword-dialog": false,
  "new-document-dialog": false,
  "final-save-dialog": false,
  "masking-progress-dialog": false,
  "finalization-success-dialog": false,
};

let shellState: ShellState = {
  activeScreen: "desk",
  activePanel: "desk",
  inspectorCollapsed: false,
  reviewQueueActivationTick: 0,
  theme: "light",
  modalVisibility: hiddenModals,
};

const listeners = new Set<ShellListener>();

function panelForScreen(screen: ShellScreen): ShellPanel {
  return screen === "review-queue" ? "documents" : screen;
}

function publish(nextState: ShellState): void {
  if (nextState === shellState) return;
  shellState = nextState;
  for (const listener of listeners) listener();
}

function subscribe(listener: ShellListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): ShellState {
  return shellState;
}

export function useShellState(): ShellState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function shellStateSnapshot(): ShellState {
  return getSnapshot();
}

export function activateScreen(activeScreen: ShellScreen): void {
  const activePanel = panelForScreen(activeScreen);
  const isReviewQueueActivation = activeScreen === "review-queue";
  if (!isReviewQueueActivation && shellState.activeScreen === activeScreen && shellState.activePanel === activePanel) return;
  publish({
    ...shellState,
    activeScreen,
    activePanel,
    reviewQueueActivationTick: isReviewQueueActivation
      ? shellState.reviewQueueActivationTick + 1
      : shellState.reviewQueueActivationTick,
  });
}

export function setActiveScreen(activeScreen: ShellScreen): void {
  activateScreen(activeScreen);
}

export function setInspectorCollapsed(inspectorCollapsed: boolean): void {
  if (shellState.inspectorCollapsed === inspectorCollapsed) return;
  publish({ ...shellState, inspectorCollapsed });
}

export function setShellTheme(theme: SettingsTheme): void {
  if (shellState.theme === theme) return;
  publish({ ...shellState, theme });
}

export function setModalVisibility(id: ShellModalId, visible: boolean): void {
  if (shellState.modalVisibility[id] === visible) return;
  publish({
    ...shellState,
    modalVisibility: { ...shellState.modalVisibility, [id]: visible },
  });
}

export function isShellModalId(id: string): id is ShellModalId {
  return SHELL_MODAL_IDS.some((modalId) => modalId === id);
}
