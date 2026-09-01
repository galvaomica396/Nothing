import { createHash } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const acceptanceDirectory = path.join(repoRoot, ".omo", "acceptance");
const defaultReportPath = path.join(repoRoot, ".omo", "evidence", "T53R-baseline.md");
const completionRule = "완료 선언은 gate:complete 통과로만";
const sourceRoots = ["src", "scripts", "src-tauri/src", "contracts"];
const localPythonBin = process.platform === "win32"
  ? path.join(repoRoot, ".venv", "Scripts")
  : path.join(repoRoot, ".venv", "bin");
const gateEnvironment = {
  ...process.env,
  PYTHONPATH: [repoRoot, path.join(repoRoot, "tests"), process.env.PYTHONPATH]
    .filter(Boolean)
    .join(path.delimiter),
};
const pythonTestEnvironment = {
  ...gateEnvironment,
  PATH: [localPythonBin, process.env.PATH].filter(Boolean).join(path.delimiter),
};

function normalizedRelativePath(relativePath) {
  return relativePath.replace(/[\\/]+/g, "/");
}

function isSourceFile(entryPath, entry) {
  if (entry.isFile()) return true;
  if (!entry.isSymbolicLink()) return false;
  try {
    return fs.statSync(entryPath).isFile();
  } catch {
    return false;
  }
}

function collectFiles(root, relativeDirectory, files) {
  const directoryPath = path.join(root, relativeDirectory);
  if (!fs.existsSync(directoryPath)) return;
  for (const entry of fs.readdirSync(directoryPath, { withFileTypes: true })) {
    if (entry.name === "__pycache__") continue;
    const relativePath = path.join(relativeDirectory, entry.name);
    const entryPath = path.join(root, relativePath);
    if (entry.isDirectory()) {
      collectFiles(root, relativePath, files);
    } else if (isSourceFile(entryPath, entry)) {
      files.push(normalizedRelativePath(relativePath));
    }
  }
}

export function sourceFiles(root = repoRoot) {
  const files = [];
  for (const sourceRoot of sourceRoots) collectFiles(root, sourceRoot, files);
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.name.toLowerCase().endsWith(".py")) continue;
    const entryPath = path.join(root, entry.name);
    if (isSourceFile(entryPath, entry)) files.push(normalizedRelativePath(entry.name));
  }
  return [...new Set(files)].sort();
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function sourceFingerprint(root = repoRoot) {
  const manifest = sourceFiles(root)
    .map((relativePath) => {
      const content = fs.readFileSync(path.join(root, relativePath));
      return `${relativePath}\t${sha256(content)}`;
    })
    .join("\n");
  return sha256(manifest);
}

