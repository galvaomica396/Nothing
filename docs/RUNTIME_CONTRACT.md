# 런타임 계약 (RUNTIME CONTRACT) — 리디자인 중 절대 보존

UI 동작은 React가 아니라 `src/legacy/**`의 부트스트랩·컨트롤러·도메인 wiring이 소유한다.
React는 정적 DOM을 한 번 렌더하고, `domBindings.ts`가 부팅 시 `$("#id")`
(non-null assertion)·`querySelector`로 요소를 조회해 컨트롤러와 wiring 모듈에 주입한다.
**아래 목록의 요소가 하나라도 없으면 부팅 시 하드 크래시.**
레이아웃/구조/클래스/스타일은 자유롭게 바꿔도 되지만, id와 data-* 값은
어떤 요소로든 반드시 DOM에 존재해야 한다.

정적 검증: `node scripts/check_runtime_contract.mjs` → `CONTRACT OK (146 selectors verified)`.

> **v4.2.0 (2026-07, 저장 권고형 전환) 반영.** 최종 저장은 항상 사용자 재량이다.
> 검증 결과는 권고로만 노출되고, 저장 직전 확인 다이얼로그("저장 전 확인")가
> 경고가 있을 때만 1회 뜬다("그대로 저장"/"취소"). 폐지된 하드 차단/검토 확인
> 표면의 id 6종(`btn-acknowledge-review`, `btn-dialog-acknowledge-review`,
> `dialog-review-state`, `btn-open-final-save-dialog`, `btn-dialog-open-output-folder`,
> `btn-save-artifact-pdf`)은 조회 코드와 함께 삭제됐고, 신설 id
> `final-save-warning-list`·`btn-dialog-cancel-save` 로 대체됐다.

> **v4 (2026-07-05, REDESIGN_V4_DARK) 구조 반영.** 이전 판(253개, 라이트·5화면)은
> 폐기됐다. v4에서 화면 5→2 통합 + 좌측 레일 폐지 + 모바일 독 삭제로 마크업·
> 컨트롤러·체커·테스트를 한 세트로 재작성했으므로, 이 문서는 **현 소스가
> 정의하는 것만** 열거한다. 사라진 표면의 id는 컨트롤러에서 조회 코드와 함께
> 삭제됐다(숨김 잔존 금지). §3 IPC는 여전히 불변이다.

## 1. 메커니즘 (변경 금지)

- **화면 전환**: `[data-screen-target]` 클릭 → `activateAppScreen()`가
  `[data-screen-panel]`에 `.is-active` 토글. 값 3종:
  `documents | masking-settings | settings`.
  (v4: `canvas` 는 별도 화면이 아니라 `documents` 로 통합됐다 — 캔버스가 곧 문서 화면.)
  - `#workspace-shell` 에 `data-active-screen` 세팅됨.
  - 상단 문서 홈이 `documents`를, 기어 2개가 `masking-settings`/`settings`를
    직접 가리킨다. 좌표 템플릿 화면과 좌측 레일은 삭제됐다.
  - 보조 화면(`settings`/`masking-settings`)은 문서 화면으로 "돌아가기"·Escape 복귀한다.
- **폐지된 메커니즘** (참조 0 — 되살리지 말 것):
  - `canvas-screen-active` body 클래스 — 캔버스가 문서 화면에 통합되며 폐지.
    (캔버스 활성 스타일은 `#workspace-shell.canvas-mode` 로만 표현.)
  - `[data-mobile-panel]` / `[data-mobile-panel-target]` 모바일 독 — 삭제.
    좁은 폭에서는 스테이지+검토 레일이 세로로 자연 스택된다.
  - `#documents-screen` 및 구 WorkRail/DocumentStage 표면(work-rail-*,
    obsidian-total/processing/ready/failed-documents, stage-*, workflow-step-*,
    workflow-progress-*, context-* 등) — 통합 문서 화면으로 흡수·삭제.
- **캔버스 도구**: `[data-canvas-tool]` = `mask|restore|select|delete|pan`.
- **설정 탭**: `[data-settings-panel]`(현재 `general` 하나). `[data-settings-tab]`
  버튼은 없음(querySelectorAll 은 빈 NodeList 허용 — 체커 allowlist).
