import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { readdir, readFile, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const REAL_CORPUS_MANIFEST_PATH = join(repoRoot, "contracts", "real-corpus.json");
export const REAL_CORPUS_SIZE = 15;
const SHA256_PATTERN = /^[a-f0-9]{64}$/i;
const CATEGORIES = new Set(["internal_review", "official_dispatch"]);
const FORBIDDEN_MANIFEST_KEYS = new Set(["filename", "fileName", "path", "absolutePath"]);

export class RealCorpusError extends Error {
  constructor(detail, problems = []) {
    super(`REAL_CORPUS_INCOMPLETE: ${detail}`);
    this.name = "RealCorpusError";
    this.code = "REAL_CORPUS_INCOMPLETE";
    this.problems = problems;
  }
}

function manifestLabel(index) {
  return `documents[${index}]`;
}

function normaliseManifest(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw) || !Array.isArray(raw.documents)) {
    throw new RealCorpusError("manifest must contain a documents array");
  }
  if (raw.documents.length !== REAL_CORPUS_SIZE) {
    throw new RealCorpusError(`manifest contains ${raw.documents.length} entries; expected ${REAL_CORPUS_SIZE}`);
  }

  const aliases = new Set();
  const hashes = new Set();
  const documents = [];
  const problems = [];
  for (const [index, value] of raw.documents.entries()) {
    const label = manifestLabel(index);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      problems.push(`${label} is not an object`);
      continue;
    }
    const forbidden = Object.keys(value).find((key) => FORBIDDEN_MANIFEST_KEYS.has(key));
    if (forbidden) {
      problems.push(`${label}.${forbidden} is forbidden`);
      continue;
    }
    const alias = typeof value.alias === "string" && /^[a-z][a-z0-9-]{1,31}$/.test(value.alias)
      ? value.alias
      : `doc-${String(index + 1).padStart(2, "0")}`;
    const sha256 = typeof value.sha256 === "string" ? value.sha256.toLowerCase() : "";
    const category = value.category;
    if (!SHA256_PATTERN.test(sha256)) problems.push(`${label}.sha256 is not a SHA-256 hash`);
    if (!CATEGORIES.has(category)) problems.push(`${label}.category is unsupported`);
    if (aliases.has(alias)) problems.push(`${label}.alias is duplicated`);
    if (hashes.has(sha256)) problems.push(`${label}.sha256 is duplicated`);
    aliases.add(alias);
    hashes.add(sha256);
    documents.push(Object.freeze({ alias, sha256, category }));
  }
  if (problems.length > 0) throw new RealCorpusError(problems.join("; "), problems);
  return Object.freeze(documents);
}

export function loadRealCorpusManifest() {
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(REAL_CORPUS_MANIFEST_PATH, "utf8"));
  } catch (error) {
    if (error instanceof RealCorpusError) throw error;
    throw new RealCorpusError(`manifest could not be read (${error instanceof Error ? error.name : "read-error"})`);
  }
  return normaliseManifest(parsed);
}

export async function sha256File(path) {
  const bytes = await readFile(path);
  return createHash("sha256").update(bytes).digest("hex");
}

async function collectPdfFiles(root) {
  const files = [];
  const pending = [root];
  while (pending.length > 0) {
    const current = pending.pop();
    let entries;
    try {
      entries = await readdir(current, { withFileTypes: true });
    } catch (error) {
      throw new RealCorpusError(`corpus directory cannot be scanned (${error instanceof Error ? error.name : "scan-error"})`);
    }
    for (const entry of entries) {
      const child = join(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(child);
      } else if (entry.isFile() && extname(entry.name).toLowerCase() === ".pdf") {
        files.push(child);
      }
    }
  }
  return files;
}

function formatProblems(problems) {
  return problems.map((problem) => {
    if (problem.kind === "duplicate") return `${problem.alias}: duplicate matching PDF`;
    return `${problem.alias}: expected hash ${problem.expected} was not found`;
  });
}

export async function resolveRealCorpus(options = {}) {
  const directoryValue = options.directory
    ?? process.env.NOTHING_REAL_CORPUS_DIR
    ?? join(homedir(), "Downloads");
  const directory = resolve(String(directoryValue));
  let directoryStat;
  try {
    directoryStat = await stat(directory);
  } catch (error) {
    throw new RealCorpusError(`corpus directory is unavailable (${error instanceof Error ? error.name : "stat-error"})`);
  }
  if (!directoryStat.isDirectory()) throw new RealCorpusError("corpus root is not a directory");

  const manifest = loadRealCorpusManifest();
  const pdfPaths = await collectPdfFiles(directory);
  const hashedFiles = [];
  for (const path of pdfPaths) {
    try {
      hashedFiles.push({ path, sha256: await sha256File(path) });
    } catch {
      // An unreadable PDF is not a candidate. The manifest comparison below
      // reports the corresponding alias as incomplete without exposing its
      // source filename.
    }
  }
  const byHash = new Map();
  for (const file of hashedFiles) {
    const matches = byHash.get(file.sha256) ?? [];
    matches.push(file);
    byHash.set(file.sha256, matches);
  }

  const problems = [];
  const resolved = [];
  for (const entry of manifest) {
    const matches = byHash.get(entry.sha256) ?? [];
    if (matches.length === 0) {
      problems.push({ alias: entry.alias, expected: entry.sha256, kind: "missing" });
      continue;
    }
    if (matches.length !== 1) {
      problems.push({ alias: entry.alias, expected: entry.sha256, kind: "duplicate" });
      continue;
    }
    resolved.push(Object.freeze({ ...entry, path: resolve(matches[0].path) }));
  }
  if (problems.length > 0) throw new RealCorpusError(formatProblems(problems), problems);

  const category = options.category;
  if (category !== undefined && !CATEGORIES.has(category)) {
    throw new RealCorpusError(`unsupported category filter ${String(category)}`);
  }
  const alias = options.alias;
  if (alias !== undefined && !resolved.some((entry) => entry.alias === alias)) {
    throw new RealCorpusError(`manifest alias ${String(alias)} was not found`);
  }
  return resolved.filter((entry) => (category === undefined || entry.category === category)
    && (alias === undefined || entry.alias === alias));
}

export async function resolveRealCorpusDocument(identifier, options = {}) {
  const documents = await resolveRealCorpus(options);
  return documents.find((entry) => entry.alias === identifier || entry.sha256 === String(identifier).toLowerCase()) ?? null;
}

function isMainModule() {
  return process.argv[1] !== undefined
    && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
}

if (isMainModule()) {
  const categoryIndex = process.argv.indexOf("--category");
  const aliasIndex = process.argv.indexOf("--alias");
  const category = categoryIndex >= 0 ? process.argv[categoryIndex + 1] : undefined;
  const alias = aliasIndex >= 0 ? process.argv[aliasIndex + 1] : undefined;
  resolveRealCorpus({ category, alias })
    .then((documents) => {
      process.stdout.write(`${JSON.stringify(documents.map(({ alias: value, sha256, category: label }) => ({
        alias: value,
        sha256,
        category: label,
      })))}\n`);
    })
    .catch((error) => {
      const code = error instanceof RealCorpusError ? error.code : "REAL_CORPUS_INCOMPLETE";
      const detail = error instanceof RealCorpusError ? error.message.replace(/^REAL_CORPUS_INCOMPLETE:\s*/, "") : "resolver failure";
      process.stderr.write(`${code}: ${detail}\n`);
      process.exitCode = 1;
    });
}
