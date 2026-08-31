import type { MouseEvent } from "react";
import { SymbolIcon } from "./ui/SymbolIcon";
import { setActiveScreen, useShellState } from "../state/shellStore";
import { cancelSettingsDraft, completeSettingsDraft, isRuleDisabled, MASKING_RULE_IDS, saveCurrentSettings, setRule, updateSettings, useSettingsState } from "../state/settingsStore";
import type { MaskingRuleId } from "../state/settingsStore";
import { applicationController } from "../state/appControllerRuntime";
import { useWorkspaceState } from "../state/workspaceStore";
import { settingsScopeStatus } from "../workflowFlow";

const RULE_LABELS: Readonly<Record<MaskingRuleId, string>> = {
  rrn: "주민/외국인번호", phone: "전화번호", business_reg: "사업자등록번호", name: "이름", address: "주소", place: "지명",
  legal_party: "원고/피고", company: "회사/법인", court: "법원명", case_title: "사건명", case_number: "사건번호",
  law_firm: "법무법인", attorney: "변호사명", approval_line: "결재선", region_context: "지역문맥", doc_meta: "문서메타",
};

export function MaskingSettingsScreen() {
  const { activePanel } = useShellState();
  const settingsState = useSettingsState();
  const workspace = useWorkspaceState();
  const { settings, rules } = settingsState;
  const scope = settingsScopeStatus({ selectedDocumentPath: workspace.selectedPath, currentDocumentName: workspace.documentTitle });
  const returnToPreviousScreen = (): void => applicationController()?.returnFromAuxScreen() ?? setActiveScreen("desk");
  const setStatus = (message: string): void => applicationController()?.setStatus(message);
  const chooseRule = (event: MouseEvent<HTMLButtonElement>, rule: MaskingRuleId, enabled: boolean): void => {
    event.preventDefault();
    setRule(rule, enabled);
  };
  const saveSettings = (): void => {
    const saved = saveCurrentSettings();
    applicationController()?.applySettings(saved);
    completeSettingsDraft();
    setStatus(saved.diagnostic.status === "failed" ? "설정을 저장하지 못했습니다. 저장소 상태를 확인하세요." : "마스킹 설정 적용 완료");
  };

  return (
    <section
      id="masking-settings-screen"
      className={activePanel === "masking-settings" ? "dm-settings-screen dm-settings-screen--masking is-active" : "dm-settings-screen dm-settings-screen--masking"}
      data-screen-panel="masking-settings"
      data-owner="react"
      aria-label="마스킹 설정"
    >
      <header className="dm-settings-topbar">
        <div className="dm-settings-topbar__lead">
          <button id="btn-masking-settings-back" className="dm-settings-back" type="button" onClick={returnToPreviousScreen}>
            <SymbolIcon name="chevron_left" />
            <span>돌아가기</span>
          </button>
          <div className="dm-settings-topbar__heading">
            <span className="dm-settings-kicker">마스킹 설정</span>
            <strong>마스킹 규칙</strong>
            <span className="dm-settings-topbar__helper">탐지 항목과 가림 방식을 현재 작업에 적용합니다.</span>
          </div>
        </div>
        <div className="dm-settings-topbar__actions">
          <button id="btn-masking-settings-cancel" className="dm-btn dm-btn--ghost" type="button" onClick={() => { if (!cancelSettingsDraft()) setStatus("취소할 설정 스냅샷이 없습니다. 설정 화면을 다시 열어주세요."); }}>변경 취소</button>
          <button id="btn-masking-settings-preview" className="dm-btn dm-btn--ghost" type="button" onClick={() => { applicationController()?.refreshFinalSaveSummary(); setStatus("현재 마스킹 설정으로 저장 조건을 미리 계산했습니다."); }}>미리보기</button>
          <button id="btn-masking-settings-apply" className="dm-btn dm-btn--primary" type="button" onClick={saveSettings}>설정 적용</button>
        </div>
      </header>

      <div className="dm-settings-scroll">
        <div className="dm-settings-layout">
          <aside className="dm-settings-subnav" aria-label="마스킹 설정 분류">
            <a className="dm-settings-subnav__item is-active" href="#masking-section-rules">마스킹 규칙</a>
            <a className="dm-settings-subnav__item" href="#masking-section-document">문서·탐지 방식</a>
            <a className="dm-settings-subnav__item" href="#masking-section-output">저장·산출물</a>
            <a className="dm-settings-subnav__item" href="#masking-section-advanced">고급 옵션</a>
            <a className="dm-settings-subnav__item" href="#masking-section-scope">적용 상태</a>
          </aside>
          <div className="dm-settings-grid dm-settings-grid--masking">
          <section className="dm-card dm-settings-card dm-settings-card--rules" aria-label="마스킹 카테고리">
            <span id="masking-section-rules" className="dm-settings-anchor" aria-hidden="true" />
            <div className="dm-card__header">
              <div>
                <span className="dm-card__title">탐지 항목</span>
                <p className="dm-settings-card-hint">각 항목을 사용하거나 제외할 수 있습니다.</p>
              </div>
            </div>
            <div className="dm-card__body">
              <div className="dm-rule-grid" id="rule-grid">
                {MASKING_RULE_IDS.map((rule) => {
                  const disabled = isRuleDisabled(rule);
                  const enabled = rules[rule];
                  const label = RULE_LABELS[rule];
                  return <label className={disabled ? "dm-rule-card is-disabled" : "dm-rule-card"} key={rule}><input type="checkbox" id={`rule-${rule}`} checked={enabled} disabled={disabled} onChange={(event) => setRule(rule, event.currentTarget.checked)} /><span className="dm-rule-card__label">{label}</span><span className="dm-rule-toggle" role="group" aria-label={`${label} 사용 여부`}><button type="button" value="enabled" data-rule-control={`rule-${rule}`} aria-pressed={enabled} disabled={disabled} onClick={(event) => chooseRule(event, rule, true)}>사용</button><button type="button" value="disabled" data-rule-control={`rule-${rule}`} aria-pressed={!enabled} disabled={disabled} onClick={(event) => chooseRule(event, rule, false)}>사용 안 함</button></span></label>;
                })}
              </div>
            </div>
          </section>

          <section className="dm-card dm-settings-card dm-settings-card--document" aria-label="문서와 탐지 방식">
            <span id="masking-section-document" className="dm-settings-anchor" aria-hidden="true" />
            <div className="dm-card__header">
              <span className="dm-card__title">문서와 탐지 방식</span>
            </div>
            <div className="dm-card__body dm-settings-fields">
              <div className="dm-setting-row">
                <div className="dm-setting-row__copy"><label htmlFor="engine">PDF 추출 엔진</label><p>문서에 맞는 추출 엔진을 선택합니다.</p></div>
                <select id="engine" className="dm-select" value={settings.engine} onChange={(event) => updateSettings({ engine: event.currentTarget.value })}>
                  <option value="auto">자동 선택</option>
                  <option value="marker">Marker PDF</option>
                  <option value="paddle">Paddle OCR</option>
                  <option value="pymupdf">PyMuPDF</option>
                  <option value="pypdf">PyPDF</option>
                </select>
              </div>
              <div className="dm-setting-row dm-setting-row--segmented">
                <div className="dm-setting-row__copy"><label htmlFor="display-mode">마스킹 방식</label><p>원문을 가린 뒤 표시할 방식을 선택합니다.</p></div>
                <div className="dm-choice-control">
                  <select id="display-mode" className="dm-select dm-visually-hidden" value={settings.displayMode} tabIndex={-1} aria-hidden="true" onChange={(event) => updateSettings({ displayMode: event.currentTarget.value === "black" || event.currentTarget.value === "label_en" || event.currentTarget.value === "label_ko" || event.currentTarget.value === "pseudonym" ? event.currentTarget.value : settings.displayMode })}>
                    <option value="black">검정 박스</option>
                    <option value="label_en">영문 라벨</option>
                    <option value="label_ko">한글 라벨</option>
                    <option value="pseudonym">가명 표시</option>
                  </select>
                  <div className="dm-inline-segment" role="group" aria-label="마스킹 방식 선택">
                    <button id="btn-display-mode-black" className="dm-choice-card" type="button" value="black" aria-controls="display-mode" aria-pressed={settings.displayMode === "black"} onClick={() => updateSettings({ displayMode: "black" })}>
                      <span className="dm-choice-card__title">블랙박스</span>
                    </button>
                    <button id="btn-display-mode-label-en" className="dm-choice-card" type="button" value="label_en" aria-controls="display-mode" aria-pressed={settings.displayMode === "label_en"} onClick={() => updateSettings({ displayMode: "label_en" })}>
                      <span className="dm-choice-card__title">영문 라벨</span>
                    </button>
                    <button id="btn-display-mode-label-ko" className="dm-choice-card" type="button" value="label_ko" aria-controls="display-mode" aria-pressed={settings.displayMode === "label_ko"} onClick={() => updateSettings({ displayMode: "label_ko" })}>
                      <span className="dm-choice-card__title">한글 라벨</span>
                    </button>
                    <button id="btn-display-mode-pseudonym" className="dm-choice-card" type="button" value="pseudonym" aria-controls="display-mode" aria-pressed={settings.displayMode === "pseudonym"} onClick={() => updateSettings({ displayMode: "pseudonym" })}>
                      <span className="dm-choice-card__title">가명 표시</span>
                    </button>
                  </div>
                </div>
              </div>
              <div className="dm-field">
                <span className="dm-field__label">TXT 추가 산출</span>
                <label className="dm-checkbox"><input type="checkbox" id="settings-export-masked-text" checked={settings.exportMaskedText} onChange={(event) => updateSettings({ exportMaskedText: event.currentTarget.checked })} /> 비식별 TXT 함께 저장</label>
                <p className="dm-field-hint">켜면 아래 변환 방식을 적용한 TXT를 최종 PDF와 함께 저장합니다. 원문 TXT는 저장하지 않습니다.</p>
              </div>
              <div className="dm-field dm-choice-field">
                <label htmlFor="deidentification-policy">TXT 비식별 변환</label>
                <p className="dm-field-hint">TXT 저장 시에만 적용하며 PDF 가림 모양과 독립적으로 선택합니다.</p>
                <div className="dm-choice-control">
                  <select id="deidentification-policy" className="dm-select dm-visually-hidden" value={settings.deidentificationMode} disabled={!settings.exportMaskedText} tabIndex={-1} aria-hidden="true" onChange={(event) => updateSettings({ deidentificationMode: event.currentTarget.value === "token" || event.currentTarget.value === "partial" || event.currentTarget.value === "pseudonym" ? event.currentTarget.value : settings.deidentificationMode })}>
                    <option value="token">완전 치환 · 유형 토큰</option>
                    <option value="partial">부분 마스킹 · 일부 형식 유지</option>
                    <option value="pseudonym">일관 가명 · 같은 값은 같은 가명</option>
                  </select>
                  <div className="dm-choice-grid" role="group" aria-label="비식별 TXT 변환 방식 선택">
                    <button id="btn-policy-token" className="dm-choice-card" type="button" value="token" aria-controls="deidentification-policy" aria-pressed={settings.deidentificationMode === "token"} disabled={!settings.exportMaskedText} onClick={() => updateSettings({ deidentificationMode: "token" })}>
                      <span className="dm-choice-card__title">완전 치환</span>
                      <span className="dm-choice-card__description">모든 값을 대표 유형 토큰으로 바꿉니다.</span>
                      <span className="dm-choice-preview"><span>홍길동 → <code>[NAME]</code></span><span>010-1234-5678 → <code>[PHONE]</code></span></span>
                    </button>
                    <button id="btn-policy-partial" className="dm-choice-card" type="button" value="partial" aria-controls="deidentification-policy" aria-pressed={settings.deidentificationMode === "partial"} disabled={!settings.exportMaskedText} onClick={() => updateSettings({ deidentificationMode: "partial" })}>
                      <span className="dm-choice-card__title">부분 마스킹</span>
                      <span className="dm-choice-card__description">형식을 유지하며 식별 가능한 일부를 가립니다.</span>
                      <span className="dm-choice-preview"><span>홍길동 → <code>홍OO</code></span><span>010-1234-5678 → <code>010-****-5678</code></span><span>hong@test.com → <code>h***@test.com</code></span></span>
                    </button>
                    <button id="btn-policy-pseudonym" className="dm-choice-card" type="button" value="pseudonym" aria-controls="deidentification-policy" aria-pressed={settings.deidentificationMode === "pseudonym"} disabled={!settings.exportMaskedText} onClick={() => updateSettings({ deidentificationMode: "pseudonym" })}>
                      <span className="dm-choice-card__title">일관 가명</span>
                      <span className="dm-choice-card__description">같은 원본 값은 같은 가명으로 바꿉니다.</span>
                      <span className="dm-choice-preview"><span>홍길동 → <code>박지훈</code></span><span>010-1234-5678 → <code>010-0000-1199</code></span></span>
                    </button>
                  </div>
                </div>
              </div>
              <div className="dm-field">
                <label htmlFor="region-scope">지역 범위</label>
                <select id="region-scope" className="dm-select" value={settings.regionScope} onChange={(event) => updateSettings({ regionScope: event.currentTarget.value === "seoul" || event.currentTarget.value === "national" || event.currentTarget.value === "custom" ? event.currentTarget.value : settings.regionScope, customRegions: event.currentTarget.value === "custom" ? settings.customRegions : "" })}>
                  <option value="seoul">서울/수도권</option>
                  <option value="national">전국</option>
                  <option value="custom">사용자 지정 지역</option>
                </select>
              </div>
              <div className="dm-field config-cell">
                <label htmlFor="custom-regions">사용자 지정 지역명</label>
                <input id="custom-regions" className="dm-input" type="text" placeholder="예: ○○특별시 ○○구, ○○동" value={settings.customRegions} disabled={settings.regionScope !== "custom"} onChange={(event) => updateSettings({ customRegions: event.currentTarget.value })} />
                <p className="dm-field-hint">지역 범위에서 사용자 지정 지역을 선택하면 직접 입력할 수 있습니다.</p>
              </div>
            </div>
          </section>

          <section className="dm-card dm-settings-card dm-settings-card--advanced" aria-label="고급 옵션">
            <span id="masking-section-advanced" className="dm-settings-anchor" aria-hidden="true" />
            <div className="dm-card__header">
              <div>
                <span className="dm-card__title">고급 옵션</span>
                <p className="dm-settings-card-hint">기본은 혼합(자동 라우팅)이며, 대부분의 문서는 별도 선택 없이 처리됩니다.</p>
              </div>
            </div>
            <div className="dm-card__body dm-settings-fields">
              <div className="dm-setting-row">
                <div className="dm-setting-row__copy"><label htmlFor="profile">문서 유형 수동 선택</label><p>내부검토·대내외 시행·legal을 선택하면 해당 유형의 고정영역 적용 범위를 확정하고 경계 검토를 생략합니다.</p></div>
                <select id="profile" className="dm-select" value={settings.profile} onChange={(event) => updateSettings({ profile: event.currentTarget.value === "internal_review" || event.currentTarget.value === "official_dispatch" || event.currentTarget.value === "mixed" || event.currentTarget.value === "legal" ? event.currentTarget.value : settings.profile })}>
                  <option value="mixed">혼합(자동 라우팅)</option>
                  <option value="internal_review">내부검토보고서</option>
                  <option value="official_dispatch">대내외 시행문서</option>
                  <option value="legal">법률문서</option>
                </select>
              </div>
            </div>
          </section>

          <section className="dm-card dm-settings-card dm-settings-card--safety" aria-label="산출물">
            <span id="masking-section-output" className="dm-settings-anchor" aria-hidden="true" />
            <div className="dm-card__header">
              <span className="dm-card__title">산출물</span>
            </div>
            <div className="dm-card__body dm-settings-fields">
              <div className="dm-field">
                <span className="dm-field__label">저장 안전장치</span>
                <label className="dm-checkbox"><input type="checkbox" id="opt-pdf-redaction" checked={settings.pdfRedaction} onChange={(event) => updateSettings({ pdfRedaction: event.currentTarget.checked })} /> 자동 PDF 레닥션</label>
                <p className="dm-field-hint">PDF 원문 영역을 실제로 제거하고 선택한 가림 모양을 새로 그립니다.</p>
              </div>
            </div>
          </section>

          <section className="dm-card dm-settings-card dm-settings-card--scope" aria-label="현재 작업 적용 상태">
            <span id="masking-section-scope" className="dm-settings-anchor" aria-hidden="true" />
            <div className="dm-card__header">
              <span className="dm-card__title">현재 작업 적용 상태</span>
            </div>
            <div className="dm-card__body">
              <div className="dm-scope-grid">
                <div className="dm-scope-item">
                  <strong>앱 기본값</strong>
                  <span>새 문서를 열 때 기본으로 사용할 마스킹 설정입니다.</span>
                </div>
                <div className="dm-scope-item">
                  <strong>현재 문서 작업값</strong>
                  <span id="settings-apply-scope-status" title={scope.scopeLabel}>{scope.applyLabel}</span>
                </div>
              </div>
            </div>
          </section>
        </div>
        </div>
      </div>
    </section>
  );
}
