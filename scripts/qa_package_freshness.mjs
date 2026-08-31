import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { PACKAGE_FRESHNESS_SIDECAR_PATH, fingerprintedResourceMapFiles, resourceGenerationRule } from "./runtime_resource_map.mjs";
import { recordMaskingEngineBuild, writePackageFingerprint } from "./prepare_package_fingerprint.mjs";
import { verifyPackageFreshness } from "./verify_package_freshness.mjs";

const repoRoot = path.resolve(import.meta.dirname, "..");
const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "nothing-package-freshness-"));
const resourceRoot = path.join(temporaryRoot, "Nothing.app", "Contents", "Resources");
const sidecarPath = path.join(resourceRoot, PACKAGE_FRESHNESS_SIDECAR_PATH);
const engineBuildProvenancePath = path.join(temporaryRoot, "masking-engine-build.json");
const resourceEntries = fingerprintedResourceMapFiles(repoRoot);
const engineResource = resourceEntries.find((entry) => resourceGenerationRule(entry)?.rule === "input-closure");
assert.notEqual(engineResource, undefined, "resource map has no masking engine entry");

function copyResources() {
  for (const entry of resourceEntries) {
    const destination = path.join(resourceRoot, entry.bundlePath);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.linkSync(entry.sourceAbsolutePath, destination);
  }
}

function restoreSidecar() {
  writePackageFingerprint(repoRoot, sidecarPath, engineBuildProvenancePath);
}

function assertFailure(label, expectedPath) {
  const result = verifyPackageFreshness(repoRoot, resourceRoot);
  assert.equal(result.valid, false, `${label} unexpectedly passed`);
  assert.equal(result.errors.some((error) => error.includes(expectedPath)), true, `${label} did not name ${expectedPath}: ${result.errors.join(" | ")}`);
  console.log(`${label}: FAIL (${result.errors.find((error) => error.includes(expectedPath))})`);
}

try {
  copyResources();
  recordMaskingEngineBuild(repoRoot, engineResource.sourcePath, engineBuildProvenancePath);
  restoreSidecar();
  assert.equal(verifyPackageFreshness(repoRoot, resourceRoot).valid, true);
  console.log("fresh bundle: PASS");

  const staleManifest = JSON.parse(fs.readFileSync(sidecarPath, "utf8"));
  staleManifest.entries[0].sha256 = "0".repeat(64);
  fs.writeFileSync(sidecarPath, `${JSON.stringify(staleManifest)}\n`, "utf8");
  assertFailure("stale file hash", staleManifest.entries[0].sourcePath);
  restoreSidecar();

  fs.unlinkSync(sidecarPath);
  assertFailure("missing sidecar", "sidecar missing");
  restoreSidecar();

  const incompleteManifest = JSON.parse(fs.readFileSync(sidecarPath, "utf8"));
  incompleteManifest.entries.pop();
  incompleteManifest.resourceMapEntryCount = incompleteManifest.entries.length;
  fs.writeFileSync(sidecarPath, `${JSON.stringify(incompleteManifest)}\n`, "utf8");
  assertFailure("entry-count mismatch", "sidecar entry count mismatch");
  restoreSidecar();

  const staleEngineManifest = JSON.parse(fs.readFileSync(sidecarPath, "utf8"));
  const engineEntry = staleEngineManifest.entries.find((entry) => entry.generation?.rule === "input-closure");
  assert.notEqual(engineEntry, undefined, "synthetic bundle has no masking engine entry");
  engineEntry.generation.inputs[0].sha256 = "0".repeat(64);
  fs.writeFileSync(sidecarPath, `${JSON.stringify(staleEngineManifest)}\n`, "utf8");
  assertFailure("stale engine input closure", engineEntry.generation.inputs[0].sourcePath);
  restoreSidecar();

  const recordedEngineBuild = JSON.parse(fs.readFileSync(engineBuildProvenancePath, "utf8"));
  recordedEngineBuild.inputs[0].sha256 = "0".repeat(64);
  fs.writeFileSync(engineBuildProvenancePath, `${JSON.stringify(recordedEngineBuild)}\n`, "utf8");
  writePackageFingerprint(repoRoot, sidecarPath, engineBuildProvenancePath);
  assertFailure("repackaged stale engine", recordedEngineBuild.inputs[0].sourcePath);
} finally {
  fs.rmSync(temporaryRoot, { recursive: true, force: true });
}
