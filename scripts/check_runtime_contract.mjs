// Static runtime-contract verifier (docs/RUNTIME_CONTRACT.md).
//
// Query side  : src/app/**/*.ts
//               + src/features/{save-gate,document-batch,keyword-dialog,
//                 masking-run,canvas-workbench}/**/*.ts
//               ($("#id"), querySelector/querySelectorAll, getElementById,
//                requiredElement(scope, "#id"), `#rule-${id}` expansion)
//               Application DOM bindings may be extracted into dedicated modules;
//               all application-composition TypeScript modules are scanned so their selectors
//               remain under the same runtime contract.
// Define side : index.html + src/components/**/*.tsx static JSX
//               (id="...", *Id="..." pass-through props, data-*="...", name="...").
//               v4: 좌측 레일(WorkspaceSidebar/WorkspaceNavigationContext)이
//               폐지되어 rail-* 정의 소스가 사라졌다. 상단 바 탭·기어가
//               [data-screen-target] 를 직접 static JSX(AppHeader.tsx)로 정의한다.
//
// Markup injected at runtime as strings (dashboardSurfaces.ts, document rows, ...)
// is intentionally NOT part of the define side; selectors that only exist there
// live in DYNAMIC_DOM_ALLOWLIST below.
//
// Exit 0 + "CONTRACT OK (N selectors verified)" when every queried selector is
// defined; exit 1 with a missing-selector listing otherwise.
//
// Transition complete (REBUILD-PLAN-2026-08 §③): every static
// [data-screen-panel] root is declared in contracts/converted-screens.json and
// data-owner="react". No legacy-owned root may be introduced again.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { acceptanceBanner } from "./gate_complete.mjs";
import { checkRuntimeResourceCompleteness } from "./runtime_resource_completeness.mjs";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

console.error(acceptanceBanner(repoRoot));

// ---------------------------------------------------------------------------
// Allowlist: selectors queried by the runtime that are only present in
// DOM injected dynamically at runtime (never in static JSX). Each entry
// documents where the element is created.
// ---------------------------------------------------------------------------
const DYNAMIC_DOM_ALLOWLIST = new Map([
  // domBindings.ts collects [data-settings-tab] buttons only via
  // querySelectorAll (an empty NodeList is tolerated: the collection is just
  // iterated). At HEAD (d8319d7) the settings screen has a single
  // data-settings-panel="general" and renders no tab buttons in static JSX,
  // so this attribute intentionally has zero definitions.
  ["data:data-settings-tab", "optional querySelectorAll collection; no settings tab buttons exist at HEAD"],
]);

const TRANSITION_CONFIG_PATH = "contracts/converted-screens.json";
const LEGACY_BINDING_MARKER = "data-legacy-binding";
const REAL_CORPUS_MANIFEST_PATH = "contracts/real-corpus.json";
const REAL_CORPUS_RESOLVERS = new Set(["scripts/real_corpus.mjs", "scripts/real_corpus.py"]);

// Fallback rule-id list (RUNTIME_CONTRACT.md §2) used only if extraction from
// the scanned application-composition TypeScript modules finds nothing.
const FALLBACK_RULE_IDS = [
  "rrn", "phone", "business_reg", "name", "address", "place", "legal_party",
  "company", "court", "case_title", "case_number", "law_firm", "attorney",
  "approval_line", "region_context", "doc_meta",
];

function read(relPath) {
  return fs.readFileSync(path.join(repoRoot, relPath), "utf8");
}

