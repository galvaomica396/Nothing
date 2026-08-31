#!/usr/bin/env bash
# Compose the durable plan -> investigate -> write workflow.
#
# The investigate result is persisted before the write brief is created. The
# mandatory disposition rules below are appended by this script, rather than
# being left to either brief author.

set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(CDPATH= cd "$SCRIPT_DIR/../.." && pwd)"
DISPATCH_BIN="${AUDIT_THEN_WRITE_DISPATCH:-$SCRIPT_DIR/dispatch.sh}"

readonly DEFAULT_MAX_ATTEMPTS=3
readonly DEFAULT_RETRY_INTERVAL_SECONDS=2
readonly MAX_ATTEMPTS="${AUDIT_THEN_WRITE_MAX_ATTEMPTS:-$DEFAULT_MAX_ATTEMPTS}"
readonly RETRY_INTERVAL_SECONDS="${AUDIT_THEN_WRITE_RETRY_INTERVAL_SECONDS:-$DEFAULT_RETRY_INTERVAL_SECONDS}"

usage() {
  printf '%s\n' \
    "usage: audit_then_write.sh <plan-brief> <audit-brief> <tag> [-o <dir>]" \
    "  -o <dir>  directory for the synthesized brief and implementation report" >&2
}

is_unsigned_integer() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

