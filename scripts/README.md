# QA & 빌드 스크립트 (v4)

릴리즈 게이트는 아래 세트로 고정됐다(REDESIGN_V4_DARK §4). 구조가 바뀌면 이 세트와
`docs/RUNTIME_CONTRACT.md`, Python 테스트를 **한 세트로** 갱신한다.

## 9중 게이트

| # | 명령 | 검증 |
|---|---|---|
| 1 | `python3 -m unittest discover -s tests` | Python 계약·라벨·상태·마스킹·릴리즈 전체 |
| 2 | `npm run build` | tsc 타입체크 + vite 프로덕션 빌드 |
| 3 | `npm run contract` | 런타임 셀렉터 계약(`check_runtime_contract.mjs`, 158개) |
| 4 | `npm run qa:smoke` | 부팅 + 3화면 전환(`[data-screen-target]`) + 콘솔 에러 0 + 화면별 스크린샷 |
| 5 | `npm run qa:canvas` | 마스킹/복원 드래그·선택·삭제·이동을 두 표면에서 실제 상호작용 |
| 6 | `npm run qa:save` | 저장 흐름 4시나리오(정상/데드엔드/잔여 하드블록/품질 게이트 실패) |
| 7 | `npm run qa:options` | 비식별 TXT 선택과 token/partial/pseudonym IPC 옵션 연결 |
| 8 | `cargo test` (`src-tauri/`, Rust 변경 시) | Tauri IPC 커맨드 |
| 9 | `npm run accept:real` | 해시 매니페스트 15종의 외부 실앱 픽셀·저장 PDF 수용 시험 |

브라우저 게이트(4~7)는 `npm run qa:all` 로 한 번에 실행한다. vite dev 서버(1420,
strictPort)가 떠 있으면 재사용하고, 없으면 각 게이트가 자체 기동 후 종료한다.
네 게이트 모두 `http://localhost:1420/` 호스트를 기본값으로 공유한다(IPv6 `::1`
재사용). 개별 실행: `npm run qa:smoke|qa:canvas|qa:save|qa:options`.

실앱 완료 근거의 위계는 외부 `screencapture` 픽셀, 실앱 `dump-state`, CLI 매니페스트,
목/픽스처 QA 순이며, 마지막 항목은 완료 근거로 사용할 수 없다. `accept:real`이
`PASS`가 아니면 실앱 작업은 미완료 또는 `PENDING`이다.

## 그 외 스크립트

- `qa_tauri_mock.mjs` — 브라우저 게이트가 공유하는 Tauri IPC 목 헬퍼(직접 실행 아님).
- `qa_plan_compliance.py` — 플랜 파일 위생(태스크 마커·개인정보 패턴) 검사.
- `check_runtime_contract.mjs` — 위 3번 게이트 본체.
- `masking_engine_entry.py` / `build_masking_engine.*` / `run_masking_pipeline.py` —
  Python 마스킹 엔진 진입·패키징.
- `e2e_*` — Tauri/Windows 로컬 e2e 스모크.
- `generate_documasker_icons.py` · `update_kr_regions.py` · `apply_manual_boxes.py`
  · `ensure_phase6_fixture.py` — 자산·데이터·픽스처 유틸.

> v4 P4에서 구 구조(좌측 레일·모바일 독·분리된 canvas/documents 화면·구 테마)를
> 조회하던 죽은 비게이트 QA 스크립트(qa_all_screens_responsive / qa_all_buttons_surfaces
> / qa_ui_interactions / qa_react_shell / qa_downloads_manual_flow / qa_full_ui_flow
> / qa_task5·11·12·13 / qa_ui_controls_responsive / ui_risk_check)는 위 게이트·Python
> 테스트와 중복이라 삭제됐다(§0 — 죽은 스크립트 방치 금지).