function realCorpusFailures() {
  const failures = [];
  let manifest;
  try {
    manifest = JSON.parse(read(REAL_CORPUS_MANIFEST_PATH));
  } catch (error) {
    failures.push(`${REAL_CORPUS_MANIFEST_PATH} could not be parsed: ${error instanceof Error ? error.message : "parse error"}`);
    return failures;
  }
  if (!isPlainObject(manifest) || !Array.isArray(manifest.documents)) {
    failures.push(`${REAL_CORPUS_MANIFEST_PATH} must contain a documents array`);
  } else {
    if (manifest.documents.length !== 15) failures.push(`${REAL_CORPUS_MANIFEST_PATH} must contain exactly 15 documents`);
    const aliases = new Set();
    const hashes = new Set();
    for (const [index, entry] of manifest.documents.entries()) {
      const label = `${REAL_CORPUS_MANIFEST_PATH}.documents[${index}]`;
      if (!isPlainObject(entry)) {
        failures.push(`${label} must be an object`);
        continue;
      }
      if (Object.keys(entry).some((key) => ["filename", "fileName", "path", "absolutePath"].includes(key))) {
        failures.push(`${label} must not contain a filename or path`);
      }
      if (typeof entry.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(entry.sha256)) {
        failures.push(`${label}.sha256 must be a SHA-256 hash`);
      } else if (hashes.has(entry.sha256.toLowerCase())) {
        failures.push(`${label}.sha256 is duplicated`);
      } else {
        hashes.add(entry.sha256.toLowerCase());
      }
      if (!["internal_review", "official_dispatch"].includes(entry.category)) {
        failures.push(`${label}.category is unsupported`);
      }
      if (entry.alias !== undefined) {
        if (typeof entry.alias !== "string" || !/^[a-z][a-z0-9-]{1,31}$/.test(entry.alias)) {
          failures.push(`${label}.alias is invalid`);
        } else if (aliases.has(entry.alias)) {
          failures.push(`${label}.alias is duplicated`);
        } else {
          aliases.add(entry.alias);
        }
      }
    }
  }

  const verificationFiles = [
    "scripts/acceptance_real_app.mjs",
    "scripts/e2e_real_app.mjs",
    "scripts/evaluate_routing_corpus.py",
  ].filter((file) => fs.existsSync(path.join(repoRoot, file)) && !REAL_CORPUS_RESOLVERS.has(file));
  const bypassPatterns = [
    { pattern: /Nothing[-_]verification[-_]corpus/i, label: "verification-corpus path" },
    { pattern: /~\/Downloads|(?:homedir|Path\.home)\s*\(\)[^\\n]*Downloads/i, label: "Downloads path outside resolver" },
    { pattern: /(?:rglob|glob)\s*\([^)]*\.pdf/i, label: "direct PDF glob outside resolver" },
  ];
  for (const relPath of verificationFiles) {
    const source = read(relPath);
    for (const { pattern, label } of bypassPatterns) {
      if (pattern.test(source)) failures.push(`${relPath} bypasses the real-corpus resolver (${label})`);
    }
  }
  return failures;
}

