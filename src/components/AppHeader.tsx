import { SymbolIcon } from "./ui/SymbolIcon";

// 상단 바는 단일 문서 작업 흐름과 두 설정 진입점만 제공한다.
export function AppHeader() {
  return (
    <header className="dm-header" aria-label="상단 작업 바">
      <div className="dm-header__brand">
        <button className="dm-header__home" type="button" data-screen-target="documents" aria-label="문서 홈">
          <img className="dm-header__logo" src="/favicon.png" alt="" aria-hidden="true" />
          <span className="dm-header__brand-name">Nothing</span>
        </button>
      </div>
      <div className="dm-header__doc" aria-label="현재 문서">
        <strong id="current-document-title">문서를 선택하세요</strong>
        <em id="obsidian-target-summary">문서 미선택</em>
      </div>
      <div className="dm-header__actions" aria-label="문서 명령">
        <button id="btn-pick-pdf" className="dm-btn" type="button">
          <SymbolIcon name="note_add" />
          <span className="dm-header__action-label">PDF 선택</span>
        </button>
        <button id="btn-pick-batch" className="dm-btn" type="button">
          <SymbolIcon name="library_add" />
          <span className="dm-header__action-label">여러 PDF</span>
        </button>
        <button id="btn-run-masking" className="dm-btn dm-btn--primary" type="button" disabled>
          <SymbolIcon name="play_arrow" className="fill dm-run-icon dm-run-icon--idle" />
          <SymbolIcon name="hourglass" className="dm-run-icon dm-run-icon--busy" />
          <span data-role="run-label">현재 PDF 마스킹</span>
        </button>
      </div>
      <div className="dm-header__health" id="app-health-strip" aria-label="작업 상태 요약" />
      {/* 통합 문서 화면에서 마스킹 설정으로 이동하는 슬림 진입점. */}
      <button
        className="dm-header__gear"
        type="button"
        data-screen-target="masking-settings"
        aria-label="탐지 기준"
        title="탐지 기준 조정"
      >
        <SymbolIcon name="find_replace" />
      </button>
      <button
        className="dm-header__gear"
        type="button"
        data-screen-target="settings"
        aria-label="설정"
        title="설정"
      >
        <SymbolIcon name="settings" />
      </button>
    </header>
  );
}