if (( $# < 3 )); then
  usage
  exit 2
fi

PLAN_BRIEF="$1"
AUDIT_BRIEF="$2"
TAG="$3"
shift 3

OUTPUT_DIR="$REPO_ROOT/.omo/evidence"
while (( $# > 0 )); do
  case "$1" in
    -o)
      if (( $# < 2 )) || [[ -z "$2" ]]; then
        usage
        exit 2
      fi
      OUTPUT_DIR="$2"
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ ! -f "$PLAN_BRIEF" ]]; then
  printf '[audit_then_write] plan brief not found: %s\n' "$PLAN_BRIEF" >&2
  exit 2
fi
if [[ ! -f "$AUDIT_BRIEF" ]]; then
  printf '[audit_then_write] audit brief not found: %s\n' "$AUDIT_BRIEF" >&2
  exit 2
fi

case "$TAG" in
  ""|*[!A-Za-z0-9._-]*)
    printf '[audit_then_write] tag must contain only letters, numbers, dot, underscore, or hyphen: %s\n' "$TAG" >&2
    exit 2
    ;;
esac

if ! is_unsigned_integer "$MAX_ATTEMPTS" || (( MAX_ATTEMPTS < 1 )); then
  printf '[audit_then_write] AUDIT_THEN_WRITE_MAX_ATTEMPTS must be a positive integer\n' >&2
  exit 2
fi
if ! is_unsigned_integer "$RETRY_INTERVAL_SECONDS"; then
  printf '[audit_then_write] AUDIT_THEN_WRITE_RETRY_INTERVAL_SECONDS must be a non-negative integer\n' >&2
  exit 2
fi

if [[ "$OUTPUT_DIR" != /* ]]; then
  OUTPUT_DIR="$(pwd)/$OUTPUT_DIR"
fi

AUDIT_DIR="$REPO_ROOT/.omo/audits"
AUDIT_FILE="$AUDIT_DIR/${TAG}.audit.txt"
SYNTHESIZED_BRIEF="$OUTPUT_DIR/${TAG}.synthesized-brief.txt"
WRITE_REPORT="$OUTPUT_DIR/${TAG}-write-result.txt"

mkdir -p "$AUDIT_DIR" "$OUTPUT_DIR"

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/audit-then-write.XXXXXX")"
cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT HUP INT TERM

extract_success_model() {
  local log_file="$1"
  local line=""
  local model=""
  while IFS= read -r line; do
    case "$line" in
      *"[dispatch] success model="*)
        model="${line##*success model=}"
        ;;
    esac
  done < "$log_file"
  if [[ -n "$model" ]]; then
    printf '%s\n' "$model"
  else
    printf '%s\n' "unknown"
  fi
}

run_dispatch_stage() {
  local stage="$1"
  local role="$2"
  local brief="$3"
  local destination="$4"
  local attempt=1
  local exit_code=0
  local attempt_output=""
  local attempt_log=""
  local all_log="$TMP_ROOT/${stage}.dispatch.log"
  local model=""

  : > "$all_log"
  while (( attempt <= MAX_ATTEMPTS )); do
    attempt_output="$TMP_ROOT/${stage}.${attempt}.out"
    attempt_log="$TMP_ROOT/${stage}.${attempt}.log"
    printf '[audit_then_write] %s attempt %d/%d\n' "$stage" "$attempt" "$MAX_ATTEMPTS" >&2

    if bash "$DISPATCH_BIN" "$role" "$brief" >"$attempt_output" 2>"$attempt_log"; then
      exit_code=0
    else
      exit_code=$?
    fi

    {
      printf '[attempt=%d exit=%d]\n' "$attempt" "$exit_code"
      cat "$attempt_log"
    } >> "$all_log"

    if (( exit_code == 0 )); then
      mv "$attempt_output" "$destination"
      model="$(extract_success_model "$all_log")"
      if [[ "$stage" == "audit" ]]; then
        AUDIT_MODEL="$model"
      else
        WRITE_MODEL="$model"
      fi
      return 0
    fi

    printf '[audit_then_write] %s attempt %d failed (exit=%d)\n' "$stage" "$attempt" "$exit_code" >&2
    cat "$attempt_log" >&2 || true
    if (( attempt < MAX_ATTEMPTS )) && (( RETRY_INTERVAL_SECONDS > 0 )); then
      sleep "$RETRY_INTERVAL_SECONDS"
    fi
    attempt=$((attempt + 1))
  done

  return 1
}

audit_started="$(date +%s)"
if ! run_dispatch_stage "audit" "investigate" "$AUDIT_BRIEF" "$AUDIT_FILE"; then
  audit_finished="$(date +%s)"
  printf '[audit_then_write] audit failed after %d attempts; write stage was not started\n' "$MAX_ATTEMPTS" >&2
  printf 'audit_duration_seconds=%d\n' "$((audit_finished - audit_started))" >&2
  exit 1
fi
audit_finished="$(date +%s)"
audit_duration="$((audit_finished - audit_started))"

synthesized_tmp="$TMP_ROOT/${TAG}.synthesized-brief.txt"
{
  printf '%s\n' "# Automatically synthesized plan -> audit -> write brief"
  printf '%s\n' "## Plan brief"
  cat "$PLAN_BRIEF"
  printf '\n%s\n' "## Investigate result"
  cat "$AUDIT_FILE"
  printf '\n%s\n' "## Mandatory audit disposition rules"
  printf '%s\n' "각 소견을 무시하지 말 것. 계획과 충돌하면 감사를 따르라."
  printf '%s\n' "감사가 계획을 기각했으면 감사 권고대로 방향을 바꿔 구현하라."
  printf '%s\n' "**단, docs/SPEC-AMENDMENTS-2026-08.md 의 사용자 확정 개정이 감사보다 우선한다.** 감사가 개정과 충돌하는 권고를 하면 개정을 따르고, 그 사실을 보고서에 명시하라."
  printf '%s\n' "소견별 처분(수용/기각+사유)을 표로 보고서에 남겨라."
} > "$synthesized_tmp"
mv "$synthesized_tmp" "$SYNTHESIZED_BRIEF"

write_started="$(date +%s)"
if ! run_dispatch_stage "write" "write" "$SYNTHESIZED_BRIEF" "$WRITE_REPORT"; then
  write_finished="$(date +%s)"
  printf '[audit_then_write] write failed after %d attempts\n' "$MAX_ATTEMPTS" >&2
  printf 'audit_file=%s\n' "$AUDIT_FILE" >&2
  printf 'synthesized_brief=%s\n' "$SYNTHESIZED_BRIEF" >&2
  printf 'write_duration_seconds=%d\n' "$((write_finished - write_started))" >&2
  exit 1
fi
write_finished="$(date +%s)"
write_duration="$((write_finished - write_started))"

printf '%s\n' \
  "[audit_then_write] completed" \
  "audit_file=$AUDIT_FILE" \
  "synthesized_brief=$SYNTHESIZED_BRIEF" \
  "implementation_report=$WRITE_REPORT" \
  "audit_duration_seconds=$audit_duration" \
  "audit_model=$AUDIT_MODEL" \
  "write_duration_seconds=$write_duration" \
  "write_model=$WRITE_MODEL"
