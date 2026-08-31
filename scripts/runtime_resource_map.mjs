import fs from "node:fs";
import path from "node:path";

export const RESOURCE_MAP_ENUMERATOR_VERSION = "1";
export const MASKING_ENGINE_VERSION = "1";
export const PACKAGE_FRESHNESS_SIDECAR_PATH = "masking_runtime/resource_fingerprint.json";
export const KR_REGIONS_GENERATOR_SOURCE_PATH = "scripts/update_kr_regions.py";

function normalizedPath(value) {
  return value.split(path.sep).join("/");
}

function resourceConfig(repoRoot) {
  const configPath = path.join(repoRoot, "src-tauri", "tauri.conf.json");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  const resources = config.bundle?.resources;
  if (resources === undefined || Array.isArray(resources) || typeof resources !== "object") {
    throw new Error(`Tauri bundle resources must be an object: ${configPath}`);
  }
  return { configPath, resources };
}

function ensureWithinRepo(repoRoot, sourcePath) {
  const relative = path.relative(repoRoot, sourcePath);
  if (relative === "" || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error(`Tauri resource source escapes repository: ${sourcePath}`);
  }
  return normalizedPath(relative);
}

function regularFiles(directory) {
  const files = [];
  const pending = [directory];
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === undefined) continue;
    for (const entry of fs.readdirSync(current, { withFileTypes: true }).sort((left, right) => left.name.localeCompare(right.name))) {
      const candidate = path.join(current, entry.name);
      if (entry.isDirectory()) pending.push(candidate);
      if (entry.isFile()) files.push(candidate);
    }
  }
  return files.sort((left, right) => left.localeCompare(right));
}

function filesForMapping(repoRoot, configDirectory, source, destination) {
  if (typeof source !== "string" || typeof destination !== "string") {
    throw new Error("Tauri bundle resources must map string source paths to string destinations");
  }
  const sourceRoot = path.resolve(configDirectory, source);
  const sourcePath = ensureWithinRepo(repoRoot, sourceRoot);
  const sourceStat = fs.statSync(sourceRoot);
  if (sourceStat.isFile()) {
    return [{ sourcePath, sourceAbsolutePath: sourceRoot, bundlePath: normalizedPath(destination) }];
  }
  if (!sourceStat.isDirectory()) throw new Error(`Tauri resource is neither file nor directory: ${sourcePath}`);
  return regularFiles(sourceRoot).map((sourceAbsolutePath) => ({
    sourcePath: ensureWithinRepo(repoRoot, sourceAbsolutePath),
    sourceAbsolutePath,
    bundlePath: path.posix.join(normalizedPath(destination), normalizedPath(path.relative(sourceRoot, sourceAbsolutePath))),
  }));
}

export function resourceMapFiles(repoRoot) {
  const { configPath, resources } = resourceConfig(repoRoot);
  const files = Object.entries(resources).flatMap(([source, destination]) => {
    const sourceAbsolutePath = path.resolve(path.dirname(configPath), source);
    if (normalizedPath(destination) === PACKAGE_FRESHNESS_SIDECAR_PATH && !fs.existsSync(sourceAbsolutePath)) return [];
    return filesForMapping(repoRoot, path.dirname(configPath), source, destination);
  });
  const duplicateBundlePath = files.find((entry, index) => files.findIndex((other) => other.bundlePath === entry.bundlePath) !== index);
  if (duplicateBundlePath !== undefined) throw new Error(`Tauri resource destination is duplicated: ${duplicateBundlePath.bundlePath}`);
  return files.sort((left, right) => left.sourcePath.localeCompare(right.sourcePath));
}

export function fingerprintedResourceMapFiles(repoRoot) {
  return resourceMapFiles(repoRoot).filter((entry) => entry.bundlePath !== PACKAGE_FRESHNESS_SIDECAR_PATH);
}

export function resourceGenerationRule(entry) {
  if (entry.bundlePath === "masking_runtime/bin/masking_engine" || entry.bundlePath === "masking_runtime/bin/masking_engine.exe") {
    return { rule: "input-closure" };
  }
  if (entry.sourcePath === "data/kr_regions.json") {
    return { rule: "generator-source", generatorSourcePath: KR_REGIONS_GENERATOR_SOURCE_PATH };
  }
  return undefined;
}
