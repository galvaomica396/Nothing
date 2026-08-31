import type { DisplayMode } from "./state/contracts";

const SETTINGS_THEMES = ["light", "dark", "system"] as const;
export const DEIDENTIFICATION_MODES = ["token", "partial", "pseudonym"] as const;

export type SettingsTheme = (typeof SETTINGS_THEMES)[number];
export type ResolvedTheme = Exclude<SettingsTheme, "system">;
export type DeidentificationMode = (typeof DEIDENTIFICATION_MODES)[number];
export const RUNTIME_MASKING_PROFILES = ["internal_review", "official_dispatch", "mixed"] as const;
export type RuntimeMaskingProfile = (typeof RUNTIME_MASKING_PROFILES)[number];
export const MASKING_PROFILES = [...RUNTIME_MASKING_PROFILES, "legal"] as const;
export type MaskingProfile = (typeof MASKING_PROFILES)[number];
export type MaskingOutputArtifacts = "pdf_safe_report" | "pdf_masked_txt_safe_report";

export type AppSettings = {
  theme: SettingsTheme;
  outputDir: string;
  profile: MaskingProfile;
  engine: string;
  displayMode: DisplayMode;
  deidentificationMode: DeidentificationMode;
  regionScope: string;
  customRegions: string;
  customKeywords: string;
  pdfRedaction: boolean;
  exportMaskedText: boolean;
  openOutputAfterSave: boolean;
};

type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

const SETTINGS_KEY = "makiiing-v2-settings";
const ENGINES = ["auto", "marker", "paddle", "pymupdf", "pypdf"];
const DISPLAY_MODES = ["black", "label_en", "label_ko", "pseudonym"] as const;
const REGION_SCOPES = ["seoul", "national", "custom"];

export const DEFAULT_SETTINGS: AppSettings = {
  theme: "light",
  outputDir: "",
  profile: "mixed",
  engine: "auto",
  displayMode: "black",
  deidentificationMode: "token",
  regionScope: "national",
  customRegions: "",
  customKeywords: "",
  pdfRedaction: true,
  exportMaskedText: false,
  openOutputAfterSave: false,
};

export type SettingsLoadDiagnostic =
  | { readonly status: "loaded" }
  | { readonly status: "defaulted"; readonly reason: "storage_unavailable" | "storage_read_failed" | "storage_parse_failed" | "invalid_payload" };
export type LoadedSettings = { readonly settings: AppSettings; readonly diagnostic: SettingsLoadDiagnostic };
export type SettingsPersistenceDiagnostic = { readonly status: "saved" } | { readonly status: "failed"; readonly reason: "storage_unavailable" | "write_failed" };
export type SavedSettings = { readonly settings: AppSettings; readonly diagnostic: SettingsPersistenceDiagnostic };
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown, fallback: string): string {
  if (value === undefined) return fallback;
  if (typeof value === "string") return value;
  throw new Error("Invalid settings string value.");
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  if (value === undefined) return fallback;
  if (typeof value === "boolean") return value;
  throw new Error("Invalid settings boolean value.");
}

function enumValue<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  if (value === undefined) return fallback;
  if (typeof value === "string" && allowed.includes(value as T)) return value as T;
  throw new Error("Invalid settings enum value.");
}

export function normalizeMaskingProfile(value: unknown): MaskingProfile {
  if (typeof value === "string" && MASKING_PROFILES.includes(value as MaskingProfile)) return value as MaskingProfile;
  throw new Error("Invalid live masking profile.");
}

/** Only historical persisted `official` is compatible with the current public `mixed` profile. */
export function migratePersistedMaskingProfile(value: unknown): MaskingProfile {
  if (value === "official") return "mixed";
  return normalizeMaskingProfile(value);
}

export function maskingOutputArtifacts(exportMaskedText: boolean): MaskingOutputArtifacts {
  return exportMaskedText ? "pdf_masked_txt_safe_report" : "pdf_safe_report";
}

export function themeAttribute(theme: unknown): SettingsTheme {
  return enumValue(theme, SETTINGS_THEMES, DEFAULT_SETTINGS.theme);
}

export function resolveTheme(theme: SettingsTheme, prefersDark: boolean): ResolvedTheme {
  return theme === "system" ? (prefersDark ? "dark" : "light") : theme;
}

function profileFromLive(value: unknown): MaskingProfile {
  return value === undefined ? DEFAULT_SETTINGS.profile : normalizeMaskingProfile(value);
}

