import { SymbolIcon } from "./ui/SymbolIcon";
import { setActiveScreen } from "../state/shellStore";
import { beginSettingsDraft } from "../state/settingsStore";
import { setDeskSearchQuery, setStorageSearchQuery, useSessionDocumentsState } from "../state/sessionDocumentsStore";
import { applicationController } from "../state/appControllerRuntime";

function openSettingsScreen(): void {
  applicationController()?.rememberAuxReturnScreen();
  setActiveScreen("settings");
}

function openMaskingSettingsScreen(): void {
  applicationController()?.rememberAuxReturnScreen();
  beginSettingsDraft();
  setActiveScreen("masking-settings");
}

// 상단 바(64px)는 현재 문서 컨텍스트와 문서 명령만 제공한다.
// 화면 전환(브랜드 홈·마스킹 설정·설정)은 좌측 사이드바로 이동했다 (2026-08 KRDS 시안).
export function AppHeader() {
  const session = useSessionDocumentsState();

  return (
    <header className="dm-header" aria-label="상단 작업 바">
      <div className="dm-header__screen dm-header__screen--desk" aria-label="문서 데스크 상단">
        <label className="dm-header__search">
          <SymbolIcon name="search" />
          <input id="desk-search-input" type="search" placeholder="문서명·유형으로 검색" aria-label="문서명·유형으로 검색" value={session.deskQuery} onChange={(event) => setDeskSearchQuery(event.currentTarget.value)} />
        </label>
        <div className="dm-header__utility">
          <button className="dm-header__icon" type="button" aria-label="도움말 준비 중" title="도움말은 아직 준비 중입니다." disabled>
            <SymbolIcon name="help" />
          </button>
          <button className="dm-header__icon" type="button" data-screen-target="settings" aria-label="화면 설정" title="설정 열기" onClick={openSettingsScreen}>
            <SymbolIcon name="settings" />
          </button>
        </div>
      </div>

      <div className="dm-header__screen dm-header__screen--storage" aria-label="저장함 상단">
        <label className="dm-header__search">
          <SymbolIcon name="search" />
          <input id="storage-search-input" type="search" placeholder="저장 문서 검색" aria-label="저장 문서 검색" value={session.storageQuery} onChange={(event) => setStorageSearchQuery(event.currentTarget.value)} />
        </label>
        <div className="dm-header__utility">
          <span className="dm-header__meta">현재 세션 저장본만 표시</span>
        </div>
      </div>

      <div className="dm-header__screen dm-header__screen--documents" aria-label="문서 검토 상단">
        <div className="dm-header__context">
          <button className="dm-header__back" type="button" data-screen-target="desk" aria-label="문서 데스크로 돌아가기" onClick={() => setActiveScreen("desk")}>
            <SymbolIcon name="chevron_left" />
          </button>
          <div className="dm-header__doc" aria-label="현재 문서">
            <strong id="current-document-title">문서를 선택하세요</strong>
            <em id="obsidian-target-summary">문서 미선택</em>
          </div>
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
        <div className="dm-header__utility">
          <button
            className="dm-header__icon"
            type="button"
            data-screen-target="masking-settings"
            aria-label="탐지 기준"
            title="탐지 기준 조정"
            onClick={openMaskingSettingsScreen}
          >
            <SymbolIcon name="find_replace" />
          </button>
          <button
            className="dm-header__icon"
            type="button"
            data-screen-target="settings"
            aria-label="설정"
            title="설정"
            onClick={openSettingsScreen}
          >
            <SymbolIcon name="settings" />
          </button>
        </div>
      </div>

      <div className="dm-header__screen dm-header__screen--aux" aria-label="보조 화면 상단">
        <div className="dm-header__aux-title">설정 화면</div>
      </div>
    </header>
  );
}
