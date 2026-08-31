# 런타임 계약

`contracts/converted-screens.json`이 화면 소유권의 단일 소스다. 다섯 정적
`[data-screen-panel]` 루트는 모두 해당 설정에 등록되고 `data-owner="react"`여야
한다. `scripts/check_runtime_contract.mjs`는 설정에 없는 루트와
`data-owner="legacy"` 재도입을 실패로 처리한다.

React는 화면 구조·이벤트·모달 가시성을 소유한다. `src/app/`은 화면 전환을 다시
소유하지 않고, React가 마운트한 캔버스와 아래의 런타임 요소를 연결해 PDF 렌더,
마스킹 실행, 배치, 진행 상태, QA IPC를 조합한다. `AppCompositionRoot`의 effect
cleanup은 document wiring, application controller, runtime controller 등록을 같은
순서로 해제한다. QA drive는 StrictMode-safe singleton 설치를 유지한다.

정적 검증은 `node scripts/check_runtime_contract.mjs`다. 현재 검증 표면은 61개
selector이며, 화면별 `ownedIds` 전체 목록은 prose가 아닌
`contracts/converted-screens.json`을 따른다.

## 1. 소유권과 조합 경계

- React 화면: `documents`, `desk`, `storage`, `settings`, `masking-settings`.
- 조합 경계: `src/app/compositionRoot.ts`, `applicationController.ts`,
  `domBindings.ts`, `wiring/documentWiring.ts`, `qaDrive.ts`.
- `src/app/domBindings.ts`가 참조하는 요소만 부팅 하드 계약이다. 캔버스 노드는
  React ref 등록(`workspaceCanvasMount`)으로 전달되므로 selector로 중복 조회하지
  않는다.
- 동적 목록과 배치 행은 정적 JSX 정의가 아니며 checker allowlist로만 관리한다.

## 2. 현재 selector 표면

상태·문서 제어:
`status`, `status-detail`, `input-path`, `viewer-meta-orig`,
`viewer-meta-result`, `zoom-info`, `btn-pick-pdf`, `btn-pick-batch`,
`btn-run-masking`, `btn-run-batch`, `btn-prev-orig`, `btn-next-orig`, `btn-save`,
`final-save-readiness`, `sync-pages`.

보기·검토·배치:
`compare-mode-pdf`, `compare-mode-text`, `toggle-original-compare`,
`original-compare-panel`, `workspace-shell`, `side-panel`,
`btn-toggle-inspector`, `batch-summary`, `batch-queue`, `final-state-card`,
`final-state-title`, `final-state-detail`, `canvas-active-tool-label`,
`canvas-tool-readiness`, `canvas-box-list`, `canvas-box-properties`,
`canvas-box-property-page`, `canvas-box-property-type`,
`canvas-box-property-coordinates`, `canvas-box-property-size`.

캔버스 편집:
`btn-canvas-tool-select`, `btn-canvas-tool-mask`, `btn-canvas-tool-restore`,
`btn-canvas-tool-pan`, `btn-canvas-tool-delete`, `btn-canvas-zoom-out`,
`btn-canvas-zoom-in`, `btn-canvas-undo`, `btn-canvas-clear`,
`btn-canvas-apply`, `btn-new-document`, `btn-canvas-box-delete`,
`btn-canvas-box-convert-mask`, `btn-canvas-box-convert-restore`.

페이지 구간 스트립:
`segment-thumbnail-strip`, `segment-boundary-kind`,
`btn-apply-segment-boundary`. 이 표면은 React store가 소유하며, pending `boundary`
검토가 없는 세션에서는 편집 컨트롤을 렌더하지 않는다. 현재 61개 런타임 query
selector 수는 그대로이고, 위 세 ID는 `converted-screens.json`의 documents owned-ID
계약에 추가된다.

모달·진행:
`new-document-dialog`, `btn-close-new-document-dialog`,
`btn-cancel-new-document`, `btn-confirm-new-document`, `final-save-dialog`,
`masking-progress-dialog`, `masking-progress-value`,
`masking-progress-percent`, `masking-progress-stage`, `masking-progress-pages`,
`masking-progress-detected`, `masking-progress-elapsed`,
`btn-close-masking-progress-dialog`, `btn-cancel-masking-progress`.

위 61개가 checker가 보고하는 ID selector 전체다. 조합 계층은 추가로
`.dm-canvas__pager-label`, `[data-screen-panel="documents"]`,
`[data-screen-target="…"]`, `[data-modal-autofocus]`에 의존한다. 이 중 화면 루트는
transition discipline 검사로 별도 검증하며, class/data selector는 63개 ID 수치에
포함하지 않는다.

## 3. Tauri IPC

`create_canvas_launch_token`, `take_canvas_launch_payload`, `read_pdf_bytes`,
`read_text_file`, `get_preview_workdir`, `pick_input_pdf`, `open_mask_canvas_window`,
`pick_input_document`, `pick_input_documents`, `pick_output_dir`,
`run_masking_pipeline`, `apply_manual_boxes`, `finalize_manual_output`,
`choose_final_pdf_path`, `finalize_manual_output_to_selected_path`,
`default_output_dir_for_document`, `existing_output_dir`의 이름과 호출 형태는
유지한다.

## 4. 변경 규칙

새 화면은 React로 만들고 `converted-screens.json`에 루트와 owned IDs를 함께
등록한다. `data-owner="legacy"` 또는 `data-legacy-binding`은 다시 추가하지
않는다. selector를 지우거나 옮길 때는 checker, build, Python gate와 실제 앱 QA를
함께 통과해야 한다.
