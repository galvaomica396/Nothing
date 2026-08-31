# Real packaged-app E2E and visible acceptance

## 완료 판정

`npm run e2e:real`과 `npm run qa:*`는 진단·회귀용이다. 실앱 픽셀 수용 기준은
`npm run accept:real`이며, 15종 해시 매니페스트를 전부 실행해 `PASS`해야 한다.
저장소의 완료 선언은 전체 검증을 포함한 `npm run gate:complete` 통과로만 한다.
증거 위계는 다음 순서로 고정한다.

1. 외부 `screencapture` 이미지의 실제 화면 픽셀 변화
2. 실앱 `qa-drive dump-state` (좌표·페이지 이동 보조)
3. CLI 매니페스트·해시
4. 목(mock)·픽스처 QA (회귀 감지 전용)

위 2~4번만 통과한 결과는 완료로 선언할 수 없다. 화면 캡처 권한, 잠금 세션,
패키지 앱 부재 등으로 수용 시험을 실행하지 못하면 결과는 `PENDING`이다.

`contracts/real-corpus.json`은 파일명이나 경로를 저장하지 않고 SHA-256, 비식별 alias,
라우팅 category만 저장한다. `scripts/real_corpus.mjs`와 `scripts/real_corpus.py`가
`NOTHING_REAL_CORPUS_DIR`(기본 `~/Downloads`)의 PDF를 해시로 대조하며, 15종 전부가
일치하지 않으면 `REAL_CORPUS_INCOMPLETE`로 즉시 실패한다. 실문서 검증은 이 리졸버를
통한 항목만 유효하다.

완료 증거의 위계는 외부 실앱 화면 픽셀 > 실앱 `qa-drive dump-state` 상태 >
CLI 매니페스트 > 목(mock)·픽스처 QA다. 따라서 `목/픽스처 QA 초록은 완료 근거로 인용 금지`이며,
상태·CLI·목 QA만 통과한 결과는 완료로 선언할 수 없다.
`npm run gate:complete`가 계약 검사, `pytest tests/`, `cargo test`, 네 가지 QA와
`npm run accept:real`을 이 순서로 실행하고 `.omo/acceptance/<git-HEAD-sha>.json`에
문서별 결과와 수용 전제조건 상태를 기록한다. `npm run contract`는 이 기록의 코드 지문
신선도를 stderr 배너로 표시하며, 완료 선언은 `gate:complete` 통과로만 한다.

이 게이트는 자발적 호출에 의존하므로 악의적인 우회를 막지 못한다. 목적은 기본 경로를
실앱 검증 포함으로 만들고 우회를 눈에 보이게 하는 것이며, 완전한 강제나 보안 장치라고
과장하지 않는다.

## Visible acceptance

```sh
npm run build
npx tauri build
npm run gate:complete
```

`accept:real`은 각 매니페스트 문서를 별도 임시 스크래치에 복사하고, 원본 폴더가 아닌
그 스크래치만 `MASK_TOOL_ALLOWED_DIRS`에 넣어 패키지 앱을 연다. 외부 스크린샷으로
자동 마스킹의 어두워진 픽셀과 occurrence rect 겹침, 다른 페이지 안내, 수동 드래그
반영, 실제 토큰 키워드 반영을 확인한다. 마지막에는 저장된 PDF를 다시 렌더해 가림
영역이 검은지 확인한다. 앱 카운터나 상태 덤프는 이 판정에 사용하지 않는다.

스크린샷은 `.omo/evidence/acceptance-real-app/`에 alias와 해시 접두를 사용한 이름으로
남기며 이 경로는 Git에서 무시한다. 저장된 PDF는 검증 후 임시 스크래치와 함께 삭제한다.
표 형식 결과는 `.omo/evidence/T52-baseline.md`에 저장된다.

