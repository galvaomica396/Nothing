import { useSyncExternalStore } from "react";
import { DEFAULT_SETTINGS, loadSettings, maskingOutputArtifacts, mergeSettings, saveSettings } from "../settingsState";
import type { AppSettings, LoadedSettings, SavedSettings, SettingsApplication } from "../settingsState";
import { BUNDLED_PUBLIC_THRESHOLD } from "../publicThreshold";
import type { MaskingOptions } from "../services/tauri/maskingContracts";
import { setShellTheme } from "./shellStore";

export const MASKING_RULE_IDS = [
  "rrn", "phone", "business_reg", "name", "address", "place", "legal_party", "company",
  "court", "case_title", "case_number", "law_firm", "attorney", "approval_line",
  "region_context", "doc_meta",
] as const;

export type MaskingRuleId = (typeof MASKING_RULE_IDS)[number];
export type MaskingRules = Readonly<Record<MaskingRuleId, boolean>>;

export type SettingsStoreState = {
  readonly settings: AppSettings;
  readonly rules: MaskingRules;
  readonly draft: AppSettings | null;
};

type SettingsListener = () => void;

const enabledRules: MaskingRules = {
  rrn: true, phone: true, business_reg: true, name: true, address: true, place: true,
  legal_party: true, company: true, court: true, case_title: true, case_number: true,
  law_firm: true, attorney: true, approval_line: true, region_context: true, doc_meta: true,
};

let state: SettingsStoreState = { settings: { ...DEFAULT_SETTINGS }, rules: enabledRules, draft: null };
const listeners = new Set<SettingsListener>();

function rulesForProfile(settings: AppSettings, rules: Partial<MaskingRules>): MaskingRules {
  const completeRules: MaskingRules = {
    rrn: rules.rrn ?? true,
    phone: rules.phone ?? true,
    business_reg: rules.business_reg ?? true,
    name: rules.name ?? true,
    address: rules.address ?? true,
    place: rules.place ?? true,
    legal_party: rules.legal_party ?? true,
    company: rules.company ?? true,
    court: rules.court ?? true,
    case_title: rules.case_title ?? true,
    case_number: rules.case_number ?? true,
    law_firm: rules.law_firm ?? true,
    attorney: rules.attorney ?? true,
    approval_line: rules.approval_line ?? true,
    region_context: rules.region_context ?? true,
    doc_meta: rules.doc_meta ?? true,
  };
  if (settings.profile === "legal") {
    return { ...completeRules, approval_line: false, region_context: false, doc_meta: false };
  }
  return completeRules;
}

function publish(nextState: SettingsStoreState): void {
  if (nextState === state) return;
  state = nextState;
  setShellTheme(nextState.settings.theme);
  for (const listener of listeners) listener();
}

function subscribe(listener: SettingsListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot(): SettingsStoreState {
  return state;
}

export function useSettingsState(): SettingsStoreState {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

export function settingsSnapshot(): SettingsStoreState {
  return state;
}

export function currentSettings(): AppSettings {
  return state.settings;
}

export function getRule(rule: MaskingRuleId): boolean {
  return rulesForProfile(state.settings, state.rules)[rule];
}

export function currentMaskingOptions(): MaskingOptions {
  const settings = state.settings;
  return {
    ...rulesForProfile(settings, state.rules),
    email: true,
    pdf_redaction: settings.pdfRedaction,
    custom_keywords: settings.customKeywords.trim(),
    extract_engine: settings.engine,
    profile: settings.profile,
    output_artifacts: maskingOutputArtifacts(settings.exportMaskedText),
    display_mode: settings.displayMode,
    deidentification_policy: settings.deidentificationMode,
    region_scope: settings.regionScope,
    custom_regions: settings.regionScope === "custom" ? settings.customRegions.trim() : "",
    return_text_preview: false,
    auto_mask_threshold: BUNDLED_PUBLIC_THRESHOLD.autoMaskThreshold,
    review_threshold: BUNDLED_PUBLIC_THRESHOLD.reviewThreshold,
  };
}

export function isRuleDisabled(rule: MaskingRuleId): boolean {
  return state.settings.profile === "legal" && (rule === "approval_line" || rule === "region_context" || rule === "doc_meta");
}

export function updateSettings(next: Partial<AppSettings>): AppSettings {
  const settings = mergeSettings({ ...state.settings, ...next });
  publish({ ...state, settings, rules: rulesForProfile(settings, state.rules) });
  return settings;
}

export function setRule(rule: MaskingRuleId, enabled: boolean): void {
  if (isRuleDisabled(rule)) return;
  publish({ ...state, rules: { ...state.rules, [rule]: enabled } });
}

export function applySettings(application: AppSettings | SettingsApplication): AppSettings {
  const settings = "settings" in application ? application.settings : application;
  const next = mergeSettings(settings);
  publish({ ...state, settings: next, rules: rulesForProfile(next, state.rules) });
  return next;
}

export function loadSettingsIntoStore(): LoadedSettings {
  const loaded = loadSettings();
  applySettings(loaded);
  return loaded;
}

export function saveCurrentSettings(): SavedSettings {
  const saved = saveSettings(state.settings);
  applySettings(saved);
  return saved;
}

export function beginSettingsDraft(): void {
  if (state.draft !== null) return;
  publish({ ...state, draft: { ...state.settings } });
}

export function cancelSettingsDraft(): boolean {
  if (state.draft === null) return false;
  const settings = state.draft;
  publish({ ...state, settings, rules: rulesForProfile(settings, state.rules), draft: null });
  return true;
}

export function completeSettingsDraft(): void {
  if (state.draft === null) return;
  publish({ ...state, draft: null });
}
