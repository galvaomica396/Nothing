#!/usr/bin/env bash
# Role-based model dispatcher for the Nothing refactor harness.
#
# 제약을 프롬프트가 아니라 프로세스 수준에서 강제한다:
#   - 역할별 샌드박스 티어 고정: audit / investigate / design-verify 는
#     --sandbox read-only 로
#     실행되므로 감사·검증 모델은 파일을 수정할 수 없다 (OS 레벨 차단).
#   - 모델 폴백은 roles.json 에 명시된 체인 안에서만 일어난다 (품질 하한선).
#   - 폴백 순서는 roles.json 선언을 따른다. 기존 OpenAI 계열과 opencode-go가 전부
#     실패하면 마지막 gjc:gpt-5.6-luna (thinking max)가 그 역할을 대행한다.
#     즉, opencode-go 체인 전멸 시 gjc luna-max 폴백을 반드시 시도한다.
#   - 레이트리밋/API 오류 시 체인의 다음 모델로 자동 failover.
#   - 체인 전체 소진 시 실패로 종료 — 임의 모델로 내려가지 않는다.
#
# usage: dispatch.sh <role> <brief-file> [-o <output-file>] [-i <image-file>]
#   role: roles.json 의 키 (write | write-light | audit | investigate | design-verify)
#   brief-file: 프롬프트 전문. audit 브리프는 검토 대상 코드/diff를 인라인할 것
#               (최후 폴백 agy는 파일 접근 없이 동작해야 하므로).

set -u
HARNESS_DIR="$(cd "$(dirname "$0")" && pwd)"
ROLES_FILE="${DISPATCH_ROLES:-$HARNESS_DIR/roles.json}"

ROLE="${1:-}"
BRIEF="${2:-}"
if [[ -z "$ROLE" || -z "$BRIEF" || ! -f "$BRIEF" ]]; then
  echo "usage: dispatch.sh <role> <brief-file> [-o <output-file>] [-i <image-file>]" >&2
  exit 2
fi
shift 2

OUT=""
IMAGE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) OUT="$2"; shift 2 ;;
    -i) IMAGE="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

CONFIG="$(python3 - "$ROLES_FILE" "$ROLE" <<'PY'
import json, sys
roles = json.load(open(sys.argv[1]))
role = roles.get(sys.argv[2])
if role is None:
    sys.exit(3)
print(role["sandbox"])
print(role.get("schema") or "-")
for m in role["chain"]:
    print(m)
PY
)" || { echo "[dispatch] unknown role: $ROLE" >&2; exit 3; }

SANDBOX="$(sed -n 1p <<<"$CONFIG")"
SCHEMA_REL="$(sed -n 2p <<<"$CONFIG")"
MODELS="$(tail -n +3 <<<"$CONFIG")"
SCHEMA=""
[[ "$SCHEMA_REL" != "-" ]] && SCHEMA="$HARNESS_DIR/$SCHEMA_REL"

PROMPT="$(cat "$BRIEF")"

while IFS= read -r MODEL; do
  [[ -z "$MODEL" ]] && continue
  echo "[dispatch] role=$ROLE model=$MODEL sandbox=$SANDBOX" >&2

  if [[ "$MODEL" == agy:* ]]; then
    # antigravity 경로: --sandbox + 권한 프롬프트 미허용 → 쓰기 불가.
    RES="$(agy --model="${MODEL#agy:}" --sandbox -p "$PROMPT" </dev/null 2>&1)"
    CODE=$?
  elif [[ "$MODEL" == gjc:* ]]; then
    # GJC에는 codex의 --sandbox가 없으므로 read-only 롤은 읽기 도구만 허용한다.
    # --no-mcp까지 함께 사용해 외부 MCP 쓰기 경로도 차단한다. --no-session은
    # 폴백 실행이 세션을 남기거나 기존 세션을 오염시키지 않게 한다.
    GJC_ARGS=(--model="${MODEL#gjc:}" --thinking=max --no-session)
    if [[ "$SANDBOX" == "read-only" ]]; then
      GJC_ARGS+=(--tools=read,find,search --no-mcp)
    fi
    # gjc는 codex의 -o 옵션을 지원하지 않으므로 성공 후 공통 출력 처리에서
    # 캡처된 응답을 OUT 파일에 기록한다.
    RES="$(gjc "${GJC_ARGS[@]}" -p "$PROMPT" </dev/null 2>&1)"
    CODE=$?
  else
    ARGS=(exec -m "$MODEL" --sandbox "$SANDBOX" --skip-git-repo-check)
    [[ -n "$SCHEMA" ]] && ARGS+=(--output-schema "$SCHEMA")
    [[ -n "$OUT" ]] && ARGS+=(-o "$OUT")
    if [[ -n "$IMAGE" ]]; then
      RES="$(codex "${ARGS[@]}" -i "$IMAGE" -- "$PROMPT" </dev/null 2>&1)"
    else
      RES="$(codex "${ARGS[@]}" "$PROMPT" </dev/null 2>&1)"
    fi
    CODE=$?
  fi

  if [[ $CODE -eq 0 ]] && ! grep -qE '^ERROR' <<<"$RES"; then
    if [[ -n "$OUT" && ( "$MODEL" == gjc:* || ! -s "$OUT" ) ]]; then
      printf '%s\n' "$RES" > "$OUT"
    fi
    echo "[dispatch] success model=$MODEL" >&2
    printf '%s\n' "$RES"
    exit 0
  fi

  echo "[dispatch] model=$MODEL failed (exit=$CODE) — failing over" >&2
  grep -E '^ERROR' <<<"$RES" | head -2 >&2 || true
done <<<"$MODELS"

echo "[dispatch] chain exhausted for role=$ROLE — refusing to use below-floor models" >&2
exit 1
