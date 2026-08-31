import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import {
  MASKING_ENGINE_VERSION,
  PACKAGE_FRESHNESS_SIDECAR_PATH,
  RESOURCE_MAP_ENUMERATOR_VERSION,
  fingerprintedResourceMapFiles,
  resourceGenerationRule,
} from "./runtime_resource_map.mjs";

const PYINSTALLER_SPEC_PATH = "packaging/pyinstaller/masking_engine.spec";

function sha256(filePath) {
  return createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function gitCommit(repoRoot) {
  try {
    return execFileSync("git", ["-C", repoRoot, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
  } catch {
    return "unknown";
  }
}

function engineInputPaths(entries) {
  return [
    ...entries.filter((entry) => entry.sourcePath.endsWith(".py")).map((entry) => entry.sourcePath),
    PYINSTALLER_SPEC_PATH,
  ].sort();
}

function safePath(root, relativePath) {
  const candidate = path.resolve(root, relativePath);
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative) ? candidate : undefined;
}

function readManifest(manifestPath, errors) {
  if (!fs.existsSync(manifestPath)) {
    errors.push(`sidecar missing: ${manifestPath}`);
    return undefined;
  }
  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    if (manifest === null || typeof manifest !== "object" || Array.isArray(manifest)) {
      errors.push(`sidecar root must be an object: ${manifestPath}`);
      return undefined;
    }
    return manifest;
  } catch (error) {
    errors.push(`sidecar is not valid JSON: ${manifestPath} (${error instanceof Error ? error.message : String(error)})`);
    return undefined;
  }
}

function validHashRecord(value) {
  return value !== null
    && typeof value === "object"
    && typeof value.sourcePath === "string"
    && typeof value.sha256 === "string";
}

function validEntry(value) {
  return validHashRecord(value) && typeof value.bundlePath === "string";
}

function validateProvenance(manifest, repoRoot, errors) {
  const provenance = manifest.provenance;
  if (provenance === null || typeof provenance !== "object") {
    errors.push("sidecar provenance missing");
    return;
  }
  if (provenance.resourceMapEnumeratorVersion !== RESOURCE_MAP_ENUMERATOR_VERSION) {
    errors.push("sidecar enumerator version does not match the verifier");
  }
  if (provenance.maskingEngineVersion !== MASKING_ENGINE_VERSION) errors.push("sidecar engine version does not match the verifier");
  const specPath = path.join(repoRoot, PYINSTALLER_SPEC_PATH);
  if (provenance.pyinstallerSpecSha256 !== sha256(specPath)) errors.push(`PyInstaller spec hash mismatch: ${PYINSTALLER_SPEC_PATH}`);
  const currentCommit = gitCommit(repoRoot);
  if (provenance.gitCommit !== currentCommit) errors.push(`git commit mismatch: sidecar=${provenance.gitCommit} source=${currentCommit}`);
}

function inputClosureGenerationErrors(entry, repoRoot) {
  const errors = [];
  const generation = entry.generation;
  if (generation === null || typeof generation !== "object" || generation.rule !== "input-closure" || !Array.isArray(generation.inputs)) {
    errors.push(`generated artifact rule missing: ${entry.sourcePath}`);
    return errors;
  }
  const expectedInputPaths = engineInputPaths(fingerprintedResourceMapFiles(repoRoot));
  const recordedPaths = generation.inputs
    .filter((input) => input !== null && typeof input === "object" && typeof input.sourcePath === "string")
    .map((input) => input.sourcePath)
    .sort();
  if (JSON.stringify(recordedPaths) !== JSON.stringify(expectedInputPaths)) {
    errors.push(`generated artifact input closure mismatch: ${entry.sourcePath}`);
  }
  for (const input of generation.inputs) {
    if (input === null || typeof input !== "object" || typeof input.sourcePath !== "string" || typeof input.sha256 !== "string") {
      errors.push(`generated artifact input is malformed: ${entry.sourcePath}`);
      continue;
    }
    const inputPath = safePath(repoRoot, input.sourcePath);
    if (inputPath === undefined || !fs.existsSync(inputPath) || !fs.statSync(inputPath).isFile()) {
      errors.push(`generated artifact input missing from source: ${input.sourcePath}`);
      continue;
    }
    if (sha256(inputPath) !== input.sha256) errors.push(`generated artifact input hash mismatch: ${input.sourcePath}`);
  }
  return errors;
}

function generatorSourceGenerationErrors(entry, repoRoot, generatorSourcePath) {
  const generation = entry.generation;
  if (generation === null || typeof generation !== "object" || generation.rule !== "generator-source" || !validHashRecord(generation.generator)) {
    return [`generated artifact rule missing: ${entry.sourcePath}`];
  }
  if (generation.generator.sourcePath !== generatorSourcePath) {
    return [`generated artifact generator mismatch: ${entry.sourcePath}`];
  }
  const generatorPath = safePath(repoRoot, generatorSourcePath);
  if (generatorPath === undefined || !fs.existsSync(generatorPath) || !fs.statSync(generatorPath).isFile()) {
    return [`generated artifact generator missing from source: ${generatorSourcePath}`];
  }
  return sha256(generatorPath) === generation.generator.sha256
    ? []
    : [`generated artifact generator hash mismatch: ${generatorSourcePath}`];
}

function generationErrors(entry, expectedEntry, repoRoot) {
  const rule = resourceGenerationRule(expectedEntry);
  if (rule?.rule === "input-closure") return inputClosureGenerationErrors(entry, repoRoot);
  if (rule?.rule === "generator-source") {
    return generatorSourceGenerationErrors(entry, repoRoot, rule.generatorSourcePath);
  }
  return entry.generation === undefined ? [] : [`unexpected generated artifact rule: ${entry.sourcePath}`];
}

function resourceRootFor(appPath) {
  const macosResources = path.join(appPath, "Contents", "Resources");
  if (fs.existsSync(macosResources)) return macosResources;
  const windowsResources = path.join(appPath, "resources");
  return fs.existsSync(windowsResources) ? windowsResources : appPath;
}

export function verifyPackageFreshness(repoRoot, resourceRoot) {
  const errors = [];
  const manifest = readManifest(path.join(resourceRoot, PACKAGE_FRESHNESS_SIDECAR_PATH), errors);
  if (manifest === undefined) return { valid: false, errors };
  if (manifest.schemaVersion !== 1) errors.push(`unsupported sidecar schema version: ${manifest.schemaVersion}`);
  validateProvenance(manifest, repoRoot, errors);
  const expectedEntries = fingerprintedResourceMapFiles(repoRoot);
  const expectedBySourcePath = new Map(expectedEntries.map((entry) => [entry.sourcePath, entry]));
  if (!Array.isArray(manifest.entries)) {
    errors.push("sidecar entries must be an array");
    return { valid: false, errors };
  }
  if (manifest.resourceMapEntryCount !== expectedEntries.length || manifest.entries.length !== expectedEntries.length) {
    errors.push(`sidecar entry count mismatch: sidecar=${manifest.entries.length} declared=${manifest.resourceMapEntryCount} resource-map=${expectedEntries.length}`);
  }
  const sidecarPaths = new Set();
  for (const entry of manifest.entries) {
    if (!validEntry(entry)) {
      errors.push("sidecar entry is malformed");
      continue;
    }
    if (sidecarPaths.has(entry.sourcePath)) errors.push(`sidecar path duplicated: ${entry.sourcePath}`);
    sidecarPaths.add(entry.sourcePath);
    const expected = expectedBySourcePath.get(entry.sourcePath);
    if (expected === undefined) {
      errors.push(`sidecar path is not in resource map: ${entry.sourcePath}`);
      continue;
    }
    if (entry.bundlePath !== expected.bundlePath) errors.push(`sidecar bundle path mismatch: ${entry.sourcePath}`);
    const sourcePath = safePath(repoRoot, entry.sourcePath);
    if (sourcePath === undefined || !fs.existsSync(sourcePath) || !fs.statSync(sourcePath).isFile()) {
      errors.push(`source path missing: ${entry.sourcePath}`);
      continue;
    }
    if (sha256(sourcePath) !== entry.sha256) errors.push(`source hash mismatch: ${entry.sourcePath}`);
    const artifactPath = safePath(resourceRoot, entry.bundlePath);
    if (artifactPath === undefined || !fs.existsSync(artifactPath) || !fs.statSync(artifactPath).isFile()) {
      errors.push(`bundle resource missing: ${entry.bundlePath}`);
    } else if (sha256(artifactPath) !== entry.sha256) {
      errors.push(`bundle resource hash mismatch: ${entry.bundlePath}`);
    }
    errors.push(...generationErrors(entry, expected, repoRoot));
  }
  for (const expected of expectedEntries) {
    if (!sidecarPaths.has(expected.sourcePath)) errors.push(`sidecar path missing: ${expected.sourcePath}`);
  }
  return { valid: errors.length === 0, errors };
}

function argumentsFor(args) {
  const values = new Map();
  for (let index = 0; index < args.length; index += 2) values.set(args[index], args[index + 1]);
  return values;
}

function main() {
  const args = argumentsFor(process.argv.slice(2));
  const repoRoot = path.resolve(args.get("--repo") ?? path.join(import.meta.dirname, ".."));
  const appPath = args.get("--app")
    ?? (process.platform === "darwin"
      ? path.join(repoRoot, "src-tauri", "target", "release", "bundle", "macos", "Nothing.app")
      : undefined);
  const explicitResourceRoot = args.get("--resource-dir");
  if (appPath === undefined && explicitResourceRoot === undefined) {
    console.error("usage: node scripts/verify_package_freshness.mjs --app <bundle.app> [--repo <repo-root>]");
    process.exitCode = 2;
    return;
  }
  const resourceRoot = explicitResourceRoot === undefined ? resourceRootFor(path.resolve(appPath)) : path.resolve(explicitResourceRoot);
  const result = verifyPackageFreshness(repoRoot, resourceRoot);
  if (result.valid) {
    console.log(`PACKAGE FRESHNESS OK (${resourceRoot})`);
    return;
  }
  console.error(`PACKAGE FRESHNESS FAILED (${result.errors.length} issue(s))`);
  for (const error of result.errors) console.error(`  - ${error}`);
  process.exitCode = 1;
}

if (import.meta.main) main();
