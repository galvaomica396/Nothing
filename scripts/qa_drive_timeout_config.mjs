import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const configPath = join(repoRoot, "contracts", "qa-drive-timeouts.json");
const config = JSON.parse(readFileSync(configPath, "utf8"));

const REQUIRED_KEYS = [
  "startup_ms",
  "open_ms",
  "control_ms",
  "navigation_ms",
  "long_ms",
  "response_ms",
  "render_cancel_ms",
  "precondition_ms",
];

for (const key of REQUIRED_KEYS) {
  if (!Number.isSafeInteger(config[key]) || config[key] <= 0) {
    throw new Error(`QA_DRIVE_TIMEOUT_CONFIG_INVALID:${key}`);
  }
}

export const QA_DRIVE_TIMEOUTS_MS = Object.freeze({
  startup: config.startup_ms,
  open: config.open_ms,
  control: config.control_ms,
  navigation: config.navigation_ms,
  long: config.long_ms,
  response: config.response_ms,
  renderCancel: config.render_cancel_ms,
  precondition: config.precondition_ms,
});

export function qaDriveCommandTimeoutMs(command) {
  const kind = String(command).trim().split(/\s+/)[0] || "unknown";
  if (kind === "open") return QA_DRIVE_TIMEOUTS_MS.open;
  if (["go-page", "scroll-to", "inspect-target", "resolve-geometry"].includes(kind)) {
    return QA_DRIVE_TIMEOUTS_MS.navigation;
  }
  if (["apply-keyword", "run-masking", "wait-idle", "resolve-review", "apply-manual", "confirm-save", "wait-save", "save-final"].includes(kind)) {
    return QA_DRIVE_TIMEOUTS_MS.long;
  }
  return QA_DRIVE_TIMEOUTS_MS.control;
}