수동 마스킹은 반드시 `drag → apply → 외부 픽셀 비교` 순서로 판정한다. 현재 구현에서
이 픽셀 비교가 실패하면 그것은 실패 결과이며, IPC 기록이나 목 QA가 초록이어도 완료가
아니다.

`npm run e2e:real` is a legacy one-document diagnostic for the packaged macOS app, not the
completion gate. It launches the bundle with a QA-only stdin command channel, runs the geometry
fixture scenario, then runs the six-step diagnostic scenario against a scratch copy of one
`contracts/real-corpus.json` entry. Screenshots are captured with `screencapture`, cropped to the
native main-window bounds returned by the running app, and checked for both visual and semantic
results.

Run this on a host with Screen Recording permission, Python 3 with Pillow (`PIL`), a current packaged app, and the bundled masking runtime:

```sh
npm run build
npx tauri build
E2E_REAL_DOC=/absolute/path/to/document.pdf npm run e2e:real
```

`E2E_REAL_DOC` may identify one manifest entry by its exact source path for this diagnostic. If it
is unset, the first hash-manifest entry is used. An unset, missing, non-file, non-PDF, or
non-manifest path fails with `E2E_REAL_DOC_MISSING`. Missing/non-file/non-PDF validation happens
before app launch; manifest membership is checked after the one-time display preflight and before
any document is copied or opened.

The process must run in the logged-in macOS desktop session that owns Screen Recording permission. A headless or restricted shell intentionally fails early with `E2E_DISPLAY_UNAVAILABLE`, `frontend-ready`, or `screencapture-*`; neither result is a masking-engine pass.

To target another bundle, set `REAL_APP_PATH` (or pass `--app` directly):

```sh
REAL_APP_PATH=/absolute/path/Nothing.app npm run e2e:real
```

The harness copies the geometry fixture and the selected manifest document into a unique temporary
directory. The real input is always opened through the fixed alias `real-document-input.pdf`; the
original path and filename are never opened, logged, or used in evidence filenames.
`MASK_TOOL_ALLOWED_DIRS` contains only that scratch directory, and the app's ordinary
output-directory logic therefore keeps runtime artifacts isolated from the source document
directory. The scratch directory is removed after the run. Real-document captures use the fixed
names `.omo/evidence/real-app-e2e/real-app-real-document-before-masking.png` and
`.omo/evidence/real-app-e2e/real-app-real-document-after-masking.png`.

The geometry fixture remains a separate scenario because T40 automatic confirmation means a real document does not generate the unresolved geometry state needed to exercise that review path. The separation is intentional: geometry coverage runs on the public fixture, while real-document coverage verifies the states the real document actually produces.

The real-document scenario is: open the isolated copy, apply the `mixed` profile, run masking and wait idle, verify `overlayPaintedPixelCount > 0`, verify a running drag is rejected with no box and the blocking status text, apply a post-run mask drag, apply a disjoint restore drag, and verify both manual modes plus `data-state="ready"` on `#final-save-readiness`. The harness prints six `[real-doc][1]` through `[real-doc][6]` evidence milestones.

The QA driver is unavailable in normal production launches. It starts only with both
`--qa-drive-stdin` and `MASK_TOOL_QA_DRIVE=1`; QA document registration additionally refuses
PDFs outside `MASK_TOOL_ALLOWED_DIRS`. The preflight-only `render-probe on|off|clear`
command toggles a visible sentinel and waits for real animation frames. The command stream
includes `open <path>`, `set-profile <profile>`, `set-tool <tool>`, `start-masking`, `run-masking`, `wait-idle`,
`go-page <page>`, `apply-keyword <token>`, `resolve-review <review-id> <action>`,
`drag-canvas <x0> <y0> <x1> <y1>`, `draw-box <x0> <y0> <x1> <y1> [mask|restore]`,
`apply-manual`, `save-final`, and `dump-state`. `save-final` uses a scratch-only QA save
target; it exercises the same finalization path as the visible confirmation button without
opening an uncontrolled native save dialog. `start-masking` begins the asynchronous run and
returns while `maskingRunning` is true so the next `drag-canvas` can verify the rejection
contract. `drag-canvas` interprets coordinates as overlay-local canvas pixels and dispatches
the production overlay's real DOM `mousedown` -> multiple `mousemove` -> window `mouseup` path.
`draw-box` is retained as a direct state-injection fixture and does not validate pointer
handling. Both paths reuse the same public manual-apply handler as `btn-canvas-apply`;
real-path scenarios must use `drag-canvas` before asserting draft creation and post-apply box
cleanup.

