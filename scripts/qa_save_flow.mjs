// Final-save advisory flow and failed-restore blocking QA.
//
// 확정된 정책(사용자 지시): **최종 저장은 항상 사용자 재량이다.** 검증(잔존 감지·
// 품질 게이트·복원 재검증)은 내부에서 계속 수행·기록되지만 결과는 "권고"로만
// 노출된다. 이전의 "하드 차단 3종 우회 불가" 정책은 폐기됐다. 마스킹본이 존재하면
// 저장 버튼은 활성이고, 경고 유무와 관계없이 저장 직전 확인 다이얼로그가 1회
// 뜬다("무시하고 그대로 저장"/"취소하고 검토하기"). 단, 복원 재검증 실패는
// 마스킹된 내용을 다시 노출할 수 있으므로 확인으로 우회할 수 없는 차단 상태다.
//
// 이 스크립트는 구성 가능한 Tauri IPC mock 위에서 전체 흐름(PDF → 기본 마스킹 →
// 저장)을 구동하며, 리포트 조합별로 다음을 단언한다:
//   - 경고가 없으면 준비 완료 상태의 다이얼로그를 확인한 뒤 finalize 가 호출된다.
//   - 경고가 있으면 저장 클릭 시 권고 상태로 열리고(그 시점 finalize 미호출),
//     경고 목록에 정확한 권고 문구가 표출되며, "무시하고 그대로 저장" 시 finalize 가
//     호출되어 저장이 완료된다.
//   - "취소하고 검토하기" 시 finalize 는 호출되지 않고 저장되지 않는다.
//   - 불변식: finalize(=사용자 폴더로의 확정 저장)는 사용자가 명시적으로 확정한
//     경우에만 호출된다(경고 상태에서 미확인/취소 시 미호출).
//   - 리포트 내부화: 저장이 성공해도 safe_report 는 사용자 폴더에 절대 복사되지 않는다.
//
// Public native evidence is captured directly from the packaged executable:
//   node scripts/qa_save_flow.mjs --scenario public-document-all --native-app-path /absolute/path/to/release.app --receipt-nonce <nonce> --threshold-artifact thresholds.json --threshold-digest <sha256>
//
// --runtime-receipt-channel is optional and only receives a validated durable copy.
// Browser IPC mocks remain advisory-only and cannot satisfy public scenarios.
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { unresolvedGeometryManifestForQa } from "./qa_tauri_mock.mjs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
function parseCliArgs(argv) {
  const allowed = new Set([
    "--scenario",
    "--native-app-path",
    "--runtime-receipt-channel",
    "--receipt-nonce",
    "--threshold-artifact",
    "--threshold-digest",
    "--url",
  ]);
  const args = new Map();
  for (let index = 2; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!allowed.has(name)) throw new Error(`QA_CLI_UNKNOWN_ARGUMENT:${name}`);
    if (args.has(name)) throw new Error(`QA_CLI_DUPLICATE_ARGUMENT:${name}`);
    if (!value || value.startsWith("--")) throw new Error(`QA_CLI_MISSING_VALUE:${name}`);
    args.set(name, value);
  }
  return args;
}

const args = parseCliArgs(process.argv);

const scenarioSelection = args.get("--scenario") ?? "legal-advisory";
const PUBLIC_SCENARIOS = new Set(["public-document-plumbing", "public-document-all"]);
if (scenarioSelection !== "legal-advisory" && !PUBLIC_SCENARIOS.has(scenarioSelection)) {
  throw new Error(`Unsupported --scenario: ${scenarioSelection}`);
}
if (PUBLIC_SCENARIOS.has(scenarioSelection)) {
  for (const obsoleteFlag of [
    "--native-lifecycle-receipt",
    "--native-receipt-nonce",
    "--native-binary-hash",
    "--native-receipt-auth-hash",
  ]) {
    if (args.has(obsoleteFlag)) throw new Error(`${obsoleteFlag} is not accepted for public native evidence`);
  }
}

const requestedUrl = args.get("--url");
if (requestedUrl) {
  const target = new URL(requestedUrl);
  if (!["localhost", "127.0.0.1", "[::1]"].includes(target.hostname)) {
    throw new Error("QA_URL_MUST_TARGET_LOCAL_CHECKOUT");
  }
}
let url;
const viewport = { width: 1440, height: 1080 };
const evidenceDir = path.join(repoRoot, "build", "save-flow-evidence");
const fixturePath = path.join(repoRoot, "tests", "fixtures", "phase6_non_sensitive.pdf");
const outputDir = path.join(evidenceDir, "output");
const previewDir = path.join(evidenceDir, "preview");

mkdirSync(evidenceDir, { recursive: true });
mkdirSync(outputDir, { recursive: true });
mkdirSync(previewDir, { recursive: true });
mkdirSync(path.join(repoRoot, "build", "redesign-evidence"), { recursive: true });

// ---------------------------------------------------------------------------
// 권고형 경고 문구(정확 문자열). src/features/save-gate/saveGate.ts finalSaveWarnings
// 와 1:1 대응해야 한다. {N}=건수.
// ---------------------------------------------------------------------------
const WARN = {
  residual: (count) => `잔존 개인정보 후보 ${count}건이 남아 있습니다. 보정 화면에서 확인하는 것을 권장합니다.`,
  missing: (count) => `마스킹되지 않은 대상 ${count}건이 있습니다. 보정 화면에서 확인하는 것을 권장합니다.`,
  quality: "자동 검증을 통과하지 못했습니다. 보정 화면에서 확인하는 것을 권장합니다.",
  advisory: "수동 검토가 권장되는 항목이 있습니다. 보정 화면에서 확인하는 것을 권장합니다.",
  restore: "복원 영역이 마스킹을 다시 노출할 수 있습니다. 보정 화면에서 확인하는 것을 권장합니다.",
};

// ---------------------------------------------------------------------------
// Safe-report scenarios. Each varies the product_checks / redaction / review
// combination the engine would return so we can prove the advisory flow behaves.
// ---------------------------------------------------------------------------

const SCENARIOS = [
  {
    // 경고 0건 → 준비 완료 다이얼로그 확인 뒤 저장된다.
    id: "clean-pass",
    label: "자동 검증 통과 (경고 0건) — 준비 완료 확인 후 저장",
    productChecks: { quality_gate_passed: true, needs_manual_review: false },
    redaction: { status: "ok", verification: { residual_hits: 0 }, missing_targets_count: 0 },
    expect: { warns: false, warnings: [], saves: true },
  },
  {
    // (구 residual-hard-block 재정의) 잔존>0 → 저장 클릭 → 다이얼로그에 경고 1
    // 표출 → "그대로 저장" → finalize 호출·저장 성공. 하드 차단 폐기 회귀 가드.
    id: "residual-warn-confirm-save",
    label: "잔존 개인정보 후보(경고 1) — 확인 다이얼로그 경유 그대로 저장",
    productChecks: { quality_gate_passed: true, needs_manual_review: false },
    redaction: { status: "unverified", verification: { residual_hits: 2 }, missing_targets_count: 0 },
    expect: { warns: true, warnings: [WARN.residual(2)], saves: true },
  },
  {
    // (구 quality-gate-fail 재정의) 품질 게이트 실패 → 경고 3 → "그대로 저장" →
    // finalize 호출·저장 성공.
    id: "quality-warn-confirm-save",
    label: "품질 게이트 실패(경고 3) — 확인 다이얼로그 경유 그대로 저장",
    productChecks: { quality_gate_passed: false, needs_manual_review: false },
    redaction: { status: "unverified", verification: { residual_hits: 0 }, missing_targets_count: 0 },
    expect: { warns: true, warnings: [WARN.quality], saves: true },
  },
  {
    // 자문(수동 검토 권장) → 경고 4 → "그대로 저장" → 저장 성공. 엔진이
    // needs_manual_review=true 를 켜되 final_submission_allowed 를 확정하지 않은
    // 자문 형태(omitFinalSubmissionAllowed)를 모사한다.
    id: "advisory-warn-confirm-save",
    label: "수동 검토 권장(경고 4) — 확인 다이얼로그 경유 그대로 저장",
    productChecks: { quality_gate_passed: true, needs_manual_review: true },
    omitFinalSubmissionAllowed: true,
    redaction: { status: "ok", verification: { residual_hits: 0 }, missing_targets_count: 0 },
    expect: { warns: true, warnings: [WARN.advisory], saves: true },
  },
  {
    // T36: 미해결 검토는 저장 전 확인으로만 진행한다. 이 시나리오는 경고가
    // 표시된 상태에서 확인 버튼을 눌러야 finalize가 호출되는 경로를 고정한다.
    id: "unresolved-review-confirm-save",
    label: "미해결 검토 경고 — 확인 버튼 경유 최종 저장",
    publicConfirmSave: true,
    productChecks: { quality_gate_passed: true, needs_manual_review: true },
    omitFinalSubmissionAllowed: true,
    redaction: { status: "ok", verification: { residual_hits: 0 }, missing_targets_count: 0 },
    expect: {
      warns: true,
      warnings: [
        "미가림 가능성: 결재선 · 2쪽 — 결재란 영역 자동확인 미완료 — 확인하고 저장",
        "미가림 가능성: staff · 2쪽 — 결재란 영역 자동확인 미완료 — 확인하고 저장",
      ],
      saves: true,
    },
  },
  {
    // 경고 상태에서 "취소" → finalize 미호출, 저장되지 않음. 저장은 사용자의
    // 명시적 확정으로만 일어난다는 불변식 가드.
    id: "warn-cancel-keeps-unsaved",
    label: "경고 상태에서 취소 — finalize 미호출·미저장",
    productChecks: { quality_gate_passed: true, needs_manual_review: false },
    redaction: { status: "unverified", verification: { residual_hits: 3 }, missing_targets_count: 0 },
    expect: { warns: true, warnings: [WARN.residual(3)], cancel: true, saves: false },
  },
  {
    // 리포트 내부화 고정: 저장이 성공(경고 0건)해도 finalize 는
    // copyReport:false 로 호출되어 safe_report 가 사용자 폴더에 미복사됨을 단언한다.
    id: "report-never-copied",
    label: "리포트 내부화 — 저장 성공 시에도 safe_report 는 사용자 폴더에 미복사",
    productChecks: { quality_gate_passed: true, needs_manual_review: false },
    redaction: { status: "ok", verification: { residual_hits: 0 }, missing_targets_count: 0 },
    assertReportNotCopied: true,
    expect: { warns: false, warnings: [], saves: true },
  },
];

// The real engine (document_masker_ocr_gui.py build_safe_report) ALWAYS sets
// final_submission_allowed = quality_gate_passed. Mirror that here so every mock
// report matches the exact product_checks shape the frontend save flow receives.
// Exception: scenario.omitFinalSubmissionAllowed leaves the field absent to
// exercise the advisory manual-review warning (final_submission_allowed !== true).
function buildProductChecks(scenario) {
  const checks = { ...scenario.productChecks };
  if (!scenario.omitFinalSubmissionAllowed && checks.final_submission_allowed === undefined) {
    checks.final_submission_allowed = checks.quality_gate_passed === true;
  }
  return checks;
}

function buildReport(scenario) {
  return {
    product_checks: buildProductChecks(scenario),
    document_redaction: scenario.redaction,
    pdf_redaction: scenario.redaction,
  };
}
const PUBLIC_DOCUMENT_STEPS = [
  "public_analyze_completed",
  "public_mixed_boundary_blocked",
  "public_ambiguous_common_only_blocked",
  "public_scan_manual_review_required",
  "public_repeated_occurrence_scoped",
  "public_review_cards_resolved",
  "public_manual_combined_resolved",
  "public_legal_advisory_isolated",
  "public_unresolved_review_blocked",
  "public_unresolved_review_confirmed",
  "public_stale_revision_blocked",
  "public_stale_manifest_hash_blocked",
  "public_tampered_manifest_blocked",
  "public_forged_resolution_blocked",
  "public_intrinsic_failure_blocked",
  "public_destination_bypass_blocked",
  "public_destination_authorized",
  "public_destination_token_issued",
  "public_threshold_hash_bound",
  "public_clean_document_verified",
  "public_atomic_promotion_failure_blocked",
  "public_finalize_promoted",
];
const PUBLIC_DOCUMENT_PLUMBING_STEPS = [
  "public_analyze_completed",
  "public_unresolved_review_blocked",
  "public_unresolved_review_confirmed",
  "public_stale_revision_blocked",
  "public_stale_manifest_hash_blocked",
  "public_tampered_manifest_blocked",
  "public_forged_resolution_blocked",
  "public_destination_bypass_blocked",
  "public_destination_authorized",
  "public_destination_token_issued",
  "public_threshold_hash_bound",
  "public_atomic_promotion_failure_blocked",
  "public_finalize_promoted",
];

