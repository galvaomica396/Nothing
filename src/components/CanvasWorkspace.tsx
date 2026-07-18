import { Modal } from "./ui/Modal";
import { SymbolIcon } from "./ui/SymbolIcon";

// v4 P2 (REDESIGN_V4_DARK §1): 문서 관제 화면과 수동 보정(캔버스) 화면을 하나의
// "문서" 화면으로 통합했다. 이 화면이 data-screen-panel="documents"를 가져가고
// 상단 탭 "문서"가 이걸 가리킨다. 빈 상태(히어로) → PDF 열면 마스킹 미리보기가
// 곧 화면 → 마스킹 실행 후 우측 슬림 레일(검토/저장)이 의미를 갖는다.
export function CanvasWorkspace() {
  return (
    <section id="canvas-workspace-screen" className="dm-canvas is-active" data-screen-panel="documents" aria-label="문서 작업공간">
      <section id="mask-canvas-window" className="dm-canvas__window" aria-label="문서 마스킹 작업">
        {/* 런타임 전용 창 제목/접기 컨트롤 (오프스크린 유지) */}
        <div className="canvas-window-title dm-visually-hidden">
          <div>
            <span className="section-kicker">문서</span>
            <strong>문서 마스킹</strong>
            <span>상단 도구를 선택한 뒤 PDF 위에서 마스킹, 복원, 삭제를 수행합니다.</span>
          </div>
          <div className="canvas-title-actions">
            <button id="btn-collapse-canvas-tools" type="button" aria-pressed="false">도구 접기</button>
            <button id="btn-collapse-canvas-properties" type="button" aria-pressed="false">속성 접기</button>
            <button id="btn-expand-canvas-panels" type="button">펼치기</button>
            <button id="btn-close-canvas" type="button">작업 닫기</button>
          </div>
        </div>

        {/* ── 상단 도구 바: 보정 도구 · 키워드 · 보기 · 반영/저장 ── */}
        <div className="dm-canvas__toolbar" aria-label="문서 도구">
          <div className="dm-canvas__tool-group dm-canvas__tool-group--edit" aria-label="보정 도구">
            <div className="dm-seg dm-canvas__palette canvas-editor-palette" role="toolbar" aria-label="편집 도구">
              <button id="btn-canvas-tool-mask" className="tool-button is-active" type="button" data-canvas-tool="mask" aria-pressed="true" title="가릴 영역을 드래그하면 검은 박스로 덮습니다."><SymbolIcon name="draw" /><span>마스킹</span></button>
              <button id="btn-canvas-tool-restore" className="tool-button" type="button" data-canvas-tool="restore" aria-pressed="false" title="가려진 영역을 드래그하면 다시 보이게 되돌립니다."><SymbolIcon name="ink_eraser" /><span>복원</span></button>
              <button id="btn-canvas-tool-select" className="tool-button" type="button" data-canvas-tool="select" aria-pressed="false" title="박스를 클릭해 유형을 바꾸거나 삭제합니다."><SymbolIcon name="ads_click" /><span>선택</span></button>
              <button id="btn-canvas-tool-delete" className="tool-button" type="button" data-canvas-tool="delete" aria-pressed="false" title="클릭한 박스를 바로 지웁니다."><SymbolIcon name="delete" /><span>삭제</span></button>
              <button id="btn-canvas-tool-pan" className="tool-button" type="button" data-canvas-tool="pan" aria-pressed="false" title="드래그해서 문서 보기를 옮깁니다."><SymbolIcon name="open_with" /><span>이동</span></button>
            </div>
          </div>

          <div className="dm-canvas__tool-group dm-canvas__tool-group--keyword" aria-label="키워드">
            <button id="btn-open-keyword-dialog" className="dm-btn" type="button"><SymbolIcon name="find_replace" /><span>키워드 관리</span></button>
          </div>

          <div className="dm-canvas__tool-group dm-canvas__tool-group--view" aria-label="보기">
            <label className="dm-canvas__view-toggle"><input id="toggle-original-compare" type="checkbox" /><span>원문 대조</span></label>
            <div className="dm-canvas__zoom">
              <button id="btn-canvas-zoom-out" className="dm-btn dm-btn--ghost dm-icon-btn" type="button" aria-label="축소"><SymbolIcon name="zoom_out" /></button>
              <span id="zoom-info" className="dm-canvas__zoom-value">120%</span>
              <button id="btn-canvas-zoom-in" className="dm-btn dm-btn--ghost dm-icon-btn" type="button" aria-label="확대"><SymbolIcon name="zoom_in" /></button>
            </div>
          </div>


          {/* 배치(여러 PDF)는 보조 액션 — 접힘 패널로 수납. 선택·실행은 상단 바(헤더)와 공유. */}
          <details className="dm-canvas__tool-group dm-canvas__batch" aria-label="여러 문서 일괄 처리">
            <summary className="dm-canvas__batch-summary">
              <SymbolIcon name="library_add" />
              <span>여러 PDF</span>
              <span id="batch-summary" className="dm-badge dm-badge--muted">0개</span>
            </summary>
            <div className="dm-canvas__batch-panel">
              <div id="batch-queue" className="dm-canvas__queue">
                <div className="batch-empty">큐에 문서가 없습니다.</div>
              </div>
              <button id="btn-run-batch" className="dm-btn dm-btn--primary dm-canvas__batch-run" type="button" hidden>
                <SymbolIcon name="playlist_play" />
                <span>대기 N개 모두 마스킹</span>
              </button>
            </div>
          </details>

          {/* 작업 완결 순서: 박스 그리기 → 수동 보정 반영 → 최종 저장 */}
          <div className="dm-canvas__tool-group dm-canvas__tool-group--commit dm-canvas__commit" aria-label="반영·저장">
            <button id="btn-canvas-apply" className="dm-btn dm-canvas__commit-step" type="button" aria-describedby="canvas-tool-readiness"><SymbolIcon name="check_circle" /><span>수동 보정 반영</span></button>
            <SymbolIcon name="chevron_right" className="dm-canvas__commit-arrow" aria-hidden="true" />
            <button id="btn-canvas-final-save" className="dm-btn dm-btn--primary dm-canvas__commit-step" type="button" aria-describedby="canvas-tool-readiness"><SymbolIcon name="save" /><span>최종 저장</span></button>
            <button id="btn-new-document" className="dm-btn dm-btn--primary dm-canvas__commit-step is-hidden" type="button"><SymbolIcon name="note_add" /><span>새 작업 시작</span></button>
          </div>
        </div>

        {/* ── 상태 스트립: 선택한 도구 한 줄 안내 + 게이트 사유 ── */}
        <div className="dm-canvas__statusbar">
          <p className="dm-canvas__guide" role="status" aria-live="polite">
            <span className="dm-canvas__guide-dot" aria-hidden="true"></span>
            <span data-guide-for="mask">드래그해 가릴 영역을 덮습니다.</span>
            <span data-guide-for="restore">드래그해 가린 영역을 되돌립니다.</span>
            <span data-guide-for="select">박스를 클릭해 유형 변경·삭제합니다.</span>
            <span data-guide-for="delete">클릭한 박스를 지웁니다.</span>
            <span data-guide-for="pan">드래그해 문서 보기를 옮깁니다.</span>
          </p>
          <p id="canvas-tool-readiness" className="dm-canvas__readiness" aria-live="polite">PDF를 불러오고 기본 마스킹 미리보기를 만든 뒤 편집할 수 있습니다.</p>
        </div>

        {/* ── 본문: 미리보기 스테이지 + 우측 검토/저장 레일 ── */}
        <div className="dm-canvas__body">
          <section className="dm-canvas__stage" aria-label="PDF 미리보기 스테이지">
            {/* 빈 상태 — 1-2-3 작업 흐름과 문서 열기 CTA. PDF가 렌더되면(.has-rendered-pdf) 숨겨진다. */}
            <div className="dm-canvas__hero" aria-label="시작 안내">
              <div className="dm-canvas__flow" aria-label="PDF 마스킹 작업 순서">
                <div className="dm-canvas__flow-step is-current">
                  <span className="dm-canvas__flow-number">1</span>
                  <strong>PDF 열기</strong>
                </div>
                <span className="dm-canvas__flow-line" aria-hidden="true"></span>
                <div className="dm-canvas__flow-step">
                  <span className="dm-canvas__flow-number">2</span>
                  <strong>자동 마스킹</strong>
                </div>
                <span className="dm-canvas__flow-line" aria-hidden="true"></span>
                <div className="dm-canvas__flow-step">
                  <span className="dm-canvas__flow-number">3</span>
                  <strong>수동 보정 및 저장</strong>
                </div>
              </div>
              <strong className="dm-canvas__hero-title">PDF를 불러와 마스킹을 시작하세요</strong>
              <span className="dm-canvas__hero-desc">공문 PDF를 열면 개인정보를 가린 미리보기가 만들어지고, 이 화면에서 직접 보정할 수 있습니다.</span>
              <div className="dm-canvas__hero-actions">
                <button id="btn-canvas-load-pdf" className="dm-btn dm-btn--primary dm-canvas__hero-cta" type="button"><SymbolIcon name="folder_open" /><span>PDF 열기</span></button>
                <button id="btn-canvas-hero-batch" className="dm-btn dm-btn--ghost" type="button" data-command-proxy="btn-pick-batch"><SymbolIcon name="library_add" /><span>여러 PDF</span></button>
              </div>
            </div>

            <div className="dm-canvas__compare">
              <div className="dm-canvas__compare-grid" id="pdf-compare-view">
                <div id="original-compare-panel" className="dm-canvas__viewer is-hidden">
                  <div className="dm-canvas__viewer-head">
                    <span className="dm-canvas__viewer-name"><span className="dm-dot dm-dot--orig" aria-hidden="true"></span>원문</span>
                    <span className="dm-canvas__viewer-meta" id="viewer-meta-orig">페이지 0/0</span>
                  </div>
                  <div className="dm-canvas__scroll">
                    <div id="canvas-wrap-orig" className="dm-canvas__wrap">
                      <canvas id="pdf-canvas-orig"></canvas>
                    </div>
                  </div>
                </div>
                <div id="masked-preview-panel" className="dm-canvas__viewer">
                  <div className="dm-canvas__viewer-head">
                    <span className="dm-canvas__viewer-name"><span className="dm-dot dm-dot--result" aria-hidden="true"></span>마스킹 미리보기</span>
                    <span className="dm-canvas__viewer-meta" id="viewer-meta-result">페이지 0/0</span>
                  </div>
                  <div className="dm-canvas__scroll">
                    <div id="canvas-wrap-result" className="dm-canvas__wrap">
                      <div className="dm-canvas__placeholder dm-empty-state" aria-label="PDF 미리보기 대기">
                        <SymbolIcon name="draw" />
                        <strong>PDF 미리보기 대기</strong>
                        <span>문서를 열고 기본 마스킹을 실행하면 이 영역에 실제 PDF 미리보기가 표시됩니다.</span>
                      </div>
                      <canvas id="pdf-canvas-result"></canvas>
                      <canvas id="overlay-canvas-result"></canvas>
                    </div>
                  </div>
                </div>
              </div>

              <div className="dm-canvas__compare-grid dm-canvas__compare-grid--text is-hidden" id="text-compare-view">
                <div className="dm-canvas__viewer">
                  <div className="dm-canvas__viewer-head"><span className="dm-canvas__viewer-name">추출 텍스트</span><span className="dm-canvas__viewer-meta">추출 결과</span></div>
                  <pre id="extracted-text-view" className="dm-canvas__text">마스킹 실행 후 추출 텍스트가 표시됩니다.</pre>
                </div>
                <div className="dm-canvas__viewer">
                  <div className="dm-canvas__viewer-head"><span className="dm-canvas__viewer-name">마스킹 텍스트</span><span className="dm-canvas__viewer-meta">마스킹 결과</span></div>
                  <pre id="masked-text-view" className="dm-canvas__text">마스킹 실행 후 마스킹 텍스트가 표시됩니다.</pre>
                </div>
              </div>
            </div>

            <div className="dm-canvas__viewbar" aria-label="문서 보기 도구">
              <div className="dm-canvas__pager">
                <button id="btn-prev-orig" className="dm-btn dm-btn--ghost dm-icon-btn" type="button" aria-label="이전 페이지"><SymbolIcon name="chevron_left" /></button>
                <span className="dm-canvas__pager-label">0 / 0</span>
                <button id="btn-next-orig" className="dm-btn dm-btn--ghost dm-icon-btn" type="button" aria-label="다음 페이지"><SymbolIcon name="chevron_right" /></button>
              </div>
              <button id="btn-prev-result" className="dm-visually-hidden" type="button">미리보기 이전</button>
              <button id="btn-next-result" className="dm-visually-hidden" type="button">미리보기 다음</button>
              <div className="dm-seg dm-canvas__compare-tabs">
                <button id="compare-mode-pdf" className="is-active" type="button" role="tab" aria-selected="true">PDF 보기</button>
                <button id="compare-mode-text" type="button" role="tab" aria-selected="false">텍스트 보기</button>
              </div>
              <label className="dm-canvas__sync"><input id="sync-pages" type="checkbox" defaultChecked /><span>페이지 동기</span></label>
            </div>
          </section>

          {/* ── 우측 슬림 레일: 검토 참고 항목 + 저장. 검증 결과는 권고이며 저장을
               막지 않는다. 마스킹 실행 전에는 검토 카드가 대기 상태다. 현재 페이지
               박스/선택 박스는 직접 편집을 돕는 최소 컨트롤로 유지(수치 없음). ── */}
          <aside className="dm-canvas__inspector dm-inspector" id="side-panel" aria-label="검토·저장 레일">
            <header className="dm-inspector__bar">
              <span className="dm-inspector__bar-title"><SymbolIcon name="fact_check" />검토·저장</span>
              <button
                id="btn-toggle-inspector"
                className="dm-btn dm-btn--ghost dm-inspector__collapse"
                type="button"
                aria-expanded="true"
                aria-controls="side-panel"
                aria-label="패널 접기"
              >
                <SymbolIcon name="dock_to_right" />
              </button>
            </header>

            <div className="dm-inspector__scroll">
              <section className="dm-inspector__card dm-detect" id="final-state-card" data-state="idle">
                <span className="dm-section-label" id="obsidian-detection-heading">검토 필요 항목</span>
                <section id="obsidian-detection-list" className="dm-detect__list">
                  <div>
                    <i className="dot-primary"></i>
                    <strong>마스킹 실행 후 표시됩니다</strong>
                    <em>0건</em>
                  </div>
                </section>
                <footer className="dm-detect__state">
                  <strong id="final-state-title">대기 중</strong>
                  <b id="final-state-detail">문서를 열고 마스킹을 실행하세요.</b>
                </footer>
              </section>

              <section className="dm-card dm-canvas__panel" aria-label="현재 페이지 박스">
                <div className="dm-card__header">
                  <span className="dm-card__title">현재 페이지 박스</span>
                </div>
                <div id="canvas-box-properties" className="dm-canvas__props is-empty">
                  <dl className="dm-canvas__prop-grid dm-visually-hidden">
                    <div><dt>페이지</dt><dd id="canvas-box-property-page">-</dd></div>
                    <div><dt>유형</dt><dd id="canvas-box-property-type">-</dd></div>
                    {/* 좌표·크기(px)는 사용자가 알 필요 없는 내부 수치 — DOM 유지, 화면에서 숨김 */}
                    <div><dt>좌표</dt><dd id="canvas-box-property-coordinates">-</dd></div>
                    <div><dt>크기</dt><dd id="canvas-box-property-size">-</dd></div>
                  </dl>
                  <div className="dm-canvas__prop-actions">
                    <button id="btn-canvas-box-convert-mask" className="dm-btn" type="button">마스킹으로 전환</button>
                    <button id="btn-canvas-box-convert-restore" className="dm-btn" type="button">복원으로 전환</button>
                    <button id="btn-canvas-box-delete" className="dm-btn dm-btn--danger" type="button">선택 삭제</button>
                  </div>
                </div>
                <div id="canvas-box-list" className="dm-canvas__box-list">
                  <div className="canvas-box-empty dm-empty-state">현재 페이지에 박스가 없습니다.</div>
                </div>
              </section>

              <section className="dm-inspector__card dm-savesummary" aria-label="저장 요약">
                <span className="dm-section-label">저장 요약</span>
                <div className="dm-savesummary__grid">
                  <div className="dm-kv"><span>마스킹 박스</span><strong id="review-summary-mask-count">0개</strong></div>
                  <div className="dm-kv"><span>복원 박스</span><strong id="review-summary-restore-count">0개</strong></div>
                  <div className="dm-kv"><span>키워드</span><strong id="review-summary-keyword-count">0개</strong></div>
                  <div className="dm-kv"><span>결과 파일</span><strong id="review-summary-output-file">-</strong></div>
                  <div className="dm-kv"><span>PDF 가림</span><strong id="review-summary-pdf-policy">검정 박스</strong></div>
                  <div className="dm-kv"><span>TXT 산출</span><strong id="review-summary-txt-policy">저장 안 함</strong></div>
                </div>
                {/* 캔버스 요약 프록시(오프스크린) — 저장 게이트 텍스트 소스 유지 */}
                <p className="dm-canvas__summary dm-visually-hidden">
                  <strong id="canvas-summary-mask-count">마스킹 박스 0개</strong>
                  <strong id="canvas-summary-restore-count">복원 박스 0개</strong>
                  <strong id="canvas-summary-keyword-count">키워드 0개</strong>
                  <span id="canvas-summary-output-state" className="dm-badge dm-badge--warning">최종 저장 전 확인 필요</span>
                </p>
                <div className="canvas-keyword-panel dm-visually-hidden" aria-label="반영 키워드">
                  <strong>반영 키워드</strong>
                  <div id="keyword-chip-preview" className="keyword-chip-preview">
                    <span>등록된 키워드 없음</span>
                  </div>
                </div>
              </section>

              {/* 저장 게이트 사유 한 줄 — 스크롤 영역 하단에 두어 고정 액션 푸터가
                  저장 버튼까지 잘리지 않도록 한다. */}
              <p id="final-save-readiness" className="dm-savegate__readiness" aria-live="polite">
                저장할 마스킹본이 준비되면 저장 위치와 파일명을 한 번에 선택합니다.
              </p>
            </div>

            <footer className="dm-inspector__actions">
              <button id="btn-save" className="dm-btn dm-btn--primary" type="button" aria-describedby="final-save-readiness">
                <SymbolIcon name="save" />
                최종 저장
              </button>
            </footer>
          </aside>
          <button id="btn-open-canvas-properties-tab" className="dm-canvas__props-tab" type="button">검토 패널 열기</button>
        </div>

        {/* 런타임 명령 프록시 + 화면에서 제거된 표면의 상태 소스(오프스크린 유지).
            page/box 카운트는 사용자가 알 필요 없는 내부 수치라 화면에서 제거하되
            컨트롤러 배선을 위해 프록시로만 유지한다. */}
        <section className="manual-command-proxy dm-visually-hidden" aria-label="수동 보정 명령">
          <label><input id="mode-mask" type="radio" name="mode" defaultChecked /> 마스킹 추가</label>
          <label><input id="mode-restore" type="radio" name="mode" /> 복원</label>
          <input id="input-path" type="text" readOnly aria-label="선택된 문서 경로" />
          <span id="page-info-orig">0/0</span>
          <span id="page-info-result">0/0</span>
          <span id="box-info">0개</span>
          <button id="btn-canvas-delete-box" type="button">선택 삭제</button>
          <button id="btn-canvas-undo" type="button">실행취소</button>
          <button id="btn-canvas-clear" type="button">전체초기화</button>
          <button id="btn-undo" type="button">실행취소</button>
          <button id="btn-clear" type="button">초기화</button>
          <button id="btn-manual-apply" type="button">미리보기 반영</button>
          <button id="btn-mask-canvas" type="button" aria-label="PDF 작업창 열기" title="PDF 작업창 열기">PDF 작업창 열기</button>
          <button id="btn-open-canvas-window" type="button">현재 창 작업공간 열기</button>
          <strong id="canvas-active-tool-label">마스킹 박스</strong>
        </section>

        <Modal
          id="keyword-dialog"
          titleId="keyword-dialog-title"
          title="키워드 관리"
          description="쉼표 또는 줄바꿈으로 구분해 입력합니다. 입력한 내용은 결과 파일이나 로그에 저장하지 않습니다."
          closeButtonId="btn-close-keyword-dialog"
          footer={(
            <button id="btn-keyword-dialog-apply" className="dm-btn dm-btn--primary" type="button">키워드 적용 후 다시 마스킹</button>
          )}
        >
          <div className="dm-keyword-dialog">
            <section className="dm-keyword-dialog__section">
              <div className="dm-section-label">키워드 입력</div>
              <div className="dm-field">
                <label htmlFor="custom-keywords">마스킹할 키워드</label>
                <textarea id="custom-keywords" className="dm-input dm-keyword-dialog__textarea" rows={6} placeholder="예: 홍길동, 서울시청&#10;프로젝트 코드" />
              </div>
              <div className="dm-section-label">등록 키워드</div>
              <div id="keyword-dialog-chip-list" className="keyword-chip-preview dm-keyword-dialog__chips">
                <span>등록된 키워드 없음</span>
              </div>
              <button id="btn-keyword-policy" className="dm-keyword-dialog__policy" type="button" data-screen-target="masking-settings">탐지 기준과 키워드 정책 보기</button>
            </section>
          </div>
        </Modal>

        <Modal
          id="new-document-dialog"
          titleId="new-document-dialog-title"
          title="진행 중인 작업이 있습니다"
          description="저장하지 않고 새 작업을 시작하시겠습니까? 기존 작업 내역은 사라집니다."
          closeButtonId="btn-close-new-document-dialog"
          footer={(
            <>
              <button id="btn-cancel-new-document" className="dm-btn dm-btn--ghost" type="button">취소</button>
              <button id="btn-confirm-new-document" className="dm-btn dm-btn--danger" type="button">새 작업 시작</button>
            </>
          )}
        >
          <div className="dm-modal-message">저장할 내용이 있다면 취소한 뒤 최종 저장을 먼저 완료하세요.</div>
        </Modal>

        {/* 저장 직전 확인 모달 — 검증 경고는 권고이며 저장을 막지 않는다.
            사용자는 검토로 돌아가거나 경고를 확인하고 그대로 저장할 수 있다. */}
        <Modal
          id="final-save-dialog"
          titleId="final-save-dialog-title"
          title="저장 전 확인 (권고사항)"
          description="문서 저장을 진행할 수 있습니다. 다만 검증 시스템이 일부 영역에 대한 재검토를 권장하고 있습니다."
          closeButtonId="btn-close-final-save-dialog"
          wide
          footer={(
            <>
              <button id="btn-dialog-cancel-save" className="dm-btn dm-btn--ghost" type="button">취소하고 검토하기</button>
              <button id="btn-dialog-save-all" className="dm-btn dm-btn--primary" type="button">무시하고 그대로 저장</button>
            </>
          )}
        >
          <div className="dm-savewarn__summary" role="note">
            <span className="dm-savewarn__summary-icon" aria-hidden="true">!</span>
            <div>
              <strong>추가 확인이 필요한 항목이 있습니다</strong>
              <span>권고 사항을 검토하거나, 확인 후 그대로 저장할 수 있습니다.</span>
              <span id="final-save-dialog-state" className="dm-badge status-chip status-chip-warn">확인할 사항 없음</span>
              <ul id="final-save-warning-list" className="dm-savewarn" aria-label="저장 전 확인 권장 사항">
                <li className="dm-savewarn__empty">권고할 사항이 없습니다. 그대로 저장할 수 있습니다.</li>
              </ul>
            </div>
          </div>
          <p className="dm-savewarn__location-note">
            권고 사항을 확인하고 계속하면 저장 위치와 파일명을 OS 저장 창에서 선택합니다.
          </p>
        </Modal>
      </section>
      <div id="canvas-mode-status" className="canvas-status-proxy dm-visually-hidden">작업창 대기</div>
    </section>
  );
}