## Preconditions

`accept:real` and `e2e:real` run the shared host preflight before resolving the
15-document manifest or copying any corpus document. The result is recorded as a
structured `Preconditions` block in the acceptance report; `e2e:real` prints the
same precondition/result objects on stdout. An unknown probe is not a pass and
never falls through to a document-by-document timeout.

- `E2E_SESSION_LOCKED` — the macOS session dictionary is read through
  `CGSessionCopyCurrentDictionary` (Quartz, with a CoreGraphics fallback). A
  locked screen or a session that is not on the console stops the run.
- `E2E_DISPLAY_UNAVAILABLE` — the preflight requires a parsed
  `ioreg -r -k AppleClamshellState` value, at least one online and awake display
  from `CGGetActiveDisplayList`, a successful screen-capture probe, and a visible
  app window. The error includes the reason (`clamshell=unknown`,
  `no-display`, `capture-unavailable`, `window-invisible`, or `render=...`)
  and tells the operator: **노트북 덮개를 열거나 외부 디스플레이를 활성화한 뒤 다시 실행하세요.**
- `E2E_WINDOW_NOT_VISIBLE` — after the frontend reports ready, the harness
  verifies a layer-0, on-screen window for the app PID and its geometric
  intersection with an active display. A missing window is distinct from a
  display that cannot render it.
- A closed clamshell is diagnostic information, not a veto. A closed lid with
  an active external display, an on-screen intersecting window, capture access,
  and a changed visible render sentinel is allowed. If the window or sentinel
  cannot be observed, the result is `E2E_DISPLAY_UNAVAILABLE`, not 15 repeated
  `open` timeouts.

The app preflight toggles a QA-only visible render sentinel and captures the
window before and after the toggle. A positive external pixel delta proves that
the visible frame is advancing; `dump-state` or an accessibility window count
alone is insufficient. The preflight is performed once per app session, before
the real corpus is resolved.

QA-drive command budgets are defined once in
`contracts/qa-drive-timeouts.json`: startup 30 seconds, `open` 180 seconds,
control 10 seconds, navigation 30 seconds, long-running analysis/save 30
minutes, and response IPC 5 seconds. A render-stage deadline returns
`QA_DRIVE_RENDER_UNAVAILABLE:stage=<stage>`; other command deadlines return a
stage-labelled `QA_DRIVE_COMMAND_TIMEOUT`. PDF loading and `RenderTask`
waiting receive the command abort signal, destroy/cancel their underlying work,
invalidate the document lifecycle on timeout, and do not publish late state or
start a subsequent measurement before cancellation cleanup.

Headless/offscreen PDF and manifest checks remain diagnostic lanes. They cannot
replace the external `screencapture` pixel evidence required by visible
acceptance.

Pass criteria:

- the dump-state reports a loaded document, rendered overlay pixels, and at least one review card;
- the after-analysis real-screen capture gains opaque dark pixels relative to the pre-analysis capture, matching the rendered detection overlay;
- the six real-document evidence milestones are printed and the final manual restore leaves the save gate ready;
- any failure exits nonzero and names its failed step.

The legacy `e2e:real` diagnostic is intentionally not the completion gate. The explicit
completion command is `gate:complete`; its final `accept:real` step remains a host-side
requirement because it needs a logged-in macOS desktop, Screen Recording permission, and the
15 local corpus PDFs.