export function gitHead(root = repoRoot) {
  return execFileSync("git", ["-C", root, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
}

export function acceptanceRecordPath(root, head) {
  return path.join(root, ".omo", "acceptance", `${head}.json`);
}

function standardBanner(status, detail = "") {
  const firstLine = detail === "" ? status : `${status} ${detail}`;
  return `${firstLine}\nACCEPTANCE: ${completionRule}`;
}

export function acceptanceBanner(root = repoRoot) {
  let head;
  try {
    head = gitHead(root);
  } catch {
    return standardBanner("ACCEPTANCE: MISSING — 완료 선언 불가 (npm run gate:complete)");
  }

  const recordPath = acceptanceRecordPath(root, head);
  if (!fs.existsSync(recordPath)) {
    return standardBanner("ACCEPTANCE: MISSING — 완료 선언 불가 (npm run gate:complete)");
  }

  let record;
  try {
    record = JSON.parse(fs.readFileSync(recordPath, "utf8"));
  } catch {
    return standardBanner("ACCEPTANCE: FAILED — 미완료", "(기록을 읽을 수 없음)");
  }

  if (
    record === null
    || typeof record !== "object"
    || Array.isArray(record)
    || record.head !== head
    || typeof record.source_fingerprint !== "string"
  ) {
    return standardBanner("ACCEPTANCE: FAILED — 미완료", "(기록 형식 오류)");
  }

  let currentFingerprint;
  try {
    currentFingerprint = sourceFingerprint(root);
  } catch {
    return standardBanner("ACCEPTANCE: STALE (검증된 코드와 현재 코드가 다름)");
  }
  if (record.source_fingerprint !== currentFingerprint) {
    return standardBanner("ACCEPTANCE: STALE (검증된 코드와 현재 코드가 다름)");
  }
  if (record.overall === "fail") {
    return standardBanner("ACCEPTANCE: FAILED — 미완료");
  }
  if (record.overall === "pass" && typeof record.finished_at === "string" && record.finished_at !== "") {
    return standardBanner(`ACCEPTANCE: OK ${head} ${record.finished_at}`);
  }
  return standardBanner("ACCEPTANCE: FAILED — 미완료", "(기록 형식 오류)");
}

function statusFromCell(cell) {
  return cell.match(/\b(PASS|FAIL|PENDING)\b/)?.[1] ?? "UNKNOWN";
}

function acceptanceColumn(header) {
  if (header === "alias") return "alias";
  if (header === "sha256") return "sha256";
  if (header.includes("저장 PDF")) return "finalPdf";
  if (header.includes("자동")) return "auto";
  if (header.includes("검토 대기")) return "pendingReview";
  if (header.includes("검토 행 제외")) return "reviewExclude";
  if (header.includes("다른 페이지")) return "otherPage";
  if (header.includes("수동 픽셀") || header.includes("수동 실제 텍스트")) return "manual";
  if (header.includes("복원/잔존")) return "restore";
  if (header.includes("키워드 픽셀")) return "keyword";
  if (header.includes("문서 판정")) return "document";
  if (header === "증거") return "evidence";
  return null;
}

function structuredAcceptanceResults(report) {
  const jsonBlock = report.match(/## Structured results\s+```json\s+([\s\S]*?)\s+```/);
  if (!jsonBlock) return null;
  let payload;
  try {
    payload = JSON.parse(jsonBlock[1]);
  } catch {
    return null;
  }
  if (
    payload === null
    || typeof payload !== "object"
    || payload.schemaVersion !== 1
    || !Array.isArray(payload.documents)
  ) return null;
  const results = [];
  for (const document of payload.documents) {
    if (
      document === null
      || typeof document !== "object"
      || !/^doc-\d+$/.test(document.alias ?? "")
      || !/^[a-f0-9]{64}$/i.test(document.sha256 ?? "")
      || !["PASS", "FAIL", "PENDING"].includes(document.status)
      || !document.checks
      || typeof document.checks !== "object"
    ) return null;
    const statusFor = (name) => {
      const value = document.checks[name];
      if (typeof value === "string") return value;
      if (value && typeof value === "object") return value.status;
      return "UNKNOWN";
    };
    results.push({
      alias: document.alias,
      sha256: document.sha256,
      status: document.status,
      checks: Object.fromEntries([
        "auto",
        "pendingReview",
        "reviewExclude",
        "otherPage",
        "manual",
        "restore",
        "keyword",
        "finalPdf",
      ].map((name) => [name, statusFor(name)])),
      attempts: Array.isArray(document.attempts) ? document.attempts : [],
      renderOutcome: typeof document.renderOutcome === "string"
        ? document.renderOutcome
        : "NOT_RUN",
    });
  }
  return results;
}

export function resultsPerDocument(root = repoRoot) {
  const reportPath = process.env.T53R_REPORT_PATH?.trim()
    ? path.resolve(root, process.env.T53R_REPORT_PATH)
    : path.join(root, ".omo", "evidence", "T53R-baseline.md");
  let report;
  try {
    report = fs.readFileSync(reportPath, "utf8");
  } catch {
    return [];
  }

  const structured = structuredAcceptanceResults(report);
  if (structured !== null) return structured;

  const results = [];
  const lines = report.split(/\r?\n/);
  const headerLine = lines.find((line) => line.startsWith("|")
    && line.split("|").slice(1, -1).some((cell) => cell.trim() === "alias")
    && line.split("|").slice(1, -1).some((cell) => cell.trim() === "sha256"));
  if (!headerLine) return results;
  const headers = headerLine.split("|").slice(1, -1).map((cell) => cell.trim());
  const columns = Object.fromEntries(headers.map((header, index) => [
    acceptanceColumn(header),
    index,
  ]).filter(([name]) => name !== null));
  const requiredColumns = ["alias", "sha256", "document"];
  if (requiredColumns.some((name) => columns[name] === undefined)) return results;
  for (const line of lines) {
    if (!line.startsWith("|")) continue;
    const cells = line.split("|").slice(1, -1).map((cell) => cell.trim());
    const alias = cells[columns.alias];
    const sha256 = cells[columns.sha256];
    if (
      cells.length !== headers.length
      || !/^doc-\d+$/.test(alias ?? "")
      || !/^[a-f0-9]{64}$/i.test(sha256 ?? "")
    ) continue;
    const cellStatus = (name) => columns[name] === undefined
      ? "UNKNOWN"
      : statusFromCell(cells[columns[name]]);
    results.push({
      alias,
      sha256,
      status: cellStatus("document"),
      checks: {
        auto: cellStatus("auto"),
        pendingReview: cellStatus("pendingReview"),
        reviewExclude: cellStatus("reviewExclude"),
        otherPage: cellStatus("otherPage"),
        manual: cellStatus("manual"),
        restore: cellStatus("restore"),
        keyword: cellStatus("keyword"),
        finalPdf: cellStatus("finalPdf"),
      },
    });
  }
  return results;
}

export function acceptancePreconditions(root = repoRoot) {
  const reportPath = process.env.T53R_REPORT_PATH?.trim()
    ? path.resolve(root, process.env.T53R_REPORT_PATH)
    : path.join(root, ".omo", "evidence", "T53R-baseline.md");
  let report;
  try {
    report = fs.readFileSync(reportPath, "utf8");
  } catch {
    return null;
  }
  const jsonBlock = report.match(/## Preconditions\s+```json\s+([\s\S]*?)\s+```/);
  if (!jsonBlock) return null;
  try {
    return JSON.parse(jsonBlock[1]);
  } catch {
    return null;
  }
}

const steps = [
  { label: "npm run contract", command: "npm", args: ["run", "contract"] },
  { label: "npm run contracts:check", command: "npm", args: ["run", "contracts:check"] },
  { label: "npm run tauri -- build", command: "npm", args: ["run", "tauri", "--", "build"] },
  { label: "npm run package:freshness", command: "npm", args: ["run", "package:freshness"] },
  { label: "pytest tests/", command: "pytest", args: ["tests/"] },
  { label: "cargo test", command: "cargo", args: ["test"], cwd: path.join(repoRoot, "src-tauri") },
  { label: "npm run qa:smoke", command: "npm", args: ["run", "qa:smoke"] },
  { label: "npm run qa:save", command: "npm", args: ["run", "qa:save"] },
  { label: "npm run qa:canvas", command: "npm", args: ["run", "qa:canvas"] },
  { label: "npm run qa:options", command: "npm", args: ["run", "qa:options"] },
  { label: "npm run accept:real", command: "npm", args: ["run", "accept:real"] },
];

function runStep(step) {
  console.error(`[gate:complete] ${step.label}`);
  let result;
  try {
    result = spawnSync(step.command, step.args, {
      cwd: step.cwd ?? repoRoot,
      env: step.label === "pytest tests/" ? pythonTestEnvironment : gateEnvironment,
      stdio: "inherit",
    });
  } catch (error) {
    console.error(`[gate:complete] ${step.label} could not start: ${error instanceof Error ? error.message : String(error)}`);
    return false;
  }
  if (result.error !== undefined) {
    console.error(`[gate:complete] ${step.label} could not start: ${result.error.message}`);
    return false;
  }
  if (result.status !== 0) {
    const suffix = result.signal === null ? `exit ${result.status}` : `signal ${result.signal}`;
    console.error(`[gate:complete] ${step.label} failed (${suffix})`);
    return false;
  }
  return true;
}

function writeAcceptanceRecord(record) {
  fs.mkdirSync(acceptanceDirectory, { recursive: true });
  const recordPath = acceptanceRecordPath(repoRoot, record.head);
  fs.writeFileSync(recordPath, `${JSON.stringify(record, null, 2)}\n`, "utf8");
  return recordPath;
}

export function runGate() {
  const startedAt = new Date().toISOString();
  const head = gitHead(repoRoot);
  const fingerprint = sourceFingerprint(repoRoot);
  let acceptedStepStarted = false;
  let passed = true;

  for (const step of steps) {
    if (step.label === "npm run accept:real") acceptedStepStarted = true;
    if (!runStep(step)) {
      passed = false;
      break;
    }
  }

  const record = {
    head,
    source_fingerprint: fingerprint,
    started_at: startedAt,
    finished_at: new Date().toISOString(),
    results_per_document: acceptedStepStarted ? resultsPerDocument(repoRoot) : [],
    acceptance_status: passed
      ? "pass"
      : acceptedStepStarted && acceptancePreconditions(repoRoot)?.status === "blocked"
        ? "pending"
        : "fail",
    preconditions: acceptedStepStarted ? acceptancePreconditions(repoRoot) : null,
    overall: passed ? "pass" : "fail",
  };
  const recordPath = writeAcceptanceRecord(record);
  console.error(`[gate:complete] recorded ${record.overall} acceptance at ${path.relative(repoRoot, recordPath)}`);
  return passed ? 0 : 1;
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = runGate();
  } catch (error) {
    console.error(`[gate:complete] ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}
