import fs from "node:fs";
import path from "node:path";
import { fingerprintedResourceMapFiles } from "./runtime_resource_map.mjs";

const RUNTIME_ENTRYPOINTS = [
  "document_masker_ocr_gui.py",
  "scripts/run_masking_pipeline.py",
  "scripts/apply_manual_boxes.py",
];

function localModules(repoRoot) {
  const modules = new Map();
  for (const directory of [repoRoot, path.join(repoRoot, "scripts")]) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.isFile() && entry.name.endsWith(".py")) {
        modules.set(path.basename(entry.name, ".py"), path.join(directory, entry.name));
      }
    }
  }
  return modules;
}

function importedLocalModules(source, modules) {
  const imports = new Set();
  for (const match of source.matchAll(/^\s*from\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s+import\s+/gm)) {
    imports.add(match[1].split(".", 1)[0]);
  }
  for (const match of source.matchAll(/^\s*import\s+([^#\n]+)/gm)) {
    for (const imported of match[1].split(",")) {
      imports.add(imported.trim().split(/\s+as\s+|\./, 1)[0]);
    }
  }
  return [...imports].flatMap((name) => modules.has(name) ? [modules.get(name)] : []);
}

function requiredRuntimeSources(repoRoot) {
  const modules = localModules(repoRoot);
  const required = new Set();
  const pending = RUNTIME_ENTRYPOINTS.map((entrypoint) => path.join(repoRoot, entrypoint));
  while (pending.length > 0) {
    const source = pending.pop();
    if (source === undefined || required.has(source)) continue;
    required.add(source);
    pending.push(...importedLocalModules(fs.readFileSync(source, "utf8"), modules));
  }
  for (const entry of fs.readdirSync(path.join(repoRoot, "data"), { withFileTypes: true })) {
    if (entry.isFile() && /^kr_regions.*\.json$/.test(entry.name)) {
      required.add(path.join(repoRoot, "data", entry.name));
    }
  }
  return required;
}

export function checkRuntimeResourceCompleteness(repoRoot) {
  const bundled = new Set(fingerprintedResourceMapFiles(repoRoot).map((entry) => entry.sourceAbsolutePath));
  return [...requiredRuntimeSources(repoRoot)]
    .filter((source) => !bundled.has(source))
    .map((source) => path.relative(repoRoot, source))
    .sort();
}