function isHash(value) {
  return typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
}
function publicReceiptPiiSafe(value) {
  const forbiddenKeys = new Set([
    "inputFile", "outputPath", "destination", "finalPath", "path", "locator",
    "runtime_channel", "stderr", "stdout", "error", "details", "app_path", "executable",
  ]);
  const forbiddenText = /(?:\b(?:\d{6}-?\d{7}|01\d-?\d{3,4}-?\d{4}|[^\s@]+@[^\s@]+\.[^\s@]+\b)|(?:^|[\s"'])\/(?:[^\s"']+)|(?:[A-Za-z]:[\\/]))/;
  if (Array.isArray(value)) return value.every((item) => publicReceiptPiiSafe(item));
  if (value && typeof value === "object") {
    return Object.entries(value).every(([name, item]) => (
      !forbiddenKeys.has(name) && publicReceiptPiiSafe(item)
    ));
  }
  if (typeof value === "string") {
    if (isHash(value)) return true;
    return !forbiddenText.test(value);
  }
  return value === null || ["boolean", "number"].includes(typeof value);
}


function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

function canonicalHash(value) {
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}
function publicReceiptAuth(receipt) {
  return canonicalHash({
    domain: "DocumentMaskerNativeQaReceiptAuthV1",
    nonce: receipt.nonce,
    binaryHash: receipt.binaryHash,
    canonicalReceiptHash: receipt.canonicalReceiptHash,
    actions: receipt.actions.map((action) => ({
      requestHash: action.requestHash,
      resultHash: action.resultHash,
      requestEvidence: action.requestEvidence,
      resultEvidence: action.resultEvidence,
    })),
  });
}


function requiredPublicSteps(selection) {
  return selection === "public-document-all" ? PUBLIC_DOCUMENT_STEPS : PUBLIC_DOCUMENT_PLUMBING_STEPS;
}

function validateThresholdArtifact(thresholdArtifact, pinnedDigest, receipt) {
  if (!thresholdArtifact) return { ok: false, reason: "THRESHOLD_ARTIFACT_UNAVAILABLE" };
  if (!isHash(pinnedDigest)) return { ok: false, reason: "THRESHOLD_DIGEST_UNPINNED" };
  let artifactBytes;
  let artifact;
  try {
    artifactBytes = readFileSync(thresholdArtifact);
    artifact = JSON.parse(artifactBytes.toString("utf8"));
  } catch {
    return { ok: false, reason: "THRESHOLD_ARTIFACT_INVALID" };
  }
  if (createHash("sha256").update(artifactBytes).digest("hex") !== pinnedDigest) return { ok: false, reason: "THRESHOLD_DIGEST_MISMATCH" };
  if (!artifact || artifact.schemaVersion !== 1 || typeof artifact.thresholdVersion !== "string" || !isHash(artifact.thresholdHash) || !isHash(artifact.thresholdValueHash)) return { ok: false, reason: "THRESHOLD_ARTIFACT_INVALID" };
  if (
    receipt.thresholdVersion !== artifact.thresholdVersion
    || receipt.thresholdHash !== artifact.thresholdHash
    || receipt.thresholdValueHash !== artifact.thresholdValueHash
  ) return { ok: false, reason: "THRESHOLD_BINDING_MISMATCH" };
  return { ok: true };
}

const PUBLIC_ACTION_SEMANTICS = {
  public_analyze_completed: { outcome: "pass", errorCode: null },
  public_mixed_boundary_blocked: { outcome: "blocked", errorCode: "MIXED_BOUNDARY_REVIEW_REQUIRED" },
  public_ambiguous_common_only_blocked: { outcome: "blocked", errorCode: "AMBIGUOUS_COMMON_ONLY_REVIEW_REQUIRED" },
  public_scan_manual_review_required: { outcome: "pass", errorCode: null },
  public_repeated_occurrence_scoped: { outcome: "pass", errorCode: null },
  public_review_cards_resolved: { outcome: "pass", errorCode: null },
  public_manual_combined_resolved: { outcome: "pass", errorCode: null },
  public_legal_advisory_isolated: { outcome: "pass", errorCode: null },
  public_unresolved_review_blocked: { outcome: "blocked", errorCode: "UNRESOLVED_REVIEW" },
  public_unresolved_review_confirmed: { outcome: "pass", errorCode: null },
  public_stale_revision_blocked: { outcome: "blocked", errorCode: "STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED" },
  public_stale_manifest_hash_blocked: { outcome: "blocked", errorCode: "STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED" },
  public_tampered_manifest_blocked: { outcome: "blocked", errorCode: "PUBLIC_FINALIZE_REJECTED" },
  public_forged_resolution_blocked: { outcome: "blocked", errorCode: "REVIEW_RESOLUTION_REJECTED" },
  public_intrinsic_failure_blocked: { outcome: "blocked", errorCode: "INTRINSIC_VERIFICATION_FAILED" },
  public_destination_bypass_blocked: { outcome: "blocked", errorCode: "PUBLIC_FINALIZE_REJECTED" },
  public_destination_authorized: { outcome: "pass", errorCode: null },
  public_destination_token_issued: { outcome: "pass", errorCode: null },
  public_threshold_hash_bound: { outcome: "pass", errorCode: null },
  public_clean_document_verified: { outcome: "pass", errorCode: null },
  public_atomic_promotion_failure_blocked: { outcome: "blocked", errorCode: "ATOMIC_PROMOTION_FAILED" },
  public_finalize_promoted: { outcome: "pass", errorCode: null },
};

function expectedActionHash(phase, receipt, action) {
  return canonicalHash({
    phase,
    scenario: receipt.scenario,
    name: action.name,
    outcome: action.outcome,
    errorCode: action.errorCode,
    requestEvidence: action.requestEvidence,
    resultEvidence: action.resultEvidence,
    nonce: receipt.nonce,
    binaryHash: receipt.binaryHash,
    runId: receipt.runId,
    analysisRevision: receipt.analysisRevision,
    manifestHash: receipt.manifestHash,
    thresholdVersion: receipt.thresholdVersion,
    thresholdHash: receipt.thresholdHash,
    thresholdValueHash: receipt.thresholdValueHash,
  });
}

function validPublicAction(action, expectedName, receipt) {
  const expectedKeys = ["errorCode", "name", "outcome", "requestEvidence", "requestHash", "resultEvidence", "resultHash"];
  const semantics = PUBLIC_ACTION_SEMANTICS[expectedName];
  const request = action?.requestEvidence;
  const result = action?.resultEvidence;
  const validHash = (value) => isHash(value);
  return Boolean(
    semantics
    && action
    && typeof action === "object"
    && Object.keys(action).sort().join(",") === expectedKeys.join(",")
    && action.name === expectedName
    && action.outcome === semantics.outcome
    && action.errorCode === semantics.errorCode
    && request && typeof request === "object"
    && Object.keys(request).sort().join(",") === ["actualRequest", "fixtureHash", "operationCode", "requestEvidenceHash"].join(",")
    && request.operationCode === expectedName
    && validHash(request.fixtureHash)
    && request.actualRequest && typeof request.actualRequest === "object"
    && request.requestEvidenceHash === canonicalHash(request.actualRequest)
    && result && typeof result === "object"
    && Object.keys(result).sort().join(",") === ["actualResult", "count", "observed", "resultCode", "resultEvidenceHash"].join(",")
    && result.observed === true
    && Number.isInteger(result.count) && result.count >= 0
    && result.actualResult && typeof result.actualResult === "object"
    && result.resultEvidenceHash === canonicalHash(result.actualResult)
    && isHash(action.requestHash)
    && isHash(action.resultHash)
    && action.requestHash === expectedActionHash("request", receipt, action)
    && action.resultHash === expectedActionHash("result", receipt, action)
    && action.requestHash !== action.resultHash
  );
}

function validPublicReceipt(receipt, selection) {
  const requiredSteps = requiredPublicSteps(selection);
  const expectedKeys = [
    "actions", "analysisRevision", "binaryHash", "canonicalReceiptHash", "manifestHash", "nonce",
    "receiptAuth", "runId", "scenario", "scenarioSteps", "schema", "schemaVersion", "thresholdHash",
    "thresholdValueHash", "thresholdVersion",
  ];
  if (!receipt || typeof receipt !== "object" || Object.keys(receipt).sort().join(",") !== expectedKeys.join(",")) {
    return { ok: false, reason: "runtime receipt schema is invalid" };
  }
  if (
    receipt.schema !== "PublicActionReceiptV1"
    || receipt.schemaVersion !== 1
    || receipt.scenario !== selection
    || typeof receipt.nonce !== "string"
    || receipt.nonce.length < 32
    || !isHash(receipt.binaryHash)
    || typeof receipt.runId !== "string"
    || receipt.runId.length === 0
    || !Number.isInteger(receipt.analysisRevision)
    || receipt.analysisRevision < 1
    || !isHash(receipt.manifestHash)
    || typeof receipt.thresholdVersion !== "string"
    || receipt.thresholdVersion.length === 0
    || !isHash(receipt.thresholdHash)
    || !isHash(receipt.thresholdValueHash)
    || !Array.isArray(receipt.scenarioSteps)
    || !Array.isArray(receipt.actions)
    || receipt.scenarioSteps.length !== requiredSteps.length
    || receipt.scenarioSteps.some((step, index) => step !== requiredSteps[index])
    || !publicReceiptPiiSafe(receipt)
    || !isHash(receipt.receiptAuth)
    || !isHash(receipt.canonicalReceiptHash)
    || receipt.canonicalReceiptHash !== canonicalHash(Object.fromEntries(Object.entries(receipt).filter(([key]) => key !== "canonicalReceiptHash" && key !== "receiptAuth")))
    || receipt.receiptAuth !== publicReceiptAuth(receipt)
  ) return { ok: false, reason: "runtime receipt identity, threshold binding, or scenario steps are invalid" };
  if (
    receipt.actions.length !== requiredSteps.length
    || receipt.actions.some((action, index) => !validPublicAction(action, requiredSteps[index], receipt))
    || new Set(receipt.actions.map((action) => action.name)).size !== requiredSteps.length
  ) return { ok: false, reason: "runtime receipt actions are malformed, substituted, duplicated, or out of scenario order" };
  return { ok: true };
}
function validHarnessReceipt(receipt, selection, harnessReceiptHash) {
  return validPublicReceipt(receipt, selection).ok
    && isHash(harnessReceiptHash)
    && canonicalHash(receipt) === harnessReceiptHash;
}



function sealPublicReceipt(receipt) {
  const unsigned = Object.fromEntries(
    Object.entries(receipt).filter(([key]) => key !== "canonicalReceiptHash" && key !== "receiptAuth"),
  );
  receipt.canonicalReceiptHash = canonicalHash(unsigned);
  receipt.receiptAuth = publicReceiptAuth(receipt);
  return receipt;
}

function publicReceiptNegativeCases() {
  const receipt = {
    schema: "PublicActionReceiptV1", schemaVersion: 1, scenario: "public-document-plumbing",
    nonce: "harness-issued-nonce-with-sufficient-length", binaryHash: "a".repeat(64),
    runId: "run-1", analysisRevision: 1, manifestHash: "b".repeat(64),
    thresholdVersion: "threshold-v1", thresholdHash: "c".repeat(64), thresholdValueHash: "d".repeat(64),
    scenarioSteps: requiredPublicSteps("public-document-plumbing"),
    actions: [],
  };
  receipt.actions = receipt.scenarioSteps.map((name) => {
    const [outcome, errorCode] = [PUBLIC_ACTION_SEMANTICS[name].outcome, PUBLIC_ACTION_SEMANTICS[name].errorCode];
    const requestEvidence = {
      operationCode: name, fixtureHash: "e".repeat(64), actualRequest: {},
      requestEvidenceHash: canonicalHash({}),
    };
    const resultEvidence = {
      resultCode: errorCode ?? "OBSERVATION_CONFIRMED", observed: true, count: 0,
      actualResult: {}, resultEvidenceHash: canonicalHash({}),
    };
    const action = { name, outcome, errorCode, requestEvidence, resultEvidence, requestHash: "", resultHash: "" };
    action.requestHash = expectedActionHash("request", receipt, action);
    action.resultHash = expectedActionHash("result", receipt, action);
    return action;
  });
  sealPublicReceipt(receipt);
  const fullyResignedForgery = {
    ...receipt,
    runId: "forged-run",
    actions: receipt.actions.map((action) => ({ ...action })),
  };
  fullyResignedForgery.actions = fullyResignedForgery.actions.map((action) => ({
    ...action,
    requestHash: expectedActionHash("request", fullyResignedForgery, action),
    resultHash: expectedActionHash("result", fullyResignedForgery, action),
  }));
  sealPublicReceipt(fullyResignedForgery);
  const evidenceSubstitution = {
    ...receipt,
    actions: receipt.actions.map((action, index) => index === 0 ? {
      ...action,
      requestEvidence: { ...action.requestEvidence, fixtureHash: "f".repeat(64) },
    } : { ...action }),
  };
  evidenceSubstitution.actions[0].requestHash = expectedActionHash(
    "request", evidenceSubstitution, evidenceSubstitution.actions[0],
  );
  evidenceSubstitution.actions[0].resultHash = expectedActionHash(
    "result", evidenceSubstitution, evidenceSubstitution.actions[0],
  );
  sealPublicReceipt(evidenceSubstitution);
  const replay = {
    ...receipt,
    nonce: "different-harness-issued-nonce-with-sufficient-length",
    actions: receipt.actions.map((action) => ({ ...action })),
  };
  replay.actions = replay.actions.map((action) => ({
    ...action,
    requestHash: expectedActionHash("request", replay, action),
    resultHash: expectedActionHash("result", replay, action),
  }));
  sealPublicReceipt(replay);
  const expectedHarnessHash = canonicalHash(receipt);
  return validHarnessReceipt(receipt, "public-document-plumbing", expectedHarnessHash)
    && publicReceiptPiiSafe("0".repeat(64))
    && !publicReceiptPiiSafe("01012345678")
    && !validHarnessReceipt(fullyResignedForgery, "public-document-plumbing", expectedHarnessHash)
    && !validHarnessReceipt(evidenceSubstitution, "public-document-plumbing", expectedHarnessHash)
    && !validHarnessReceipt(replay, "public-document-plumbing", expectedHarnessHash);
}


function publicLifecycleEvidence(selection) {
  const appPath = args.get("--native-app-path");
  const runtimeReceiptChannel = args.get("--runtime-receipt-channel");
  const receiptNonce = args.get("--receipt-nonce");
  const thresholdArtifact = args.get("--threshold-artifact");
  const thresholdDigest = args.get("--threshold-digest");
  const result = {
    scenario: selection,
    status: "fail",
    evidenceAuthority: "native_app_emitted_receipt",
    piiSafe: true,
    runtimeReceiptPresent: false,
    directNativeReceiptCaptured: false,
    thresholdArtifactPresent: false,
    requiredSteps: requiredPublicSteps(selection),
    provenSteps: [],
    failure: "native app receipt evidence unavailable",
  };
  if (
    typeof appPath !== "string" || !path.isAbsolute(appPath)
    || typeof receiptNonce !== "string" || receiptNonce.length < 32
  ) return result;
  if (runtimeReceiptChannel !== undefined && !path.isAbsolute(runtimeReceiptChannel)) {
    result.failure = "runtime receipt channel must be an absolute durable-copy path";
    return result;
  }
  const harnessArgs = [
    path.join(repoRoot, "scripts", "e2e_tauri_local_smoke.py"),
    "--repo-root", repoRoot,
    "--app-path", appPath,
    "--scenario", selection,
    "--receipt-nonce", receiptNonce,
    "--threshold-artifact", thresholdArtifact ?? "",
    "--threshold-digest", thresholdDigest ?? "",
  ];
  if (typeof runtimeReceiptChannel === "string" && path.isAbsolute(runtimeReceiptChannel)) {
    harnessArgs.push("--runtime-receipt-channel", runtimeReceiptChannel);
  }
  const harnessTimeoutMs = selection === "public-document-all" ? 600_000 : 300_000;
  const harness = spawnSync(
    "python3",
    harnessArgs,
    {
      cwd: repoRoot,
      encoding: "utf8",
      timeout: harnessTimeoutMs,
      killSignal: "SIGKILL",
      maxBuffer: 1024 * 1024,
    },
  );
  if (harness.error || harness.status !== 0) {
    result.failure = "native Analyze→Resolve→Finalize harness did not produce successful evidence";
    return result;
  }
  let harnessResult;
  try {
    harnessResult = JSON.parse(harness.stdout);
  } catch {
    result.failure = "trusted native harness emitted malformed evidence";
    return result;
  }
  if (
    !harnessResult
    || typeof harnessResult !== "object"
    || harnessResult.status !== "pass"
    || harnessResult.scenario !== selection
    || harnessResult.runtime?.status !== "pass"
    || harnessResult.public_document_lifecycle?.status !== "pass"
    || harnessResult.pii_safe !== true
  ) {
    result.failure = "trusted native harness lifecycle evidence is incomplete";
    return result;
  }
  const receipt = harnessResult.public_action_receipt;
  if (!validHarnessReceipt(receipt, selection, harnessResult.harness_receipt_hash)) {
    result.failure = "trusted native harness receipt is invalid or substituted";
    return result;
  }
  result.runtimeReceiptPresent = true;
  result.directNativeReceiptCaptured = true;
  result.provenSteps = receipt.actions.map((action) => action.name);

  const threshold = validateThresholdArtifact(thresholdArtifact, thresholdDigest, receipt);
  result.thresholdArtifactPresent = threshold.ok;
  if (!threshold.ok) {
    result.failure = threshold.reason;
    return result;
  }
  result.status = "pass";
  result.failure = null;
  return result;
}

// ---------------------------------------------------------------------------
// Dev server
// ---------------------------------------------------------------------------
async function isDevServerUp(target) {
  try {
    return (await fetch(target, { signal: AbortSignal.timeout(1500) })).ok;
  } catch {
    return false;
  }
}

function childViteUrl(output) {
  const match = output.match(/Local:\s+(https?:\/\/[^\s]+)/);
  return match?.[1] ?? null;
}

async function ensureDevServer() {
  console.log("[dev] starting isolated checkout-owned vite dev server...");
  const child = spawn("npx", ["vite", "--host", "127.0.0.1", "--port", "0", "--strictPort"], {
    cwd: repoRoot,
    stdio: ["ignore", "pipe", "pipe"],
    detached: false,
  });
  let output = "";
  child.stdout.on("data", (chunk) => { output += chunk; });
  child.stderr.on("data", (chunk) => { output += chunk; });
  const deadline = Date.now() + 60_000;
  try {
    while (Date.now() < deadline) {
      if (child.exitCode !== null) throw new Error(`vite dev exited early (code ${child.exitCode}):\n${output}`);
      const childUrl = childViteUrl(output);
      if (childUrl && await isDevServerUp(childUrl)) return { child, url: childUrl };
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error(`vite dev did not publish a ready child-bound URL within 60s:\n${output}`);
  } catch (error) {
    if (child.exitCode === null) child.kill("SIGTERM");
    throw error;
  }
}

function isMissingSystemChrome(error) {
  return error instanceof Error && /(executable (does not exist|was not found)|browserType\.launch: Executable)/i.test(error.message);
}

function launchDiagnostic(error) {
  return error instanceof Error ? error.message : String(error);
}

async function launchBrowser() {
  try {
    const browser = await chromium.launch({ channel: "chrome", headless: true });
    return { browser, selection: "system-chrome" };
  } catch (systemChromeError) {
    if (!isMissingSystemChrome(systemChromeError)) {
      throw new Error(`system Chrome launch failed: ${launchDiagnostic(systemChromeError)}`);
    }
    try {
      const browser = await chromium.launch({ headless: true });
      return { browser, selection: "bundled-chromium", systemChromeDiagnostic: launchDiagnostic(systemChromeError) };
    } catch (bundledChromiumError) {
      throw new Error(`browser launch failed; system Chrome: ${launchDiagnostic(systemChromeError)}; bundled Chromium: ${launchDiagnostic(bundledChromiumError)}`);
    }
  }
}
async function createQaPage(browser, errors) {
  const page = await browser.newPage({ viewport });
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console:${message.text()}`);
  });
  page.on("pageerror", (error) => errors.push(`pageerror:${error.message}`));
  return page;
}

// ---------------------------------------------------------------------------
// Page helpers
// ---------------------------------------------------------------------------
const FINALIZE_PAYLOAD_KEYS = [
  "copyReport",
  "extractedPath",
  "maskedPath",
  "originalPdf",
  "outputPath",
  "previewPdf",
  "reportPath",
  "saveToken",
];

function isExactFinalizePayload(payload) {
  return Object.keys(payload ?? {}).sort().join(",") === FINALIZE_PAYLOAD_KEYS.join(",")
    && Object.prototype.hasOwnProperty.call(payload, "copyReport")
    && payload.copyReport === false
    && payload.extractedPath === ""
    && typeof payload.maskedPath === "string"
    && typeof payload.saveToken === "string"
    && payload.saveToken.length > 0;
}
function createMockState(options = {}) {
  return {
    deletedPaths: new Set(),
    existingPaths: new Set([fixturePath]),
    finalizedPaths: [],
    finalizationCount: 0,
    tokens: new Map(),
    tokenCounter: 0,
    saveTokenCounter: 0,
    canvasLaunchAttempts: [],
    applyInputs: [],
    applyOperations: [],
    failFinalReadPending: options.failFinalRead === true,
    failCanvasTokenPending: options.failCanvasToken === true,
  };
}

function isPathWithin(candidate, directory) {
  const resolvedCandidate = path.resolve(candidate);
  const resolvedDirectory = path.resolve(directory);
  return resolvedCandidate === resolvedDirectory || resolvedCandidate.startsWith(`${resolvedDirectory}${path.sep}`);
}

function isDeletedMockPath(mockState, candidate) {
  return mockState.deletedPaths.has(path.resolve(candidate));
}

async function installMock(page, report, options = {}) {
  const pdfBytes = [...readFileSync(fixturePath)];
  const mockState = options.mockState ?? createMockState(options);
  // Restore-revalidation outcome for this scenario: "passed" (safe restore) or
  // "failed" (restore re-exposed a masked region). Mask-only applies never reach
  // this — the real backend returns no revalidation report for them.
  const manualOutcome = options.manualOutcome ?? "passed";
  let failManualPreviewLoad = options.failManualPreviewLoad === true;
  const finalizeDelayMs = options.finalizeDelayMs ?? 0;
  const applyDelayMs = options.applyDelayMs ?? 0;
  const publicManifest = options.publicManifest ?? null;
  // The restore-revalidation safe report this scenario's backend would have
  // written. Hoisted so read_text_file (frontend adoption) reads the SAME report
  // bytes the real backend produced. A failed restore report keeps blocking
  // fields (residual/missing/quality) and must not reach finalization.
  const manualRevalReport =
    manualOutcome === "failed"
      ? {
          product_checks: { quality_gate_passed: false, needs_manual_review: true, final_submission_allowed: false },
          document_redaction: {
            status: "manual_revalidation_failed",
            missing_targets_count: 1,
            verification: { verified: false, residual_hits: 1, reason_code: "manual_restore_reexposure" },
          },
        }
      : {
          product_checks: { quality_gate_passed: true, needs_manual_review: false, final_submission_allowed: true },
          document_redaction: { status: "manual_revalidated", missing_targets_count: 0, verification: { verified: true, residual_hits: 0 } },
        };
  const invokeLog = [];
  const finalizeCalls = [];
  let selectedFinalTarget = null;
  await page.exposeFunction("__qaSaveInvoke", async (cmd, payload = {}) => {
    invokeLog.push(cmd);
    switch (cmd) {
      case "pick_input_pdf":
      case "pick_input_document":
        return fixturePath;
      case "pick_input_documents":
        return [fixturePath];
      case "default_output_dir_for_document":
      case "pick_output_dir":
        return outputDir;
      case "choose_final_pdf_path":
        selectedFinalTarget = {
          outputPath: `${outputDir}/${String(payload.defaultFileName || "masked")}.pdf`,
          saveToken: (++mockState.saveTokenCounter).toString(16).padStart(32, "0"),
        };
        return { ...selectedFinalTarget };
      case "get_preview_workdir":
        return previewDir;
      case "read_pdf_bytes":
        return pdfBytes;
      case "read_text_file":
        if (String(payload.path || "").includes("manual_revalidation.safe_report.json")) {
          // Mirrors the real backend contract: a restore-bearing apply performs
          // an actual revalidation and writes a passing OR blocking safe report.
          return JSON.stringify(manualRevalReport);
        }
        if (String(payload.path || "").includes("safe_report.json")) {
          return JSON.stringify(report);
        }
        return "원문 REVIEW TOKEN\n마스킹 [KEYWORD]";
      case "run_masking_pipeline": {
        const maskedPreview = `${outputDir}/masked.pdf`;
        mockState.existingPaths.add(path.resolve(maskedPreview));
        return {
          extracted_path: "",
          masked_path: "",
          report_path: `${outputDir}/safe_report.json`,
          extracted_text: "",
          masked_text: "",
          runtime_manifest: {
            outputs: {
              preview_pdf_source_file: fixturePath,
              masked_pdf_file: maskedPreview,
              safe_report_path: `${outputDir}/safe_report.json`,
              extracted_file: null,
              masked_file: null,
            },
          },
          report,
        };
      }
      case "analyze_masking_run":
        if (!publicManifest) throw new Error("QA_PUBLIC_ANALYZE_UNAVAILABLE");
        return publicManifest;
      case "finalize_masking_run": {
        const request = payload.request;
        if (
          !publicManifest
          || request?.runId !== publicManifest.runId
          || request?.analysisRevision !== publicManifest.analysisRevision
          || request?.manifestHash !== publicManifest.manifestHash
          || request?.warningsConfirmed !== true
          || !selectedFinalTarget
          || request?.destination !== selectedFinalTarget.outputPath
          || request?.saveToken !== selectedFinalTarget.saveToken
        ) throw new Error("QA_PUBLIC_FINALIZE_REQUEST_REJECTED");
        const finalOutput = selectedFinalTarget.outputPath;
        selectedFinalTarget = null;
        mockState.finalizationCount += 1;
        mockState.existingPaths.add(path.resolve(finalOutput));
        mockState.finalizedPaths.push(path.resolve(finalOutput));
        finalizeCalls.push({ cmd, finalOutput, rawPayload: { ...payload }, copied_files: [], copyReport: false });
        const manuallyReplacedOccurrences = new Set(
          publicManifest.manualActions.flatMap((action) => action.linkedOccurrenceId === null ? [] : [action.linkedOccurrenceId]),
        );
        const finalizedMaskCount = new Set(publicManifest.occurrences
          .filter((occurrence) => occurrence.proposedAction === "mask"
            && (occurrence.state === "confirmed" || occurrence.state === "user_confirmed")
            && !manuallyReplacedOccurrences.has(occurrence.occurrenceId))
          .map((occurrence) => occurrence.occurrenceId)).size
          + publicManifest.manualActions.filter((action) => action.mode === "mask").length;
        const manualMaskCount = publicManifest.manualActions.filter((action) => action.mode === "mask").length;
        const restoreCount = publicManifest.manualActions.filter((action) => action.mode === "restore").length;
        const unresolvedReviews = publicManifest.reviewItems
          .filter((item) => item.status === "pending")
          .map((item) => {
            const occurrence = publicManifest.occurrences.find((candidate) => candidate.occurrenceId === item.targetId);
            const region = publicManifest.regions.find((candidate) => candidate.regionId === item.targetId);
            return {
              kind: item.kind,
              targetId: item.targetId,
              category: occurrence?.category ?? region?.kind ?? item.kind,
              pageStart: item.pageStart,
              pageEnd: item.pageEnd,
              reasonCodes: item.reasonCodes,
            };
          });
        return {
          runId: publicManifest.runId,
          analysisRevision: publicManifest.analysisRevision,
          manifestHash: publicManifest.manifestHash,
          finalPath: finalOutput,
          finalHash: "6".repeat(64),
          finalHashAttested: true,
          occurrenceCount: finalizedMaskCount,
          appliedMaskCount: finalizedMaskCount,
          manualMaskCount,
          restoreCount,
          effectiveMaskCount: finalizedMaskCount,
          restoreAuthorization: {
            actionIdHash: restoreCount > 0 ? "c".repeat(64) : "0".repeat(64),
            targetOccurrenceIdHash: restoreCount > 0 ? "d".repeat(64) : "0".repeat(64),
            authorizationEvent: "none",
          },
          saveConfirmation: {
            status: unresolvedReviews.length === 0 ? "not_required" : "user_confirmed",
            unresolvedReviews,
          },
          status: "promoted",
        };
      }
      case "apply_manual_boxes": {
        if (applyDelayMs > 0) {
          await new Promise((resolve) => setTimeout(resolve, applyDelayMs));
        }
        const inputPdf = String(payload.inputPdf || "");
        const originalPdf = String(payload.originalPdf || "");
        mockState.applyInputs.push(inputPdf);
        if (isDeletedMockPath(mockState, inputPdf)) {
          throw new Error("APPLY_SOURCE_REJECTED: preview was deleted after final save");
        }
        const boxes = payload.boxes ?? [];
        mockState.applyOperations.push({
          inputPdf,
          originalPdf,
          maskCount: boxes.filter((box) => box.mode === "mask").length,
          restoreCount: boxes.filter((box) => box.mode === "restore").length,
        });
        const restoreApplied = boxes.filter((box) => box.mode === "restore").length;
        const requiresRevalidation = restoreApplied > 0;
        const manualPreview = `${previewDir}/manual_preview_${mockState.applyInputs.length}.pdf`;
        mockState.existingPaths.add(path.resolve(manualPreview));
        const result = {
          status: "applied",
          output_file: manualPreview,
          mask_count: boxes.filter((box) => box.mode === "mask").length,
          restore_count: restoreApplied,
          applied_count: boxes.length,
          mask_boxes_applied: boxes.filter((box) => box.mode === "mask").length,
          unmask_boxes_applied: restoreApplied,
          skipped_boxes: 0,
          warnings: [],
          requires_revalidation: requiresRevalidation,
        };
        if (requiresRevalidation) {
          const passed = manualOutcome !== "failed";
          result.revalidation_report = `${previewDir}/manual_preview.manual_revalidation.safe_report.json`;
          result.revalidation_status = passed ? "passed" : "failed";
        }
        return result;
      }
      case "finalize_manual_output_to_selected_path": {
        if (manualOutcome === "failed") {
          throw new Error("RESTORE_REVALIDATION_FAILED");
        }
        // Rust의 exact-path 최종 저장과 동일하게 네이티브 저장 창에서 선택한
        // 정확한 PDF 경로만 허용한다.
        const previewPdf = String(payload.previewPdf || "");
        const selectedOutputPath = String(payload.outputPath || "");
        const selectedSaveToken = String(payload.saveToken || "");
        const canonicalPreview = previewPdf ? path.resolve(previewPdf) : "";
        const canonicalOutput = selectedOutputPath ? path.resolve(selectedOutputPath) : "";
        const confirmedTarget = selectedFinalTarget;
        selectedFinalTarget = null;
        if (
          !confirmedTarget
          || canonicalOutput !== path.resolve(confirmedTarget.outputPath)
          || selectedSaveToken !== confirmedTarget.saveToken
        ) {
          throw new Error("SAVE_OUTPUT_PATH_REJECTED: 저장 경로를 확인할 수 없습니다.");
        }
        const previewIsRegistered =
          canonicalPreview.startsWith(`${path.resolve(outputDir)}${path.sep}`)
          || canonicalPreview.startsWith(`${path.resolve(previewDir)}${path.sep}`);
        if (!previewIsRegistered) {
          throw new Error("SAVE_SOURCE_REJECTED: 저장 원본을 확인할 수 없습니다.");
        }
        // v4.1: Rust finalize copy_report 기본값 false 이고 프론트는 항상
        // copyReport:false 로 호출한다. 리포트는 사용자 산출 폴더로 복사되지 않으므로
        // copied_files 에는 safe_report 가 포함되면 안 된다(내부 리포트는 임시폴더에만).
        if (
          isDeletedMockPath(mockState, canonicalPreview)
          || !mockState.existingPaths.has(canonicalPreview)
          || path.extname(canonicalPreview).toLowerCase() !== ".pdf"
        ) {
          throw new Error("SAVE_SOURCE_REJECTED: preview is missing, deleted, or not a PDF.");
        }
        const copyReport = payload.copyReport === true;
        const copiedFiles = copyReport ? [`${outputDir}/safe_report.json`] : [];
        mockState.finalizationCount += 1;
        const finalOutput = confirmedTarget.outputPath;
        mockState.existingPaths.add(path.resolve(finalOutput));
        mockState.finalizedPaths.push(path.resolve(finalOutput));
        if (isPathWithin(canonicalPreview, previewDir)) {
          mockState.deletedPaths.add(canonicalPreview);
          mockState.existingPaths.delete(canonicalPreview);
        }
        finalizeCalls.push({
          cmd,
          previewPdf,
          finalOutput,
          copyReport,
          copied_files: copiedFiles,
          rawPayload: { ...payload },
        });
        if (finalizeDelayMs > 0) {
          await new Promise((resolve) => setTimeout(resolve, finalizeDelayMs));
        }
        return { final_output_file: finalOutput, copied_files: copiedFiles };
      }
      case "create_canvas_launch_token": {
        const tokenPayload = payload.payload ?? {};
        const targetPath = String(tokenPayload.targetPath || "");
        mockState.canvasLaunchAttempts.push(targetPath);
        if (
          targetPath
          && (
            isDeletedMockPath(mockState, targetPath)
            || !mockState.existingPaths.has(path.resolve(targetPath))
          )
        ) {
          throw new Error("CANVAS_SOURCE_REJECTED: target PDF is missing or deleted");
        }
        if (mockState.failCanvasTokenPending) {
          mockState.failCanvasTokenPending = false;
          throw new Error("CANVAS_SOURCE_REJECTED: injected final candidate failure");
        }
        const token = `qa-token-${++mockState.tokenCounter}`;
        mockState.tokens.set(token, tokenPayload);
        return token;
      }
      case "take_canvas_launch_payload": {
        const token = String(payload.token || "");
        const tokenPayload = mockState.tokens.get(token) ?? null;
        mockState.tokens.delete(token);
        return tokenPayload;
      }
      case "plugin:opener|open_path":
      case "open_mask_canvas_window":
        return "ok";
      default:
        throw new Error(`QA_UNKNOWN_IPC:${cmd}`);
    }
  });
  await page.exposeFunction("__qaSaveCheckPdfPath", async (requestedPath = "") => {
    invokeLog.push("read_pdf_bytes");
    const canonicalPath = path.resolve(String(requestedPath || ""));
    if (
      isDeletedMockPath(mockState, canonicalPath)
      || !mockState.existingPaths.has(canonicalPath)
      || path.extname(canonicalPath).toLowerCase() !== ".pdf"
    ) {
      throw new Error("READ_FAILED: PDF path is missing, deleted, or unauthorized");
    }
    if (mockState.failFinalReadPending && mockState.finalizedPaths.includes(canonicalPath)) {
      mockState.failFinalReadPending = false;
      throw new Error("READ_FAILED: injected final successor load failure");
    }
    if (failManualPreviewLoad && canonicalPath.includes("manual_preview_")) {
      failManualPreviewLoad = false;
      throw new Error("READ_FAILED: injected manual preview load failure");
    }
    return true;
  });
  await page.addInitScript(({ localPdfBytes }) => {
    window.confirm = () => true;
    let callbackId = 1;
    window.__TAURI_INTERNALS__ = {
      plugins: { path: { sep: "/", delimiter: ":" } },
      // Keep large PDF bytes in-page. The Node-side probe validates existence,
      // deletion, extension, and injected failures without serializing the PDF
      // through the Playwright bridge on every render.
      invoke: (cmd, payload) => {
        if (cmd === "read_pdf_bytes") {
          const pathValue = String(payload?.path ?? "");
          return window.__qaSaveCheckPdfPath(pathValue).then(() => localPdfBytes);
        }
        return window.__qaSaveInvoke(cmd, payload);
      },
      transformCallback: (callback) => {
        const id = callbackId++;
        window[`__qa_callback_${id}`] = callback;
        return id;
      },
      unregisterCallback: () => {},
    };
    window.__TAURI__ = { core: { invoke: window.__TAURI_INTERNALS__.invoke } };
  }, { localPdfBytes: pdfBytes });
  return { invokeLog, finalizeCalls, mockState };
}

async function waitForStatus(page, pattern, timeout = 60_000) {
  await page.waitForFunction(
    (source) => new RegExp(source).test(document.querySelector("#status")?.textContent ?? ""),
    pattern.source,
    { timeout },
  );
  return page.locator("#status").innerText();
}

async function pickPdfAndMask(page, profile = "legal") {
  // React markup can be attached a few ticks before the composition root finishes
  // binding its handlers. Waiting for the bootstrap terminal status makes each
  // isolated scenario deterministic instead of racing the first click.
  await page.waitForFunction(
    () => (document.querySelector("#status")?.textContent ?? "").includes("대기 중: PDF 열기"),
    undefined,
    { timeout: 20_000 },
  );
  const deskPicker = page.locator("#btn-desk-open-pdf");
  if (await deskPicker.isVisible()) await deskPicker.click();
  else await page.locator("#btn-pick-pdf").click();
  await page.waitForFunction((expected) => document.querySelector("#input-path")?.value === expected, fixturePath, { timeout: 20_000 });
  // This suite exercises the legacy legal-report advisory/manual-redaction flow.
  // Desk selection may synchronize its own profile, so set legal after picking.
  await page.locator("#profile").evaluate((element, selectedProfile) => {
    element.value = selectedProfile;
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }, profile);
  const loadStatus = await waitForStatus(page, /원문 PDF 로드 완료|문서 로드 실패/, 20_000);
  if (loadStatus.includes("실패")) throw new Error(`document load failed: ${loadStatus}`);
  await page.locator("#btn-run-masking").click();
  // 완료 문구는 프로필별로 다르다. legal 리포트는 "마스킹 완료(...)"로 끝나고,
  // 공공(official_dispatch) 분석은 "마스킹 분석 완료: 검토 항목 N건"으로 끝난다.
  // 실패 문구는 두 경로 모두 "마스킹 실패 (...)"로 동일하다.
  const completionPattern = profile === "official_dispatch"
    ? /마스킹 분석 완료|마스킹 실패/
    : /마스킹 완료|마스킹 실패/;
  const status = await waitForStatus(page, completionPattern);
  if (status.includes("실패")) throw new Error(`base masking failed: ${status}`);
  // 후속 보정 도구 활성화는 두 경로 공통으로 성립한다. 공공 분석도 resultDoc
  // (원문 PDF)을 세우므로 canEdit=true 가 되어 마스크 도구가 활성화된다.
  await page.waitForFunction(
    () => document.querySelector("#btn-canvas-tool-mask")?.disabled === false,
    undefined,
    { timeout: 20_000 },
  );
}


async function saveButtonState(page) {
  return page.evaluate(() => {
    const controls = ["#btn-save"].map((selector) => {
      const element = document.querySelector(selector);
      const visible = element instanceof HTMLElement && getComputedStyle(element).display !== "none"
        && getComputedStyle(element).visibility !== "hidden" && element.getClientRects().length > 0;
      return {
        selector,
        visible,
        disabled: element instanceof HTMLButtonElement ? element.disabled : true,
        title: element?.getAttribute("title") ?? "",
      };
    });
    const visible = controls.filter((control) => control.visible);
    return {
      disabled: visible.length === 0 || visible.some((control) => control.disabled),
      title: visible.length > 0 ? visible[0].title : `no-visible-controls:${JSON.stringify(controls)}`,
      readiness: document.querySelector("#final-save-readiness")?.textContent ?? "",
    };
  });
}

// 저장 전 확인 다이얼로그의 현재 상태를 읽는다. 신설 ID(final-save-warning-list,
// final-save-dialog-state)로 단언하며, 권고 항목(dm-savewarn__item)만 warnings 로
// 추린다(빈 상태 dm-savewarn__empty 는 제외).
async function finalSaveDialogState(page) {
  return page.evaluate(() => {
    const dialog = document.querySelector("#final-save-dialog");
    const open = Boolean(dialog) && !dialog.classList.contains("is-hidden");
    const listEl = document.querySelector("#final-save-warning-list");
    const rows = listEl
      ? [...listEl.querySelectorAll("li")].map((li) => ({
          text: (li.textContent ?? "").trim(),
          empty: li.classList.contains("dm-savewarn__empty"),
        }))
      : [];
    const cancelBtn = document.querySelector("#btn-dialog-cancel-save");
    const confirmBtn = document.querySelector("#btn-dialog-save-all");
    return {
      open,
      badge: (document.querySelector("#final-save-dialog-state")?.textContent ?? "").trim(),
      warnings: rows.filter((row) => !row.empty).map((row) => row.text),
      emptyRows: rows.filter((row) => row.empty).map((row) => row.text),
      cancelPresent: Boolean(cancelBtn),
      cancelText: (cancelBtn?.textContent ?? "").trim(),
      confirmText: (confirmBtn?.textContent ?? "").trim(),
      confirmDisabled: confirmBtn?.disabled === true,
      cancelSecondary: cancelBtn?.classList.contains("dm-btn--ghost") === true && cancelBtn?.classList.contains("dm-btn--danger") !== true,
      confirmPrimary: confirmBtn?.classList.contains("dm-btn--primary") === true && confirmBtn?.classList.contains("dm-btn--danger") !== true,
    };
  });
}

async function publicConfirmSaveSnapshot(page, invokeLog, stage) {
  return {
    stage,
    controls: await page.evaluate(() => ["#btn-save", "#btn-dialog-save-all"].map((selector) => {
      const element = document.querySelector(selector);
      return {
        selector,
        present: element instanceof HTMLElement,
        disabled: element instanceof HTMLButtonElement ? element.disabled : null,
        text: element?.textContent?.trim() ?? "",
      };
    })),
    readiness: await page.evaluate(() => document.querySelector("#final-save-readiness")?.textContent ?? ""),
    dialog: await finalSaveDialogState(page),
    invokes: [...invokeLog],
  };
}

// 저장 버튼을 클릭하고, 경고 유무와 관계없이 확인 다이얼로그가 열리거나
// 전제 미충족 상태 문구가 렌더될 때까지 결정적으로 기다린다.
async function clickSaveAndSettle(page, expectedSelector) {
  const controls = await page.evaluate(() => ["#btn-save"].map((selector) => {
    const element = document.querySelector(selector);
    const visible = element instanceof HTMLElement && getComputedStyle(element).display !== "none"
      && getComputedStyle(element).visibility !== "hidden" && element.getClientRects().length > 0;
    return { selector, visible, disabled: element instanceof HTMLButtonElement ? element.disabled : null };
  }));
  const expected = controls.find((control) => control.selector === expectedSelector);
  const visible = controls.filter((control) => control.visible);
  if (!expected?.visible || visible.some((control) => control.disabled !== expected.disabled)) {
    throw new Error(`Expected a visible, state-consistent final-save control (${expectedSelector}), got ${JSON.stringify(controls)}`);
  }
  if (expected.disabled) return { clicked: false };
  await page.locator(expectedSelector).click();
  await page.waitForFunction(
    () => {
      const dialog = document.querySelector("#final-save-dialog");
      const open = Boolean(dialog) && !dialog.classList.contains("is-hidden");
      const status = document.querySelector("#status")?.textContent ?? "";
      return open || /최종 저장 완료|최종 저장 실패|파일은 저장되었으나|저장할 마스킹본이 없습니다|차단되었습니다|재검증.*실패|복원.*재검증/.test(status);
    },
    undefined,
    { timeout: 40_000 },
  );
  return { clicked: true };
}

async function confirmDialogSave(page) {
  await page.locator("#btn-dialog-save-all").click();
  const status = await waitForStatus(page, /최종 저장 완료|최종 저장 실패|파일은 저장되었으나/, 40_000);
  const successDialog = page.locator("#finalization-success-dialog");
  if (status.includes("완료") && await successDialog.isVisible()) {
    await page.locator("#btn-close-finalization-success-dialog").click();
    await successDialog.waitFor({ state: "hidden" });
  }
  return { saved: status.includes("완료"), status };
}

async function clickAndConfirmSave(page, expectedSelector) {
  const result = await clickSaveAndSettle(page, expectedSelector);
  if (result.clicked) await confirmDialogSave(page);
  return result;
}

async function cancelDialogSave(page) {
  await page.locator("#btn-dialog-cancel-save").click();
  await page.waitForFunction(
    () => {
      const dialog = document.querySelector("#final-save-dialog");
      return !dialog || dialog.classList.contains("is-hidden");
    },
    undefined,
    { timeout: 10_000 },
  );
}

// Drag on the visible PDF to draw a manual box (same primitive the canvas QA uses).
async function dragOnPdf(page, fromFrac, toFrac) {
  const box = await page.locator("#pdf-canvas-result").boundingBox();
  if (!box) throw new Error("#pdf-canvas-result has no bounding box");
  const start = { x: box.x + box.width * fromFrac.x, y: box.y + box.height * fromFrac.y };
  const end = { x: box.x + box.width * toFrac.x, y: box.y + box.height * toFrac.y };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move((start.x + end.x) / 2, (start.y + end.y) / 2, { steps: 6 });
  await page.mouse.move(end.x, end.y, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(120);
}

async function selectCanvasTool(page, toolId) {
  const tool = page.locator(`#${toolId}`);
  if (!(await tool.isVisible())) await page.locator("#canvas-tool-menu-trigger").click();
  await tool.click();
}

async function totalBoxCount(page) {
  const counts = await Promise.all(
    ["#review-summary-mask-count", "#review-summary-restore-count"].map(async (selector) => {
      const text = (await page.locator(selector).textContent()) ?? "";
      return Number(text.match(/(\d+)\s*개/)?.[1] ?? Number.NaN);
    }),
  );
  return counts.every(Number.isFinite) ? counts.reduce((sum, count) => sum + count, 0) : Number.NaN;
}

async function waitForInvoke(invokeLog, cmd, timeout = 30_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (invokeLog.includes(cmd)) return true;
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
  throw new Error(`invoke '${cmd}' not observed within ${timeout}ms (log=${invokeLog.join(",")})`);
}

async function waitForApplyComplete(page, invokeLog) {
  await waitForInvoke(invokeLog, "apply_manual_boxes");
  await page.waitForFunction(
    () => {
      const status = document.querySelector("#status")?.textContent ?? "";
      const terminalStatus = /\(미리보기\):|재검증 필요|복원 반영됨|실패/.test(status);
      const applyReady = document.querySelector("#btn-canvas-apply")?.disabled === false;
      const saveReady = document.querySelector("#btn-save")?.disabled === false;
      const saveBlocked = document.querySelector("#final-save-readiness")?.getAttribute("data-state") === "blocked";
      return terminalStatus && (applyReady || saveReady || saveBlocked);
    },
    undefined,
    { timeout: 30_000 },
  );
}

function finalizeCount(invokeLog) {
  return invokeLog.filter((cmd) => cmd === "finalize_manual_output_to_selected_path" || cmd === "finalize_masking_run").length;
}

function saveDialogCount(invokeLog) {
  return invokeLog.filter((cmd) => cmd === "choose_final_pdf_path").length;
}

// ---------------------------------------------------------------------------
// Manual-correction save scenarios. After applying a
// manual correction:
//   · mask-only add   → no revalidation needed, no warnings → ready confirmation.
//   · restore add (passed) → revalidation auto-runs, clean report adopted → confirmation.
//   · restore add (failed) → 재노출 가능성이므로 저장과 native 경로 선택을 차단한다.
// saveVia distinguishes the two ways an apply reaches the save flow:
//   · "button-apply" — the user clicks 반영(apply) first, then 저장(save).
//   · "auto-apply"   — the user skips apply and clicks 저장 directly; saveFinalOutput
//                      auto-applies the pending boxes, then evaluates warnings.
// ---------------------------------------------------------------------------
const MANUAL_SCENARIOS = [
  {
    id: "manual-mask-only-keeps-gate",
    label: "수동 마스킹 박스만 추가 → 반영 후 저장: 준비 완료 확인 후 저장 성공",
    tool: "mask",
    manualOutcome: "passed",
    saveVia: "button-apply",
    expect: { warns: false, saves: true },
  },
  {
    id: "manual-mask-only-autoapply-saves",
    label: "수동 마스킹 박스만 추가 → 반영 없이 저장: 자동 반영 뒤 확인 후 저장 성공",
    tool: "mask",
    manualOutcome: "passed",
    saveVia: "auto-apply",
    expect: { warns: false, saves: true },
  },
  {
    id: "manual-restore-auto-revalidate",
    label: "복원 포함(재검증 통과) → 반영 후 저장: 통과 리포트 채택, 경고 없이 저장 성공",
    tool: "restore",
    manualOutcome: "passed",
    saveVia: "button-apply",
    expect: { warns: false, saves: true },
  },
  {
    id: "manual-restore-reexposure-blocked",
    label: "복원 재노출(반영 후 저장) → 재검증 실패로 저장 차단",
    tool: "restore",
    manualOutcome: "failed",
    saveVia: "button-apply",
    expect: { blocked: true, saves: false },
  },
  {
    id: "manual-restore-reexposure-autoapply-blocked",
    label: "복원 재노출(반영 없이 바로 저장) → 자동 반영 후 저장 차단",
    tool: "restore",
    manualOutcome: "failed",
    saveVia: "auto-apply",
    expect: { blocked: true, saves: false },
  },
];

async function runManualScenario(browser, scenario) {
  const consoleErrors = [];
  const page = await createQaPage(browser, consoleErrors);
  const result = { id: scenario.id, label: scenario.label, checks: [], pass: true };
  const check = (name, ok, detail) => {
    result.checks.push({ name, ok, detail });
    if (!ok) result.pass = false;
  };
  try {
    const cleanReport = buildReport(SCENARIOS[0]);
    const manualLoadFailureProbe = scenario.id === "manual-mask-only-keeps-gate";
    const { invokeLog } = await installMock(page, cleanReport, {
      manualOutcome: scenario.manualOutcome,
      failManualPreviewLoad: manualLoadFailureProbe,
    });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });

    await pickPdfAndMask(page);

    // 저장 버튼은 마스킹본이 있으면 항상 활성이다(사용자 재량).
    const saveOpenBefore = await saveButtonState(page);
    check("save-enabled-when-masked", saveOpenBefore.disabled === false, `title=${saveOpenBefore.title}`);

    await selectCanvasTool(page, `btn-canvas-tool-${scenario.tool}`);
    await dragOnPdf(page, { x: 0.15, y: 0.2 }, { x: 0.6, y: 0.4 });
    check("manual-box-drawn", (await totalBoxCount(page)) === 1, "one manual box created");

    const baseRunsBeforeApply = invokeLog.filter((cmd) => cmd === "run_masking_pipeline").length;

    if (scenario.saveVia === "button-apply") {
      await page.locator("#btn-canvas-apply").click();
      await waitForApplyComplete(page, invokeLog);
      if (manualLoadFailureProbe) {
        check("failed-manual-load-keeps-box", (await totalBoxCount(page)) === 1, "one box retained after failed preview load");
        check("failed-manual-load-does-not-finalize", finalizeCount(invokeLog) === 0, `count=${finalizeCount(invokeLog)}`);
        await page.locator("#btn-canvas-apply").click();
        await waitForStatus(page, /\(미리보기\):/, 30_000);
      }
      // Core regression: recovering the flow must NOT require re-running base masking.
      const baseRunsAfterApply = invokeLog.filter((cmd) => cmd === "run_masking_pipeline").length;
      check("no-base-masking-rerun", baseRunsAfterApply === baseRunsBeforeApply, `before=${baseRunsBeforeApply} after=${baseRunsAfterApply}`);
    }

    const finalizeBeforeSave = finalizeCount(invokeLog);
    const clickRes = await clickSaveAndSettle(page, "#btn-save");
    check(
      "save-click-state",
      clickRes.clicked === (scenario.saveVia === "auto-apply" || !scenario.expect.blocked),
      `clicked=${clickRes.clicked}`,
    );

    if (scenario.saveVia === "auto-apply") {
      check("autoapply-invoked", invokeLog.includes("apply_manual_boxes"), "auto-apply ran during save");
      const baseRunsAfterSave = invokeLog.filter((cmd) => cmd === "run_masking_pipeline").length;
      check("no-base-masking-rerun", baseRunsAfterSave === baseRunsBeforeApply, `before=${baseRunsBeforeApply} after=${baseRunsAfterSave}`);
    }

    if (scenario.expect.blocked) {
      const dialog = await finalSaveDialogState(page);
      const saveState = await saveButtonState(page);
      const status = await page.locator("#status").innerText();
      check("failed-restore-save-disabled", saveState.disabled === true, JSON.stringify(saveState));
      check("failed-restore-dialog-not-opened", dialog.open === false, JSON.stringify(dialog));
      check("failed-restore-status-blocking", /차단|재검증|복원/.test(`${status} ${saveState.readiness}`), `status=${status} readiness=${saveState.readiness}`);
      check("failed-restore-finalize-not-called", finalizeCount(invokeLog) === finalizeBeforeSave, `count=${finalizeCount(invokeLog)}`);
      check("failed-restore-native-dialog-not-called", saveDialogCount(invokeLog) === 0, `count=${saveDialogCount(invokeLog)}`);
    } else if (scenario.expect.warns) {
      const dialog = await finalSaveDialogState(page);
      check("warning-dialog-opened", dialog.open === true, `open=${dialog.open}`);
      if (scenario.expect.expectRestoreWarning) {
        check("restore-warning-present", dialog.warnings.includes(WARN.restore), `warnings=${JSON.stringify(dialog.warnings)}`);
      }
      // Invariant: finalize must NOT be called while the dialog is unconfirmed.
      check("finalize-not-called-before-confirm", finalizeCount(invokeLog) === finalizeBeforeSave, `count=${finalizeCount(invokeLog)}`);
      const confirm = await confirmDialogSave(page);
      check("saved-after-confirm", confirm.saved === scenario.expect.saves, `saved=${confirm.saved} expected=${scenario.expect.saves} status=${confirm.status}`);
    } else {
      const dialog = await finalSaveDialogState(page);
      check("ready-dialog-opened", dialog.open === true, `open=${dialog.open}`);
      check("ready-dialog-has-no-warnings", dialog.warnings.length === 0 && dialog.badge === "저장 준비 완료", JSON.stringify(dialog));
      check("finalize-not-called-before-confirm", finalizeCount(invokeLog) === finalizeBeforeSave, `count=${finalizeCount(invokeLog)}`);
      const confirm = await confirmDialogSave(page);
      check("saved-after-ready-confirm", confirm.saved === scenario.expect.saves, `saved=${confirm.saved} expected=${scenario.expect.saves} status=${confirm.status}`);
    }

    // finalize(=사용자 확정 저장)는 저장이 실제로 완료된 경우에만 호출되어야 한다.
    const expectedFinalizeCount = scenario.expect.saves ? 1 : 0;
    check("finalize-called-exactly-once", finalizeCount(invokeLog) === expectedFinalizeCount, `count=${finalizeCount(invokeLog)} expected=${expectedFinalizeCount}`);
    if (scenario.id === "manual-mask-only-keeps-gate" && scenario.expect.saves) {
      await page.locator("#btn-new-document").click();
      await page.waitForFunction(() => !document.querySelector("#canvas-wrap-result")?.classList.contains("has-rendered-pdf"));
      const resetState = await page.evaluate(() => ({
        title: document.querySelector("#current-document-title")?.textContent,
        path: document.querySelector("#input-path")?.value,
        queue: document.querySelectorAll("#batch-queue .batch-item").length,
        heroVisible: getComputedStyle(document.querySelector(".dm-canvas__hero")).display !== "none",
      }));
      check("new-work-reset-restores-empty-hero", resetState.title === "문서를 선택하세요" && resetState.path === "" && resetState.queue === 0 && resetState.heroVisible, JSON.stringify(resetState));
    }

    check("no-page-errors", consoleErrors.length === 0, consoleErrors.join(" | "));
    await page.screenshot({ path: path.join(evidenceDir, `${scenario.id}.png`), fullPage: true });
  } catch (error) {
    check("scenario-threw", false, error instanceof Error ? error.message : String(error));
    await page.screenshot({ path: path.join(evidenceDir, `${scenario.id}-error.png`), fullPage: true });
  } finally {
    await page.close();
  }
  return result;
}

async function runScenario(browser, scenario) {
  const consoleErrors = [];
  const page = await createQaPage(browser, consoleErrors);
  const result = { id: scenario.id, label: scenario.label, checks: [], pass: true };
  const publicConfirmSaveTrace = [];
  let publicConfirmSaveInvokeLog = [];
  let publicConfirmSaveStage = "mock installation";
  const check = (name, ok, detail) => {
    result.checks.push({ name, ok, detail });
    if (!ok) result.pass = false;
  };
  try {
    const doubleSaveProbe = scenario.id === "clean-pass";
    const publicManifest = scenario.publicConfirmSave ? unresolvedGeometryManifestForQa() : null;
    const { invokeLog, finalizeCalls } = await installMock(page, buildReport(scenario), {
      finalizeDelayMs: doubleSaveProbe ? 800 : 0,
      publicManifest,
    });
    publicConfirmSaveInvokeLog = invokeLog;
    if (scenario.publicConfirmSave) publicConfirmSaveTrace.push(await publicConfirmSaveSnapshot(page, invokeLog, publicConfirmSaveStage));
    publicConfirmSaveStage = "workspace bootstrap";
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
    if (scenario.publicConfirmSave) publicConfirmSaveTrace.push(await publicConfirmSaveSnapshot(page, invokeLog, publicConfirmSaveStage));

    publicConfirmSaveStage = "public analysis completed";
    await pickPdfAndMask(page, scenario.publicConfirmSave ? "official_dispatch" : "legal");
    if (scenario.publicConfirmSave) publicConfirmSaveTrace.push(await publicConfirmSaveSnapshot(page, invokeLog, publicConfirmSaveStage));

    // v4.2.0: 마스킹본이 존재하면 저장 버튼은 검증 결과와 무관하게 항상 활성이다.
    publicConfirmSaveStage = "confirm-save action available";
    const saveBefore = await saveButtonState(page);
    check("save-enabled-when-masked", saveBefore.disabled === false, `disabled=${saveBefore.disabled} title=${saveBefore.title}`);
    if (scenario.publicConfirmSave) publicConfirmSaveTrace.push(await publicConfirmSaveSnapshot(page, invokeLog, publicConfirmSaveStage));

    // 저장 클릭: 경고 유무와 관계없이 확인 다이얼로그가 열린다.
    publicConfirmSaveStage = "warning dialog opened";
    let clickRes;
    if (doubleSaveProbe) {
      clickRes = await clickSaveAndSettle(page, "#btn-save");
      const readyDialog = await finalSaveDialogState(page);
      check("ready-dialog-opened", readyDialog.open === true && readyDialog.badge === "저장 준비 완료", JSON.stringify(readyDialog));
      await page.locator("#btn-dialog-save-all").click();
      await waitForInvoke(invokeLog, "finalize_manual_output_to_selected_path");
      const disabledDuringSave = await page.evaluate(() => ({
        primary: document.querySelector("#btn-save")?.disabled === true,
        maskTool: document.querySelector("#btn-canvas-tool-mask")?.disabled === true,
        restoreTool: document.querySelector("#btn-canvas-tool-restore")?.disabled === true,
        deleteTool: document.querySelector("#btn-canvas-tool-delete")?.disabled === true,
      }));
      await page.evaluate(() => {
        document.querySelector("#btn-save")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await page.waitForTimeout(50);
      const boxesBeforeConcurrentDrag = await totalBoxCount(page);
      await dragOnPdf(page, { x: 0.2, y: 0.2 }, { x: 0.45, y: 0.35 });
      const boxesAfterConcurrentDrag = await totalBoxCount(page);
      check("save-button-disabled-during-finalize", disabledDuringSave.primary, JSON.stringify(disabledDuringSave));
      check("canvas-edits-locked-during-finalize", boxesAfterConcurrentDrag === boxesBeforeConcurrentDrag, `before=${boxesBeforeConcurrentDrag} after=${boxesAfterConcurrentDrag}`);
      check("canvas-tools-disabled-during-finalize", disabledDuringSave.maskTool && disabledDuringSave.restoreTool && disabledDuringSave.deleteTool, JSON.stringify(disabledDuringSave));
      check("concurrent-save-finalizes-once", finalizeCount(invokeLog) === 1, `count=${finalizeCount(invokeLog)}`);
      await waitForStatus(page, /최종 저장 완료|최종 저장 실패/, 40_000);
      clickRes = { clicked: true };
    } else {
      clickRes = await clickSaveAndSettle(page, "#btn-save");
    }
    check("save-click-registered", clickRes.clicked === true, `clicked=${clickRes.clicked}`);
    if (scenario.publicConfirmSave) publicConfirmSaveTrace.push(await publicConfirmSaveSnapshot(page, invokeLog, publicConfirmSaveStage));

    if (scenario.expect.warns) {
      publicConfirmSaveStage = "warning dialog verified";
      const dialog = await finalSaveDialogState(page);
      check("warning-dialog-opened", dialog.open === true, `open=${dialog.open}`);
      check(
        "warning-list-matches-exact",
        JSON.stringify(dialog.warnings) === JSON.stringify(scenario.expect.warnings),
        `got=${JSON.stringify(dialog.warnings)} expected=${JSON.stringify(scenario.expect.warnings)}`,
      );
      const expectedBadge = scenario.publicConfirmSave
        ? `확인 후 저장 가능 ${scenario.expect.warnings.length}건`
        : `확인 권장 ${scenario.expect.warnings.length}건`;
      check("dialog-badge-shows-count", dialog.badge === expectedBadge, `badge=${dialog.badge}`);
      // 권고형 선택 카피("취소하고 검토하기"/"무시하고 그대로 저장") 가드.
      const expectedCancel = scenario.publicConfirmSave ? "검토로 돌아가기" : "취소하고 검토하기";
      const expectedConfirm = scenario.publicConfirmSave ? "경고 확인 후 부분 마스킹본 저장" : "무시하고 그대로 저장";
      check("cancel-control-present", dialog.cancelPresent && dialog.cancelText === expectedCancel, `cancelText=${dialog.cancelText}`);
      check("confirm-control-label", dialog.confirmText === expectedConfirm && dialog.confirmDisabled === false, `confirmText=${dialog.confirmText} disabled=${dialog.confirmDisabled}`);
      check("cancel-control-hierarchy", dialog.cancelSecondary === true, `secondary=${dialog.cancelSecondary}`);
      check("confirm-control-hierarchy", dialog.confirmPrimary === true, `primary=${dialog.confirmPrimary}`);
      // 불변식: 다이얼로그가 미확인 상태일 때 finalize 는 호출되지 않아야 한다.
      check("finalize-not-called-before-confirm", finalizeCount(invokeLog) === 0, `count=${finalizeCount(invokeLog)}`);

      if (scenario.expect.cancel) {
        await cancelDialogSave(page);
        check("finalize-not-called-after-cancel", finalizeCount(invokeLog) === 0, `count=${finalizeCount(invokeLog)}`);
        const status = await page.locator("#status").innerText();
        check("not-saved-after-cancel", /최종 저장 완료/.test(status) === false, `status=${status}`);
      } else {
        publicConfirmSaveStage = "warning confirmation submitted";
        const confirm = await confirmDialogSave(page);
        check("saved-after-confirm", confirm.saved === scenario.expect.saves, `saved=${confirm.saved} expected=${scenario.expect.saves} status=${confirm.status}`);
        if (scenario.publicConfirmSave) {
          const completion = await page.evaluate(() => ({
            status: document.querySelector("#final-save-result-status")?.textContent ?? "",
            warnings: [...document.querySelectorAll("#final-save-result-warnings li")].map((item) => item.textContent ?? ""),
          }));
          check("partial-save-status-is-prominent", completion.status.includes("확인 저장"), JSON.stringify(completion));
          check(
            "partial-save-warning-category-pages-visible",
            completion.warnings.length === scenario.expect.warnings.length
              && completion.warnings.every((warning) => /미가림 가능성: .+ · \d+(?:–\d+)?쪽/.test(warning)),
            JSON.stringify(completion),
          );
        }
      }
      if (scenario.publicConfirmSave) publicConfirmSaveTrace.push(await publicConfirmSaveSnapshot(page, invokeLog, publicConfirmSaveStage));
    } else if (!doubleSaveProbe) {
      const dialog = await finalSaveDialogState(page);
      check("ready-dialog-opened", dialog.open === true, `open=${dialog.open}`);
      check("ready-dialog-has-no-warnings", dialog.warnings.length === 0 && dialog.badge === "저장 준비 완료", JSON.stringify(dialog));
      check("finalize-not-called-before-confirm", finalizeCount(invokeLog) === 0, `count=${finalizeCount(invokeLog)}`);
      const confirm = await confirmDialogSave(page);
      check("saved-after-ready-confirm", confirm.saved === scenario.expect.saves, `saved=${confirm.saved} expected=${scenario.expect.saves} status=${confirm.status}`);
    } else {
      const status = await page.locator("#status").innerText();
      check("saved-after-ready-confirm", /최종 저장 완료/.test(status) === scenario.expect.saves, `status=${status}`);
    }

    // 불변식: finalize 는 저장이 실제로 완료된 경우에만 호출된다(경고+취소 시 미호출).
    const expectedFinalizeCount = scenario.expect.saves ? 1 : 0;
    check("finalize-called-exactly-once", finalizeCount(invokeLog) === expectedFinalizeCount, `count=${finalizeCount(invokeLog)} expected=${expectedFinalizeCount}`);
    check("native-save-dialog-called-once", saveDialogCount(invokeLog) === expectedFinalizeCount, `count=${saveDialogCount(invokeLog)} expected=${expectedFinalizeCount}`);
    if (scenario.expect.saves) {
      const outputPath = String(finalizeCalls[0]?.rawPayload?.outputPath || finalizeCalls[0]?.rawPayload?.request?.destination || "");
      check(
        "native-default-filename-used",
        path.basename(outputPath) === `phase6_non_sensitive_${scenario.publicConfirmSave ? "partial" : "masked"}.pdf`,
        `outputPath=${outputPath}`,
      );
      check(
        "legacy-output-dir-omitted",
        !Object.hasOwn(finalizeCalls[0]?.rawPayload ?? {}, "outputDir"),
        `payload=${JSON.stringify(finalizeCalls[0]?.rawPayload ?? {})}`,
      );
      const completionActions = await page.evaluate(() => ({
        newWorkVisible: !document.querySelector("#btn-new-document")?.classList.contains("is-hidden"),
        applyHidden: document.querySelector("#btn-canvas-apply")?.classList.contains("is-hidden") === true,
        saveHidden: document.querySelector("#btn-save")?.classList.contains("is-hidden") === true,
      }));
      check("saved-session-replaces-commit-actions", completionActions.newWorkVisible && completionActions.applyHidden && completionActions.saveHidden, JSON.stringify(completionActions));
    }

    // report-never-copied: 저장이 성공한 경우에도 finalize 는 copyReport:false 로
    // 호출되어야 하고 copied_files 에 safe_report 가 들어가면 안 된다.
    if (scenario.assertReportNotCopied) {
      const copiedAll = finalizeCalls.flatMap((call) => call.copied_files);
      check(
        "finalize-copyReport-always-false",
        finalizeCalls.length > 0 && finalizeCalls.every((call) => call.copyReport === false),
        `calls=${JSON.stringify(finalizeCalls.map((call) => call.copyReport))}`,
      );
      check(
        "safe-report-never-copied-to-output",
        !copiedAll.some((file) => /safe_report/.test(file)),
        `copied=${JSON.stringify(copiedAll)}`,
      );
    }

    check("no-page-errors", consoleErrors.length === 0, consoleErrors.join(" | "));
    await page.screenshot({ path: path.join(evidenceDir, `${scenario.id}.png`), fullPage: true });
  } catch (error) {
    const diagnostic = scenario.publicConfirmSave
      ? await publicConfirmSaveSnapshot(page, publicConfirmSaveInvokeLog, publicConfirmSaveStage)
      : null;
    check(
      "scenario-threw",
      false,
      `${error instanceof Error ? error.message : String(error)}; stage=${publicConfirmSaveStage}; trace=${JSON.stringify(publicConfirmSaveTrace)}; diagnostic=${JSON.stringify(diagnostic)}`,
    );
    await page.screenshot({ path: path.join(evidenceDir, `${scenario.id}-error.png`), fullPage: true });
  } finally {
    await page.close();
  }
  return result;
}

async function runManualClearRaceScenario(browser) {
  const consoleErrors = [];
  const page = await createQaPage(browser, consoleErrors);
  const result = { id: "manual-apply-clear-race", label: "수동 반영 중 Clear 차단 및 세션 소유권 유지", checks: [], pass: true };
  const check = (name, ok, detail) => {
    result.checks.push({ name, ok, detail });
    if (!ok) result.pass = false;
  };
  try {
    const { invokeLog } = await installMock(page, buildReport(SCENARIOS[0]), { applyDelayMs: 300 });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
    await pickPdfAndMask(page);
    await selectCanvasTool(page, "btn-canvas-tool-mask");
    await dragOnPdf(page, { x: 0.15, y: 0.2 }, { x: 0.6, y: 0.4 });
    await page.locator("#btn-canvas-apply").click();
    await waitForInvoke(invokeLog, "apply_manual_boxes");
    const disabled = await page.evaluate(
      () => document.querySelector("#btn-canvas-clear")?.disabled === true,
    );
    check("clear-control-disabled-during-apply", disabled, JSON.stringify(disabled));
    const beforeClear = await page.evaluate(() => ({
      documentPath: document.querySelector("#input-path")?.value,
      rendered: document.querySelector("#canvas-wrap-result")?.classList.contains("has-rendered-pdf") === true,
      status: document.querySelector("#status")?.textContent,
    }));
    await page.evaluate(() => {
      document.querySelector("#btn-canvas-clear")
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    const duringApply = await page.evaluate(() => {
      const counts = ["#review-summary-mask-count", "#review-summary-restore-count"].map((selector) => {
        const text = document.querySelector(selector)?.textContent ?? "";
        return Number(text.match(/(\d+)\s*개/)?.[1] ?? Number.NaN);
      });
      return {
        boxes: counts.every(Number.isFinite) ? counts.reduce((sum, count) => sum + count, 0) : Number.NaN,
        documentPath: document.querySelector("#input-path")?.value,
        status: document.querySelector("#status")?.textContent,
        applyDisabled: document.querySelector("#btn-canvas-apply")?.disabled === true,
      };
    });
    check(
      "forced-clear-preserves-pending-box-and-session-in-flight",
      duringApply.boxes === 1
        && duringApply.documentPath === beforeClear.documentPath
        && duringApply.applyDisabled
        && /실행 중입니다/.test(duringApply.status ?? ""),
      JSON.stringify({ beforeClear, duringApply }),
    );
    await waitForApplyComplete(page, invokeLog);
    const afterClear = await page.evaluate(() => ({
      documentPath: document.querySelector("#input-path")?.value,
      rendered: document.querySelector("#canvas-wrap-result")?.classList.contains("has-rendered-pdf") === true,
      status: document.querySelector("#status")?.textContent,
    }));
    check("manual-result-commits-after-blocked-clear", (await totalBoxCount(page)) === 0, `boxes=${await totalBoxCount(page)}`);
    check("blocked-clear-preserves-document-and-applied-preview", afterClear.documentPath === beforeClear.documentPath && afterClear.rendered && /미리보기|수동/.test(afterClear.status ?? ""), JSON.stringify({ beforeClear, afterClear }));
    check("apply-finalized-once", invokeLog.filter((cmd) => cmd === "apply_manual_boxes").length === 1, `count=${invokeLog.filter((cmd) => cmd === "apply_manual_boxes").length}`);
    check("no-page-errors", consoleErrors.length === 0, consoleErrors.join(" | "));
  } catch (error) {
    check("scenario-threw", false, error instanceof Error ? error.message : String(error));
  } finally {
    await page.close();
  }
  return result;
}
async function runPostSaveContinuationScenario(browser) {
  const consoleErrors = [];
  const page = await createQaPage(browser, consoleErrors);
  const result = {
    id: "post-save-inline-final-successor",
    label: "최종 저장 뒤 인라인 복원은 확정본에서 계속",
    checks: [],
    pass: true,
  };
  const check = (name, ok, detail) => {
    result.checks.push({ name, ok, detail });
    if (!ok) result.pass = false;
  };
  try {
    const mockState = createMockState();
    const firstMock = await installMock(page, buildReport(SCENARIOS[0]), { mockState });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
    await pickPdfAndMask(page);
    await selectCanvasTool(page, "btn-canvas-tool-mask");
    await dragOnPdf(page, { x: 0.15, y: 0.2 }, { x: 0.6, y: 0.4 });
    await page.locator("#btn-canvas-apply").click();
    await waitForApplyComplete(page, firstMock.invokeLog);
    await clickAndConfirmSave(page, "#btn-save");
    const firstFinalize = firstMock.finalizeCalls[0];
    const firstSubmittedPreview = path.resolve(firstFinalize?.previewPdf || "");
    const firstFinal = mockState.finalizedPaths[0];
    check("first-submitted-preview-deleted", isDeletedMockPath(mockState, firstSubmittedPreview), `preview=${firstSubmittedPreview}`);
    check("first-final-exists-in-mock-state", Boolean(firstFinal) && mockState.existingPaths.has(firstFinal), `final=${firstFinal}`);
    check("first-save-keeps-wire-and-privacy-contract", isExactFinalizePayload(firstFinalize?.rawPayload) && firstFinalize?.copied_files.length === 0, JSON.stringify(firstFinalize ?? {}));

    const continuationPage = page;
    const continuationLog = firstMock.invokeLog;
    const continuationFinalizes = firstMock.finalizeCalls;

    await selectCanvasTool(continuationPage, "btn-canvas-tool-restore");
    await dragOnPdf(continuationPage, { x: 0.22, y: 0.24 }, { x: 0.55, y: 0.38 });
    await continuationPage.locator("#btn-canvas-apply").click();
    await waitForApplyComplete(continuationPage, continuationLog);
    const restoreOperation = mockState.applyOperations.at(-1);
    const restoreInput = mockState.applyInputs.at(-1);
    check("restore-applies-from-first-final", restoreInput === firstFinal, `input=${restoreInput} firstFinal=${firstFinal}`);
    check(
      "original-path-remains-restore-reference",
      mockState.applyOperations.every((operation) => operation.originalPdf === fixturePath),
      `operations=${JSON.stringify(mockState.applyOperations)}`,
    );
    check("restore-count-is-explicit-only", restoreOperation?.restoreCount === 1 && restoreOperation.maskCount === 0, JSON.stringify(restoreOperation ?? {}));
    check(
      "masking-counts-change-only-by-explicit-operations",
      mockState.applyOperations.length === 2
        && mockState.applyOperations[0]?.maskCount === 1
        && mockState.applyOperations[0]?.restoreCount === 0
        && restoreOperation?.maskCount === 0
        && restoreOperation.restoreCount === 1,
      JSON.stringify(mockState.applyOperations),
    );

    const secondSaveAttempt = await clickAndConfirmSave(
      continuationPage,
      "#btn-save",
    );
    const secondSaveControls = await continuationPage.evaluate(() => ["#btn-save"].map((selector) => {
      const element = document.querySelector(selector);
      return {
        selector,
        disabled: element instanceof HTMLButtonElement ? element.disabled : null,
        hidden: element instanceof HTMLElement ? element.classList.contains("is-hidden") : null,
        title: element instanceof HTMLElement ? element.getAttribute("title") : null,
      };
    }));
    check("second-final-save-control-clicked", secondSaveAttempt.clicked, JSON.stringify(secondSaveControls));
    const secondFinalize = continuationFinalizes.at(-1);
    const secondSubmittedPreview = path.resolve(secondFinalize?.previewPdf || "");
    const secondFinal = mockState.finalizedPaths[1];
    check("second-save-uses-fresh-preview", Boolean(secondSubmittedPreview) && secondSubmittedPreview !== firstFinal && isPathWithin(secondSubmittedPreview, previewDir), `preview=${secondSubmittedPreview} firstFinal=${firstFinal}`);
    check("second-save-overwrites-selected-path", Boolean(secondFinal) && secondFinal === firstFinal, `first=${firstFinal} second=${secondFinal}`);
    check("second-final-keeps-wire-and-privacy-contract", isExactFinalizePayload(secondFinalize?.rawPayload) && secondFinalize?.copied_files.length === 0, JSON.stringify(secondFinalize ?? {}));
    check("second-submitted-preview-deleted", isDeletedMockPath(mockState, secondSubmittedPreview), `preview=${secondSubmittedPreview}`);
    check("no-page-errors", consoleErrors.length === 0, consoleErrors.join(" | "));
  } catch (error) {
    check("scenario-threw", false, error instanceof Error ? error.message : String(error));
  } finally {
    await page.close();
  }
  return result;
}


async function runFinalLoadFailureScenario(browser) {
  const consoleErrors = [];
  const page = await createQaPage(browser, consoleErrors);
  const result = { id: "post-save-final-load-failure", label: "확정본 로드 실패는 파일 기록과 무결성 실패를 분리", checks: [], pass: true };
  const check = (name, ok, detail) => {
    result.checks.push({ name, ok, detail });
    if (!ok) result.pass = false;
  };
  try {
    const mockState = createMockState({ failFinalRead: true });
    const { invokeLog, finalizeCalls } = await installMock(page, buildReport(SCENARIOS[0]), { mockState });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
    await pickPdfAndMask(page);
    await selectCanvasTool(page, "btn-canvas-tool-mask");
    await dragOnPdf(page, { x: 0.15, y: 0.2 }, { x: 0.6, y: 0.4 });
    await page.locator("#btn-canvas-apply").click();
    await waitForApplyComplete(page, invokeLog);
    await clickAndConfirmSave(page, "#btn-save");
    const status = await page.locator("#status").innerText();
    const statusDetail = await page.locator("#status-detail").innerText();
    const controls = await page.evaluate(() => ({
      apply: document.querySelector("#btn-canvas-apply")?.disabled === true,
      save: document.querySelector("#btn-save")?.disabled === true,
      mask: document.querySelector("#btn-canvas-tool-mask")?.disabled === true,
      restore: document.querySelector("#btn-canvas-tool-restore")?.disabled === true,
      baseMasking: document.querySelector("#btn-run-masking")?.disabled === true,
    }));
    const maskingRunsBeforeRetry = invokeLog.filter((cmd) => cmd === "run_masking_pipeline").length;
    await page.locator("#btn-run-masking").evaluate((element) => element.click());
    await page.waitForTimeout(50);
    const maskingRunsAfterRetry = invokeLog.filter((cmd) => cmd === "run_masking_pipeline").length;
    check("finalize-completed-before-load-failure", finalizeCalls.length === 1 && mockState.finalizedPaths.length === 1, `finalize=${finalizeCalls.length} finals=${JSON.stringify(mockState.finalizedPaths)}`);
    check("final-load-failure-separates-written-file-from-integrity-failure", /파일은 저장되었으나/.test(status) && /무결성 확인/.test(status) && /다시 열/.test(status) && !/최종 저장 완료/.test(status) && !/최종 저장 실패/.test(status), `status=${status}`);
    check("final-load-failure-does-not-record-verified-save-time", /저장 -/.test(statusDetail), `statusDetail=${statusDetail}`);
    check("final-load-failure-disables-edit-and-save-controls", controls.apply && controls.save && controls.mask && controls.restore, JSON.stringify(controls));
    check("final-load-failure-disables-base-remasking", controls.baseMasking && maskingRunsAfterRetry === maskingRunsBeforeRetry, `controls=${JSON.stringify(controls)} before=${maskingRunsBeforeRetry} after=${maskingRunsAfterRetry}`);
    check("no-page-errors", consoleErrors.length === 0, consoleErrors.join(" | "));
  } catch (error) {
    check("scenario-threw", false, error instanceof Error ? error.message : String(error));
  } finally {
    await page.close();
  }
  return result;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
if (PUBLIC_SCENARIOS.has(scenarioSelection)) {
  const negativeCasesPassed = publicReceiptNegativeCases();
  const lifecycle = publicLifecycleEvidence(scenarioSelection);
  if (!negativeCasesPassed) {
    lifecycle.status = "fail";
    lifecycle.failure = "public receipt adversarial rejection contract failed";
  }
  const summary = {
    status: lifecycle.status,
    scenario: scenarioSelection,
    piiSafe: true,
    evidenceAuthority: "native_app_emitted_receipt",
    negativeCasesPassed,
    lifecycle,
  };
  writeFileSync(path.join(evidenceDir, "save_flow_summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
  console.log(`PUBLIC-DOCUMENT ${summary.status.toUpperCase()} — ${scenarioSelection}`);
  process.exit(summary.status === "pass" ? 0 : 1);
}
let devServer;
let browser;
const results = [];
try {
  const server = await ensureDevServer();
  devServer = server.child;
  url = server.url;
  const launched = await launchBrowser();
  browser = launched.browser;
  console.log(`[browser] ${launched.selection}${launched.systemChromeDiagnostic ? `; system Chrome unavailable: ${launched.systemChromeDiagnostic}` : ""}`);
  for (const scenario of SCENARIOS) {
    const result = await runScenario(browser, scenario);
    results.push(result);
    const status = result.pass ? "PASS" : "FAIL";
    console.log(`[${status}] ${result.id} — ${result.label}`);
    for (const c of result.checks) {
      if (!c.ok) console.log(`    ✗ ${c.name}: ${c.detail}`);
    }
  }
  for (const scenario of MANUAL_SCENARIOS) {
    const result = await runManualScenario(browser, scenario);
    results.push(result);
    const status = result.pass ? "PASS" : "FAIL";
    console.log(`[${status}] ${result.id} — ${result.label}`);
    for (const c of result.checks) {
      if (!c.ok) console.log(`    ✗ ${c.name}: ${c.detail}`);
    }
  }
  const clearRaceResult = await runManualClearRaceScenario(browser);
  results.push(clearRaceResult);
  console.log(`[${clearRaceResult.pass ? "PASS" : "FAIL"}] ${clearRaceResult.id} — ${clearRaceResult.label}`);
  for (const check of clearRaceResult.checks) {
    if (!check.ok) console.log(`    ✗ ${check.name}: ${check.detail}`);
  }
  const continuationResult = await runPostSaveContinuationScenario(browser);
  results.push(continuationResult);
  console.log(`[${continuationResult.pass ? "PASS" : "FAIL"}] ${continuationResult.id} — ${continuationResult.label}`);
  for (const check of continuationResult.checks) {
    if (!check.ok) console.log(`    ✗ ${check.name}: ${check.detail}`);
  }
  const finalLoadFailureResult = await runFinalLoadFailureScenario(browser);
  results.push(finalLoadFailureResult);
  console.log(`[${finalLoadFailureResult.pass ? "PASS" : "FAIL"}] ${finalLoadFailureResult.id} — ${finalLoadFailureResult.label}`);
  for (const check of finalLoadFailureResult.checks) {
    if (!check.ok) console.log(`    ✗ ${check.name}: ${check.detail}`);
  }
} finally {
  await browser?.close();
  if (devServer) {
    devServer.kill("SIGTERM");
    console.log("[dev] stopped vite dev server");
  }
}

const summary = {
  status: results.every((result) => result.pass) ? "pass" : "fail",
  url,
  scenarios: results,
  evidenceAuthority: "mock_ui_non_authoritative",
};
writeFileSync(path.join(evidenceDir, "save_flow_summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
console.log(`\nSAVE-FLOW ${summary.status.toUpperCase()} — ${results.filter((r) => r.pass).length}/${results.length} scenarios`);
process.exit(summary.status === "pass" ? 0 : 1);
