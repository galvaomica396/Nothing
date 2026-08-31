import { SymbolIcon } from "./ui/SymbolIcon";
import { setActiveScreen, useShellState } from "../state/shellStore";
import { beginSettingsDraft, completeSettingsDraft, saveCurrentSettings, updateSettings, useSettingsState } from "../state/settingsStore";
import { applicationController } from "../state/appControllerRuntime";

const THEME_CHOICES = [
  {
    value: "light",
    title: "라이트 모드",
    description: "웜 페이퍼와 화이트 서페이스",
  },
  {
    value: "dark",
    title: "다크 모드",
    description: "정제된 다크 서페이스와 트러스트 블루",
  },
  {
    value: "system",
    title: "시스템 설정 따름",
    description: "Windows 화면 모드 변경에 자동 반응",
  },
] as const;

export function SettingsScreen() {
  const { activePanel } = useShellState();
  const { settings } = useSettingsState();
  const returnToPreviousScreen = (): void => applicationController()?.returnFromAuxScreen() ?? setActiveScreen("desk");
  const setStatus = (message: string): void => applicationController()?.setStatus(message);
  const saveSettings = (): void => {
    const saved = saveCurrentSettings();
    applicationController()?.applySettings(saved);
    completeSettingsDraft();
    setStatus(saved.diagnostic.status === "failed" ? "설정을 저장하지 못했습니다. 저장소 상태를 확인하세요." : "설정 저장 완료");
  };

  return (
    <section
      id="settings-screen"
      className={activePanel === "settings" ? "dm-settings-screen dm-settings-screen--app is-active" : "dm-settings-screen dm-settings-screen--app"}
      data-screen-panel="settings"
      data-owner="react"
      aria-label="일반 설정"
    >
      <header className="dm-settings-topbar">
        <div className="dm-settings-topbar__lead">
          <button id="btn-settings-back" className="dm-settings-back" type="button" onClick={returnToPreviousScreen}>
            <SymbolIcon name="chevron_left" />
            <span>돌아가기</span>
          </button>
          <div className="dm-settings-topbar__heading">
            <span className="dm-settings-kicker">설정</span>
            <strong id="settings-title">화면과 앱 동작을 설정합니다</strong>
          </div>
        </div>
        <div className="dm-settings-topbar__actions">
          <button id="btn-settings-close" className="dm-btn dm-btn--ghost" type="button" onClick={returnToPreviousScreen}>닫기</button>
        </div>
      </header>

      <div className="dm-settings-scroll">
        <div className="dm-settings-layout">
          <aside className="dm-settings-subnav" aria-label="설정 분류">
            <span className="dm-settings-subnav__item is-active" aria-current="page">일반</span>
            <button className="dm-settings-subnav__item" type="button" data-screen-target="masking-settings" onClick={() => { beginSettingsDraft(); setActiveScreen("masking-settings"); }}>마스킹 규칙</button>
            <span className="dm-settings-subnav__item dm-settings-subnav__item--disabled" aria-disabled="true">탐지 항목 준비 중</span>
            <span className="dm-settings-subnav__item dm-settings-subnav__item--disabled" aria-disabled="true">보안·개인정보 준비 중</span>
            <span className="dm-settings-subnav__item dm-settings-subnav__item--disabled" aria-disabled="true">저장·내보내기 준비 중</span>
            <span className="dm-settings-subnav__item dm-settings-subnav__item--disabled" aria-disabled="true">정보 준비 중</span>
          </aside>
          <section className="dm-settings-panel" data-settings-panel="general" role="tabpanel" aria-label="일반">
            <div className="dm-settings-grid dm-settings-grid--app">
            <section className="dm-card dm-settings-card" aria-label="화면 색상">
              <div className="dm-card__header">
                <div>
                  <span className="dm-card__title">화면 색상</span>
                  <p className="dm-settings-card-hint">선택 즉시 앱 전체에 적용되고 저장됩니다.</p>
                </div>
              </div>
              <div className="dm-card__body">
                <div className="dm-theme-grid">
                  {THEME_CHOICES.map((theme) => (
                    <label className="dm-theme-choice" data-theme-preview={theme.value} key={theme.value}>
                      <input type="radio" name="settings-theme" value={theme.value} checked={settings.theme === theme.value} onChange={() => { updateSettings({ theme: theme.value }); saveCurrentSettings(); }} />
                      <span className="dm-theme-swatch" aria-hidden="true">
                        <i className="dm-theme-swatch__rail" />
                        <i className="dm-theme-swatch__head" />
                        <i className="dm-theme-swatch__accent" />
                      </span>
                      <span className="dm-theme-copy">
                        <strong>{theme.title}</strong>
                        <small>{theme.description}</small>
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            </section>

            <section className="dm-card dm-settings-card" aria-label="앱 동작">
              <div className="dm-card__header">
                <span className="dm-card__title">앱 동작</span>
              </div>
              <div className="dm-card__body">
                <label className="dm-switch">
                  <input type="checkbox" id="settings-open-output-after-save" checked={settings.openOutputAfterSave} onChange={(event) => updateSettings({ openOutputAfterSave: event.currentTarget.checked })} />
                  <span className="dm-switch__track" aria-hidden="true"><i className="dm-switch__thumb" /></span>
                  <span className="dm-switch__copy">
                    <strong>최종 저장 후 파일 위치 열기</strong>
                    <small>최종 저장이 끝나면 저장된 파일이 있는 폴더를 엽니다.</small>
                  </span>
                </label>
                <div className="dm-settings-version dm-kv">
                  <span>버전</span>
                  <strong>v{__APP_VERSION__}</strong>
                </div>
              </div>
            </section>
            </div>
          </section>
        </div>
      </div>

      <footer className="dm-settings-footer">
        <button id="btn-app-settings-reset" className="dm-btn" type="button" onClick={() => { updateSettings({ theme: "light", profile: "mixed", engine: "auto", displayMode: "black", deidentificationMode: "token", regionScope: "national", customRegions: "", customKeywords: "", pdfRedaction: true, exportMaskedText: false, openOutputAfterSave: false }); setStatus("설정 기본값 미리보기"); }}>기본값</button>
        <div className="dm-settings-footer__main">
          <button id="btn-app-settings-save" className="dm-btn dm-btn--primary" type="button" onClick={saveSettings}>설정 저장</button>
          <button id="btn-app-settings-close" className="dm-btn" type="button" onClick={returnToPreviousScreen}>닫기</button>
        </div>
      </footer>
    </section>
  );
}