- **테마**: `<html data-theme="light|dark">`, 라디오
  `input[name="settings-theme"]` 값 `light|dark|system`.
  `data-theme-preference`는 저장 선택값을 보존하고 `system`은 OS 설정에 반응한다.
- **상태 훅**: `data-state` 속성(`final-save-readiness`, `final-state-card`,
  `final-state-card`) — JS가 세팅, CSS가 스타일링.
- **모달**: `hidden`/`is-hidden` 토글 — `#final-save-dialog`, `#keyword-dialog`,
  `#new-document-dialog`.
- **선택 라디오**: `input[name="mode"]`(`mode-mask`/`mode-restore`) — 수동 보정
  명령 프록시(오프스크린).

## 2. 보존해야 할 ID 전체 목록 (정의 컴포넌트별)

### AppHeader.tsx (상단 바 — 문서 홈 · 문서 명령 · 기어 2개)
`current-document-title` `obsidian-target-summary` `btn-pick-pdf` `btn-pick-batch`
`btn-run-masking` `app-health-strip`

### layout/AppShell.tsx · StatusRibbon.tsx
`workspace-shell` `status` `status-detail`

### CanvasWorkspace.tsx (통합 "문서" 화면 — `data-screen-panel="documents"`)
루트·창: `canvas-workspace-screen` `mask-canvas-window` `masked-preview-panel`
`btn-collapse-canvas-tools` `btn-collapse-canvas-properties`
`btn-expand-canvas-panels` `btn-close-canvas`

편집 도구·키워드·보기: `btn-canvas-tool-mask` `btn-canvas-tool-restore`
`btn-canvas-tool-select` `btn-canvas-tool-delete` `btn-canvas-tool-pan`
`custom-keywords` `toggle-original-compare`
`btn-canvas-zoom-out` `zoom-info` `btn-canvas-zoom-in` `btn-open-keyword-dialog`
`batch-summary` `batch-queue` `btn-run-batch`

반영·저장: `btn-canvas-apply` `btn-canvas-final-save` `btn-new-document`
`canvas-tool-readiness`

히어로(빈 상태) + 미리보기 스테이지: `btn-canvas-load-pdf` `btn-canvas-hero-batch`
`pdf-compare-view` `original-compare-panel`
`viewer-meta-orig` `canvas-wrap-orig` `pdf-canvas-orig` `viewer-meta-result`
`canvas-wrap-result` `pdf-canvas-result` `overlay-canvas-result`
`text-compare-view` `extracted-text-view` `masked-text-view`
`btn-prev-orig` `btn-next-orig` `btn-prev-result` `btn-next-result`
`compare-mode-pdf` `compare-mode-text` `sync-pages`

우측 검토·저장 레일(`#side-panel`): `btn-toggle-inspector` `final-state-card`
`obsidian-detection-heading` `obsidian-detection-list` `final-state-title`
`final-state-detail` `canvas-box-properties` `canvas-box-property-page`
`canvas-box-property-type` `canvas-box-property-coordinates`
`canvas-box-property-size` `btn-canvas-box-convert-mask`
`btn-canvas-box-convert-restore` `btn-canvas-box-delete` `canvas-box-list`
`review-summary-mask-count` `review-summary-restore-count`
`review-summary-keyword-count` `review-summary-output-file`
`canvas-summary-mask-count` `canvas-summary-restore-count`
`canvas-summary-keyword-count` `canvas-summary-output-state`
`keyword-chip-preview` `final-save-readiness` `btn-save`
`btn-open-canvas-properties-tab`

명령 프록시(오프스크린): `mode-mask` `mode-restore` `input-path` `page-info-orig`
`page-info-result` `box-info` `btn-canvas-delete-box` `btn-canvas-undo`
`btn-canvas-clear` `btn-undo` `btn-clear` `btn-manual-apply` `btn-mask-canvas`
`btn-open-canvas-window` `canvas-active-tool-label` `canvas-mode-status`

키워드 다이얼로그(`#keyword-dialog`): `keyword-dialog-title`
`btn-close-keyword-dialog` `custom-keywords` `keyword-dialog-chip-list`
`btn-keyword-policy` `btn-keyword-dialog-apply`