export function mergeSettings(raw: unknown): AppSettings {
  const fields = ["theme", "outputDir", "profile", "engine", "displayMode", "deidentificationMode", "regionScope", "customRegions", "customKeywords", "pdfRedaction", "exportMaskedText", "openOutputAfterSave"];
  if (!isRecord(raw) || !Object.keys(raw).every((key) => fields.includes(key))) throw new Error("Invalid live settings payload.");
  const value = raw;
  return {
    theme: themeAttribute(value.theme),
    outputDir: stringValue(value.outputDir, DEFAULT_SETTINGS.outputDir),
    profile: profileFromLive(value.profile),
    engine: enumValue(value.engine, ENGINES, DEFAULT_SETTINGS.engine),
    displayMode: enumValue(value.displayMode, DISPLAY_MODES, DEFAULT_SETTINGS.displayMode),
    deidentificationMode: enumValue(value.deidentificationMode, DEIDENTIFICATION_MODES, DEFAULT_SETTINGS.deidentificationMode),
    regionScope: enumValue(value.regionScope, REGION_SCOPES, DEFAULT_SETTINGS.regionScope),
    customRegions: stringValue(value.customRegions, DEFAULT_SETTINGS.customRegions),
    customKeywords: stringValue(value.customKeywords, DEFAULT_SETTINGS.customKeywords),
    pdfRedaction: booleanValue(value.pdfRedaction, DEFAULT_SETTINGS.pdfRedaction),
    exportMaskedText: booleanValue(value.exportMaskedText, DEFAULT_SETTINGS.exportMaskedText),
    openOutputAfterSave: booleanValue(value.openOutputAfterSave, DEFAULT_SETTINGS.openOutputAfterSave),
  };
}

function mergeStoredSettings(raw: unknown): AppSettings {
  const fields = ["theme", "profile", "engine", "displayMode", "deidentificationMode", "regionScope", "pdfRedaction", "exportMaskedText", "openOutputAfterSave"];
  if (!isRecord(raw) || !Object.keys(raw).every((key) => fields.includes(key))) throw new Error("Invalid persisted settings payload.");
  const profile = raw.profile === undefined ? DEFAULT_SETTINGS.profile : migratePersistedMaskingProfile(raw.profile);
  const settings = mergeSettings({ ...raw, profile });
  return {
    ...settings,
    theme: Object.prototype.hasOwnProperty.call(raw, "theme") ? settings.theme : DEFAULT_SETTINGS.theme,
    outputDir: DEFAULT_SETTINGS.outputDir,
    customRegions: DEFAULT_SETTINGS.customRegions,
    customKeywords: DEFAULT_SETTINGS.customKeywords,
  };
}

function recoveredSettings(reason: Extract<SettingsLoadDiagnostic, { status: "defaulted" }> ["reason"]): LoadedSettings {
  return { settings: { ...DEFAULT_SETTINGS }, diagnostic: { status: "defaulted", reason } };
}

function defaultStorage(): StorageLike | null {
  try {
    return typeof globalThis !== "undefined" && "localStorage" in globalThis ? globalThis.localStorage : null;
  } catch {
    return null;
  }
}

export function loadSettings(storage?: StorageLike | null): LoadedSettings {
  const resolvedStorage = storage === undefined ? defaultStorage() : storage;
  if (!resolvedStorage) return recoveredSettings("storage_unavailable");
  let stored: string | null;
  try {
    stored = resolvedStorage.getItem(SETTINGS_KEY);
  } catch {
    return recoveredSettings("storage_read_failed");
  }
  if (stored === null) return { settings: { ...DEFAULT_SETTINGS }, diagnostic: { status: "loaded" } };
  try {
    return { settings: mergeStoredSettings(JSON.parse(stored)), diagnostic: { status: "loaded" } };
  } catch (error) {
    return recoveredSettings(error instanceof SyntaxError ? "storage_parse_failed" : "invalid_payload");
  }
}

function storageSafeSettings(settings: AppSettings): Partial<AppSettings> {
  return {
    theme: settings.theme,
    profile: settings.profile,
    engine: settings.engine,
    displayMode: settings.displayMode,
    deidentificationMode: settings.deidentificationMode,
    regionScope: settings.regionScope === "custom" ? DEFAULT_SETTINGS.regionScope : settings.regionScope,
    pdfRedaction: settings.pdfRedaction,
    exportMaskedText: settings.exportMaskedText,
    openOutputAfterSave: settings.openOutputAfterSave,
  };
}

export type SettingsApplication = LoadedSettings | SavedSettings;

export function saveSettings(
  nextSettings: Partial<AppSettings>,
  storage?: StorageLike | null,
): SavedSettings {
  const settings = mergeSettings({ ...DEFAULT_SETTINGS, ...nextSettings });
  const resolvedStorage = storage === undefined ? defaultStorage() : storage;
  if (!resolvedStorage) return { settings, diagnostic: { status: "failed", reason: "storage_unavailable" } };
  try {
    resolvedStorage.setItem(SETTINGS_KEY, JSON.stringify(storageSafeSettings(settings)));
  } catch {
    return { settings, diagnostic: { status: "failed", reason: "write_failed" } };
  }
  return { settings, diagnostic: { status: "saved" } };
}