function walk(dir, predicate, found = []) {
  for (const entry of fs.readdirSync(path.join(repoRoot, dir), { withFileTypes: true })) {
    const rel = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(rel, predicate, found);
    else if (predicate(rel)) found.push(rel);
  }
  return found;
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function lineOf(source, index) {
  return source.slice(0, index).split("\n").length;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isScreenId(value) {
  return typeof value === "string" && /^[A-Za-z][\w-]*$/.test(value);
}

function isCalendarDate(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00.000Z`);
  return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === value;
}

function readConvertedScreensConfig() {
  let raw;
  try {
    raw = JSON.parse(read(TRANSITION_CONFIG_PATH));
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return { screens: [], errors: [`Could not parse ${TRANSITION_CONFIG_PATH}: ${detail}`] };
  }

  if (!isPlainObject(raw) || Object.keys(raw).length !== 1 || !Array.isArray(raw.screens)) {
    return { screens: [], errors: [`${TRANSITION_CONFIG_PATH} must be { "screens": [...] }`] };
  }

  const errors = [];
  const screens = [];
  const screenIds = new Set();
  const rootSelectors = new Set();
  const ownedIds = new Set();

  for (const [index, entry] of raw.screens.entries()) {
    const label = `${TRANSITION_CONFIG_PATH}.screens[${index}]`;
    if (!isPlainObject(entry)) {
      errors.push(`${label} must be an object`);
      continue;
    }
    if (!isScreenId(entry.screenId)) errors.push(`${label}.screenId must be a valid screen id`);
    if (typeof entry.rootSelector !== "string" || !/^#[A-Za-z][\w-]*$/.test(entry.rootSelector)) {
      errors.push(`${label}.rootSelector must be an id selector such as #document-desk-screen`);
    }
    if (!Array.isArray(entry.ownedIds) || !entry.ownedIds.every(isScreenId)) {
      errors.push(`${label}.ownedIds must be an array of valid element ids`);
    }

    const graceEntries = entry.legacyReferenceGrace ?? [];
    if (!Array.isArray(graceEntries)) {
      errors.push(`${label}.legacyReferenceGrace must be an array when present`);
      continue;
    }
    const graceIds = new Set();
    for (const [graceIndex, grace] of graceEntries.entries()) {
      const graceLabel = `${label}.legacyReferenceGrace[${graceIndex}]`;
      if (!isPlainObject(grace) || Object.keys(grace).length !== 3) {
        errors.push(`${graceLabel} must contain id, expiresOn, and comment`);
        continue;
      }
      if (!isScreenId(grace.id)) errors.push(`${graceLabel}.id must be a valid element id`);
      if (!isCalendarDate(grace.expiresOn)) errors.push(`${graceLabel}.expiresOn must be a real YYYY-MM-DD date`);
      if (typeof grace.comment !== "string" || grace.comment.trim() === "") {
        errors.push(`${graceLabel}.comment must document the expiry reason`);
      }
      if (graceIds.has(grace.id)) errors.push(`${graceLabel}.id is duplicated`);
      graceIds.add(grace.id);
      if (!entry.ownedIds?.includes(grace.id)) {
        errors.push(`${graceLabel}.id must be listed in ${label}.ownedIds`);
      }
    }

    if (screenIds.has(entry.screenId)) errors.push(`${label}.screenId is duplicated: ${entry.screenId}`);
    if (rootSelectors.has(entry.rootSelector)) errors.push(`${label}.rootSelector is duplicated: ${entry.rootSelector}`);
    screenIds.add(entry.screenId);
    rootSelectors.add(entry.rootSelector);
    for (const id of entry.ownedIds ?? []) {
      if (ownedIds.has(id)) errors.push(`${label}.ownedIds duplicates another converted screen id: ${id}`);
      ownedIds.add(id);
    }
    screens.push(entry);
  }

  return { screens, errors };
}

function findElementSubtree(source, openingTagIndex, tagName) {
  const tagPattern = new RegExp(`<\\/?${escapeRegex(tagName)}\\b[^>]*>`, "g");
  tagPattern.lastIndex = openingTagIndex;
  let depth = 0;
  for (const match of source.matchAll(tagPattern)) {
    if (match.index < openingTagIndex) continue;
    if (match[0].startsWith("</")) {
      depth -= 1;
      if (depth === 0) return source.slice(openingTagIndex, match.index + match[0].length);
    } else if (!match[0].endsWith("/>")) {
      depth += 1;
    }
  }
  return source.slice(openingTagIndex);
}

function extractScreenRoots(defineFiles) {
  const roots = [];
  for (const file of defineFiles) {
    const source = read(file);
    for (const match of source.matchAll(/<([A-Za-z][\w.]*)\b[^>]*\bdata-screen-panel="([^"]+)"[^>]*>/g)) {
      const openingTag = match[0];
      const id = openingTag.match(/\bid="([^"]+)"/)?.[1] ?? null;
      const owner = openingTag.match(/\bdata-owner="([^"]+)"/)?.[1] ?? null;
      roots.push({
        file,
        line: lineOf(source, match.index),
        screenId: match[2],
        selector: id === null ? null : `#${id}`,
        owner,
        subtree: findElementSubtree(source, match.index, match[1]),
      });
    }
  }
  return roots;
}

