import type { DisplayMode } from "./state/contracts";

const SETTINGS_THEMES = ["light", "dark", "system"] as const;
export const DEIDENTIFICATION_MODES = ["token", "partial", "pseudonym"] as const;

export type SettingsTheme = (typeof SETTINGS_THEMES)[number];
export type ResolvedTheme = Exclude<SettingsTheme, "system">;
export type DeidentificationMode = (typeof DEIDENTIFICATION_MODES)[number];
export type MaskingOutputArtifacts = "pdf_safe_report" | "pdf_masked_txt_safe_report";

export type AppSettings = {
  theme: SettingsTheme;
  outputDir: string;
  profile: string;
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
  theme: "system",
  outputDir: "",
  profile: "official",
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

function stringValue(value: unknown, fallback: string) {
  return typeof value === "string" ? value : fallback;
}

function booleanValue(value: unknown, fallback: boolean) {
  return typeof value === "boolean" ? value : fallback;
}

function enumValue<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && allowed.includes(value as T) ? (value as T) : fallback;
}

export function maskingOutputArtifacts(exportMaskedText: boolean): MaskingOutputArtifacts {
  return exportMaskedText ? "pdf_masked_txt_safe_report" : "pdf_safe_report";
}

export function themeAttribute(theme: unknown): SettingsTheme {
  switch (theme) {
    case "light":
    case "dark":
    case "system":
      return theme;
    case "default":
    default:
      return "dark";
  }
}

export function resolveTheme(theme: SettingsTheme, prefersDark: boolean): ResolvedTheme {
  if (theme === "system") return prefersDark ? "dark" : "light";
  return theme;
}

export function mergeSettings(raw: unknown): AppSettings {
  const value = raw && typeof raw === "object" ? (raw as Partial<AppSettings>) : {};
  return {
    theme: themeAttribute(value.theme),
    outputDir: stringValue(value.outputDir, DEFAULT_SETTINGS.outputDir),
    profile: value.profile === "legal" ? "legal" : DEFAULT_SETTINGS.profile,
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

function mergeStoredSettings(raw: unknown): AppSettings {
  const settings = mergeSettings(raw);
  return {
    ...settings,
    theme: raw && typeof raw === "object" && Object.prototype.hasOwnProperty.call(raw, "theme")
      ? settings.theme
      : "dark",
    outputDir: DEFAULT_SETTINGS.outputDir,
    customRegions: DEFAULT_SETTINGS.customRegions,
    customKeywords: DEFAULT_SETTINGS.customKeywords,
  };
}

export function loadSettings(storage: StorageLike | null | undefined = globalThis.localStorage): AppSettings {
  if (!storage) return { ...DEFAULT_SETTINGS };
  let stored: string | null;
  try {
    stored = storage.getItem(SETTINGS_KEY);
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
  if (!stored) return { ...DEFAULT_SETTINGS };
  try {
    return mergeStoredSettings(JSON.parse(stored));
  } catch {
    return { ...DEFAULT_SETTINGS, theme: "dark" };
  }
}

export function saveSettings(
  nextSettings: Partial<AppSettings>,
  storage: StorageLike | null | undefined = globalThis.localStorage,
): AppSettings {
  const settings = mergeSettings({ ...DEFAULT_SETTINGS, ...nextSettings });
  if (storage) {
    try {
      storage.setItem(SETTINGS_KEY, JSON.stringify(storageSafeSettings(settings)));
    } catch {
      return settings;
    }
  }
  return settings;
}
