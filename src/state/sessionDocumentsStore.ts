import { useSyncExternalStore } from "react";

export const deskProfileOptions = [
  { value: "internal_review", label: "내부검토" },
  { value: "official_dispatch", label: "대내외 시행문서" },
  { value: "mixed", label: "혼합 문서" },
  { value: "legal", label: "법률 문서" },
] as const;

export type DeskProfile = (typeof deskProfileOptions)[number]["value"];

export type SessionDocumentSurfaceItem = {
  readonly path: string;
  readonly status: string;
  readonly detectedCount: number;
  readonly maskCount: number | null;
  readonly pendingCount: number;
  readonly profileLabel: string;
};

export type SessionSaveSurfaceItem = {
  readonly id: string;
  readonly path: string;
  readonly maskCount: number | null;
  readonly savedAt: string;
};

export type SessionDocumentsState = {
  readonly documents: readonly SessionDocumentSurfaceItem[];
  readonly saves: readonly SessionSaveSurfaceItem[];
  readonly profile: DeskProfile;
  readonly deskQuery: string;
  readonly storageQuery: string;
};

type SessionDocumentsSurfaceInput = Pick<SessionDocumentsState, "documents" | "saves" | "profile">;

const emptyState: SessionDocumentsState = {
  documents: [],
  saves: [],
  profile: "mixed",
  deskQuery: "",
  storageQuery: "",
};

let state = emptyState;
const listeners = new Set<() => void>();

function publish(next: SessionDocumentsState): void {
  state = next;
  for (const listener of listeners) listener();
}

export function publishSessionDocuments(input: SessionDocumentsSurfaceInput): void {
  publish({ ...state, documents: [...input.documents], saves: [...input.saves], profile: input.profile });
}

export function sessionDocumentsState(): SessionDocumentsState {
  return state;
}

export function isDeskProfile(value: string): value is DeskProfile {
  return deskProfileOptions.some((option) => option.value === value);
}

export function deskProfileFromValue(value: string): DeskProfile {
  return isDeskProfile(value) ? value : "mixed";
}

export function deskProfileLabel(value: string): string {
  return deskProfileOptions.find((option) => option.value === value)?.label ?? "PDF";
}

export function setDeskSearchQuery(deskQuery: string): void {
  if (state.deskQuery === deskQuery) return;
  publish({ ...state, deskQuery });
}

export function setStorageSearchQuery(storageQuery: string): void {
  if (state.storageQuery === storageQuery) return;
  publish({ ...state, storageQuery });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot(): SessionDocumentsState {
  return state;
}

export function useSessionDocumentsState(): SessionDocumentsState {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