function transitionDisciplineFailures({ config, screenRoots, compositionReferenceFiles }) {
  const failures = [...config.errors];
  const rootsBySelector = new Map(screenRoots.map((root) => [root.selector, root]));
  const convertedBySelector = new Map(config.screens.map((screen) => [screen.rootSelector, screen]));

  for (const root of screenRoots) {
    if (root.selector === null) {
      failures.push(`${root.file}:${root.line} screen root data-screen-panel="${root.screenId}" needs a static id for ownership checks`);
      continue;
    }
    const conversion = convertedBySelector.get(root.selector);
    if (conversion === undefined) {
      failures.push(`${root.file}:${root.line} ${root.selector} must be declared in ${TRANSITION_CONFIG_PATH}; the React transition is complete`);
      continue;
    }
    if (root.owner !== "react") {
      failures.push(`${root.file}:${root.line} ${root.selector} must declare data-owner="react"`);
    }
    if (conversion !== undefined && conversion.screenId !== root.screenId) {
      failures.push(`${root.file}:${root.line} ${root.selector} has data-screen-panel="${root.screenId}" but config declares screenId "${conversion.screenId}"`);
    }
    if (conversion !== undefined) {
      const marker = new RegExp(`\\b${escapeRegex(LEGACY_BINDING_MARKER)}(?:\\s*=|\\s|>)`);
      if (marker.test(root.subtree)) {
        failures.push(`${root.file}:${root.line} React-owned ${root.selector} contains ${LEGACY_BINDING_MARKER}`);
      }
    }
  }

  for (const screen of config.screens) {
    if (!rootsBySelector.has(screen.rootSelector)) {
      failures.push(`${TRANSITION_CONFIG_PATH} declares ${screen.rootSelector}, but no static screen root matches it`);
    }
  }

  const today = new Date().toISOString().slice(0, 10);
  for (const screen of config.screens) {
    const graceById = new Map(screen.legacyReferenceGrace?.map((entry) => [entry.id, entry]) ?? []);
    for (const id of screen.ownedIds) {
      const grace = graceById.get(id);
      if (grace !== undefined && grace.expiresOn < today) {
        failures.push(`${TRANSITION_CONFIG_PATH} grace for "${id}" expired on ${grace.expiresOn}: ${grace.comment}`);
      }
      if (grace !== undefined && grace.expiresOn >= today) continue;

      const reference = new RegExp(`(?<![A-Za-z0-9_-])${escapeRegex(id)}(?![A-Za-z0-9_-])`);
      for (const file of compositionReferenceFiles) {
        const source = read(file);
        const match = reference.exec(source);
        if (match !== null) {
          failures.push(`Application composition code references React-owned id "${id}" at ${file}:${lineOf(source, match.index)}`);
        }
      }
    }
  }

  return failures;
}

// ---------------------------------------------------------------------------
// Query-side extraction
// ---------------------------------------------------------------------------

/**
 * Parse a CSS selector string into requirements.
 * Only #id, [data-*] and [name="..."] tokens are contract-relevant;
 * tag/class-only selector parts are ignored.
 */
