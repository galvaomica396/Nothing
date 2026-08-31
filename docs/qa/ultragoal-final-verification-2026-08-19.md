# Nothing 최종 검증 기록 (리팩토링 + 프론트 교체 + 실문서 QA)

- 기록일: 2026-08-19 (Asia/Seoul)
- 범위: `Nothing` 리팩토링, Pencil A-G 프론트 교체, 다운로드 실문서로 테스트
- 정책: `thresholds-v2`, 자동 마스킹 0.85, 검토 0.50
- 개인정보 안전 계약: 다운로드 원문은 읽기 전용, 파일명/경로/원문/검출값/좌표는 이 기록과 저장소에 미기재(입력 동일성은 SHA-256만).

## 1. 전체 자동 검증 스위트 (all green)

```
cd [REPOSITORY_ROOT]
.venv-test/bin/python -m pytest -q        # 483 passed, 1 skipped, 710 subtests passed
cd src-tauri && cargo test                 # 102 passed
.venv-test/bin/python -m pytest -q tests/test_tauri_local_smoke.py   # 21 passed
.venv-test/bin/python -m pytest -q scripts/e2e_fixture_smoke.py      # pass (안전리포트 내부 전용)
.venv-test/bin/python -m pytest -q scripts/e2e_tauri_local_smoke.py  # pass (startup/render)
npm run build                              # tsc + vite build, exit 0
npm run contract                           # CONTRACT OK (147 selectors verified)
npm run qa:all                             # 아래 4개 전체 통과
```

`npm run qa:all` 구성:

| QA | 결과 |
| --- | --- |
| `qa:smoke` | SMOKE OK — dark/light 반응형 화면, 0 console/page 오류, 125 고유 스크린샷 |
| `qa:canvas` | CANVAS QA OK — 마스크/복원 드래그, 선택, 삭제, 팬, 양쪽 표면 |
| `qa:save` | SAVE-FLOW PASS — 14/14 시나리오 (자동/경고/취소/수동/복원/레이스/최종 후속) |
| `qa:options` | MASKING OPTIONS QA PASS |

## 2. 네이티브 공개문서 수신 (새 번들, public-document-plumbing)

EXIT=0, 12개 단계 전부 proven, `pii_safe:true`.
증거: `build/native-evidence/public-document-plumbing-final.json`
번들: `src-tauri/target/release/bundle/macos/Nothing.app`

## 3. 실문서 QA (fail-closed)

자세한 표는 `docs/qa/ultragoal-real-document-qa-2026-08-16.md` 참조.
- 내부검토보고서 2건, 대내외 시행문서 2건 — 전부 사람 검토 대기(fail-closed), 출력 PDF 미생성, PII 안전.
- 근거 부족 항목은 자동 확정하지 않고 검토 대기로 유지했다.

## 4. 이번 턴에 추가로 수정한 것

- `scripts/qa_redesign_smoke.mjs`: `assertModalFocusTrap`을 setup 재시도 루프로 로버스트화.
  모달 autofocus가 `setTimeout(0)` 비동기라 전-스위트 부하 시 focus 트랩 검사가 간헐적으로 흔들렸던
  타이밍 레이스를 흡수. 각 시도는 동일한 랩 동작을 요구하므로 실제 회귀는 가릴 수 없음.
  결과: 이전에 간헐 실패하던 `keyword-modal-focus`가 dark/light에서 안정 통과.

## 5. 알려진 비차단 관찰

- PyMuPDF 구형 `fitz` import 경고 출력됨(추출 분석엔 영향 없음). 의존성 변경 없이 유지.
- 번들 재빌드 시 사전 존재·무관 경고(`refresh_manifest_hash` unused)만 존재 — 테스트 전용.
- 비전 사이드카 없음(ChatGPT 로그인 없음) + 이미지 뷰 429 제한으로 A-G 픽셀 등급 직접 재산정은 불가;
  기능/접근성/증거는 스모크가 자동 검증.
