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
export const MASKING_ENGINE_BUILD_PROVENANCE_PATH = "build/masking_engine_build_provenance.json";

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

function engineInputClosure(repoRoot, entries) {
  const inputPaths = [
    ...entries.filter((entry) => entry.sourcePath.endsWith(".py")).map((entry) => entry.sourcePath),
    PYINSTALLER_SPEC_PATH,
  ].sort();
  return inputPaths.map((sourcePath) => ({ sourcePath, sha256: sha256(path.join(repoRoot, sourcePath)) }));
}

function engineEntry(entries, sourcePath) {
  return entries.find((entry) => entry.sourcePath === sourcePath && resourceGenerationRule(entry)?.rule === "input-closure");
}

function validHashRecord(value) {
  return value !== null && typeof value === "object" && typeof value.sourcePath === "string" && typeof value.sha256 === "string";
}

function readEngineBuildProvenance(repoRoot, provenancePath, entries) {
  if (!fs.existsSync(provenancePath)) {
    throw new Error(`masking engine build provenance missing: ${provenancePath}; run scripts/build_masking_engine.sh first`);
  }
  const provenance = JSON.parse(fs.readFileSync(provenancePath, "utf8"));
  if (provenance === null || typeof provenance !== "object" || provenance.schemaVersion !== 1 || !validHashRecord(provenance.engine) || !Array.isArray(provenance.inputs)) {
    throw new Error(`masking engine build provenance is malformed: ${provenancePath}`);
  }
  const expectedEngine = engineEntry(entries, provenance.engine.sourcePath);
  if (expectedEngine === undefined) throw new Error(`masking engine build provenance names an unmapped engine: ${provenance.engine.sourcePath}`);
  if (sha256(expectedEngine.sourceAbsolutePath) !== provenance.engine.sha256) {
    throw new Error(`masking engine binary changed after provenance was recorded: ${provenance.engine.sourcePath}`);
  }
  if (!provenance.inputs.every(validHashRecord)) throw new Error(`masking engine build provenance inputs are malformed: ${provenancePath}`);
  const expectedInputPaths = engineInputClosure(repoRoot, entries).map((input) => input.sourcePath);
  const recordedInputPaths = provenance.inputs.map((input) => input.sourcePath).sort();
  if (JSON.stringify(recordedInputPaths) !== JSON.stringify(expectedInputPaths)) {
    throw new Error(`masking engine build provenance input closure is incomplete: ${provenancePath}`);
  }
  return provenance;
}

export function recordMaskingEngineBuild(repoRoot, engineSourcePath, outputPath) {
  const entries = fingerprintedResourceMapFiles(repoRoot);
  const engine = engineEntry(entries, engineSourcePath);
  if (engine === undefined) throw new Error(`resource map has no generated masking engine at ${engineSourcePath}`);
  const provenance = {
    schemaVersion: 1,
    engine: { sourcePath: engine.sourcePath, sha256: sha256(engine.sourceAbsolutePath) },
    inputs: engineInputClosure(repoRoot, entries),
  };
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(provenance, null, 2)}\n`, "utf8");
  return provenance;
}

export function buildPackageFingerprint(repoRoot, engineBuildProvenancePath = path.join(repoRoot, MASKING_ENGINE_BUILD_PROVENANCE_PATH)) {
  const entries = fingerprintedResourceMapFiles(repoRoot);
  const engineBuild = readEngineBuildProvenance(repoRoot, engineBuildProvenancePath, entries);
  const specPath = path.join(repoRoot, PYINSTALLER_SPEC_PATH);
  return {
    schemaVersion: 1,
    provenance: {
      gitCommit: gitCommit(repoRoot),
      pyinstallerSpecSha256: sha256(specPath),
      resourceMapEnumeratorVersion: RESOURCE_MAP_ENUMERATOR_VERSION,
      maskingEngineVersion: MASKING_ENGINE_VERSION,
    },
    resourceMapEntryCount: entries.length,
    entries: entries.map((entry) => {
      const generationRule = resourceGenerationRule(entry);
      const fingerprint = {
        sourcePath: entry.sourcePath,
        bundlePath: entry.bundlePath,
        sha256: sha256(entry.sourceAbsolutePath),
      };
      if (generationRule?.rule === "input-closure") {
        if (entry.sourcePath !== engineBuild.engine.sourcePath) {
          throw new Error(`masking engine build provenance does not cover generated artifact: ${entry.sourcePath}`);
        }
        return { ...fingerprint, generation: { rule: "input-closure", inputs: engineBuild.inputs } };
      }
      if (generationRule?.rule === "generator-source") {
        const generatorPath = path.join(repoRoot, generationRule.generatorSourcePath);
        return {
          ...fingerprint,
          generation: {
            rule: "generator-source",
            generator: { sourcePath: generationRule.generatorSourcePath, sha256: sha256(generatorPath) },
          },
        };
      }
      return fingerprint;
    }),
  };
}

export function writePackageFingerprint(repoRoot, outputPath, engineBuildProvenancePath) {
  const manifest = buildPackageFingerprint(repoRoot, engineBuildProvenancePath);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  return manifest;
}

function argumentsFor(args) {
  const values = new Map();
  for (let index = 0; index < args.length; index += 2) values.set(args[index], args[index + 1]);
  return values;
}

function main() {
  const args = argumentsFor(process.argv.slice(2));
  const repoRoot = path.resolve(args.get("--repo") ?? path.join(import.meta.dirname, ".."));
  const outputPath = path.resolve(args.get("--output") ?? path.join(repoRoot, PACKAGE_FRESHNESS_SIDECAR_PATH));
  const engineBuildProvenancePath = path.resolve(args.get("--engine-provenance") ?? path.join(repoRoot, MASKING_ENGINE_BUILD_PROVENANCE_PATH));
  const engineSourcePath = args.get("--record-engine-build");
  if (engineSourcePath !== undefined) recordMaskingEngineBuild(repoRoot, engineSourcePath, engineBuildProvenancePath);
  const manifest = writePackageFingerprint(repoRoot, outputPath, engineBuildProvenancePath);
  console.log(`PACKAGE FINGERPRINT READY (${manifest.resourceMapEntryCount} entries): ${outputPath}`);
}

if (import.meta.main) main();