function selectorRequirements(selector) {
  const requirements = [];
  for (const idMatch of selector.matchAll(/#([A-Za-z][\w-]*)/g)) {
    requirements.push({ kind: "id", value: idMatch[1] });
  }
  for (const attrMatch of selector.matchAll(/\[(data-[a-z-]+|name)(?:="([^"]*)")?\]/g)) {
    const [, attr, value] = attrMatch;
    if (attr === "name") {
      if (value) requirements.push({ kind: "name", value });
    } else {
      requirements.push({ kind: "data", attr, value: value ?? null });
    }
  }
  return requirements;
}

function extractRuleIds(compositionSource) {
  const ids = new Set();
  for (const match of compositionSource.matchAll(/getRule\(\s*"([a-z_]+)"\s*\)/g)) ids.add(match[1]);
  for (const match of compositionSource.matchAll(/setRuleState\(\s*"([a-z_]+)"/g)) ids.add(match[1]);
  return ids.size > 0 ? [...ids] : FALLBACK_RULE_IDS;
}

function extractQueries(relPath, source, ruleIds) {
  const queries = []; // { requirement, file, line }
  const lineOf = (index) => source.slice(0, index).split("\n").length;
  const push = (selector, index) => {
    for (const requirement of selectorRequirements(selector)) {
      queries.push({ requirement, file: relPath, line: lineOf(index) });
    }
  };

  // $("...") and $('...')
  for (const match of source.matchAll(/\$\(\s*(["'])([^"'`]+)\1\s*\)/g)) push(match[2], match.index);
  // document/scope.querySelector("...") / querySelectorAll("...") incl. generics
  for (const match of source.matchAll(/querySelector(?:All)?(?:<[^>]*>)?\(\s*(["'])([^"'`]+)\1/g)) push(match[2], match.index);
  // getElementById("...")
  for (const match of source.matchAll(/getElementById\(\s*(["'])([^"'`]+)\1\s*\)/g)) push(`#${match[2]}`, match.index);
  // requiredElement<T>(scope, "#...")
  for (const match of source.matchAll(/requiredElement(?:<[^>]*>)?\(\s*[\w$.]+\s*,\s*(["'])([^"'`]+)\1/g)) push(match[2], match.index);

  // Dynamic pattern: $(`#rule-${id}`) — expand with rule ids from source.
  if (/\$\(\s*`#rule-\$\{/.test(source)) {
    for (const ruleId of ruleIds) {
      queries.push({ requirement: { kind: "id", value: `rule-${ruleId}` }, file: relPath, line: 0 });
    }
  }
  // Dynamic pattern: document.getElementById(step.id) over the workflow step
  // array literal in compositionRoot.ts — expand with the literal step ids.
  if (/getElementById\(\s*step\.id\s*\)/.test(source)) {
    for (const match of source.matchAll(/"(workflow-step-[a-z0-9-]+)"/g)) {
      queries.push({ requirement: { kind: "id", value: match[1] }, file: relPath, line: lineOf(match.index) });
    }
  }
  return queries;
}

// ---------------------------------------------------------------------------
// Define-side extraction (static JSX / HTML only)
// ---------------------------------------------------------------------------

function extractDefinitions(relPath, source, defined) {
  // id="..." plus *Id="..." pass-through props (Modal closeButtonId/titleId,
  // MetricCard id, ...) that components render as element ids.
  for (const match of source.matchAll(/\b(?:id|[a-zA-Z]+Id)="([^"]+)"/g)) {
    defined.ids.set(match[1], relPath);
  }
  for (const match of source.matchAll(/\b(data-[a-z-]+)="([^"]*)"/g)) {
    defined.dataAttrs.add(match[1]);
    defined.dataValues.add(`${match[1]}=${match[2]}`);
  }
  for (const match of source.matchAll(/\bname="([^"]+)"/g)) {
    defined.names.add(match[1]);
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const compositionFiles = walk("src/app", (file) => file.endsWith(".ts"));
const compositionReferenceFiles = walk("src/app", () => true);
const compositionSource = compositionFiles.map(read).join("\n");
const ruleIds = extractRuleIds(compositionSource);
const convertedScreensConfig = readConvertedScreensConfig();
const corpusFailures = realCorpusFailures();

const queryFiles = [
  ...compositionFiles,
  ...walk("src/features/save-gate", (file) => file.endsWith(".ts")),
  ...walk("src/features/document-batch", (file) => file.endsWith(".ts")),
  ...walk("src/features/keyword-dialog", (file) => file.endsWith(".ts")),
  ...walk("src/features/masking-run", (file) => file.endsWith(".ts")),
  ...walk("src/features/canvas-workbench", (file) => file.endsWith(".ts")),
];
const queries = queryFiles.flatMap((file) => extractQueries(file, read(file), ruleIds));

const defined = { ids: new Map(), dataAttrs: new Set(), dataValues: new Set(), names: new Set() };
const defineFiles = ["index.html", ...walk("src/components", (file) => file.endsWith(".tsx"))];
for (const file of defineFiles) extractDefinitions(file, read(file), defined);
const screenRoots = extractScreenRoots(defineFiles);

// v4: 좌측 레일이 폐지되어 rail-* 정의 소스(WorkspaceNavigationContext.tsx)가
// 삭제되었다. 상단 바(AppHeader.tsx)의 탭·기어가 [data-screen-target] 를 static
// JSX 로 직접 정의하므로 별도 추출이 필요 없다.

function requirementKey(requirement) {
  if (requirement.kind === "id") return `id:${requirement.value}`;
  if (requirement.kind === "name") return `name:${requirement.value}`;
  return requirement.value === null ? `data:${requirement.attr}` : `data:${requirement.attr}=${requirement.value}`;
}

function isSatisfied(requirement) {
  if (requirement.kind === "id") return defined.ids.has(requirement.value);
  if (requirement.kind === "name") return defined.names.has(requirement.value);
  if (requirement.value === null) return defined.dataAttrs.has(requirement.attr);
  return defined.dataValues.has(`${requirement.attr}=${requirement.value}`);
}

const verified = new Set();
const allowlisted = new Set();
const missing = new Map(); // key -> Set of "file:line"

for (const { requirement, file, line } of queries) {
  const key = requirementKey(requirement);
  if (DYNAMIC_DOM_ALLOWLIST.has(key)) {
    allowlisted.add(key);
    continue;
  }
  if (isSatisfied(requirement)) {
    verified.add(key);
  } else {
    if (!missing.has(key)) missing.set(key, new Set());
    missing.get(key).add(line > 0 ? `${file}:${line}` : `${file} (dynamic expansion)`);
  }
}

// Informational cross-check against RUNTIME_CONTRACT.md §2: report contract ids
// that no longer exist on the define side (doc drift), without failing.
try {
  const contractDoc = read("docs/RUNTIME_CONTRACT.md");
  const section = contractDoc.split(/^## 2\..*$/m)[1]?.split(/^## 3\./m)[0] ?? "";
  const documentedIds = [...section.matchAll(/`([a-z][\w-]*)`/g)]
    .map((match) => match[1])
    .filter((token) => !token.includes("=") && token.includes("-"));
  const drifted = [...new Set(documentedIds)].filter(
    (id) => !defined.ids.has(id) && !defined.names.has(id) && !DYNAMIC_DOM_ALLOWLIST.has(`id:${id}`),
  );
  if (drifted.length > 0) {
    console.log(`[info] RUNTIME_CONTRACT.md §2 ids not found in static JSX (doc drift, non-fatal): ${drifted.join(", ")}`);
  }
} catch {
  // docs missing — the fatal check above is the source of truth.
}

if (allowlisted.size > 0) {
  console.log(`[info] allowlisted dynamic-DOM selectors: ${[...allowlisted].join(", ")}`);
}

if (missing.size > 0) {
  console.error(`CONTRACT BROKEN — ${missing.size} selector(s) queried but not defined:`);
  for (const [key, sites] of [...missing.entries()].sort()) {
    console.error(`  - ${key}`);
    for (const site of sites) console.error(`      queried by ${site}`);
  }
  process.exit(1);
}

const missingRuntimeResources = checkRuntimeResourceCompleteness(repoRoot);
if (missingRuntimeResources.length > 0) {
  console.error(`CONTRACT BROKEN — ${missingRuntimeResources.length} masking runtime resource(s) missing from tauri.conf.json:`);
  for (const source of missingRuntimeResources) console.error(`  - ${source}`);
  process.exit(1);
}

if (corpusFailures.length > 0) {
  console.error(`CONTRACT BROKEN — ${corpusFailures.length} real-corpus contract violation(s):`);
  for (const failure of corpusFailures) console.error(`  - ${failure}`);
  process.exit(1);
}

const transitionFailures = transitionDisciplineFailures({
  config: convertedScreensConfig,
  screenRoots,
  compositionReferenceFiles,
});
if (transitionFailures.length > 0) {
  console.error(`CONTRACT BROKEN — ${transitionFailures.length} transition discipline violation(s):`);
  for (const failure of transitionFailures) console.error(`  - ${failure}`);
  process.exit(1);
}

console.log(`CONTRACT OK (${verified.size} selectors, ${screenRoots.length} screen ownership roots, and masking runtime resources verified)`);