새 문서 확인 다이얼로그(`#new-document-dialog`): `new-document-dialog-title`
`btn-close-new-document-dialog` `btn-cancel-new-document` `btn-confirm-new-document`

최종 저장 권고 다이얼로그(`#final-save-dialog`, "저장 전 확인"): `final-save-dialog-title`
`btn-close-final-save-dialog` `final-save-dialog-state` `final-save-warning-list`
`btn-dialog-cancel-save` `btn-dialog-save-all`

### MaskingSettingsScreen.tsx (`data-screen-panel="masking-settings"`, 기어 진입)
`masking-settings-screen` `btn-masking-settings-back` `btn-masking-settings-cancel`
`btn-masking-settings-preview` `btn-masking-settings-apply`
룰 체크박스 16종(`rule-grid` 컨테이너 안, 동적 조회 `#rule-${id}` — id 패턴 유지 필수):
`rule-rrn` `rule-phone` `rule-business_reg` `rule-name` `rule-address`
`rule-place` `rule-legal_party` `rule-company` `rule-court` `rule-case_title`
`rule-case_number` `rule-law_firm` `rule-attorney` `rule-approval_line`
`rule-region_context` `rule-doc_meta`
`profile` `engine` `display-mode` `deidentification-policy` `region-scope`
`custom-regions` `opt-pdf-redaction` `settings-export-masked-text`
`settings-apply-scope-status`

### SettingsScreen.tsx (`data-screen-panel="settings"`, 기어 진입)
`settings-screen` `btn-settings-back` `settings-title` `btn-settings-close`
`settings-open-output-after-save` `btn-app-settings-reset` `btn-app-settings-save`
`btn-app-settings-close`
+ `input[name="settings-theme"]` 라디오 3개(`data-theme-preview="light|dark|system"`),
  `data-settings-panel="general"`

## 3. Tauri IPC (프론트에서 호출 — 이름/서명 변경 금지 · v4 무수정 확인)

`create_canvas_launch_token` `take_canvas_launch_payload` `read_pdf_bytes`
`read_text_file` `get_preview_workdir` `pick_input_pdf` `open_mask_canvas_window`
`pick_input_document` `pick_input_documents` `pick_output_dir`
`run_masking_pipeline` `apply_manual_boxes` `finalize_manual_output`
`choose_final_pdf_path` `finalize_manual_output_to_selected_path`
`default_output_dir_for_document` `existing_output_dir`

`choose_final_pdf_path`는 `{ outputPath, saveToken }`을 반환하며, 신규
`finalize_manual_output_to_selected_path`는 두 값을 함께 받아 정확한 경로에 묶인
일회용 권한을 한 번만 소비한다. 새 다이얼로그 호출이나 취소는 미소비 권한을
무효화한다.

호환성 때문에 다음 9개 이름과 시그니처는 등록을 유지하지만, 제품 실행 경로는 없고
모두 `FeatureRetired` 오류를 반환한다:
`start_coordinate_batch` `cancel_coordinate_batch` `retry_coordinate_batch`
`preflight_coordinate_batch` `enumerate_coordinate_batch_targets`
`list_coordinate_templates` `load_coordinate_template` `save_coordinate_template`
`delete_coordinate_template`

## 4. 기타 주의

- URL 파라미터 `?mode=canvas`, `?token=...` 로 standalone 캔버스 창이 뜬다.
  `body.standalone-canvas-window` 상태의 스타일을 반드시 유지(§1의 폐지 목록과 무관 —
  이 body 클래스는 유효하다).
- `data-command-proxy` 속성은 JS가 읽지 않지만(문서화 용도) 제거하지 말 것 —
  Python 테스트가 소스 텍스트를 검사할 수 있음.
- `dashboardSurfaces.ts`가 헬스 스트립·배치 큐 등 DOM을 **문자열로 생성·주입**한다.
  주입 마크업의 클래스만 조정 가능(로직 변경 금지). 이 셀렉터는 static JSX 정의가
  없으므로 체커의 `DYNAMIC_DOM_ALLOWLIST` 로만 관리된다.
- Python 테스트(tests/test_frontend_labels.py 등)가 JSX 소스 텍스트의 한국어
  라벨을 검사한다. 라벨 변경 시 테스트도 함께 갱신한다.
