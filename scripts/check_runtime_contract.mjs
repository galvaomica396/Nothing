// Static runtime-contract verifier (docs/RUNTIME_CONTRACT.md).
//
// Query side  : src/legacy/**/*.ts
//               + src/features/{save-gate,document-batch,keyword-dialog,
//                 masking-run,canvas-workbench,app-settings}/**/*.ts
//               ($("#id"), querySelector/querySelectorAll, getElementById,
//                requiredElement(scope, "#id"), `#rule-${id}` expansion)
//               Legacy DOM bindings may be extracted into dedicated modules;
//               all legacy TypeScript modules are scanned so their selectors
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

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

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

// Fallback rule-id list (RUNTIME_CONTRACT.md §2) used only if extraction from
// the scanned legacy TypeScript modules finds nothing.
const FALLBACK_RULE_IDS = [
  "rrn", "phone", "business_reg", "name", "address", "place", "legal_party",
  "company", "court", "case_title", "case_number", "law_firm", "attorney",
  "approval_line", "region_context", "doc_meta",
];

function read(relPath) {
  return fs.readFileSync(path.join(repoRoot, relPath), "utf8");
}

function walk(dir, predicate, found = []) {
  for (const entry of fs.readdirSync(path.join(repoRoot, dir), { withFileTypes: true })) {
    const rel = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(rel, predicate, found);
    else if (predicate(rel)) found.push(rel);
  }
  return found;
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

function extractRuleIds(legacySource) {
  const ids = new Set();
  for (const match of legacySource.matchAll(/getRule\(\s*"([a-z_]+)"\s*\)/g)) ids.add(match[1]);
  for (const match of legacySource.matchAll(/setRuleState\(\s*"([a-z_]+)"/g)) ids.add(match[1]);
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
  // array literal in startLegacyApp.ts — expand with the literal step ids.
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

const legacyFiles = walk("src/legacy", (file) => file.endsWith(".ts"));
const legacySource = legacyFiles.map(read).join("\n");
const ruleIds = extractRuleIds(legacySource);

const queryFiles = [
  ...legacyFiles,
  ...walk("src/features/save-gate", (file) => file.endsWith(".ts")),
  ...walk("src/features/document-batch", (file) => file.endsWith(".ts")),
  ...walk("src/features/keyword-dialog", (file) => file.endsWith(".ts")),
  ...walk("src/features/masking-run", (file) => file.endsWith(".ts")),
  ...walk("src/features/canvas-workbench", (file) => file.endsWith(".ts")),
  ...walk("src/features/app-settings", (file) => file.endsWith(".ts")),
];
const queries = queryFiles.flatMap((file) => extractQueries(file, read(file), ruleIds));

const defined = { ids: new Map(), dataAttrs: new Set(), dataValues: new Set(), names: new Set() };
const defineFiles = ["index.html", ...walk("src/components", (file) => file.endsWith(".tsx"))];
for (const file of defineFiles) extractDefinitions(file, read(file), defined);

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

console.log(`CONTRACT OK (${verified.size} selectors verified)`);
